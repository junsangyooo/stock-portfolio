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
    by_period = {r.period: r for r in rows}
    placeholders = ",".join(["?"] * (len(COLS) + 2))  # + symbol_id, filed_at
    updates = ", ".join(f"{c}=excluded.{c}" for c in COLS[1:] + ["filed_at"])
    with connect() as c:
        marks = ",".join("?" * len(by_period))
        existing = {r[0] for r in c.execute(
            f"SELECT period FROM financials WHERE symbol_id = ? "
            f"AND period IN ({marks})", [symbol_id, *by_period]).fetchall()}
        c.executemany(
            f"INSERT INTO financials (symbol_id, {', '.join(COLS)}, filed_at) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(symbol_id, period) DO UPDATE SET {updates}",
            [(symbol_id, *[asdict(r)[k] for k in COLS], filed_at)
             for r in by_period.values()],
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


def _row_to_dict(row) -> dict:
    out = {}
    for c in COLS:
        v = row[c]
        if c in NUMERIC:
            out[c] = None if pd.isna(v) else float(v)
        else:
            out[c] = None if pd.isna(v) else str(v)
    return out


def rows_as_dicts(symbol_id: str, n: int = 0) -> List[dict]:
    df = read(symbol_id, n=n)
    return [_row_to_dict(df.iloc[i]) for i in range(len(df))]


