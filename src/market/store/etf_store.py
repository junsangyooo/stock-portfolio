"""Per-ETF daily snapshot store (data/market/etf/{MARKET}/{code}.json).

Single file per ETF, OVERWRITTEN each day. History lives in the OHLCV parquet
(daily price), not here — this file is the latest-known snapshot of facts that
don't fit in OHLCV: NAV, 괴리율, AUM, 3M return, category. The fundamental
agent reads this when `is_etf=True` instead of statements/valuation parquets.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ...core.paths import etf_path, split_symbol


def read(symbol_id: str) -> Optional[dict]:
    market, code = split_symbol(symbol_id)
    p = etf_path(market, code)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write(symbol_id: str, snapshot: dict) -> dict:
    """Persist a normalized snapshot (see naver_etf_source.shape)."""
    market, code = split_symbol(symbol_id)
    p = etf_path(market, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "symbol_id": symbol_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **snapshot,
    }
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def is_fresh(symbol_id: str, max_age_hours: int = 36) -> bool:
    """True if a snapshot exists and is younger than max_age (default 36h —
    one daily cycle + slack). Used by the daily job to skip same-day refetch."""
    rec = read(symbol_id)
    if not rec:
        return False
    try:
        ts = datetime.fromisoformat(rec["updated_at"])
    except (ValueError, KeyError, TypeError):
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    return age < max_age_hours
