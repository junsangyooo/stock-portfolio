"""plans (plan_id) — 매매 계획 노트, 계좌 단위.

A plan is intent, not a position: nothing here ever touches `holdings`. When a
plan is actually executed the user records the trade (POST /trades) and that
trade row moves `status` — armed → entered → done (trade_store).

Every query is scoped by account_id as well as plan_id. `require_account`
already gates the routes; this is the second lock, so a wrong id cannot read
or delete another account's note even if that gate is ever bypassed.
"""
from __future__ import annotations

import time
from typing import List, Optional

from .db import connect

SIDES = ("buy", "sell")


def _row(r) -> dict:
    return {
        "plan_id": r["plan_id"],
        "symbol_id": r["symbol_id"],
        "name": r["name"],
        "side": r["side"],
        "price": float(r["price"]),
        "qty": float(r["qty"]),
        "note": r["note"] or "",
        "status": r["status"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def list_all(account_id: str) -> List[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM plans WHERE account_id = ? ORDER BY created_at DESC",
            (account_id,),
        ).fetchall()
        return [_row(r) for r in rows]


def create(account_id: str, *, symbol_id: str, name: str, side: str,
           price: float, qty: float, note: str = "") -> dict:
    now = int(time.time())
    with connect() as c:
        cur = c.execute(
            """
            INSERT INTO plans (account_id, symbol_id, name, side, price, qty,
                               note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, symbol_id, name, side, float(price), float(qty),
             note or None, now, now),
        )
        c.commit()
        row = c.execute("SELECT * FROM plans WHERE plan_id = ?",
                        (cur.lastrowid,)).fetchone()
        return _row(row)


def update(account_id: str, plan_id: int, **fields) -> Optional[dict]:
    """Patch the named columns. Returns None when the plan is not this
    account's (or is gone), so the route can answer 404 without a second read."""
    allowed = ("symbol_id", "name", "side", "price", "qty", "note")
    sets, args = [], []
    for k in allowed:
        if k in fields and fields[k] is not None:
            sets.append(f"{k} = ?")
            args.append(fields[k])
    if not sets:
        return get(account_id, plan_id)
    sets.append("updated_at = ?")
    args.extend([int(time.time()), plan_id, account_id])
    with connect() as c:
        cur = c.execute(
            f"UPDATE plans SET {', '.join(sets)} "
            "WHERE plan_id = ? AND account_id = ?", args)
        c.commit()
        if cur.rowcount == 0:
            return None
        row = c.execute("SELECT * FROM plans WHERE plan_id = ?",
                        (plan_id,)).fetchone()
        return _row(row) if row else None


def get(account_id: str, plan_id: int) -> Optional[dict]:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM plans WHERE plan_id = ? AND account_id = ?",
            (plan_id, account_id),
        ).fetchone()
        return _row(row) if row else None


def delete(account_id: str, plan_id: int) -> bool:
    with connect() as c:
        cur = c.execute(
            "DELETE FROM plans WHERE plan_id = ? AND account_id = ?",
            (plan_id, account_id))
        c.commit()
        return cur.rowcount > 0
