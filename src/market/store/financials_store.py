"""Quarterly/annual statement store (L2) — data/market.db, table `financials`.

One row per reporting period. Dedup key = (symbol_id, period); a re-fetch of
the same period overwrites, which is how restatements land.

Period ordering ('2024Q3' < '2024FY') is not lexicographic, so it stays in
Python — a per-symbol series is ~20 rows and sorting them here is cheaper than
teaching SQL the rule.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, List, Optional

import pandas as pd

from ..source.base import FinancialRow
from .db import connect

NUMERIC = ["revenue", "gross_profit", "operating_income", "net_income", "eps",
           "total_assets", "total_liabilities", "total_equity",
           "current_assets", "current_liabilities",
           "operating_cf", "investing_cf", "financing_cf"]
COLS = ["period", "fs_div", "report_date", "source"] + NUMERIC


def _period_sort_key(period: str) -> tuple:
    """'2024Q3' → (2024, 3); '2024FY' → (2024, 5) so FY sorts after Q4."""
    try:
        year = int(period[:4])
    except (ValueError, TypeError):
        return (0, 0)
    suffix = period[4:]
    q = {"FY": 5}.get(suffix, int(suffix[1:]) if suffix.startswith("Q") else 0)
    return (year, q)


def write(symbol_id: str, rows: Iterable[FinancialRow],
          filed_at: Optional[str] = None) -> int:
    """Upsert on period (re-fetch overwrites). Returns number of NEW periods.

    `filed_at` is the disclosure receipt date, stamped on every row of this
    batch. The event-driven refresh (DART 공시검색 / SEC daily-index) passes it
    so a later run can tell "we already have this filing" from "the company
    restated"; the plain backfill leaves it NULL.
    """
    rows = list(rows)
    if not rows:
        return 0
    by_period = {r.period: asdict(r) for r in rows}
    placeholders = ",".join(["?"] * (len(COLS) + 2))  # + symbol_id, filed_at
    # A batch that does not know the filing date must not erase one we already
    # have: the daily backfill passes None, so `filed_at=excluded.filed_at`
    # wiped the stamp on every symbol it touched, every day.
    updates = ", ".join(
        [f"{c}=excluded.{c}" for c in COLS[1:]]
        + ["filed_at=COALESCE(excluded.filed_at, financials.filed_at)"])
    marks = ",".join("?" * len(by_period))
    with connect() as c:
        existing = {r[0] for r in c.execute(
            f"SELECT period FROM financials WHERE symbol_id = ? "
            f"AND period IN ({marks})", [symbol_id, *by_period]).fetchall()}
        c.executemany(
            f"INSERT INTO financials (symbol_id, {', '.join(COLS)}, filed_at) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(symbol_id, period) DO UPDATE SET {updates}",
            [(symbol_id, *[d[k] for k in COLS], filed_at)
             for d in by_period.values()],
        )
        c.commit()
    return len(by_period) - len(existing)


def read(symbol_id: str, n: int = 0) -> pd.DataFrame:
    cols = ", ".join(COLS)
    with connect() as c:
        rows = c.execute(f"SELECT {cols} FROM financials WHERE symbol_id = ?",
                         (symbol_id,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame([tuple(r) for r in rows], columns=COLS)
    df = df.astype({c: "float64" for c in NUMERIC})   # see ohlcv_store.read
    df = df.sort_values("period", key=lambda s: s.map(_period_sort_key))
    df = df.reset_index(drop=True)
    if n > 0:
        df = df.tail(n).reset_index(drop=True)
    return df


_NUMERIC = set(NUMERIC)


def rows_as_dicts(symbol_id: str, n: int = 0) -> List[dict]:
    return [{c: None if pd.isna(v) else (float(v) if c in _NUMERIC else str(v))
             for c, v in row.items()}
            for row in read(symbol_id, n=n).to_dict("records")]


def latest_filed_at(symbol_ids: Iterable[str]) -> dict:
    """{symbol_id: newest filed_at}, in ONE query, for those that have one.

    `filed_at` is the disclosure's own receipt date, which is what lets a
    caller ask "has a filing landed since we last looked at this symbol?"
    without calling DART or EDGAR. Rows migrated before the column existed
    carry NULL and simply do not answer.
    """
    ids = list(dict.fromkeys(symbol_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with connect() as c:
        rows = c.execute(
            "SELECT symbol_id, MAX(filed_at) FROM financials "
            "WHERE filed_at IS NOT NULL AND symbol_id IN (" + marks + ") "
            "GROUP BY symbol_id", ids).fetchall()
    return {str(sid): str(f) for sid, f in rows if f}
