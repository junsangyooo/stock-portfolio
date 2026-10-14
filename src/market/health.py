"""How fresh the stored market facts are, for a given list of symbols.

The sources dashboard asks "is each price/statement source still delivering?".
Every answer here is a count over the symbols it is handed (ops notes §4: the
caller supplies the list, this layer never asks who holds what), plus the
caches that are not per symbol (KRX screener).

A bar is late when its date is more than BAR_LATE_DAYS calendar days behind
today: four days covers a weekend plus a holiday without flagging it.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Iterable

from ..core import kst
from .store import (etf_store, financials_store, ohlcv_store, quote_store,
                    sector_store, valuation_store)

BAR_LATE_DAYS = 4
QUOTE_LATE_S = BAR_LATE_DAYS * 86400


def _count(ages: list, late: float) -> dict:
    """ages: one entry per symbol, None when nothing is stored."""
    have = [a for a in ages if a is not None]
    return {"n": len(ages), "missing": len(ages) - len(have),
            "late": sum(a > late for a in have),
            "oldest": max(have) if have else None}


def _days_since(day: str | None, today: date) -> int | None:
    if not day:
        return None
    return (today - date.fromisoformat(str(day)[:10])).days


def symbols(symbol_ids: Iterable[str]) -> dict:
    """Per stored fact, how many of these symbols have it and how many lag.
    Ages are in days (quotes in seconds)."""
    ids = list(dict.fromkeys(symbol_ids))
    today = kst.now().date()
    now = time.time()

    def bar_age(sid):
        ts = ohlcv_store.last_ts(sid)
        return (today - datetime.fromtimestamp(ts, kst.KST).date()).days if ts else None

    def quote_age(sid):
        q = quote_store.get(sid)
        return int(now - q.ts) if q else None

    def valuation_present(sid):
        return 0 if valuation_store.latest(sid) else None

    filed = financials_store.latest_filed_at(ids)
    etfs = [s for s in ids if etf_store.read(s) is not None]
    return {
        "bars": _count([bar_age(s) for s in ids], BAR_LATE_DAYS),
        "quotes": _count([quote_age(s) for s in ids], QUOTE_LATE_S),
        "valuation": _count([valuation_present(s) for s in ids], 0),
        "filings": {"n": len(ids), "with_filed_at": len(filed),
                    "newest": max(filed.values()) if filed else None},
        "sector": _count([0 if sector_store.read(s) else None for s in ids], 0),
        "etf": _count([_days_since((etf_store.read(s) or {}).get("updated_at"), today)
                       for s in etfs], 2),
    }


def screener() -> dict:
    """The newest KRX factor table cached, and how many days old it is."""
    from .source.krx_screener import SCREENER_DIR
    files = sorted(SCREENER_DIR.glob("*.parquet")) if SCREENER_DIR.exists() else []
    if not files:
        return {"date": None, "age_days": None}
    ymd = files[-1].stem
    day = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return {"date": day, "age_days": _days_since(day, kst.now().date())}
