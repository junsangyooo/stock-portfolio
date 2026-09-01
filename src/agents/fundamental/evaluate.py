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
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...core.kst import KST as _KST
from ..verdict import clamp_confidence, clamp_score, label_from_score, persist
from ...market.store import valuation_store, financials_store, sector_store, etf_store
from ...market import fundamental_metrics
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json

_RULES = (Path(__file__).parent / "fundamental_rules.md").read_text(encoding="utf-8")


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

# The per-domain judgment (_judge / evaluate_symbol) lived here until
# 2026-09-01. It is gone: judging a domain in isolation hardens a
# one-dimensional conclusion, and the merge that followed could no longer undo
# it. src/agents/analyze/ now sees all three domains in one call, with this
# module's assembly as its input and fundamental_rules.md injected as that domain's
# dictionary.


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
