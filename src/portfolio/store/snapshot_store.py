"""snapshots (date, account_id, currency) — daily history."""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List

from ...core import kst
from .db import connect


def has_for(date: str, account_id: str) -> bool:
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM snapshots WHERE date = ? AND account_id = ? LIMIT 1",
            (date, account_id),
        ).fetchone()
        return row is not None


def write(date: str, account_id: str, per_currency: Dict[str, dict]) -> int:
    rows = [(date, account_id, ccy,
             *(float(t.get(k, 0)) for k in ("eval", "cost", "pnl", "cash",
                                             "total_assets")))
            for ccy, t in per_currency.items()]
    if not rows:
        return 0
    with connect() as c:
        c.executemany(
            """
            INSERT INTO snapshots
              (date, account_id, currency, eval, cost, pnl, cash, total_assets)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, account_id, currency) DO UPDATE SET
              eval=excluded.eval, cost=excluded.cost, pnl=excluded.pnl,
              cash=excluded.cash, total_assets=excluded.total_assets
            """,
            rows,
        )
        c.commit()
    return len(rows)


def read(account_id: str, currency: str = "KRW", days: int = 90) -> List[dict]:
    cutoff = (kst.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with connect() as c:
        rows = c.execute(
            "SELECT date, eval, cost, pnl, cash, total_assets "
            "FROM snapshots WHERE account_id = ? AND currency = ? AND date >= ? "
            "ORDER BY date ASC",
            (account_id, currency, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def maybe_snapshot_today(account_id: str, per_currency: Dict[str, dict]) -> None:
    # Always re-write to capture mid-day changes (still one row per ccy/account).
    write(kst.today(), account_id, per_currency)
