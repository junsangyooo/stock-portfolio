"""Daily OHLCV store — data/market.db, table `ohlcv`.

We store ONLY daily bars. Coarser periods (week/month/year) are derived on
the fly by src/market/resample.py — storage cost of duplicates
is higher than CPU cost of resampling, and avoids divergence.

Was one parquet per symbol. At 53 held symbols that was fine; at the whole
universe it is 11k files that cannot answer "every symbol on this date"
without opening all of them.
"""
from __future__ import annotations

from typing import Iterable, List

import pandas as pd

from ..source.base import OHLCVBar
from .db import connect

COLS = ["ts", "open", "high", "low", "close", "volume"]


def write(symbol_id: str, bars: Iterable[OHLCVBar]) -> int:
    """Append, ignoring bars already stored. Returns number of NEW rows."""
    bars = list(bars)
    if not bars:
        return 0
    with connect() as c:
        before = c.total_changes
        c.executemany(
            "INSERT OR IGNORE INTO ohlcv "
            "(symbol_id, ts, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?)",
            [(symbol_id, int(b.ts), b.open, b.high, b.low, b.close, b.volume)
             for b in bars],
        )
        c.commit()
        return c.total_changes - before


def read(symbol_id: str, n: int = 0) -> pd.DataFrame:
    """Ascending daily bars; `n` > 0 keeps only the most recent n."""
    sql = f"SELECT {', '.join(COLS)} FROM ohlcv WHERE symbol_id = ? ORDER BY ts"
    with connect() as c:
        if n > 0:
            # newest n, then flip back to ascending
            rows = c.execute(sql + " DESC LIMIT ?", (symbol_id, n)).fetchall()[::-1]
        else:
            rows = c.execute(sql, (symbol_id,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame([tuple(r) for r in rows], columns=COLS)
    # Pin the dtypes. Parquet carried them; SQLite does not, so an all-NULL
    # column would come back as `object` and change arithmetic downstream.
    return df.astype({"ts": "int64", **{c: "float64" for c in COLS[1:]}})


def read_closes(symbol_ids: Iterable[str], n: int) -> dict:
    """{symbol_id: [close, …]} — the newest `n` closes per symbol, ascending,
    in ONE query. The account page draws a sparkline per holding; a
    DataFrame per symbol was a connection and a query each."""
    ids = list(dict.fromkeys(symbol_ids))
    if not ids or n <= 0:
        return {}
    marks = ",".join("?" * len(ids))
    with connect() as c:
        rows = c.execute(
            "SELECT symbol_id, close FROM ("
            "  SELECT symbol_id, ts, close, "
            "         ROW_NUMBER() OVER (PARTITION BY symbol_id ORDER BY ts DESC) AS rn "
            "  FROM ohlcv WHERE close IS NOT NULL AND symbol_id IN (" + marks + ")"
            ") WHERE rn <= ? ORDER BY symbol_id, ts",
            (*ids, n)).fetchall()
    out: dict = {}
    for sid, close in rows:
        out.setdefault(sid, []).append(float(close))
    return out


def last_ts(symbol_id: str) -> int:
    """Newest stored bar time, 0 when the symbol has none. Used by the gap
    filler to decide between a 3-year backfill and a short catch-up, so it
    must not pay for reading the whole series."""
    with connect() as c:
        row = c.execute("SELECT MAX(ts) FROM ohlcv WHERE symbol_id = ?",
                        (symbol_id,)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def to_payload(df: pd.DataFrame) -> List[dict]:
    if df.empty:
        return []
    return [
        {"ts": int(r.ts), "open": float(r.open), "high": float(r.high),
         "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
        for r in df.itertuples(index=False)
    ]
