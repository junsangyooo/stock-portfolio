"""Fundamental facts — per-symbol 재무 assembly, no LLM.

Reads the symbol's ALREADY-COLLECTED valuation + financials (the daily 06:00
KST batch does the collecting — see src/market/pipeline/fundamental_job.py)
and shapes them for two readers:

  assemble_inputs      — enriched L1/L2/L3 snapshot + trend series + quality
                         metrics (QoE/Sloan/Altman Z/Piotroski F) + valuation
                         band + sector tag. The fundamental domain of
                         src/research/analyze, which reads it next to chart
                         and news with fundamental_rules.md as the dictionary.
  assemble_etf_inputs  — the ETF shape of the same (snapshot + holdings).
  assemble_display     — the 재무 tab's statements and metrics.
  assemble_page        — assemble_display + the stock page's tiles (peer
                         percentiles, the ETF NAV snapshot); what
                         GET /api/analysis/fundamental/{m}/{c}/facts returns.

Judgment does not happen here: a domain judged alone hardens a one-dimensional
conclusion the later merge cannot undo.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from ...market.store import valuation_store, financials_store, sector_store, etf_store
from ...market import fundamental_metrics, peer

log = logging.getLogger(__name__)


def _valuation_band(symbol_id: str, valuation: dict) -> dict:
    """The symbol's own PER/PBR band position (value axis).

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
    """Universe-driven ETF check (routes assemble_display to the ETF shape).
    Returns False on any failure so the regular path still runs."""
    try:
        from ...universe import search as uni_search
        return bool((uni_search.get(symbol_id) or {}).get("is_etf"))
    except Exception:  # noqa: BLE001
        return False


def _sector(symbol_id: str) -> dict:
    rec = sector_store.read(symbol_id) or {}
    return {"sector": rec.get("sector"), "industry": rec.get("industry")}


def assemble_etf_inputs(symbol_id: str) -> dict:
    """ETF-specific input snapshot. NAV / 괴리율 / 3M return / AUM (from
    Naver via etf_store, refreshed by src.market.pipeline.etf_job). No
    statements, no per-share valuation — those don't apply to ETFs."""
    snap = etf_store.read(symbol_id) or {}
    return {
        "is_etf": True,
        "etf": snap,
        "sector": _sector(symbol_id),
        "data_available": {
            "etf_snapshot": bool(snap),
            "nav": snap.get("nav") is not None,
            "aum": snap.get("aum_eokwon") is not None,
        },
    }


def assemble_inputs(symbol_id: str, rows: Optional[list] = None) -> dict:
    """Gather the enriched L1/L2/L3 snapshot from the (pre-collected) stores.

    Read-only — does NOT fetch from DART/yfinance. Collection is the daily
    batch's job; this reads only what is already stored. `rows` passes the
    statement rows a caller already read.

    Enriched so the dictionary's trend / F-Score / band / sector entries have
    numbers to point at:
      - derived_series : last 8 periods of margins/ROE/debt/YoY (trends)
      - quality        : QoE, Sloan accrual, Altman Z, partial Piotroski F
      - valuation_band : PER/PBR percentile vs the symbol's own history
      - sector         : GICS-style sector/industry tag (for §E correction)
    """
    valuation = valuation_store.latest(symbol_id)
    if rows is None:
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

    sector = _sector(symbol_id)
    return {
        "valuation": valuation,
        "financials_latest": latest,
        "latest_period": latest.get("period"),
        "n_periods": len(rows),
        "derived": derived,
        "derived_series": fundamental_metrics.derived_series(rows, last_n=8),
        "quality": quality,
        "valuation_band": _valuation_band(symbol_id, valuation),
        "sector": sector,
        "data_available": {
            "valuation": bool(valuation),
            "financials": bool(latest),
            "sector": bool(sector["sector"]),
        },
    }


def assemble_display(symbol_id: str, name: Optional[str] = None,
                     n_periods_table: int = 8) -> dict:
    """Read-only facts for the UI's DEFAULT fundamental view (no LLM call).

    Mirrors the chart tab's live facts: assemble_inputs() + the raw L2
    statement series so the panel can show a 재무제표 table + metrics + trend
    (and export CSV) the moment the tab opens, before any AI interpretation.
    """
    head = {"symbol_id": symbol_id, "name": name or symbol_id,
            "kind": "fundamental_facts"}
    if _is_etf(symbol_id):
        # ETFs have no statements
        return {**head, "financials_series": [], **assemble_etf_inputs(symbol_id)}
    # One read serves both: the table is the newest `n_periods_table` rows.
    rows = financials_store.rows_as_dicts(symbol_id)
    series = rows[-n_periods_table:] if n_periods_table > 0 else rows
    return {**head, "financials_series": series, **assemble_inputs(symbol_id, rows)}


def assemble_page(symbol_id: str, name: Optional[str] = None) -> dict:
    """The stock page's 재무 facts (no LLM): assemble_display, plus where the
    symbol sits among its peers and, for an ETF, its NAV snapshot. Never
    raises — a missing store or a failed tile comes back as `error` beside
    what could be read, so the tab still opens."""
    try:
        facts = assemble_display(symbol_id, name=name)
    except Exception as e:  # noqa: BLE001 — a missing store must not 500 the tab
        log.warning("fundamental facts %s failed: %s", symbol_id, e)
        return {"kind": "fundamental_facts", "symbol_id": symbol_id, "error": str(e)[:300]}
    try:
        facts["peer"] = peer.percentiles(symbol_id)
    except Exception as e:  # noqa: BLE001 — a tile, not the tab
        facts["peer"] = {"error": str(e)[:200]}
    try:
        facts["etf"] = etf_store.read(symbol_id)
    except Exception as e:  # noqa: BLE001 — a tile, not the tab
        log.warning("etf snapshot %s failed: %s", symbol_id, e)
        facts["etf"] = None
    return facts
