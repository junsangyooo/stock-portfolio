"""Per-symbol quarterly/annual financial-statement parquet store (L2).

Path: data/market/financials/{MARKET}/{code}.parquet
Wide table, one row per reporting period:
    period, fs_div, report_date, source,
    revenue, gross_profit, operating_income, net_income,
    total_assets, total_liabilities, total_equity,
    current_assets, current_liabilities,
    operating_cf, investing_cf, financing_cf

Dedup key = period. A re-fetch of the same period overwrites (restatements).
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List

import pandas as pd

from ...core.paths import ensure_dirs, financials_path, split_symbol
from ..source.base import FinancialRow

NUMERIC = ["revenue", "gross_profit", "operating_income", "net_income", "eps",
           "total_assets", "total_liabilities", "total_equity",
           "current_assets", "current_liabilities",
           "operating_cf", "investing_cf", "financing_cf"]
COLS = ["period", "fs_div", "report_date", "source"] + NUMERIC


def _path(symbol_id: str) -> Path:
    market, code = split_symbol(symbol_id)
    p = financials_path(market, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _period_sort_key(period: str) -> tuple:
    """'2024Q3' → (2024, 3); '2024FY' → (2024, 5) so FY sorts after Q4."""
    try:
        year = int(period[:4])
    except (ValueError, TypeError):
        return (0, 0)
    suffix = period[4:]
    q = {"FY": 5}.get(suffix, int(suffix[1:]) if suffix.startswith("Q") else 0)
    return (year, q)


def write(symbol_id: str, rows: Iterable[FinancialRow]) -> int:
    """Upsert on period (re-fetch overwrites). Returns number of NEW periods."""
    ensure_dirs()
    rows = list(rows)
    if not rows:
        return 0
    new_df = pd.DataFrame([asdict(r) for r in rows])[COLS]
    path = _path(symbol_id)
    if path.exists():
        old = pd.read_parquet(path)
        before = set(old["period"].tolist())
        merged = pd.concat([old, new_df], ignore_index=True)
        merged = merged.drop_duplicates("period", keep="last")
        added = len([p for p in merged["period"] if p not in before])
    else:
        merged = new_df.drop_duplicates("period", keep="last")
        added = len(merged)
    merged = merged.sort_values(
        "period", key=lambda s: s.map(_period_sort_key)).reset_index(drop=True)
    merged.to_parquet(path, index=False)
    return added


def read(symbol_id: str, n: int = 0) -> pd.DataFrame:
    path = _path(symbol_id)
    if not path.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_parquet(path)
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


def latest(symbol_id: str) -> dict:
    """Most recent period row as a dict (or empty)."""
    df = read(symbol_id, n=1)
    if df.empty:
        return {}
    return _row_to_dict(df.iloc[-1])
