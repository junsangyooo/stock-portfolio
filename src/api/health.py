"""/api/health — is every source and scheduled job delivering? (portfolio.health)

Behind the global /api gate: any account's session opens it, and the report
names no holding (counts only).
"""
from __future__ import annotations

from fastapi import APIRouter

from ..portfolio import health

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/sources")
def sources() -> dict:
    return health.report()
