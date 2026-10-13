"""/api/market — ohlcv + quotes (universal, account-independent)."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from ..market import quotes
from ..market.pipeline import gap_filler
from ..market import resample
from ..market.quotes import warm
from ..market.source.registry import yfinance_for
from ..market.store import ohlcv_store

router = APIRouter(prefix="/api/market", tags=["market"])

log = logging.getLogger(__name__)

_INTRADAY = {"1m": ("5d", "1m"), "5m": ("60d", "5m"),
             "15m": ("60d", "15m"), "30m": ("60d", "30m"),
             "60m": ("60d", "60m")}


@router.post("/refresh")
def refresh_all() -> dict:
    """Gap-fill OHLCV + warm quotes for every symbol portfolio.db holds.

    This is the ↻ button behind account.html, so it covers every account of
    the book, not just the visible one, because market data is per-symbol and
    account-agnostic (§1 단일 진실). No require_account: nothing
    account-specific is returned.
    """
    from ..portfolio.store.db import connect
    with connect() as conn:
        sids = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol_id FROM holdings ORDER BY symbol_id")]
    filled = gap_filler.fill_many(sids)
    fresh = sum(1 for q in warm(sids, max_age_s=10).values() if q is not None)
    return {"ok": True, "symbols": len(sids),
            "filled": filled, "quotes_fresh": fresh}


@router.get("/ohlcv/{market}/{code}")
def get_ohlcv(market: str, code: str, period: str = "day", n: int = 240,
              refresh: bool = False) -> dict:
    """period = 1m|5m|15m|30m|60m|day|week|month|year."""
    symbol_id = f"{market.upper()}:{code}"

    if period in _INTRADAY:
        period_back, interval = _INTRADAY[period]
        # KR pykrx has no intraday → force yfinance for both
        src = yfinance_for(symbol_id)
        bars = src.get_intraday(symbol_id, interval=interval, period=period_back)
        bars = bars[-n:] if n > 0 else bars
        return {
            "symbol_id": symbol_id, "period": period, "n": len(bars),
            "bars": [{"ts": b.ts, "open": b.open, "high": b.high,
                      "low": b.low, "close": b.close, "volume": b.volume}
                     for b in bars],
        }

    stale = False
    if refresh:
        try:
            gap_filler.fill_one(symbol_id)
        except Exception as e:  # noqa: BLE001 — serve what we have, but say so
            log.warning("ohlcv refresh %s failed: %s", symbol_id, e)
            stale = True

    df = ohlcv_store.read(symbol_id)
    if period != "day":
        df = resample.resample(df, period)
    if n > 0:
        df = df.tail(n).reset_index(drop=True)
    return {"symbol_id": symbol_id, "period": period, "n": len(df),
            "bars": ohlcv_store.to_payload(df), "refresh_failed": stale}


@router.get("/quotes")
def get_quotes(symbols: str = "", max_age_s: int = 30) -> dict:
    """symbols = comma-separated 'KR:005930,US:AAPL'."""
    ids = [s.strip() for s in symbols.split(",") if s.strip()]
    return quotes.quotes(ids, max_age_s)
