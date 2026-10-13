"""Latest prices — the quote cache, topped up from the source when stale.

The market router's GET /quotes and the chat agent's `quote` tool both answer
from here, so a caller that wants a price never assembles the cache rule
itself.
"""
from __future__ import annotations

import logging
import time

from .source.registry import for_symbol
from .store import quote_store

log = logging.getLogger(__name__)


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


def _payload(q) -> dict:
    if q is None:
        return {"price": None, "stale": True}
    return {"price": q.price, "prev_close": q.prev_close, "ts": q.ts,
            "age_s": int(time.time() - q.ts), "stale": False}


def quotes(symbol_ids, max_age_s: int = 30) -> dict:
    """{"quotes": {symbol_id: {price, prev_close, ts, age_s, stale}}}."""
    return {"quotes": {sid: _payload(q)
                       for sid, q in warm(symbol_ids, max_age_s).items()}}
