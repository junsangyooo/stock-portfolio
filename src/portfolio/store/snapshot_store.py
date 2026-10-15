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


_INSERT = """
    INSERT INTO snapshots
      (date, account_id, currency, eval, cost, pnl, cash, total_assets)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def _rows(date: str, account_id: str, per_currency: Dict[str, dict]) -> list:
    return [(date, account_id, ccy,
             *(float(t.get(k, 0)) for k in ("eval", "cost", "pnl", "cash",
                                             "total_assets")))
            for ccy, t in per_currency.items()]


def write(date: str, account_id: str, per_currency: Dict[str, dict]) -> int:
    """Write the day's rows, replacing any already there — the scheduled
    closes (snapshot_job, 06:20 and 16:35 KST) only."""
    rows = _rows(date, account_id, per_currency)
    if not rows:
        return 0
    with connect() as c:
        c.executemany(
            _INSERT + """
            ON CONFLICT(date, account_id, currency) DO UPDATE SET
              eval=excluded.eval, cost=excluded.cost, pnl=excluded.pnl,
              cash=excluded.cash, total_assets=excluded.total_assets
            """,
            rows,
        )
        c.commit()
    return len(rows)


def write_if_absent(date: str, account_id: str, per_currency: Dict[str, dict]) -> int:
    """Write only the currencies that have no row for the day yet — a page
    view. The scheduled closes are the record; a view only fills a day they
    have not written yet, and never moves one they have (it used to rewrite
    the day on every read, so the curve showed whatever the last visit saw).
    Returns the rows actually written."""
    rows = _rows(date, account_id, per_currency)
    if not rows:
        return 0
    with connect() as c:
        n = 0
        for r in rows:
            n += c.execute(_INSERT + " ON CONFLICT(date, account_id, currency) DO NOTHING",
                           r).rowcount
        c.commit()
    return n


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
