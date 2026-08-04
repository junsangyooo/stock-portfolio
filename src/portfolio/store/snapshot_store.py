"""snapshots (date, account_id, currency) — daily history."""
from __future__ import annotations

from datetime import datetime, timedelta

from ...core.kst import KST
from typing import Dict, List

from .db import connect



def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def has_for(date: str, account_id: str) -> bool:
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM snapshots WHERE date = ? AND account_id = ? LIMIT 1",
            (date, account_id),
        ).fetchone()
        return row is not None


def write(date: str, account_id: str, per_currency: Dict[str, dict]) -> int:
    rows = []
    for ccy, t in per_currency.items():
        rows.append((
            date, account_id, ccy,
            float(t.get("eval", 0)), float(t.get("cost", 0)),
            float(t.get("pnl", 0)), float(t.get("cash", 0)),
            float(t.get("total_assets", 0)),
        ))
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
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    with connect() as c:
        rows = c.execute(
            "SELECT date, eval, cost, pnl, cash, total_assets "
            "FROM snapshots WHERE account_id = ? AND currency = ? AND date >= ? "
            "ORDER BY date ASC",
            (account_id, currency, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def maybe_snapshot_today(account_id: str, per_currency: Dict[str, dict]) -> bool:
    today = today_kst()
    first = not has_for(today, account_id)
    # Always re-write to capture mid-day changes (still one row per ccy/account).
    write(today, account_id, per_currency)
    return first
