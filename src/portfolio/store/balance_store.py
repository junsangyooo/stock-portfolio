"""cash (account_id, currency) — 예수금.

The table is `cash` (schema.py); it was `balances` here until the
ledger was unified. The module keeps its name because callers say
"balance" for the amount.
"""
from __future__ import annotations

import time
from typing import Dict

from .db import connect


def get_all(account_id: str) -> Dict[str, float]:
    with connect() as c:
        rows = c.execute(
            "SELECT currency, amount FROM cash WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        return {r["currency"]: float(r["amount"]) for r in rows}


def set_many(account_id: str, balances: Dict[str, float]) -> int:
    now = int(time.time())
    rows = [(account_id, (k or "").upper(), float(v), now)
            for k, v in balances.items() if k]
    if not rows:
        return 0
    with connect() as c:
        c.executemany(
            """
            INSERT INTO cash (account_id, currency, amount, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, currency) DO UPDATE SET
              amount=excluded.amount, updated_at=excluded.updated_at
            """,
            rows,
        )
        c.commit()
        return len(rows)
