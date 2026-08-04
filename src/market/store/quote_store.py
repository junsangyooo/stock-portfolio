"""Live quote cache.

In-memory dict, persisted to data/market/quote/last.json on every write so
the dashboard has something to render before the poller's first cycle after
restart.
"""
from __future__ import annotations

import json
import time
from threading import Lock
from typing import Dict, Optional

from ...core.paths import QUOTE_DIR, ensure_dirs
from ..source.base import Quote

_FILE = QUOTE_DIR / "last.json"

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
    with _lock:
        _load_disk()
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
