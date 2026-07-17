"""Fundamental agent — per-symbol quantitative (재무) evaluation.

Triggered on-demand by the UI "재무 분석" button (POST /api/analysis/
fundamental/{market}/{code}/run). Reads the symbol's ALREADY-COLLECTED
valuation + financials (the daily 06:00 KST batch does the collecting — see
src/market/pipeline/fundamental_job.py), assembles the L1/L2/L3 snapshot, and
produces a 5-axis verdict (value / profitability / growth / stability /
shareholder_return) per src/agents/fundamental/fundamental_rules.md.

Output uses the SAME 5-level label vocabulary as the chart / news agents so the
Judge can fuse all three. Written to data/analysis/fundamental/{symbol}/
{date}.json, which the existing GET /api/analysis/fundamental/{market}/{code}
endpoint already serves.

Pipeline (all live):
  assemble_inputs  — enriched L1/L2/L3 snapshot + trend series + quality
                     metrics (QoE/Sloan/Altman Z/Piotroski F) + valuation band
                     + sector tag (read-only; the daily batch collects).
  _judge           — applies fundamental_rules.md via claude -p (Sonnet) and
                     returns a layperson-first 5-axis verdict (plain-Korean
                     summary, per-axis notes, per-metric explanations).
  evaluate_symbol  — assemble → judge → persist.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ...core.paths import analysis_path
from ...market.store import valuation_store, financials_store, sector_store, etf_store
from ...market import fundamental_metrics
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json

_KST = timezone(timedelta(hours=9))

# 5-level label vocabulary, identical to the news agent (Judge normalizes).
_LABELS = ("strong_sell", "sell", "neutral", "buy", "strong_buy")

_RULES = (Path(__file__).parent / "fundamental_rules.md").read_text(encoding="utf-8")


def label_from_score(score: float) -> str:
    s = max(-1.0, min(1.0, float(score)))
    if s < -0.5:
        return "strong_sell"
    if s < -0.1:
        return "sell"
    if s <= 0.1:
        return "neutral"
    if s <= 0.5:
        return "buy"
    return "strong_buy"


def _valuation_band(symbol_id: str, valuation: dict) -> dict:
    """The symbol's own PER/PBR band position (rulebook §H value axis).

    Percentile of the latest PER/PBR within its own trailing history — answers
    "cheap vs my own past?". KR has native daily history; US valuation accrues
    one point per fetch date, so its band is sparse (returns None until enough
    points). Honest by construction."""
    df = valuation_store.read(symbol_id)
    if df.empty:
        return {"available": False}

    def _clean(col):
        return [v for v in df[col].tolist()
                if v is not None and not (isinstance(v, float) and math.isnan(v))]

    per_hist, pbr_hist = _clean("per"), _clean("pbr")
    per_now, pbr_now = valuation.get("per"), valuation.get("pbr")
    out = {
        "available": True,
        "window_n": len(df),
        "per_pctile": fundamental_metrics.percentile_rank(per_hist, per_now),
        "pbr_pctile": fundamental_metrics.percentile_rank(pbr_hist, pbr_now),
    }
    if per_hist:
        out["per_min"], out["per_max"] = round(min(per_hist), 2), round(max(per_hist), 2)
    if pbr_hist:
        out["pbr_min"], out["pbr_max"] = round(min(pbr_hist), 2), round(max(pbr_hist), 2)
    return out


def _is_etf(symbol_id: str) -> bool:
    """Universe-driven ETF check (used to route assemble/_judge to the ETF
    branch). Returns False on any failure so the regular path still runs."""
    try:
        from ...universe import search as uni_search
        return bool((uni_search.get(symbol_id) or {}).get("is_etf"))
    except Exception:  # noqa: BLE001
        return False


def assemble_etf_inputs(symbol_id: str) -> dict:
    """ETF-specific input snapshot. NAV / 괴리율 / 3M return / AUM (from
    Naver via etf_store, refreshed by src.market.pipeline.etf_job). No
    statements, no per-share valuation — those don't apply to ETFs."""
    snap = etf_store.read(symbol_id) or {}
    sector_rec = sector_store.read(symbol_id) or {}
    return {
        "is_etf": True,
        "etf": snap,
        "sector": {"sector": sector_rec.get("sector"),
                   "industry": sector_rec.get("industry")},
        "data_available": {
            "etf_snapshot": bool(snap),
            "nav": snap.get("nav") is not None if snap else False,
            "aum": snap.get("aum_eokwon") is not None if snap else False,
        },
    }


def assemble_inputs(symbol_id: str) -> dict:
    """Gather the enriched L1/L2/L3 snapshot from the (pre-collected) stores.

    Read-only — does NOT fetch from DART/yfinance. Collection is the daily
    batch's job; the button only evaluates what is already stored.

    Enriched (vs the original snapshot) so the rulebook's trend / F-Score /
    band / sector rules are actually computable:
      - derived_series : last 8 periods of margins/ROE/debt/YoY (trends)
      - quality        : QoE, Sloan accrual, Altman Z, partial Piotroski F
      - valuation_band : PER/PBR percentile vs the symbol's own history
      - sector         : GICS-style sector/industry tag (for §E correction)
    """
    valuation = valuation_store.latest(symbol_id)
    rows = financials_store.rows_as_dicts(symbol_id)
    latest = rows[-1] if rows else {}
    prior = fundamental_metrics.find_prior_year(rows, latest.get("period", "")) if latest else None
    derived = fundamental_metrics.compute(latest, prior_year=prior, valuation=valuation)

    quality = {
        "qoe": fundamental_metrics.quality_of_earnings(
            latest.get("operating_cf"), latest.get("net_income")),
        "sloan_accrual": fundamental_metrics.sloan_accrual(
            latest.get("net_income"), latest.get("operating_cf"),
            latest.get("investing_cf"), latest.get("total_assets")),
        "altman_z": fundamental_metrics.altman_z(latest, valuation.get("market_cap")),
        "piotroski_f": fundamental_metrics.piotroski_f(latest, prior),
    } if latest else {}

    sector_rec = sector_store.read(symbol_id) or {}

    return {
        "valuation": valuation,
        "financials_latest": latest,
        "latest_period": latest.get("period"),
        "n_periods": len(rows),
        "derived": derived,
        "derived_series": fundamental_metrics.derived_series(rows, last_n=8),
        "quality": quality,
        "valuation_band": _valuation_band(symbol_id, valuation),
        "sector": {"sector": sector_rec.get("sector"),
                   "industry": sector_rec.get("industry")},
        "data_available": {
            "valuation": bool(valuation),
            "financials": bool(latest),
            "sector": bool(sector_rec.get("sector")),
        },
    }


_AXES = ("value", "profitability", "growth", "stability", "shareholder_return")

# System directive: rulebook (the analyst frameworks) + a layperson-output
# contract. The user's #1 goal is "explain it so a non-reader understands",
# so the agent must produce plain-Korean summary + per-axis + per-metric prose.
_SYSTEM = (
    "너는 개인투자자를 돕는 재무 분석가다. 아래 룰북을 적용해 한 종목의 펀더멘털을 "
    "5축(밸류·수익성·성장성·안정성·주주환원)으로 평가한다.\n"
    "원칙(반드시 지킬 것):\n"
    "1) 단일 지표로 단독 결론 금지 — 축마다 2개 이상 룰을 결합한다(§B/§C).\n"
    "2) 절대 임계 금지 — 섹터(§E)와 한국시장 컨텍스트(§D, 코리아 디스카운트·밸류업)로 보정한다.\n"
    "3) 레드플래그(§F)를 교차 점검한다.\n"
    "4) 입력의 quality(QoE·Sloan·Altman Z·Piotroski F)와 valuation_band·derived_series는 "
    "이미 계산된 사실이다. 다시 계산하지 말고 해석에 쓴다. approx/부분 항목은 신뢰도를 낮춘다.\n"
    "5) sector가 null이면 회사명으로 업종을 추정하되 그 사실을 sector_assumed에 적고 confidence를 낮춘다.\n"
    "6) 데이터가 부족하면 단정하지 말고 confidence를 낮추고 근거에 명시한다(추측 금지).\n"
    "★ 가장 중요: 사용자는 재무제표를 못 읽는 비전문가다. summary_ko와 axis_notes, "
    "metric_explanations는 전문용어 없이 '쉬운 한국어 한 줄'로 풀어 쓴다. "
    "예: 'PER 44 → 이익의 44배 값이라 비싼 편'.\n\n"
    "반드시 아래 JSON 스키마 1개만 출력한다(설명·코드펜스 금지):\n"
    '{\n'
    '  "score": -1.0~1.0,                // 펀더멘털 종합(음수=부정, 양수=긍정)\n'
    '  "summary_ko": "비전문가용 한 줄 결론(1~2문장)",\n'
    '  "axes": {"value":0~100,"profitability":0~100,"growth":0~100,"stability":0~100,"shareholder_return":0~100},\n'
    '  "axis_notes": {"value":"싼가? …","profitability":"버는가? …","growth":"자라는가? …","stability":"버티는가? …","shareholder_return":"돌려주나? …"},\n'
    '  "metric_explanations": [{"key":"per","label":"PER","value":44.3,"plain":"쉬운 풀이"}],  // 핵심 4~6개\n'
    '  "drivers": ["긍정 동인"], "risks": ["위험 요인"], "red_flags": ["레드플래그(없으면 빈 배열)"],\n'
    '  "sector_assumed": "적용한 업종",\n'
    '  "confidence": 0.0~1.0,            // 데이터 충분성·근사 여부 반영\n'
    '  "methodology": ["적용 룰 ID 예: B1, C2"]\n'
    '}\n\n'
    "── 룰북 ──\n" + _RULES
)


def _insufficient(reason: str) -> dict:
    """Verdict for when there is no financial data — no LLM call, low conf."""
    return {
        "status": "insufficient_data",
        "score": None, "label": "pending",
        "summary_ko": reason,
        "axes": {a: None for a in _AXES},
        "axis_notes": {}, "metric_explanations": [],
        "drivers": [], "risks": [], "red_flags": [],
        "sector_assumed": None, "confidence": 0.0, "methodology": [],
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


def assemble_display(symbol_id: str, name: Optional[str] = None,
                     n_periods_table: int = 8) -> dict:
    """Read-only facts for the UI's DEFAULT fundamental view (no LLM call).

    Mirrors the chart tab's live facts: assemble_inputs() + the raw L2
    statement series so the panel can show a 재무제표 table + metrics + trend
    (and export CSV) the moment the tab opens, before any AI interpretation.
    """
    name = name or symbol_id
    if _is_etf(symbol_id):
        inputs = assemble_etf_inputs(symbol_id)
        return {
            "symbol_id": symbol_id, "name": name,
            "kind": "fundamental_facts",
            "financials_series": [],   # ETFs have no statements
            **inputs,
        }
    inputs = assemble_inputs(symbol_id)
    series = financials_store.rows_as_dicts(symbol_id, n=n_periods_table)
    return {
        "symbol_id": symbol_id,
        "name": name,
        "kind": "fundamental_facts",
        "financials_series": series,
        **inputs,
    }


def _judge(symbol_id: str, name: str, inputs: dict, *,
           backend=None, timeout_s: float = 280) -> dict:
    """Apply the rulebook (LLM) to the assembled snapshot → 5-axis verdict.

    Layperson-first output: plain-Korean summary, per-axis notes and per-metric
    explanations alongside the analyst scores. Returns a low-confidence
    `insufficient_data` verdict WITHOUT calling the model when no financials
    are available, so the button never burns a claude call on empty data.
    """
    avail = inputs.get("data_available", {})
    if not avail.get("financials"):
        return _insufficient("재무제표 데이터가 아직 없습니다. 매일 새벽 수집 후 다시 시도하세요.")

    backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
    user = json.dumps({"symbol_id": symbol_id, "name": name, **inputs},
                      ensure_ascii=False, default=str)
    raw = backend.call(_SYSTEM, user, timeout_s=timeout_s, model=SLOW_MODEL)
    data = _parse_json(raw)

    score = max(-1.0, min(1.0, float(data.get("score", 0))))
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
        "axes": _coerce_axes(data.get("axes", {})),
        "axis_notes": data.get("axis_notes", {}) or {},
        "metric_explanations": (data.get("metric_explanations") or [])[:8],
        "drivers": (data.get("drivers") or [])[:6],
        "risks": (data.get("risks") or [])[:6],
        "red_flags": (data.get("red_flags") or [])[:6],
        "sector_assumed": data.get("sector_assumed"),
        "confidence": conf,
        "methodology": (data.get("methodology") or [])[:12],
    }


_ETF_SYSTEM = (
    "너는 개인투자자를 돕는 ETF 분석가다. ETF는 개별 주식이 아니므로 PER/ROE/매출 같은 "
    "지표는 의미가 없다. 대신 다음 4축으로 평가한다 (각 0~100).\n"
    "1) tracking      : NAV 대비 시장가 괴리율(premium_pct) — |값| 가 작을수록 100점. "
    "                    ±0.3% 이내=우수, ±1% 이내=양호, ±3% 이상=주의(유동성 부족 신호).\n"
    "2) liquidity     : AUM(aum_eokwon, 억원)과 일평균 거래대금(value_mwon, 백만원). "
    "                    AUM<500억 또는 거래대금<10억원이면 청산위험·매매슬리피지 경고.\n"
    "3) recent_return : return_3m_pct(3개월 수익률). 절댓값이 아니라 카테고리 컨텍스트로 해석. "
    "                    (예: 변동성 큰 테마 ETF에서 −5%는 평범)\n"
    "4) fit           : 사용자가 보유한 이유로 추정되는 category(tab_code/카테고리명)와 "
    "                    이름이 합치하는가. (광역지수 vs 테마/액티브 ETF는 역할이 다르다)\n"
    "원칙:\n"
    "- 단정 금지: ETF에 'buy/sell' 라벨은 종목 분석만큼 강하게 쓰지 않는다. 점수는 보통 −0.3~+0.3 "
    "  범위에 머문다(테마 ETF가 모멘텀상 강하면 +0.5까지 가능).\n"
    "- '쉬운 한국어'로 풀어 쓴다(전문용어 금지). 예: '괴리율 +0.4% → 시장가가 NAV보다 살짝 비쌈'.\n"
    "- 데이터가 없으면 confidence를 낮추고 추측하지 않는다.\n\n"
    "반드시 아래 JSON 1개만 출력(설명·코드펜스 금지):\n"
    "{\n"
    '  "score": -1.0~1.0,\n'
    '  "summary_ko": "한 줄 결론",\n'
    '  "axes": {"tracking":0~100,"liquidity":0~100,"recent_return":0~100,"fit":0~100},\n'
    '  "axis_notes": {"tracking":"…","liquidity":"…","recent_return":"…","fit":"…"},\n'
    '  "metric_explanations": [{"key":"premium_pct","label":"괴리율","value":0.4,"plain":"…"}],\n'
    '  "drivers": [], "risks": [], "red_flags": [],\n'
    '  "sector_assumed": "ETF 카테고리",\n'
    '  "confidence": 0.0~1.0,\n'
    '  "methodology": []\n'
    "}\n"
)


def _judge_etf(symbol_id: str, name: str, inputs: dict, *,
               backend=None, timeout_s: float = 200) -> dict:
    """ETF branch — uses _ETF_SYSTEM (no statements rulebook). Skips LLM when
    the Naver snapshot is missing."""
    if not inputs.get("data_available", {}).get("etf_snapshot"):
        return _insufficient("ETF 스냅샷이 아직 없습니다. 매일 새벽 수집 후 다시 시도하세요.")
    backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
    user = json.dumps({"symbol_id": symbol_id, "name": name, **inputs},
                      ensure_ascii=False, default=str)
    raw = backend.call(_ETF_SYSTEM, user, timeout_s=timeout_s, model=SLOW_MODEL)
    data = _parse_json(raw)
    score = max(-1.0, min(1.0, float(data.get("score", 0))))
    conf = data.get("confidence", 0.5)
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.5
    # ETF axes use different keys; pass through as-is (UI tolerates extras).
    return {
        "status": "ok",
        "score": round(score, 4),
        "label": label_from_score(score),
        "summary_ko": data.get("summary_ko", ""),
        "axes": data.get("axes", {}) or {},
        "axis_notes": data.get("axis_notes", {}) or {},
        "metric_explanations": (data.get("metric_explanations") or [])[:8],
        "drivers": (data.get("drivers") or [])[:6],
        "risks": (data.get("risks") or [])[:6],
        "red_flags": (data.get("red_flags") or [])[:6],
        "sector_assumed": data.get("sector_assumed"),
        "confidence": conf,
        "methodology": (data.get("methodology") or [])[:12],
        "is_etf": True,
    }


def evaluate_symbol(symbol_id: str, *, name: Optional[str] = None,
                    date: Optional[str] = None, backend=None,
                    timeout_s: float = 280) -> dict:
    """Assemble inputs → judge → persist. Returns the result dict.
    ETFs follow the ETF rulebook branch (NAV/괴리율/AUM/3M)."""
    date = date or datetime.now(_KST).strftime("%Y-%m-%d")
    name = name or symbol_id

    if _is_etf(symbol_id):
        inputs = assemble_etf_inputs(symbol_id)
        verdict = _judge_etf(symbol_id, name, inputs,
                             backend=backend, timeout_s=timeout_s)
    else:
        inputs = assemble_inputs(symbol_id)
        verdict = _judge(symbol_id, name, inputs,
                         backend=backend, timeout_s=timeout_s)

    result = {
        "symbol_id": symbol_id,
        "kind": "fundamental",
        "date": date,
        "name": name,
        **verdict,
        "inputs": inputs,
        "generated_at": datetime.now(_KST).isoformat(),
    }
    path = analysis_path("fundamental", symbol_id, date.replace("-", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return result
