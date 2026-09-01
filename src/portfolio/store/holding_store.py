"""holdings (account_id, symbol_id) CRUD."""
from __future__ import annotations

import time
from typing import Iterable, List, Optional

from .db import connect


def upsert(account_id: str, symbol_id: str, *,
           avg_cost: Optional[float] = None, qty: Optional[float] = None,
           note: Optional[str] = None, name: Optional[str] = None) -> None:
    """`name` overrides the universe display name; omitting it keeps whatever
    override is already stored (the edit form only sends it when changed)."""
    now = int(time.time())
    with connect() as c:
        c.execute(
            """
            INSERT INTO holdings
              (account_id, symbol_id, avg_cost, qty, note, name,
               added_at, sort_order, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, symbol_id) DO UPDATE SET
              avg_cost=excluded.avg_cost, qty=excluded.qty, note=excluded.note,
              name=COALESCE(excluded.name, holdings.name),
              updated_at=excluded.updated_at
            """,
            (account_id, symbol_id, avg_cost, qty, note, name, now, now, now),
        )
        c.commit()


def remove(account_id: str, symbol_id: str) -> int:
    with connect() as c:
        cur = c.execute(
            "DELETE FROM holdings WHERE account_id = ? AND symbol_id = ?",
            (account_id, symbol_id))
        c.commit()
        return cur.rowcount


def list_by_account(account_id: str) -> List[dict]:
    with connect() as c:
        rows = c.execute(
            """
            SELECT account_id, symbol_id, avg_cost, qty, note, name,
                   added_at, sort_order
            FROM holdings WHERE account_id = ?
            ORDER BY sort_order ASC, added_at ASC
            """,
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_all() -> List[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM holdings ORDER BY account_id, sort_order"
        ).fetchall()
        return [dict(r) for r in rows]


def all_symbol_ids() -> List[str]:
    """Distinct symbol_ids across all accounts — used by quote poller and
    daily gap-filler to know which symbols need market data."""
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT symbol_id FROM holdings"
        ).fetchall()
        return [r["symbol_id"] for r in rows]


def reorder(account_id: str, symbol_ids: Iterable[str]) -> int:
    pairs = [(i + 1, account_id, sid) for i, sid in enumerate(symbol_ids)]
    if not pairs:
        return 0
    with connect() as c:
        c.executemany(
            "UPDATE holdings SET sort_order = ? WHERE account_id = ? AND symbol_id = ?",
            pairs)
        c.commit()
        return len(pairs)
