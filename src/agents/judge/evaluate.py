"""Judge agent — single cross-domain evaluator for the 🧠 종합 분석 button.

Reads three domains' deterministic facts + (when present) recently-cached
domain verdicts and asks claude to apply judge_rules.md ONCE to produce a
unified verdict. NO orchestration / no sub-agent fan-out — see
judge_rules.md for the rationale (single call beats 4× for
this workload).

Pipeline:
  assemble_inputs   chart facts (live, deterministic) + fundamental facts
                    (live, ETF-aware) + news state (read theme states) +
                    cached_verdicts (last 24h judgment cache, optional) +
                    holding_context (qty/avg_cost/pnl/weight)
  _judge            apply judge_rules.md via claude -p (Sonnet) → unified
                    verdict with cross_reads
  evaluate_symbol   assemble → judge → persist to data/analysis/judge/...

Output schema is what _judge() below returns.
Ops (design §⑥): claude -p failures retry once, then fall back to a heuristic
synthesis of cached_verdicts (no LLM); every call is logged to
data/logs/judge.log (latency, score, rule IDs, missing domains).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ...core.kst import KST as _KST
from ..verdict import (
    LABELS as _LABELS, clamp_confidence, clamp_score, label_from_score, persist,
)
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json


_ACTIONS = ("exit", "trim", "hold", "watch", "add")
_PRICED_IN = ("unpriced", "partially_priced", "mostly_priced")
_HORIZONS = ("immediate", "near", "mid", "long")

_RULES = (Path(__file__).parent / "judge_rules.md").read_text(encoding="utf-8")

# Cache window for sourcing other agents' verdicts. Anything older than this
# is ignored — markets move; stale verdicts can mislead more than help.
_VERDICT_CACHE_HOURS = 24

_log = logging.getLogger("judge")


def _get_log() -> logging.Logger:
    """Dedicated file logger → data/logs/judge.log (design §⑥). Lazy so the
    dir exists by first use and module import stays side-effect free."""
    if not _log.handlers:
        from ...core.paths import LOGS_DIR
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(LOGS_DIR / "judge.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(h)
        _log.setLevel(logging.INFO)
        _log.propagate = False
    return _log


# ── input assembly ──────────────────────────────────────────────────────────

def _latest_cached_verdict(kind: str, symbol_id: str) -> Optional[dict]:
    """Return the most recent verdict for a domain if it's within
    _VERDICT_CACHE_HOURS, else None.  Files live at
    data/analysis/{kind}/{MARKET_CODE}/{YYYYMMDD}.json."""
    from ...core.paths import ANALYSIS_DIR
    safe = symbol_id.replace(":", "_")
    d = ANALYSIS_DIR / kind / safe
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    try:
        body = json.loads(files[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    ts_str = body.get("generated_at") or body.get("queried_at")
    if not ts_str:
        return body  # no timestamp — accept (chart verdicts may not have one)
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return body
    age_h = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600
    if age_h > _VERDICT_CACHE_HOURS:
        return None
    # Slim down — only the fields the judge actually reads.
    return {
        "date": body.get("date"),
        "score": body.get("score"),
        "label": body.get("label"),
        "confidence": body.get("confidence"),
        "summary_ko": body.get("summary_ko"),
        "drivers": body.get("drivers"),
        "risks": body.get("risks"),
        "priced_in": body.get("priced_in"),
        "time_horizon": body.get("time_horizon"),
        "axes": body.get("axes"),
        "stance": body.get("stance"),
    }


def _holding_context(symbol_id: str) -> Optional[dict]:
    """Holdings/qty/avg_cost across all accounts (judge cares about total
    exposure, not per-account). Latest quote → pnl + weight_in_book."""
    try:
        from ...portfolio.store.db import connect
        from ...market.store import quote_store
    except Exception:  # noqa: BLE001 — judge can run without holdings context
        return None
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT account_id, avg_cost, qty FROM holdings WHERE symbol_id=?",
                (symbol_id,),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    total_qty = sum(float(r[2] or 0) for r in rows)
    if total_qty <= 0:
        return None
    total_cost = sum(float(r[1] or 0) * float(r[2] or 0) for r in rows)
    avg_cost = total_cost / total_qty if total_qty else 0.0
    try:
        q = quote_store.get(symbol_id)
        # Quote is a dataclass-like with .price attribute (or dict)
        px = float(getattr(q, "price", None) if q else 0) or 0.0
    except Exception:  # noqa: BLE001
        px = 0.0
    eval_now = px * total_qty
    pnl_pct = ((px - avg_cost) / avg_cost * 100) if avg_cost else None
    return {
        "qty": total_qty,
        "avg_cost": round(avg_cost, 2) if avg_cost else None,
        "current_price": px or None,
        "eval": round(eval_now, 0) if eval_now else None,
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        # weight_in_account is harder to compute generically without a target
        # account context; left null. UI can pass it if needed later.
    }


def assemble_inputs(symbol_id: str, name: Optional[str] = None) -> dict:
    """Gather everything the judge sees — read-only, no LLM calls.

    Three live snapshots (chart facts / fundamental facts / news state) +
    three optional cached verdicts (24h window) + holding context. Any
    domain may be missing; the rulebook §C5/§C6 handles that explicitly."""
    name = name or symbol_id

    # ── chart: live deterministic facts (chart_agent has no batch step) ──
    chart_facts: Optional[dict]
    try:
        from ..chart import chart_agent
        chart_facts = chart_agent.collect_facts(symbol_id, persist=False)
    except Exception as e:  # noqa: BLE001 — judge tolerates missing domain
        chart_facts = {"error": str(e)[:200]}

    # ── fundamental: ETF-aware assemble_display (no LLM) ──
    try:
        from ..fundamental.evaluate import assemble_display as fund_assemble
        fundamental_facts = fund_assemble(symbol_id, name=name)
    except Exception as e:  # noqa: BLE001
        fundamental_facts = {"error": str(e)[:200]}

    # ── news: theme-state assembly (no collect, no LLM) ──
    try:
        from ..news.evaluate import assemble_inputs as news_assemble
        news_state = news_assemble(symbol_id)
    except Exception as e:  # noqa: BLE001
        news_state = {"error": str(e)[:200]}

    cached = {
        "chart":       _latest_cached_verdict("chart", symbol_id),
        "fundamental": _latest_cached_verdict("fundamental", symbol_id),
        "news":        _latest_cached_verdict("news", symbol_id),
    }

    return {
        "symbol_id": symbol_id,
        "name": name,
        "is_etf": bool(fundamental_facts.get("is_etf")) if isinstance(fundamental_facts, dict) else False,
        "chart_facts": chart_facts,
        "fundamental_facts": fundamental_facts,
        "news_state": news_state,
        "cached_verdicts": cached,
        "holding_context": _holding_context(symbol_id),
        "data_available": {
            "chart": isinstance(chart_facts, dict) and "error" not in chart_facts,
            "fundamental": isinstance(fundamental_facts, dict) and "error" not in fundamental_facts,
            "news": isinstance(news_state, dict) and news_state.get("data_available", {}).get("themes_mapped"),
        },
    }


# ── judgment (claude -p applies the rulebook) ──────────────────────────────

_SYSTEM = (
    "너는 개인투자자를 돕는 종합 판단 분석가다. 차트(정량)·재무(펀더멘털)·뉴스(정성) 세 도메인의 "
    "결정론적 facts와 (있으면) 캐시된 도메인 verdict를 종합해 단일 결론을 낸다.\n"
    "원칙 (반드시):\n"
    "1) 단일 도메인만으로 strong_buy/strong_sell 금지 (§K1) — 그 도메인의 라벨이 strong이어도 종합은 buy/sell까지.\n"
    "2) drivers와 risks 둘 다 채운다 (§K2).\n"
    "3) 결손 데이터는 confidence 페널티만 — 결손을 부정 신호로 해석 금지 (§K3).\n"
    "4) 모르면 추측 금지 — 'watch' 또는 'limitations'에 명시 (§K4).\n"
    "5) summary_ko·drivers·risks·cross_reads.implication_ko 는 모두 비전문가용 쉬운 한국어.\n"
    "6) immediate score 절댓값 ≤ 0.5, ETF score 절댓값 ≤ 0.5 (§K5-6).\n"
    "7) 가장 빠른 동인이 score 방향 (§G).\n"
    "8) methodology 에 적용 룰 ID 2개 이상 (§K7).\n"
    "9) priced_in 추정이 자신없으면 partially_priced 디폴트.\n"
    "10) 차트가 이미 급등(3M>40 / RSI>70 / bull stack) 이면 news.priced_in 한 단계 상향 (§H).\n\n"
    "출력은 다음 JSON 1개만 (설명·코드펜스 금지):\n"
    "{\n"
    '  "score": -1.0~1.0,\n'
    '  "label": "strong_sell|sell|neutral|buy|strong_buy",\n'
    '  "confidence": 0.0~1.0,\n'
    '  "summary_ko": "비전문가용 한 줄 결론 (1~2 문장)",\n'
    '  "action_ko": "exit|trim|hold|watch|add",\n'
    '  "domain_summary": {\n'
    '    "chart":       {"label": "...", "score": -1~1, "one_line_ko": "...", "key_signal": "..."},\n'
    '    "fundamental": {"label": "...", "score": -1~1, "one_line_ko": "...", "key_signal": "..."},\n'
    '    "news":        {"label": "...", "score": -1~1, "one_line_ko": "...", "key_signal": "..."}\n'
    '  },\n'
    '  "cross_reads": [\n'
    '    {"pattern": "D1|D2|...|E2|F1|...", "evidence": ["..."], "implication_ko": "쉬운 한국어"}\n'
    '  ],\n'
    '  "drivers": ["…"], "risks": ["…"], "watch": ["…"],\n'
    '  "time_horizon": "immediate|near|mid|long",\n'
    '  "priced_in": "unpriced|partially_priced|mostly_priced",\n'
    '  "disagreement_note_ko": null | "한 줄",\n'
    '  "methodology": ["D4","F3", …],\n'
    '  "limitations": ["…(결손·근사 솔직히)…"]\n'
    "}\n\n"
    "── 룰북 ──\n" + _RULES
)


def _coerce_enum(v, allowed: tuple, default=None):
    return v if v in allowed else default


def _insufficient(reason: str) -> dict:
    return {
        "status": "insufficient_data",
        "score": 0.0, "label": "neutral", "confidence": 0.0,
        "summary_ko": reason, "action_ko": "hold",
        "domain_summary": {}, "cross_reads": [],
        "drivers": [], "risks": [], "watch": [],
        "time_horizon": None, "priced_in": None,
        "disagreement_note_ko": None,
        "methodology": [], "limitations": [reason],
    }


# Domain weights for the no-LLM fallback synthesis, normalized over whichever
# cached verdicts are actually present (chart leads: it moves fastest).
_FALLBACK_W = {"chart": 0.40, "fundamental": 0.35, "news": 0.25}
_DOMAIN_KO = {"chart": "차트", "fundamental": "재무", "news": "뉴스"}


def _fallback_verdict(inputs: dict, reason: str) -> dict:
    """Heuristic verdict when claude -p failed twice (design §⑥): weighted
    mean of the ≤24h cached domain verdicts, no LLM. Deliberately
    conservative — action is only hold/watch, confidence capped low, and the
    summary says it's a stopgap. cross_reads stay empty (rulebook not applied).
    """
    cached = {k: v for k, v in (inputs.get("cached_verdicts") or {}).items()
              if v and isinstance(v.get("score"), (int, float))}
    if not cached:
        out = _insufficient(
            "AI 종합 판단 생성에 실패했고 참고할 최근 도메인 분석도 없습니다. "
            "잠시 후 다시 시도해 주세요."
        )
        out["limitations"].append(f"claude 실패: {reason[:150]}")
        return out

    wsum = sum(_FALLBACK_W[k] for k in cached)
    score = clamp_score(
        sum(_FALLBACK_W[k] * float(v["score"]) for k, v in cached.items()) / wsum)
    confs = [float(v.get("confidence") or 0.3) for v in cached.values()]
    used = ", ".join(_DOMAIN_KO[k] for k in cached)
    return {
        "status": "fallback",
        "score": round(score, 4),
        "label": label_from_score(score),
        "confidence": round(min(0.35, sum(confs) / len(confs) * 0.5), 2),
        "summary_ko": (f"(자동 합성) AI 종합 판단 생성에 실패해 최근 {used} 분석을 "
                       "평균한 잠정 결론입니다. 다음 분석에서 갱신됩니다."),
        "action_ko": "hold" if abs(score) <= 0.1 else "watch",
        "domain_summary": {k: {"label": v.get("label"), "score": v.get("score"),
                               "one_line_ko": v.get("summary_ko"),
                               "key_signal": (v.get("drivers") or [None])[0]}
                           for k, v in cached.items()},
        "cross_reads": [],
        "drivers": [d for v in cached.values() for d in (v.get("drivers") or [])[:2]][:6],
        "risks": [r for v in cached.values() for r in (v.get("risks") or [])[:2]][:6],
        "watch": [],
        "time_horizon": None, "priced_in": None,
        "disagreement_note_ko": None,
        "methodology": ["fallback_cache"],
        "limitations": [
            f"claude 호출 실패로 캐시 verdict 가중평균으로 합성 (사유: {reason[:150]})",
            "룰북 교차판독(cross_reads) 미적용 — 참고용으로만 사용",
        ],
    }


def _judge(symbol_id: str, name: str, inputs: dict, *,
           backend=None, timeout_s: float = 1800) -> dict:
    avail = inputs.get("data_available", {})
    # Need at least 2 of 3 domains to render a meaningful judgment.
    n_have = sum(1 for v in avail.values() if v)
    if n_have < 2:
        missing = [k for k, v in avail.items() if not v]
        return _insufficient(
            f"분석 가능한 도메인이 부족합니다 (결손: {','.join(missing) or '전부'}). "
            "각 분석을 먼저 돌려보거나 새벽 수집을 기다려 주세요."
        )

    backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
    user = json.dumps({
        "symbol_id": symbol_id, "name": name,
        "is_etf": inputs.get("is_etf"),
        "chart_facts": inputs.get("chart_facts"),
        "fundamental_facts": inputs.get("fundamental_facts"),
        "news_state": inputs.get("news_state"),
        "cached_verdicts": inputs.get("cached_verdicts"),
        "holding_context": inputs.get("holding_context"),
    }, ensure_ascii=False, default=str)
    # design §⑥ — one retry on any claude -p failure (exit code, timeout,
    # unparseable output), then heuristic fallback instead of raising.
    data = None
    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            raw = backend.call(_SYSTEM, user, timeout_s=timeout_s, model=SLOW_MODEL)
            data = _parse_json(raw)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            _get_log().warning("judge %s attempt %d/2 failed: %s",
                               symbol_id, attempt, str(e)[:200])
            if attempt == 1:
                time.sleep(5)
    if data is None:
        return _fallback_verdict(inputs, str(last_err))

    score = clamp_score(data.get("score", 0))
    conf = clamp_confidence(data.get("confidence", 0.5))

    return {
        "status": "ok",
        "score": round(score, 4),
        "label": _coerce_enum(data.get("label"), _LABELS, label_from_score(score)),
        "confidence": conf,
        "summary_ko": data.get("summary_ko", ""),
        "action_ko": _coerce_enum(data.get("action_ko"), _ACTIONS, "hold"),
        "domain_summary": data.get("domain_summary", {}) or {},
        "cross_reads": (data.get("cross_reads") or [])[:8],
        "drivers": (data.get("drivers") or [])[:6],
        "risks": (data.get("risks") or [])[:6],
        "watch": (data.get("watch") or [])[:6],
        "time_horizon": _coerce_enum(data.get("time_horizon"), _HORIZONS),
        "priced_in": _coerce_enum(data.get("priced_in"), _PRICED_IN),
        "disagreement_note_ko": data.get("disagreement_note_ko"),
        "methodology": (data.get("methodology") or [])[:12],
        "limitations": (data.get("limitations") or [])[:6],
    }


def evaluate_symbol(symbol_id: str, *, name: Optional[str] = None,
                    date: Optional[str] = None, backend=None,
                    timeout_s: float = 1800) -> dict:
    """assemble → judge → persist. Returns the result dict (also written to
    data/analysis/judge/{symbol}/{YYYYMMDD}.json)."""
    date = date or datetime.now(_KST).strftime("%Y-%m-%d")
    name = name or symbol_id

    t0 = time.monotonic()
    inputs = assemble_inputs(symbol_id, name=name)
    verdict = _judge(symbol_id, name, inputs,
                     backend=backend, timeout_s=timeout_s)
    latency_s = time.monotonic() - t0

    result = {
        "symbol_id": symbol_id,
        "kind": "judge",
        "date": date,
        "name": name,
        **verdict,
        # Don't echo the full inputs (chart_facts alone is huge); just keep
        # which domains were available + cached verdict dates for the UI's
        # provenance footer. holding_context is small and kept verbatim so
        # the batch (and the UI) can verify it was attached.
        "data_available": inputs.get("data_available"),
        "cached_dates": {k: (v or {}).get("date")
                         for k, v in (inputs.get("cached_verdicts") or {}).items()},
        "holding_context": inputs.get("holding_context"),
        "generated_at": datetime.now(_KST).isoformat(),
    }
    missing = [k for k, v in (inputs.get("data_available") or {}).items() if not v]
    _get_log().info(
        "judge %s status=%s score=%s label=%s action=%s conf=%s latency=%.1fs "
        "missing=%s holding_ctx=%s rules=%s",
        symbol_id, verdict.get("status"), verdict.get("score"),
        verdict.get("label"), verdict.get("action_ko"), verdict.get("confidence"),
        latency_s, ",".join(missing) or "-",
        "yes" if inputs.get("holding_context") else "no",
        ",".join(verdict.get("methodology") or []) or "-")
    return persist("judge", symbol_id, date, result)
