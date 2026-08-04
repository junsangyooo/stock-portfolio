"""balances (account_id, currency) — user-edited cash (예수금)."""
from __future__ import annotations

import time
from typing import Dict

from .db import connect


def get_all(account_id: str) -> Dict[str, float]:
    with connect() as c:
        rows = c.execute(
            "SELECT currency, amount FROM balances WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        return {r["currency"]: float(r["amount"]) for r in rows}


def adjust(account_id: str, currency: str, delta: float) -> float:
    """Apply a signed delta to a currency balance and return the new amount.

    Used on trades: buy → negative delta (cash out), sell → positive delta
    (cash in). The row is created (starting at 0) if it doesn't exist yet, so
    the resulting amount may go negative (we do not block overdrafts here)."""
    currency = (currency or "").upper()
    if not currency or not delta:
        return get_all(account_id).get(currency, 0.0)
    now = int(time.time())
    with connect() as c:
        c.execute(
            """
            INSERT INTO balances (account_id, currency, amount, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, currency) DO UPDATE SET
              amount=balances.amount + excluded.amount,
              updated_at=excluded.updated_at
            """,
            (account_id, currency, float(delta), now),
        )
        c.commit()
        row = c.execute(
            "SELECT amount FROM balances WHERE account_id = ? AND currency = ?",
            (account_id, currency),
        ).fetchone()
        return float(row["amount"]) if row else 0.0


def set_many(account_id: str, balances: Dict[str, float]) -> int:
    now = int(time.time())
    rows = [(account_id, (k or "").upper(), float(v), now)
            for k, v in balances.items() if k]
    if not rows:
        return 0
    with connect() as c:
        c.executemany(
            """
            INSERT INTO balances (account_id, currency, amount, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, currency) DO UPDATE SET
              amount=excluded.amount, updated_at=excluded.updated_at
            """,
            rows,
        )
        c.commit()
        return len(rows)
