"""Aggregate FastAPI router — mounts all /api/* subroutes."""
from __future__ import annotations

from fastapi import APIRouter

from . import analysis, market, portfolio_api, universe

router = APIRouter()
router.include_router(universe.router)
router.include_router(portfolio_api.router)
router.include_router(market.router)
router.include_router(analysis.router)
