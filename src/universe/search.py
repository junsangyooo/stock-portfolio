"""In-memory universe search.

Loads data/universe/{kr,us}.json once per process (and every ~6h to pick up
nightly refreshes). All searches hit memory — no external API call.

API:
  search(q, region=None, limit=20) → list[item]
  get(symbol_id) → item | None
  currency_of(symbol_id, meta=None) → 'KRW' | 'USD' | …
"""
from __future__ import annotations

import json
import logging
import time
from threading import Lock
from typing import List, Optional

from ..core.paths import UNIVERSE_DIR

log = logging.getLogger(__name__)

_CACHE_TTL_S = 6 * 3600

_lock = Lock()
_items: List[dict] = []
_by_id: dict[str, dict] = {}
_loaded_at: float = 0.0


def _is_stale() -> bool:
    return (time.time() - _loaded_at) > _CACHE_TTL_S


def _load_locked() -> None:
    """Caller must hold _lock."""
    global _items, _by_id, _loaded_at
    items: List[dict] = []
    for fname in ("kr.json", "us.json"):
        p = UNIVERSE_DIR / fname
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text())
            items.extend(payload.get("items", []))
        except (json.JSONDecodeError, OSError) as e:
            log.error("universe %s unreadable, searching without it: %s", fname, e)
    _items = items
    _by_id = {it["symbol_id"]: it for it in items}
    _loaded_at = time.time()


def _ensure_loaded() -> None:
    with _lock:
        if not _items or _is_stale():
            _load_locked()


def get(symbol_id: str) -> Optional[dict]:
    _ensure_loaded()
    return _by_id.get(symbol_id)


def currency_of(symbol_id: str, meta: Optional[dict] = None) -> str:
    """The currency a symbol trades and settles in — the one rule for it.
    Its universe record first (`meta`, or looked up); else the symbol_id's
    market (US → USD, anything else KRW). A trade's cash leg and an
    account's per-currency totals both ask here, so they never disagree."""
    if meta is None:
        meta = get(symbol_id) or {}
    ccy = meta.get("currency")
    if ccy:
        return str(ccy).upper()
    return "USD" if symbol_id.split(":", 1)[0].upper() == "US" else "KRW"


def search(q: str, region: Optional[str] = None, limit: int = 20) -> List[dict]:
    """Substring match (case-insensitive) on code | name_kr | name_en.

    region ∈ {'KR', 'US'} restricts to that side. None = both.
    Prioritization: exact code > prefix > substring.
    """
    q = (q or "").strip()
    if not q:
        return []
    _ensure_loaded()

    q_lower = q.lower()
    region_filter = region.upper() if region else None

    exact: List[dict] = []
    prefix: List[dict] = []
    contains: List[dict] = []
    seen: set[str] = set()

    for it in _items:
        sid = it["symbol_id"]
        if region_filter and not sid.startswith(region_filter + ":"):
            continue
        if sid in seen:
            continue
        code = (it.get("code") or "").lower()
        nk = (it.get("name_kr") or "").lower()
        ne = (it.get("name_en") or "").lower()

        if code == q_lower:
            exact.append(it); seen.add(sid); continue
        if code.startswith(q_lower) or nk.startswith(q_lower) or ne.startswith(q_lower):
            prefix.append(it); seen.add(sid); continue
        if q_lower in code or q_lower in nk or q_lower in ne:
            contains.append(it); seen.add(sid); continue

    return (exact + prefix + contains)[:limit]


def stats() -> dict:
    _ensure_loaded()
    by_region: dict[str, int] = {}
    for it in _items:
        r = it["symbol_id"].split(":", 1)[0]
        by_region[r] = by_region.get(r, 0) + 1
    return {"total": len(_items), "by_region": by_region,
            "loaded_at": _loaded_at}
