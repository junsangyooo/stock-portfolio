"""accounts table CRUD."""
from __future__ import annotations

import time
from typing import List, Optional

from .db import connect


def create(account_id: str, name: str, account_type: str,
           broker: Optional[str] = None, base_currency: str = "KRW",
           note: Optional[str] = None) -> None:
    now = int(time.time())
    with connect() as c:
        # sort_order = max+1
        row = c.execute("SELECT COALESCE(MAX(sort_order), 0) FROM accounts").fetchone()
        next_order = int(row[0]) + 1
        c.execute(
            """
            INSERT INTO accounts (account_id, name, account_type, broker,
                                  base_currency, sort_order, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, name, account_type, broker, base_currency,
             next_order, note, now),
        )
        c.commit()


def update(account_id: str, *, name: Optional[str] = None,
           account_type: Optional[str] = None, broker: Optional[str] = None,
           base_currency: Optional[str] = None, note: Optional[str] = None) -> int:
    fields, vals = [], []
    for k, v in (("name", name), ("account_type", account_type),
                 ("broker", broker), ("base_currency", base_currency),
                 ("note", note)):
        if v is not None:
            fields.append(f"{k} = ?")
            vals.append(v)
    if not fields:
        return 0
    vals.append(account_id)
    with connect() as c:
        cur = c.execute(
            f"UPDATE accounts SET {', '.join(fields)} WHERE account_id = ?", vals)
        c.commit()
        return cur.rowcount


def delete(account_id: str) -> int:
    with connect() as c:
        cur = c.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
        c.commit()
        return cur.rowcount


def set_pin_hash(account_id: str, pin_hash: Optional[str]) -> int:
    with connect() as c:
        cur = c.execute(
            "UPDATE accounts SET pin_hash = ? WHERE account_id = ?",
            (pin_hash, account_id))
        c.commit()
        return cur.rowcount


def get_pin_hash(account_id: str) -> Optional[str]:
    with connect() as c:
        row = c.execute(
            "SELECT pin_hash FROM accounts WHERE account_id = ?",
            (account_id,)).fetchone()
        return row["pin_hash"] if row else None


def get(account_id: str) -> Optional[dict]:
    """The account row WITHOUT pin_hash. Callers hand this straight to API
    responses (service.aggregate embeds it), so the hash must not ride along;
    get_pin_hash() is the one way to read it."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d.pop("pin_hash", None)
        return d


def list_ids(kind: str = "human") -> List[str]:
    """Just the ids, in the order the page shows them — what a batch iterates.

    Keyed on kind so a book that ever grows an auto row does not hand it to
    the human morning pass.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT account_id FROM accounts WHERE kind = ?"
            " ORDER BY sort_order, account_id", (kind,)).fetchall()
        return [r["account_id"] for r in rows]


def list_all() -> List[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM accounts ORDER BY sort_order ASC"
        ).fetchall()
        return [dict(r) for r in rows]


