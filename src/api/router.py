"""Aggregate FastAPI router — mounts all /api/* subroutes."""
from __future__ import annotations

from fastapi import APIRouter

from . import accounts, analysis, chat, market, universe

router = APIRouter()
router.include_router(universe.router)
router.include_router(accounts.router)
router.include_router(market.router)
router.include_router(analysis.router)
router.include_router(chat.router)
