"""Bridge last stored ts → today for all held symbols."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

from ...core import kst
from ...core.kst import KST
from ..source.registry import for_symbol
from ..store import ohlcv_store

log = logging.getLogger(__name__)


def _ymd(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def fill_one(symbol_id: str) -> int:
    src = for_symbol(symbol_id)
    if src is None:
        return 0
    last = ohlcv_store.last_ts(symbol_id)
    today = kst.now()
    if last <= 0:
        start = today - timedelta(days=365 * 3 + 10)
    else:
        start = datetime.fromtimestamp(last, tz=KST) + timedelta(days=1)
        if start > today:
            return 0
    bars = src.get_daily(symbol_id, _ymd(start), _ymd(today))
    return ohlcv_store.write(symbol_id, bars)


def fill_many(symbol_ids: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for sid in symbol_ids:
        try:
            out[sid] = fill_one(sid)
        except Exception as e:  # noqa: BLE001 — one symbol is not the batch
            log.warning("gap fill %s failed: %s", sid, e)
            out[sid] = -1                     # callers name these (ohlcv_errors)
    return out
