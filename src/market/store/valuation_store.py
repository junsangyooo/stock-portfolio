"""Daily valuation store (L1) — data/market.db, table `valuation`.

One row per date: ts, per, pbr, psr, eps, bps, dps, div_yield, market_cap.
KR rows are derived from DART financials + closes (kr_valuation); US
(yfinance.info) appends a single snapshot row per fetch date.

Upsert on (symbol_id, ts) — a re-derivation of the same date wins, because a
later run has strictly better inputs (more statements, corrected closes).
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

from ..source.base import ValuationPoint
from .db import connect

COLS = ["ts", "per", "pbr", "psr", "eps", "bps", "dps", "div_yield",
        "market_cap"]
_VAL_COLS = COLS[1:]


def write(symbol_id: str, points: Iterable[ValuationPoint]) -> int:
    """Upsert on ts (newer wins). Returns number of NEW rows."""
    points = list(points)
    if not points:
        return 0
    # Later duplicates within one batch win, matching the old concat+keep=last.
    by_ts = {int(p.ts): p for p in points}
    with connect() as c:
        marks = ",".join("?" * len(by_ts))
        existing = {r[0] for r in c.execute(
            f"SELECT ts FROM valuation WHERE symbol_id = ? AND ts IN ({marks})",
            [symbol_id, *by_ts]).fetchall()}
        c.executemany(
            "INSERT INTO valuation (symbol_id, ts, per, pbr, psr, eps, bps, "
            "dps, div_yield, market_cap) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol_id, ts) DO UPDATE SET "
            "per=excluded.per, pbr=excluded.pbr, psr=excluded.psr, "
            "eps=excluded.eps, bps=excluded.bps, dps=excluded.dps, "
            "div_yield=excluded.div_yield, market_cap=excluded.market_cap",
            [(symbol_id, ts, p.per, p.pbr, p.psr, p.eps, p.bps, p.dps,
              p.div_yield, p.market_cap) for ts, p in by_ts.items()],
        )
        c.commit()
    return len(by_ts) - len(existing)


def read(symbol_id: str, n: int = 0) -> pd.DataFrame:
    cols = ", ".join(COLS)
    with connect() as c:
        if n > 0:
            rows = c.execute(
                f"SELECT {cols} FROM valuation WHERE symbol_id = ? "
                "ORDER BY ts DESC LIMIT ?", (symbol_id, n)).fetchall()[::-1]
        else:
            rows = c.execute(
                f"SELECT {cols} FROM valuation WHERE symbol_id = ? ORDER BY ts",
                (symbol_id,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame([tuple(r) for r in rows], columns=COLS)
    # Pin the dtypes — SQLite does not carry them, and a valuation column is
    # routinely all-NULL (psr, dps), which would otherwise arrive as `object`.
    return df.astype({"ts": "int64", **{c: "float64" for c in _VAL_COLS}})


def latest(symbol_id: str) -> dict:
    """Most recent valuation row as a dict (or empty dict)."""
    cols = ", ".join(COLS)
    with connect() as c:
        row = c.execute(
            f"SELECT {cols} FROM valuation WHERE symbol_id = ? "
            "ORDER BY ts DESC LIMIT 1", (symbol_id,)).fetchone()
    if row is None:
        return {}
    return {k: (None if row[k] is None else float(row[k])) for k in COLS}
