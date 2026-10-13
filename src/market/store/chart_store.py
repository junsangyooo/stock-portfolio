"""Chart facts store — data/market.db, table `chart_facts`.

One row per (symbol, trading day). `date` is the last closed daily bar.
Recomputing on the same trading day overwrites; a new trading day is a new
row.
"""
from __future__ import annotations

import json
from typing import Optional

from ...core import kst
from .db import connect

_SELECT = "SELECT date, facts, engine_version, built_at FROM chart_facts "


def read(symbol_id: str, date: Optional[str] = None) -> Optional[dict]:
    """One day's facts. Newest when `date` is omitted."""
    with connect() as c:
        if date:
            row = c.execute(_SELECT + "WHERE symbol_id = ? AND date = ?",
                            (symbol_id, date)).fetchone()
        else:
            row = c.execute(_SELECT + "WHERE symbol_id = ? ORDER BY date DESC "
                            "LIMIT 1", (symbol_id,)).fetchone()
    if not row:
        return None
    try:
        facts = json.loads(row["facts"])
    except json.JSONDecodeError:
        return None
    return {"symbol_id": symbol_id, "date": row["date"],
            "engine_version": row["engine_version"],
            "built_at": row["built_at"], **facts}


def write(symbol_id: str, date: str, facts: dict, engine_version: str) -> dict:
    """Upsert one trading day's facts."""
    built_at = kst.now().isoformat()
    with connect() as c:
        c.execute(
            "INSERT INTO chart_facts (symbol_id, date, facts, engine_version, "
            "built_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(symbol_id, date) DO UPDATE SET "
            "facts=excluded.facts, engine_version=excluded.engine_version, "
            "built_at=excluded.built_at",
            (symbol_id, date, json.dumps(facts, ensure_ascii=False, default=str),
             engine_version, built_at))
        c.commit()
    return {"symbol_id": symbol_id, "date": date,
            "engine_version": engine_version, "built_at": built_at, **facts}
