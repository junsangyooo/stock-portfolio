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
        df = resampler.resample(df, period)
    if n > 0:
        df = df.tail(n).reset_index(drop=True)
    return {"symbol_id": symbol_id, "period": period, "n": len(df),
            "bars": ohlcv_store.to_payload(df), "refresh_failed": stale}


def warm(symbol_ids, max_age_s: int) -> dict:
    """{symbol_id: Quote | None}: cached when fresh enough, else fetched —
    and every fetched quote stored with one flush. Never raises."""
    out = {sid: quote_store.get(sid, max_age_s=max_age_s) for sid in symbol_ids}
    fetched = []
    for sid, q in out.items():
        if q is not None:
            continue
        src = for_symbol(sid)
        if src is None:
            continue
        try:
            q = src.get_quote(sid)
        except Exception as e:  # noqa: BLE001 — a dead source must not 500
            log.warning("quote fetch %s failed: %s", sid, e)
            continue
        if q is not None:
            out[sid] = q
            fetched.append(q)
    quote_store.set_many(fetched)
    return out


def _quote_payload(q) -> dict:
    if q is None:
        return {"price": None, "stale": True}
    return {"price": q.price, "prev_close": q.prev_close, "ts": q.ts,
            "age_s": int(time.time() - q.ts), "stale": False}


@router.get("/quotes")
def get_quotes(symbols: str = "", max_age_s: int = 30) -> dict:
    """symbols = comma-separated 'KR:005930,US:AAPL'."""
    ids = [s.strip() for s in symbols.split(",") if s.strip()]
    return {"quotes": {sid: _quote_payload(q)
                       for sid, q in warm(ids, max_age_s).items()}}
