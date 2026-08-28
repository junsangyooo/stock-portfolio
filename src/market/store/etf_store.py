"""ETF snapshot store — data/market.db, table `etf`.

One row per ETF, OVERWRITTEN each day. History lives in ohlcv (daily price),
not here — this row is the latest-known snapshot of facts that don't fit in
OHLCV: NAV, 괴리율, AUM, 3M return, category. The fundamental agent reads it
when `is_etf=True` instead of statements/valuation.

The snapshot keeps the source's own shape (naver_etf_source.shape) in a JSON
column: it varies by provider and none of its fields are ever queried on.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .db import connect


def read(symbol_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT snapshot, updated_at FROM etf "
                        "WHERE symbol_id = ?", (symbol_id,)).fetchone()
    if not row:
        return None
    try:
        snap = json.loads(row["snapshot"])
    except json.JSONDecodeError:
        return None
    return {"symbol_id": symbol_id, "updated_at": row["updated_at"], **snap}


def write(symbol_id: str, snapshot: dict) -> dict:
    """Persist a normalized snapshot (see naver_etf_source.shape)."""
    updated_at = datetime.now(timezone.utc).isoformat()
    with connect() as c:
        c.execute(
            "INSERT INTO etf (symbol_id, snapshot, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(symbol_id) DO UPDATE SET "
            "snapshot=excluded.snapshot, updated_at=excluded.updated_at",
            (symbol_id, json.dumps(snapshot, ensure_ascii=False), updated_at))
        c.commit()
    return {"symbol_id": symbol_id, "updated_at": updated_at, **snapshot}
