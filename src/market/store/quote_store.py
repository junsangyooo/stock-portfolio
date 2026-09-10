"""Live quote cache.

In-memory dict, persisted to data/market/quote/last.json on every write so
the dashboard has something to render before the poller's first cycle after
restart.
"""
from __future__ import annotations

import json
import time
from threading import Lock
from typing import Dict, Iterable, Optional

from ...core.paths import QUOTE_DIR, ensure_dirs
from ..source.base import Quote

_FILE = QUOTE_DIR / "last.json"

# The ceiling for "this is still a price". `get()` defaults to no limit
# because the poller wants whatever is cached; anything that VALUES a
# position or feeds a judgment must pass a limit, or a quote from a symbol
# whose source died months ago reads as the current price and the numbers
# built on it look ordinary. Measured: the cache held quotes 13 to 108 days
# old while every reader treated them as live.
#
# 8 days, not one: a long KR holiday (설·추석 + the weekends around it) can
# close the market for five straight sessions, and refusing a real price
# through 추석 would empty the account page for a week. Everything the freeze
# actually produced was older than this.
STALE_S = 8 * 86400

_lock = Lock()
_cache: Dict[str, Quote] = {}
_loaded = False


def _load_disk() -> None:
    global _loaded
    if _loaded:
        return
    ensure_dirs()
    if _FILE.exists():
        try:
            raw = json.loads(_FILE.read_text())
            for sid, q in raw.items():
                _cache[sid] = Quote(
                    symbol_id=sid, price=float(q["price"]),
                    prev_close=float(q["prev_close"]), ts=int(q["ts"]),
                )
        except Exception:
            pass
    _loaded = True


def _flush_locked() -> None:
    snap = {sid: {"price": q.price, "prev_close": q.prev_close, "ts": q.ts}
            for sid, q in _cache.items()}
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap))
    tmp.replace(_FILE)


def set_quote(q: Quote) -> None:
    set_many([q])


def set_many(quotes: Iterable[Quote]) -> None:
    """Store several quotes with ONE disk flush — the refresh path warms
    every held symbol at once, and a flush per symbol rewrote the whole file
    N times."""
    quotes = list(quotes)
    if not quotes:
        return
    with _lock:
        _load_disk()
        for q in quotes:
            _cache[q.symbol_id] = q
        _flush_locked()


def get(symbol_id: str, *, max_age_s: int = 0) -> Optional[Quote]:
    with _lock:
        _load_disk()
        q = _cache.get(symbol_id)
        if q is None:
            return None
        if max_age_s > 0 and (time.time() - q.ts) > max_age_s:
            return None
        return q
