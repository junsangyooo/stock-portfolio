"""/api/market — ohlcv + quotes (universal, account-independent)."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter

from ..market.pipeline import gap_filler, resampler
from ..market.source.registry import for_symbol, yfinance_for
from ..market.store import ohlcv_store, quote_store

router = APIRouter(prefix="/api/market", tags=["market"])

log = logging.getLogger(__name__)

_INTRADAY = {"1m": ("5d", "1m"), "5m": ("60d", "5m"),
             "15m": ("60d", "15m"), "30m": ("60d", "30m"),
             "60m": ("60d", "60m")}


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
        df = resampler.resample(df, period)
    if n > 0:
        df = df.tail(n).reset_index(drop=True)
    return {"symbol_id": symbol_id, "period": period, "n": len(df),
            "bars": ohlcv_store.to_payload(df), "refresh_failed": stale}


def _quote_payload(symbol_id: str, max_age_s: int) -> dict:
    """Cached quote, else fetch-and-store once. Never raises."""
    q = quote_store.get(symbol_id, max_age_s=max_age_s)
    if q is None:
        src = for_symbol(symbol_id)
        if src is not None:
            try:
                q = src.get_quote(symbol_id)
                if q is not None:
                    quote_store.set_quote(q)
            except Exception as e:  # noqa: BLE001 — a dead source must not 500
                log.warning("quote fetch %s failed: %s", symbol_id, e)
                q = None
    if q is None:
        return {"price": None, "stale": True}
    return {"price": q.price, "prev_close": q.prev_close, "ts": q.ts,
            "age_s": int(time.time() - q.ts), "stale": False}


@router.get("/quotes")
def get_quotes(symbols: str = "", max_age_s: int = 30) -> dict:
    """symbols = comma-separated 'KR:005930,US:AAPL'."""
    ids = [s.strip() for s in symbols.split(",") if s.strip()]
    return {"quotes": {sid: _quote_payload(sid, max_age_s) for sid in ids}}
