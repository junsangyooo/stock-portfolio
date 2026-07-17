"""/api/universe — local symbol master lookup + search."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..universe import search as uni_search
from ..universe.builder import refresh as uni_refresh

router = APIRouter(prefix="/api/universe", tags=["universe"])


@router.get("/search")
def search(q: str = "", region: Optional[str] = None, limit: int = 20) -> dict:
    items = uni_search.search(q, region=region, limit=limit)
    return {"q": q, "region": region, "n": len(items), "results": items}


@router.get("/symbol/{symbol_id:path}")
def get_symbol(symbol_id: str) -> dict:
    item = uni_search.get(symbol_id)
    return {"symbol_id": symbol_id, "exists": item is not None, "item": item}


@router.get("/stats")
def stats() -> dict:
    return uni_search.stats()


@router.post("/refresh")
def refresh(region: str = "all") -> dict:
    """Trigger a sync refresh (slow — KOSPI+KOSDAQ+ETF+NASDAQ+NYSE+AMEX).
    Typically called once a day via cron, not from the dashboard."""
    summary = uni_refresh(region=region)
    uni_search.reload()
    return summary
