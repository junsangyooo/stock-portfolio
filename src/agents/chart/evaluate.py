"""Chart agent — per-symbol quantitative (차트/정량) evaluation.

Triggered on-demand by the UI 📈 button (POST /api/analysis/chart/{m}/{c}/run).
Reads the symbol's already-collected OHLCV (the market pipeline does the
collecting), extracts the deterministic facts via chart_agent.collect_facts,
and applies src/agents/chart/chart_rules.md via claude -p to produce a RICH
judgment.

Philosophy (user directive): give the user MORE, not less — surface every
opportunity / risk / scenario the chart implies, in plain Korean, and let the
human choose. So the verdict is intentionally richer than the fundamental/news
ones: trend_context (MTF) + 4-axis read + the dominant setup + concrete price
levels + bull/bear/base scenarios + always-checked risk patterns (§E) + watch
triggers. Direction stays open-ended (the chart can't see catalysts).

Output uses the SAME 5-level label vocabulary as the fundamental / news agents
so the 🧠 Judge can fuse all three. Written to data/analysis/chart/{symbol}/
{date}.json (served by GET /api/analysis/chart/{market}/{code}/verdict).

Pipeline:
  assemble_inputs — chart_agent.collect_facts (deterministic, read-only).
  _judge          — applies chart_rules.md via claude -p (Sonnet) → rich verdict.
  evaluate_symbol — assemble → judge → persist.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ...core.paths import analysis_path
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json
from . import chart_agent

_KST = timezone(timedelta(hours=9))

# 5-level label vocabulary, identical to the fundamental / news agents.
_LABELS = ("strong_sell", "sell", "neutral", "buy", "strong_buy")
_AXES = ("trend", "momentum", "volatility", "volume")
_STANCES = ("actionable_long", "watch", "actionable_short", "stay_out")

_RULES = (Path(__file__).parent / "chart_rules.md").read_text(encoding="utf-8")


def clamp_score(score: float) -> float:
    return max(-1.0, min(1.0, float(score)))


def label_from_score(score: float) -> str:
    """5-level label (shared with fundamental/news). Thresholds -0.5/-0.1/+0.1/+0.5."""
    s = clamp_score(score)
    if s < -0.5:
        return "strong_sell"
    if s < -0.1:
        return "sell"
    if s <= 0.1:
        return "neutral"
    if s <= 0.5:
        return "buy"
    return "strong_buy"


# ── input assembly ──────────────────────────────────────────────────────────

def assemble_inputs(symbol_id: str) -> dict:
    """Collect the deterministic chart facts (read-only — does NOT fetch).

    Returns the facts dict (timeframes + structure) plus a data_available flag
    so the judge can short-circuit empty symbols without burning a claude run.
    """
    facts = chart_agent.collect_facts(symbol_id, persist=False)
    tfs = facts.get("timeframes", {})
    any_data = any((tfs.get(t) or {}).get("has_data") for t in ("1d", "1w", "1mo"))
    facts["data_available"] = {"ohlcv": bool(any_data)}
    return facts


# ── judgment (claude -p applies the rulebook) ───────────────────────────────

# System directive: rulebook (the analyst frameworks) + a rich, layperson-first
# output contract (§H). The user's #1 goal across all agents is "explain it so a
# non-reader understands AND give them everything to decide for themselves".
_SYSTEM = (
    "너는 개인투자자를 돕는 차트(정량) 분석가다. 아래 룰북을 적용해 한 종목의 차트를 "
    "종합 평가한다.\n"
    "원칙(반드시 지킬 것):\n"
    "1) 단일 지표로 단독 결론 금지 — 서로 다른 축 2개 이상을 결합한다(§B/§C). 같은 계열 "
    "오실레이터 중복을 confluence로 착각 금지.\n"
    "2) 절대 임계 금지 — 추세장/횡보장 컨텍스트(§B2 ADX 게이트)와 상위 타임프레임(§C1·§D)으로 "
    "보정한다. 상위 TF와 반대인 하위 TF 신호는 강등한다.\n"
    "3) 돌파·반등·반전은 거래량(volume_spike)으로 확인한다. 미동반 시 신뢰도 강등(§F).\n"
    "4) 위험패턴(§E E1~E8)을 항상 교차 점검하고, 활성된 것은 방향과 무관하게 risk_patterns에 "
    "경고로 병기한다. 차트는 정성 촉매(뉴스·실적)를 모르므로 단정하지 말고 오픈형으로 제시한다.\n"
    "5) confidence는 confluence(정렬된 축 수)·TF정렬·거래량확인·데이터 신선도로 산출한다. "
    "데이터가 부족하거나 mixed/in_cloud가 많으면 낮춘다(추측 금지).\n"
    "6) input의 facts는 이미 계산된 객관적 사실이다. 다시 계산하지 말고 해석에 쓴다. "
    "no_data 타임프레임은 없는 것으로 다룬다.\n"
    "7) levels의 모든 price(supports·resistances·targets)는 **반드시 input structure facts에 "
    "실재하는 가격**(support_resistance.levels / fibonacci / pivots)에서 고른다. facts에 없는 "
    "가격을 지어내지 말 것. 측정 목표가가 필요하면 가장 가까운 S/R·피보로 근사하고 그 근거를 why에 적는다.\n"
    "★ 가장 중요(2가지):\n"
    "  (가) 사용자는 차트를 못 읽는 비전문가다 — summary_ko·axis_notes·setup.plain·scenarios·"
    "drivers·risks·watch는 전문용어 없이 '쉬운 한국어'로 풀어 쓴다.\n"
    "  (나) 정보는 적게가 아니라 '풀어서 많이' 준다 — 기회·위험·가능성을 모두 끌어내라. 선택은 "
    "사용자의 몫이다. drivers와 risks를 둘 다 충분히 채우고, scenarios는 bull/bear/base 모두 쓴다.\n\n"
    "반드시 아래 JSON 스키마 1개만 출력한다(설명·코드펜스 금지):\n"
    "{\n"
    '  "score": -1.0~1.0,                 // 차트 종합(음수=약세, 양수=강세). 룰 기반 종합\n'
    '  "summary_ko": "비전문가용 2~3문장 큰 그림(지금 상태 + 무엇이 기회·무엇이 위험)",\n'
    '  "confidence": 0.0~1.0,             // confluence·TF정렬·거래량·데이터 신선도\n'
    '  "trend_context": {"monthly":"up|down|sideways|no_data","weekly":"...","daily":"...",\n'
    '                    "alignment":"aligned_up|aligned_down|conflict|mixed","note":"쉬운 한 줄"},\n'
    '  "axes": {"trend":0~100,"momentum":0~100,"volatility":0~100,"volume":0~100}, // 50=중립, 강세 가리키는 정도\n'
    '  "axis_notes": {"trend":"추세 한 줄","momentum":"모멘텀 한 줄","volatility":"변동성 한 줄","volume":"수급 한 줄"},\n'
    '  "setup": {"name":"눌림목매수|돌파-retest|평균회귀|추세없음 관망|천장경계 등","framework":"C1|C2|C6|...",\n'
    '            "stance":"actionable_long|watch|actionable_short|stay_out","plain":"쉬운 설명"},\n'
    '  "levels": {"supports":[{"price":0,"tests":0,"why":"근거"}],"resistances":[{"price":0,"tests":0,"why":"근거"}],\n'
    '             "entry_zone":"진입 가격대+조건","invalidation":"이 선 깨지면 무효(손절)","targets":["1차","2차"]},\n'
    '  "scenarios": {"bull":"만약 X면 → Y(기회)","bear":"만약 W면 → U(위험)","base":"현재 가장 가능성 높은 전개"},\n'
    '  "drivers": ["강세/기회 신호(쉬운 말, 끝에 룰ID 태그 권장)"],\n'
    '  "risks": ["약세/위험 신호(쉬운 말)"],\n'
    '  "risk_patterns": [{"pattern":"dead_cat_bounce|overextension|bull_trap|bearish_divergence 등","active":true,"plain":"경고 설명"}],\n'
    '  "watch": ["다음에 지켜볼 트리거(예: 거래량 동반 돌파 확인, RSI 50 회복)"],\n'
    '  "methodology": ["적용 룰 ID 예: C7, B1, B9, E8"]\n'
    "}\n\n"
    "── 룰북 ──\n" + _RULES
)


def _insufficient(reason: str) -> dict:
    """Verdict for when there is no OHLCV — no LLM call, low confidence."""
    return {
        "status": "insufficient_data",
        "score": None, "label": "pending",
        "summary_ko": reason,
        "confidence": 0.0,
        "trend_context": {}, "axes": {a: None for a in _AXES}, "axis_notes": {},
        "setup": {}, "levels": {}, "scenarios": {},
        "drivers": [], "risks": [], "risk_patterns": [], "watch": [],
        "methodology": [],
    }


def _coerce_axes(raw: dict) -> dict:
    out = {}
    for a in _AXES:
        v = (raw or {}).get(a)
        try:
            out[a] = None if v is None else max(0, min(100, int(round(float(v)))))
        except (TypeError, ValueError):
            out[a] = None
    return out


def _coerce_setup(raw: dict) -> dict:
    raw = raw or {}
    stance = raw.get("stance")
    return {
        "name": raw.get("name"),
        "framework": raw.get("framework"),
        "stance": stance if stance in _STANCES else None,
        "plain": raw.get("plain"),
    }


def _anchor_prices(facts: dict) -> list:
    """Trustworthy price anchors extracted DETERMINISTICALLY from structure —
    the set the LLM's quoted levels are validated against (P0-B). Pulls swing
    S/R levels (with test counts), fibonacci levels, and classic pivots."""
    st = (facts or {}).get("structure", {}) or {}
    out = []
    sr = st.get("support_resistance", {}) or {}
    for lv in (sr.get("levels") or []):
        if isinstance(lv, dict) and lv.get("price") is not None:
            try:
                out.append((float(lv["price"]), int(lv.get("tests", 1) or 1)))
            except (TypeError, ValueError):
                pass
    fib = (st.get("fibonacci", {}) or {}).get("levels", {}) or {}
    for v in fib.values():
        try:
            if v is not None:
                out.append((float(v), 1))
        except (TypeError, ValueError):
            pass
    piv = st.get("pivots", {}) or {}
    for k, v in piv.items():
        if k in ("PP", "R1", "R2", "R3", "S1", "S2", "S3") and isinstance(v, (int, float)):
            out.append((float(v), 1))
    return out


def _validate_price(price, anchors: list, tol: float = 0.015) -> dict:
    """Snap an LLM-quoted price to the nearest deterministic anchor within `tol`
    (1.5%), tagging provenance. No anchor in range → keep but flag `llm_only`
    so the UI can warn the user the number is model-invented (P0-B). Never drops
    — the user wants the info, just labelled."""
    if price is None:
        return {"price": None, "anchored": False}
    if not anchors:
        return {"price": price, "anchored": False, "llm_only": False}  # nothing to check against
    best = min(anchors, key=lambda a: abs(a[0] - price))
    if best[0] and abs(best[0] - price) / best[0] <= tol:
        out = {"price": round(best[0], 4), "anchored": True, "anchor_tests": best[1]}
        if abs(best[0] - price) > 1e-9:
            out["llm_raw"] = round(price, 4)  # what the model said before snapping
        return out
    return {"price": price, "anchored": False, "llm_only": True}


def _coerce_levels(raw: dict, facts: Optional[dict] = None) -> dict:
    raw = raw or {}
    anchors = _anchor_prices(facts or {})

    def _side(key):
        out = []
        for lv in (raw.get(key) or [])[:5]:
            if not isinstance(lv, dict):
                continue
            try:
                price = float(lv.get("price")) if lv.get("price") is not None else None
            except (TypeError, ValueError):
                price = None
            v = _validate_price(price, anchors)
            out.append({**v, "tests": lv.get("tests"), "why": lv.get("why")})
        return out

    def _targets():
        out = []
        for t in (raw.get("targets") or [])[:4]:
            # targets may be a bare price or a string ("1차 72,000"); validate
            # only when numeric, otherwise pass through untouched.
            if isinstance(t, (int, float)):
                out.append(_validate_price(float(t), anchors))
            else:
                out.append(t)
        return out

    return {
        "supports": _side("supports"),
        "resistances": _side("resistances"),
        "entry_zone": raw.get("entry_zone"),
        "invalidation": raw.get("invalidation"),
        "targets": _targets(),
    }


def _coerce_risk_patterns(raw) -> list:
    out = []
    for rp in (raw or [])[:8]:
        if isinstance(rp, dict):
            out.append({"pattern": rp.get("pattern"),
                        "active": bool(rp.get("active", True)),
                        "plain": rp.get("plain")})
        elif isinstance(rp, str):
            out.append({"pattern": rp, "active": True, "plain": None})
    return out


def _judge(symbol_id: str, name: str, facts: dict, *,
           backend=None, timeout_s: float = 280) -> dict:
    """Apply chart_rules.md (claude -p) to the extracted facts → rich verdict.

    Short-circuits to a low-confidence insufficient_data verdict WITHOUT calling
    the model when the symbol has no OHLCV, so the button never burns a claude
    run on empty data."""
    avail = facts.get("data_available", {})
    if not avail.get("ohlcv"):
        return _insufficient("이 종목의 OHLCV 데이터가 아직 없습니다(백필 필요). 잠시 후 다시 시도하세요.")

    backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
    user = json.dumps({
        "symbol_id": symbol_id, "name": name,
        "asof": facts.get("asof"),
        "timeframes": facts.get("timeframes", {}),
        "structure": facts.get("structure", {}),
    }, ensure_ascii=False, default=str)
    raw = backend.call(_SYSTEM, user, timeout_s=timeout_s, model=SLOW_MODEL)
    data = _parse_json(raw)

    score = clamp_score(float(data.get("score", 0)))
    conf = data.get("confidence", 0.5)
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "status": "ok",
        "score": round(score, 4),
        "label": label_from_score(score),
        "summary_ko": data.get("summary_ko", ""),
        "confidence": conf,
        "trend_context": data.get("trend_context", {}) or {},
        "axes": _coerce_axes(data.get("axes", {})),
        "axis_notes": data.get("axis_notes", {}) or {},
        "setup": _coerce_setup(data.get("setup")),
        "levels": _coerce_levels(data.get("levels"), facts),
        "scenarios": data.get("scenarios", {}) or {},
        "drivers": (data.get("drivers") or [])[:8],
        "risks": (data.get("risks") or [])[:8],
        "risk_patterns": _coerce_risk_patterns(data.get("risk_patterns")),
        "watch": (data.get("watch") or [])[:6],
        "methodology": (data.get("methodology") or [])[:12],
    }


def evaluate_symbol(symbol_id: str, *, name: Optional[str] = None,
                    date: Optional[str] = None, backend=None,
                    timeout_s: float = 280) -> dict:
    """Collect facts → judge (rulebook via claude -p) → persist. Returns dict."""
    date = date or datetime.now(_KST).strftime("%Y-%m-%d")
    name = name or symbol_id

    facts = assemble_inputs(symbol_id)
    verdict = _judge(symbol_id, name, facts, backend=backend, timeout_s=timeout_s)

    result = {
        "symbol_id": symbol_id,
        "kind": "chart",
        "date": date,
        "name": name,
        **verdict,
        "facts": facts,
        "generated_at": datetime.now(_KST).isoformat(),
    }
    path = analysis_path("chart", symbol_id, date.replace("-", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    return result
