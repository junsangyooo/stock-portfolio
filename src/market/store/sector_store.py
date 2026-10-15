"""Sector/industry tag store — data/market.db, table `sector`.

Sector is (nearly) static, so the daily batch only refetches when the cache is
missing or stale. The comprehensive analysis reads it (research/fundamental
assembles it) next to fundamental_rules.md §E's sector-relative norms, and
market/peer.py groups peers by it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .db import connect


def read(symbol_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute(
            "SELECT symbol_id, sector, industry, source, updated_at "
            "FROM sector WHERE symbol_id = ?", (symbol_id,)).fetchone()
    return dict(row) if row else None


def write(symbol_id: str, sector: Optional[str], industry: Optional[str],
          source: str = "yfinance") -> dict:
    rec = {
        "symbol_id": symbol_id,
        "sector": sector or None,
        "industry": industry or None,
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with connect() as c:
        c.execute(
            "INSERT INTO sector (symbol_id, sector, industry, source, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(symbol_id) DO UPDATE SET "
            "sector=excluded.sector, industry=excluded.industry, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (symbol_id, rec["sector"], rec["industry"], source,
             rec["updated_at"]))
        c.commit()
    return rec


def is_fresh(symbol_id: str, max_age_days: int = 30) -> bool:
    """True if a non-empty cached sector exists and is younger than max_age."""
    rec = read(symbol_id)
    if not rec or not rec.get("sector"):
        return False
    try:
        ts = datetime.fromisoformat(rec["updated_at"])
    except (ValueError, KeyError, TypeError):
        return False
    age = datetime.now(timezone.utc) - ts
    return age.days < max_age_days
