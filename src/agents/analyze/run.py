"""Run the analysis: assemble → one call → validate → store.

    python -m src.agents.analyze.run KR:005930 [--force-chart] [--dry]

Validation is deliberately shallow. Enums are coerced, numbers are clamped,
and prices that are not numbers are dropped — but nothing is invented to fill
a gap. A missing invalidation level stays missing, because a planner that
receives a made-up stop is worse off than one that receives none.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from typing import Optional

from ...core.kst import KST
from ..llm_backend import SLOW_MODEL, ClaudeCliBackend, _parse_json
from ..verdict import clamp_confidence, clamp_score, label_from_score, persist
from . import inputs, prompt

log = logging.getLogger(__name__)

KIND = "analysis"
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


def _levels(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}

    def band(items, with_strength: bool) -> list:
        out = []
        for it in (items or [])[:4]:
            if not isinstance(it, dict):
                continue
            px = _num(it.get("price"))
            if px is None:
                continue
            row = {"price": px, "basis_ko": str(it.get("basis_ko") or "")[:200]}
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
        return {**vals, "rationale_ko": str(d.get("rationale_ko") or "")[:300]}

    return {
        "current": _num(raw.get("current")),
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
            "narrative_ko": str(sc.get("narrative_ko") or "")[:600],
            "confirm": [str(x)[:200] for x in (sc.get("confirm") or [])][:5],
            "invalidate": [str(x)[:200] for x in (sc.get("invalidate") or [])][:5],
        })
    return {
        "horizon": _enum(raw.get("horizon"), _HORIZONS),
        "base_ko": str(raw.get("base_ko") or "")[:800],
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
                        "fact_ko": str(o["fact_ko"])[:400],
                        "cite": str(o.get("cite") or "")[:120]})
    readings = []
    for r in (data.get("readings") or [])[:8]:
        if isinstance(r, dict) and str(r.get("claim_ko") or "").strip():
            readings.append({
                "claim_ko": str(r["claim_ko"])[:400],
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
            "case_ko": str(counter.get("case_ko") or "")[:800],
            "what_would_show_it": _strs(counter.get("what_would_show_it"), 5),
        },
        "score": score,
        "label": label,
        "confidence": clamp_confidence(v.get("confidence", 0.5)),
        "summary_ko": str(v.get("summary_ko") or "")[:800],
        "drivers": _strs(v.get("drivers"), 8),
        "risks": _strs(v.get("risks"), 8),
        "domain_view": domain_view,
        "disagreement_ko": (str(v["disagreement_ko"])[:400]
                            if v.get("disagreement_ko") else None),
        "outlook": _outlook(data.get("outlook")),
        "levels": _levels(data.get("levels")),
        "reassess_when": _strs(data.get("reassess_when"), 8),
        "limitations": _strs(data.get("limitations"), 8),
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


def analyze(symbol_id: str, name: Optional[str] = None, *,
            force_chart: bool = False, backend=None,
            timeout_s: float = 900, store: bool = True) -> dict:
    """The whole thing. One deterministic pass, one call, one stored result."""
    t0 = time.monotonic()
    envelope = inputs.build(symbol_id, name, force_chart=force_chart)
    if not envelope["available"]:
        out = _facts_only(envelope, "수집된 데이터가 전혀 없습니다")
    else:
        backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
        user = prompt.build(envelope)
        out, err = None, ""
        for attempt in (1, 2):
            try:
                raw = backend.call(prompt.SYSTEM, user, timeout_s=timeout_s,
                                   model=SLOW_MODEL)
                out = normalize(_parse_json(raw), envelope)
                break
            except Exception as e:  # noqa: BLE001 — retry once, then be honest
                err = str(e)[:300]
                log.warning("analyze %s attempt %d failed: %s", symbol_id,
                            attempt, err)
        if out is None:
            out = _facts_only(envelope, err)
    out["elapsed_s"] = round(time.monotonic() - t0, 1)
    out["generated_at"] = datetime.now(KST).isoformat()
    return persist(KIND, symbol_id, out["date"], out) if store else out


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol_id")
    ap.add_argument("--name", default=None)
    ap.add_argument("--force-chart", action="store_true",
                    help="recompute chart facts even if today's row exists")
    ap.add_argument("--dry", action="store_true",
                    help="print the assembled prompt, make no call")
    args = ap.parse_args()

    if args.dry:
        env = inputs.build(args.symbol_id, args.name,
                           force_chart=args.force_chart)
        text = prompt.build(env)
        print(f"# domains: {[(d['id'], d['status']) for d in env['domains']]}",
              file=sys.stderr)
        print(f"# prompt chars: {len(text):,}", file=sys.stderr)
        print(text)
        return 0

    out = analyze(args.symbol_id, args.name, force_chart=args.force_chart)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
