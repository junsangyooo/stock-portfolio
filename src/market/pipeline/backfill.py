"""Backfill OHLCV when a symbol is first referenced by any holding.

3 years of daily bars (idempotent; safe to call repeatedly)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..source.registry import for_symbol
from ..store import ohlcv_store

KST = timezone(timedelta(hours=9))


def _ymd(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def daily(symbol_id: str, years: int = 3) -> int:
    src = for_symbol(symbol_id)
    if src is None:
        return 0
    today = datetime.now(KST)
    start = today - timedelta(days=365 * years + 10)
    bars = src.get_daily(symbol_id, _ymd(start), _ymd(today))
    return ohlcv_store.write(symbol_id, bars)
