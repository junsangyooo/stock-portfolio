"""/api/market — ohlcv + quote (universal, account-independent)."""
from __future__ import annotations

import time

from fastapi import APIRouter

from ..market.pipeline import gap_filler, resampler, fundamental_backfill
from ..market.source.registry import for_symbol, yfinance_for
from ..market.store import ohlcv_store, quote_store, valuation_store, financials_store
from ..market import fundamental_metrics

router = APIRouter(prefix="/api/market", tags=["market"])


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

    if refresh:
        try:
            gap_filler.fill_one(symbol_id)
        except Exception:
            pass

    df = ohlcv_store.read(symbol_id)
    if period != "day":
        df = resampler.resample(df, period)
    if n > 0:
        df = df.tail(n).reset_index(drop=True)
    return {"symbol_id": symbol_id, "period": period, "n": len(df),
            "bars": ohlcv_store.to_payload(df)}


@router.get("/quote/{market}/{code}")
def get_quote(market: str, code: str, max_age_s: int = 30) -> dict:
    symbol_id = f"{market.upper()}:{code}"
    q = quote_store.get(symbol_id, max_age_s=max_age_s)
    if q is None:
        src = for_symbol(symbol_id)
        if src is not None:
            try:
                q = src.get_quote(symbol_id)
                if q is not None:
                    quote_store.set_quote(q)
            except Exception:
                q = None
    if q is None:
        return {"symbol_id": symbol_id, "price": None, "stale": True}
    return {"symbol_id": symbol_id, "price": q.price,
            "prev_close": q.prev_close, "ts": q.ts,
            "age_s": int(time.time() - q.ts), "stale": False}


@router.get("/quotes")
def get_quotes(symbols: str = "", max_age_s: int = 30) -> dict:
    """symbols = comma-separated 'KR:005930,US:AAPL'."""
    ids = [s.strip() for s in symbols.split(",") if s.strip()]
    out: dict[str, dict] = {}
    for sid in ids:
        q = quote_store.get(sid, max_age_s=max_age_s)
        if q is None:
            src = for_symbol(sid)
            if src is not None:
                try:
                    q = src.get_quote(sid)
                    if q is not None:
                        quote_store.set_quote(q)
                except Exception:
                    q = None
        if q is None:
            out[sid] = {"price": None, "stale": True}
        else:
            out[sid] = {"price": q.price, "prev_close": q.prev_close,
                        "ts": q.ts, "age_s": int(time.time() - q.ts),
                        "stale": False}
    return {"quotes": out}


# ── fundamental (L1 valuation + L2 financials + L3 derived) ───────────────
@router.get("/valuation/{market}/{code}")
def get_valuation(market: str, code: str, n: int = 0,
                  refresh: bool = False) -> dict:
    symbol_id = f"{market.upper()}:{code}"
    if refresh:
        try:
            fundamental_backfill.valuation(symbol_id)
        except Exception:
            pass
    df = valuation_store.read(symbol_id, n=n)
    return {"symbol_id": symbol_id, "n": len(df),
            "series": valuation_store.to_payload(df),
            "latest": valuation_store.latest(symbol_id)}


@router.get("/financials/{market}/{code}")
def get_financials(market: str, code: str, n: int = 0,
                   refresh: bool = False) -> dict:
    """L2 statement rows + L3 derived metrics (computed on the fly, D5)."""
    symbol_id = f"{market.upper()}:{code}"
    if refresh:
        try:
            fundamental_backfill.financials(symbol_id)
        except Exception:
            pass
    rows = financials_store.rows_as_dicts(symbol_id, n=n)
    latest = rows[-1] if rows else {}
    prior = fundamental_metrics.find_prior_year(
        rows, latest.get("period", "")) if latest else None
    derived = fundamental_metrics.compute(
        latest, prior_year=prior, valuation=valuation_store.latest(symbol_id))
    return {"symbol_id": symbol_id, "n": len(rows),
            "statements": rows, "latest_period": latest.get("period"),
            "derived": derived}

