"""Chart facts store — data/market.db, table `chart_facts`.

One row per (symbol, trading day). Was one JSON file per symbol per day under
data/analysis/chart/, which collided with the verdict written to the very same
path by verdict.persist("chart", ...) — whichever ran last won. Nothing reads
the files any more.

`date` is the last closed daily bar. Recomputing on the same trading day
overwrites; a new trading day is a new row.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from ...core.kst import KST
from .db import connect


def read(symbol_id: str, date: Optional[str] = None) -> Optional[dict]:
    """One day's facts. Newest when `date` is omitted."""
    with connect() as c:
        if date:
            row = c.execute(
                "SELECT date, facts, engine_version, built_at FROM chart_facts "
                "WHERE symbol_id = ? AND date = ?", (symbol_id, date)).fetchone()
        else:
            row = c.execute(
                "SELECT date, facts, engine_version, built_at FROM chart_facts "
                "WHERE symbol_id = ? ORDER BY date DESC LIMIT 1",
                (symbol_id,)).fetchone()
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
    built_at = datetime.now(KST).isoformat()
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


def has(symbol_id: str, date: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM chart_facts WHERE symbol_id = ? "
                         "AND date = ? LIMIT 1",
                         (symbol_id, date)).fetchone() is not None
