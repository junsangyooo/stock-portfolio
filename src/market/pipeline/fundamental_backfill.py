"""Fundamental backfill — fetch L1 valuation + L2 financials for a symbol.

Routing (per design):
  KR  valuation  ← pykrx   (historical daily series)
      financials ← DART    (corp_code → finstate_all)
  US  valuation  ← yfinance.info snapshot (appended per fetch date)
      financials ← yfinance quarterly statements

Idempotent: stores append/upsert and dedup. Safe to call repeatedly.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import List

from ..source.yfinance_source import YFinanceSource
from ..store import valuation_store, financials_store, ohlcv_store
from .. import kr_valuation, fundamental_metrics
from ...core.paths import split_symbol

KST = timezone(timedelta(hours=9))

_yf = YFinanceSource()
_dart = None  # lazy: only constructed when a KR symbol needs DART


def _dart_source():
    global _dart
    if _dart is None:
        from ..source.dart_source import DartSource
        _dart = DartSource()
    return _dart


def _ymd(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def valuation(symbol_id: str, n: int = 760) -> int:
    """KR: derive from stored DART financials + OHLCV close (run financials
    first). US: yfinance .info snapshot appended for this fetch date."""
    market, _ = split_symbol(symbol_id)
    if market == "KR":
        rows = financials_store.rows_as_dicts(symbol_id)
        df = ohlcv_store.read(symbol_id, n=n)
        if df.empty or not rows:
            return 0
        closes = [(int(r.ts), float(r.close)) for r in df.itertuples(index=False)]
        try:
            dps_rec = _dart_source().get_dps(symbol_id)
        except Exception:  # noqa: BLE001 — dividend is enrichment, not core
            dps_rec = None
        pts = kr_valuation.build(rows, closes,
                                 dps=(dps_rec or {}).get("dps"))
    else:
        pts = _yf.get_valuation(symbol_id)
    return valuation_store.write(symbol_id, pts)


def sanity_check(symbol_id: str) -> List[str]:
    """Anomaly flags for the latest stored period's derived metrics — catches
    implausible L2 inputs (mis-parsed filings) at collection time. Logged as
    warnings by fill_one, never a failure."""
    rows = financials_store.rows_as_dicts(symbol_id)
    if not rows:
        return []
    latest = rows[-1]
    prior = fundamental_metrics.find_prior_year(rows, latest.get("period", ""))
    derived = fundamental_metrics.compute(latest, prior_year=prior)
    quality = {
        "qoe": fundamental_metrics.quality_of_earnings(
            latest.get("operating_cf"), latest.get("net_income")),
        "sloan_accrual": fundamental_metrics.sloan_accrual(
            latest.get("net_income"), latest.get("operating_cf"),
            latest.get("investing_cf"), latest.get("total_assets")),
    }
    return [f"{latest.get('period')} {f}"
            for f in fundamental_metrics.anomaly_flags(derived, quality)]


def financials(symbol_id: str, years: int = 4) -> int:
    market, _ = split_symbol(symbol_id)
    if market == "KR":
        try:
            rows = _dart_source().get_financials(symbol_id, years=years)
        except Exception:
            rows = []
    else:
        rows = _yf.get_financials(symbol_id, years=years)
    return financials_store.write(symbol_id, rows)


def _is_etf(symbol_id: str) -> bool:
    """ETFs have no financial statements / per-share valuation — skip them in
    the fundamental backfill. The etf_job covers them with NAV/AUM snapshots."""
    try:
        from ...universe import search as uni_search
        rec = uni_search.get(symbol_id) or {}
        return bool(rec.get("is_etf"))
    except Exception:  # noqa: BLE001 — fall through to non-etf path
        return False


def fill_one(symbol_id: str, years: int = 4) -> dict:
    """Backfill both layers. Financials FIRST (KR valuation derives from it).
    ETFs are skipped here — see src/market/pipeline/etf_job.py instead."""
    if _is_etf(symbol_id):
        return {"symbol_id": symbol_id, "valuation_rows": 0,
                "financials_rows": 0, "skipped": "etf"}
    f = financials(symbol_id, years=years)
    v = valuation(symbol_id)
    out = {"symbol_id": symbol_id, "valuation_rows": v, "financials_rows": f}
    flags = sanity_check(symbol_id)
    if flags:
        for flag in flags:
            print(f"[anomaly] {symbol_id} {flag}", file=sys.stderr)
        out["anomalies"] = flags
    return out
