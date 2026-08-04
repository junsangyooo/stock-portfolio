"""/api/universe — local symbol master lookup.

Read-only: the master itself is rebuilt by the nightly `src.universe.refresh_job`
cron, and the in-memory cache re-reads it on its own TTL.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..universe import search as uni_search

router = APIRouter(prefix="/api/universe", tags=["universe"])


@router.get("/search")
def search(q: str = "", region: Optional[str] = None, limit: int = 20) -> dict:
    items = uni_search.search(q, region=region, limit=limit)
    return {"q": q, "region": region, "n": len(items), "results": items}


@router.get("/stats")
def stats() -> dict:
    """Cache health (counts + load time) — the ops check in ops notes §8."""
    return uni_search.stats()
