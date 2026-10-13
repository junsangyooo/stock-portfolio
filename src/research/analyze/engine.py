"""Run the analysis: assemble → one call → validate → store.

    python -m src.research.analyze KR:005930 [--force-chart] [--dry]

Validation is deliberately shallow. Enums are coerced, numbers are clamped,
and prices that are not numbers are dropped — but nothing is invented to fill
a gap. A missing invalidation level stays missing, because a planner that
receives a made-up stop is worse off than one that receives none.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Optional

from ...core.kst import KST
from ..llm_backend import SLOW_MODEL, ClaudeCliBackend, _parse_json
from ..verdict import KIND, clamp_confidence, clamp_score, label_from_score, persist
from . import inputs, prompt

log = logging.getLogger(__name__)

_LABELS = ("strong_sell", "sell", "neutral", "buy", "strong_buy")
_HORIZONS = ("immediate", "near", "mid", "long")
_LEANS = ("bullish", "neutral", "bearish")
_CASES = ("bull", "base", "bear")
_STRENGTH = ("weak", "medium", "strong")


def _enum(v, allowed: tuple, default=None):
    return v if v in allowed else default


def _num(v) -> Optional[float]:
    """A price only survives if it is actually a number."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def anchors_from(envelope: dict) -> list:
    """Price levels the chart pipeline actually detected — swing S/R (with
    their test counts), fibonacci retracements, classic pivots.

    Every price the model quotes is checked against these. The account card
    sets these prices beside a holding as its stop and target, so a number
    that came from nowhere has to be labelled as such rather than passed along
    looking like the rest.
    """
    facts = next((d["data"] for d in envelope.get("domains", [])
                  if d["id"] == "chart" and d.get("data")), None) or {}
    st = facts.get("structure") or {}
    out: list = []
    for lv in ((st.get("support_resistance") or {}).get("levels") or []):
        px = _num(lv.get("price")) if isinstance(lv, dict) else None
        if px is not None:
            try:
                out.append((px, int(lv.get("tests") or 1)))
            except (TypeError, ValueError):
                out.append((px, 1))
    for v in ((st.get("fibonacci") or {}).get("levels") or {}).values():
        px = _num(v)
        if px is not None:
            out.append((px, 1))
    for k, v in (st.get("pivots") or {}).items():
        if k in ("PP", "R1", "R2", "R3", "S1", "S2", "S3"):
            px = _num(v)
            if px is not None:
                out.append((px, 1))
    return out


def _anchor(price: Optional[float], anchors: list, tol: float = 0.015) -> dict:
    """Snap a quoted price to the nearest detected level within 1.5%.

    Nothing is dropped — a number outside every anchor is kept and flagged
    `llm_only`, because the reader wants to see it and the planner needs to
    know it is not grounded.
    """
    if price is None:
        return {"price": None, "anchored": False}
    if not anchors:
        return {"price": price, "anchored": False}
    best = min(anchors, key=lambda a: abs(a[0] - price))
    if best[0] and abs(best[0] - price) / abs(best[0]) <= tol:
        out = {"price": round(best[0], 4), "anchored": True,
               "anchor_tests": best[1]}
        if abs(best[0] - price) > 1e-9:
            out["quoted"] = round(price, 4)     # what the model said
        return out
    return {"price": price, "anchored": False, "llm_only": True}


def _levels(raw, anchors: Optional[list] = None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    anchors = anchors or []

    def band(items, with_strength: bool) -> list:
        out = []
        for it in (items or [])[:4]:
            if not isinstance(it, dict):
                continue
            px = _num(it.get("price"))
            if px is None:
                continue
            row = {**_anchor(px, anchors),
                   "basis_ko": str(it.get("basis_ko") or "")[:100]}
            if with_strength:
                row["strength"] = _enum(it.get("strength"), _STRENGTH, "medium")
            out.append(row)
        return out

    def point(d, *keys) -> Optional[dict]:
        if not isinstance(d, dict):
            return None
        vals = {k: _num(d.get(k)) for k in keys}
        if any(v is None for v in vals.values()):
            return None
        out = {k: _anchor(v, anchors) for k, v in vals.items()}
        # keep the flat number too: a planner reads `price`, the UI reads the
        # anchoring, and neither should have to know about the other
        return {**{k: out[k]["price"] for k in keys},
                "anchoring": out,
                "rationale_ko": str(d.get("rationale_ko") or "")[:120]}

    return {
        "current": _num(raw.get("current")),
        "anchored_from": len(anchors),
        "support": band(raw.get("support"), True),
        "resistance": band(raw.get("resistance"), True),
        "entry_zone": point(raw.get("entry_zone"), "low", "high"),
        "invalidation": point(raw.get("invalidation"), "price"),
        "targets": band(raw.get("targets"), False),
    }


def _outlook(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    scenarios = []
    for sc in (raw.get("scenarios") or [])[:3]:
        if not isinstance(sc, dict):
            continue
        case = _enum(sc.get("case"), _CASES)
        if case is None:
            continue
        scenarios.append({
            "case": case,
            "narrative_ko": str(sc.get("narrative_ko") or "")[:150],
            "confirm": [str(x)[:120] for x in (sc.get("confirm") or [])][:4],
            "invalidate": [str(x)[:120] for x in (sc.get("invalidate") or [])][:4],
        })
    return {
        "horizon": _enum(raw.get("horizon"), _HORIZONS),
        "base_ko": str(raw.get("base_ko") or "")[:300],
        "scenarios": scenarios,
    }


def _strs(v, n: int, cap: int = 300) -> list:
    return [str(x)[:cap] for x in (v or []) if str(x).strip()][:n] \
        if isinstance(v, list) else []


def normalize(data: dict, envelope: dict) -> dict:
    """Coerce the model's JSON into the contract. Fills nothing in."""
    v = data.get("verdict") if isinstance(data.get("verdict"), dict) else {}
    score = clamp_score(v.get("score", 0))
    label = _enum(v.get("label"), _LABELS) or label_from_score(score)

    domain_view = {}
    for d in ("chart", "fundamental", "news"):
        blk = (v.get("domain_view") or {}).get(d)
        blk = blk if isinstance(blk, dict) else {}
        domain_view[d] = {
            "one_line_ko": str(blk.get("one_line_ko") or "")[:300],
            "lean": _enum(blk.get("lean"), _LEANS, "neutral"),
        }

    obs = []
    for o in (data.get("observations") or [])[:16]:
        if isinstance(o, dict) and str(o.get("fact_ko") or "").strip():
            obs.append({"domain": str(o.get("domain") or "")[:20],
                        "fact_ko": str(o["fact_ko"])[:150],
                        "cite": str(o.get("cite") or "")[:60]})
    readings = []
    for r in (data.get("readings") or [])[:8]:
        if isinstance(r, dict) and str(r.get("claim_ko") or "").strip():
            readings.append({
                "claim_ko": str(r["claim_ko"])[:150],
                "from": [i for i in (r.get("from") or [])
                         if isinstance(i, int) and 0 <= i < len(obs)][:8],
                "confidence": clamp_confidence(r.get("confidence", 0.5)),
            })
    counter = data.get("counter") if isinstance(data.get("counter"), dict) else {}

    return {
        "symbol_id": envelope["symbol"]["symbol_id"],
        "name": envelope["symbol"]["name"],
        "kind": KIND,
        "date": envelope["asof"],
        "status": "ok",
        "available_domains": envelope["available"],
        "observations": obs,
        "readings": readings,
        "counter": {
            "case_ko": str(counter.get("case_ko") or "")[:400],
            "what_would_show_it": _strs(counter.get("what_would_show_it"), 3),
        },
        "score": score,
        "label": label,
        "confidence": clamp_confidence(v.get("confidence", 0.5)),
        "summary_ko": str(v.get("summary_ko") or "")[:400],
        "drivers": _strs(v.get("drivers"), 3, 80),
        "risks": _strs(v.get("risks"), 3, 80),
        "domain_view": domain_view,
        "disagreement_ko": (str(v["disagreement_ko"])[:400]
                            if v.get("disagreement_ko") else None),
        "outlook": _outlook(data.get("outlook")),
        "levels": _levels(data.get("levels"), anchors_from(envelope)),
        "reassess_when": _strs(data.get("reassess_when"), 4),
        "limitations": _strs(data.get("limitations"), 3),
    }


def _facts_only(envelope: dict, reason: str) -> dict:
    """When the call fails twice, show what was computed and say so.

    The old judge averaged whatever cached domain verdicts it could find. With
    a single call there is nothing to average, and inventing a number from
    nothing would be worse than an honest blank.
    """
    return {
        "symbol_id": envelope["symbol"]["symbol_id"],
        "name": envelope["symbol"]["name"],
        "kind": KIND, "date": envelope["asof"], "status": "facts_only",
        "available_domains": envelope["available"],
        "score": 0.0, "label": "neutral", "confidence": 0.0,
        "summary_ko": ("AI 종합 판단을 만들지 못했습니다. 아래 지표는 계산된 "
                       "사실이며, 판단은 붙어 있지 않습니다."),
        "observations": [], "readings": [],
        "counter": {"case_ko": "", "what_would_show_it": []},
        "drivers": [], "risks": [], "domain_view": {},
        "disagreement_ko": None,
        "outlook": {"horizon": None, "base_ko": "", "scenarios": []},
        "levels": _levels(None), "reassess_when": [],
        "limitations": [f"판단 생성 실패: {reason[:200]}"],
    }


# How long a batch root is reused before a fresh one is made. A fork off a
# 13-minute-old root still read its dictionaries from cache (measured
# 2026-09-15); past that nothing was measured, so the refresh sits below it.
# One refresh is one cheap call — a cold root would cost the whole prompt on
# every symbol after it.
_ROOT_TTL_S = 20 * 60
# One root per reasoning budget, keyed by the `--effort` value (None = the CLI
# default). The thinking settings are part of what the prompt cache matches
# on, so a fork asked at `low` off a root made at the default most likely
# bought the dictionaries again: in the week to 2026-10-13 the server read
# only about 20% of its input from cache, with every fork at `low` and the
# root at the default.
_roots: dict[Optional[str], tuple] = {}
_root_lock = threading.Lock()


def batch_root(backend=None, effort: Optional[str] = None) -> Optional[str]:
    """A session that already holds the dictionaries, for a batch to fork.

    Measured: a fork reads 50,743 tokens from cache and pays only for the
    symbol's own data — $0.086 a symbol against $0.20~0.24 for the whole
    prompt each time. `claude -p` caches a user message as one block, so this
    session boundary is the only place the saving can come from; reordering
    inside one message saves nothing.

    `effort` must be the budget the forks will be asked at: the root is made
    with the same `--effort`, so a fork's thinking settings match the cached
    prefix. Each budget keeps its own root.

    None means the root call failed. The caller then sends the full prompt,
    which is exactly what every call did before this existed.
    """
    with _root_lock:
        held = _roots.get(effort)
        if held and time.monotonic() - held[0] < _ROOT_TTL_S:
            return held[1]
        sid = str(uuid.uuid4())
        extra = ["--session-id", sid]
        if effort:
            extra += ["--effort", effort]
        try:
            (backend or ClaudeCliBackend(model=SLOW_MODEL,
                                         stage="analyze:root")).call(
                prompt.SYSTEM, prompt.root_context(inputs.references()),
                model=SLOW_MODEL, extra_args=extra)
        except Exception as e:  # noqa: BLE001 — a dead root is a slower batch
            # Remember the failure for the same TTL. Without this a caller
            # asking per symbol retries root creation for every one of them,
            # and an outage (expired auth, overload — both have happened)
            # turns a 20-symbol batch into 40 failing calls.
            log.warning("batch root failed, full prompts instead: %s",
                        str(e)[:200])
            _roots[effort] = (time.monotonic(), None)
            return None
        _roots[effort] = (time.monotonic(), sid)
        return sid


def analyze(symbol_id: str, name: Optional[str] = None, *,
            force_chart: bool = False, backend=None,
            store: bool = True, root: Optional[str] = None,
            effort: Optional[str] = None) -> dict:
    """The whole thing. One deterministic pass, one call, one stored result.

    `root` is a batch session holding the dictionaries (see `batch_root`); the
    first attempt forks it and sends only this symbol's data. The retry always
    sends the whole prompt — if the fork failed, the session is the first
    thing to suspect, and a symbol must not be lost to it. The root must have
    been made with the same `effort` (`batch_root(effort=…)`), or the fork
    re-buys the dictionaries.

    `effort` is the CLI's reasoning budget (low|medium|high|xhigh|max).
    Measured on US:DHT 2026-09-15: the default spent 21,945 output tokens,
    `low` spent 5,448 for an answer of the same shape (14 observations, 8/8
    prices anchored, same targets) — most of the default budget is thinking
    that never reaches the stored result. It did not land on the same verdict
    though (buy → neutral), so which is better is a question for
    `analyze.score`, and the level used is stored on the result so it can
    tell them apart.
    """
    t0 = time.monotonic()
    envelope = inputs.build(symbol_id, name, force_chart=force_chart)
    if not envelope["available"]:
        out = _facts_only(envelope, "수집된 데이터가 전혀 없습니다")
    else:
        backend = backend or ClaudeCliBackend(model=SLOW_MODEL,
                                              stage="analyze")
        out, err = None, ""
        for attempt, sid in ((1, root), (2, None)):
            if sid:
                system, user = "", prompt.symbol_part(envelope)
                extra = ["--resume", sid, "--fork-session"]
            else:
                system, user, extra = prompt.SYSTEM, prompt.build(envelope), []
            if effort:
                extra = list(extra) + ["--effort", effort]
            try:
                raw = backend.call(system, user, model=SLOW_MODEL,
                                   extra_args=extra)
                out = normalize(_parse_json(raw), envelope)
                break
            except Exception as e:  # noqa: BLE001 — retry once, then be honest
                err = str(e)[:300]
                log.warning("analyze %s attempt %d (%s) failed: %s", symbol_id,
                            attempt, "fork" if sid else "full", err)
        if out is None:
            out = _facts_only(envelope, err)
    out["effort"] = effort or "default"
    out["elapsed_s"] = round(time.monotonic() - t0, 1)
    out["generated_at"] = datetime.now(KST).isoformat()
    return persist(symbol_id, out["date"], out) if store else out
