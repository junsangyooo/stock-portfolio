"""plans (plan_id) — 매매 계획 노트, 계좌 단위.

A plan is intent, not a position: nothing here ever touches `holdings`. When a
plan is actually executed the user records the trade (POST /trades) and that
trade row moves `status` — armed → entered → done (trade_store).

Every query is scoped by account_id as well as plan_id. `require_account`
already gates the routes; this is the second lock, so a wrong id cannot read
or delete another account's note even if that gate is ever bypassed.
"""
from __future__ import annotations

import json
import time
from typing import List, Optional

from .db import connect

SIDES = ("buy", "sell")
# What a person may set by hand (PATCH). entered/done normally arrive through
# a trade row; they are allowed here for the case the trade could not pick a
# plan. stopped/expired are quant vocabulary — a human just abandons.
#
# `proposed` is the planner's output: an agent wrote it, and it does nothing
# until the person approves it into `armed`. It is reachable by PATCH only as
# a source (approve / reject), never as a destination — nothing may turn an
# armed plan back into a proposal.
HUMAN_STATUSES = ("armed", "entered", "done", "abandoned", "proposed")
_FROM_PROPOSED = {"armed": "approve", "abandoned": "reject"}


def all_active_symbol_ids() -> List[str]:
    """Distinct symbol_ids under a live plan, across all accounts.

    A proposal counts: somebody is about to read its coordinates and decide,
    so it is exactly the symbol whose analysis must not be a week old.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT symbol_id FROM plans "
            "WHERE status IN ('armed', 'entered', 'proposed')").fetchall()
        return [r["symbol_id"] for r in rows]


def _conditions(raw):
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


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
        # who wrote it — the card shows an approve/reject pair only on a
        # proposal, and only an agent's plan may be rewritten by a later pass.
        "source": r["source"],
        # what is actually being approved. A proposal whose stop and target
        # the screen cannot show is a button a person presses blind.
        "conditions": _conditions(r["conditions"]),
        "expires_at": r["expires_at"],
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
                               note, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?, ?)
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
    allowed = ("symbol_id", "name", "side", "price", "qty", "note", "status")
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


def decide(account_id: str, plan_id: int, status: str) -> Optional[dict]:
    """Approve (`armed`) or reject (`abandoned`) an agent's proposal.

    Returns None when the plan is not this account's, is not a proposal, or is
    not the agent's — so the route answers 404 without a second read, and a
    person's own plan can never be closed by this door.
    """
    if status not in _FROM_PROPOSED:
        return None
    with connect() as c:
        cur = c.execute(
            "UPDATE plans SET status = ?, updated_at = ?"
            " WHERE plan_id = ? AND account_id = ?"
            "   AND status = 'proposed' AND source = 'agent'",
            (status, int(time.time()), plan_id, account_id))
        c.commit()
        if cur.rowcount == 0:
            return None
        row = c.execute("SELECT * FROM plans WHERE plan_id = ?",
                        (plan_id,)).fetchone()
        return _row(row) if row else None
