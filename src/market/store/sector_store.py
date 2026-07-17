"""Per-symbol sector/industry tag cache.

A tiny JSON file per symbol at data/market/sector/{MARKET}/{code}.json:
    {"symbol_id", "sector", "industry", "source", "updated_at"}

Sector is (nearly) static, so the daily batch only refetches when the cache is
missing or stale. The fundamental agent reads it to apply the rulebook's §E
sector-relative norms (we do NOT compute live peer medians — see the design
doc's scope boundary).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ...core.paths import sector_path, split_symbol


def read(symbol_id: str) -> Optional[dict]:
    market, code = split_symbol(symbol_id)
    p = sector_path(market, code)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write(symbol_id: str, sector: Optional[str], industry: Optional[str],
          source: str = "yfinance") -> dict:
    market, code = split_symbol(symbol_id)
    p = sector_path(market, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "symbol_id": symbol_id,
        "sector": sector or None,
        "industry": industry or None,
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
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
