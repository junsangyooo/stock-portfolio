"""Per-symbol daily OHLCV parquet store.

Path: data/market/ohlcv/{MARKET}/{code}.parquet
       where MARKET ∈ {KR, US} (matches symbol_id prefix).

We store ONLY daily bars. Coarser periods (week/month/year) are derived on
the fly by src/market/pipeline/resampler.py — storage cost of duplicates
is higher than CPU cost of resampling, and avoids divergence.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pandas as pd

from ...core.paths import ensure_dirs, ohlcv_path, split_symbol
from ..source.base import OHLCVBar

COLS = ["ts", "open", "high", "low", "close", "volume"]


def _path(symbol_id: str) -> Path:
    market, code = split_symbol(symbol_id)
    p = ohlcv_path(market, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write(symbol_id: str, bars: Iterable[OHLCVBar]) -> int:
    """Append + dedup on ts. Returns number of NEW rows added."""
    ensure_dirs()
    bars = list(bars)
    if not bars:
        return 0
    new_df = pd.DataFrame([
        {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for b in bars
    ])
    path = _path(symbol_id)
    if path.exists():
        old = pd.read_parquet(path)
        old_ts = set(old["ts"].astype(int).tolist())
        new_df = new_df[~new_df["ts"].astype(int).isin(old_ts)]
        if new_df.empty:
            return 0
        merged = pd.concat([old, new_df], ignore_index=True)
        merged = merged.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    else:
        merged = new_df.sort_values("ts").reset_index(drop=True)
    merged.to_parquet(path, index=False)
    return len(new_df)


def read(symbol_id: str, n: int = 0) -> pd.DataFrame:
    path = _path(symbol_id)
    if not path.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_parquet(path).sort_values("ts").reset_index(drop=True)
    if n > 0:
        df = df.tail(n).reset_index(drop=True)
    return df


def last_ts(symbol_id: str) -> int:
    df = read(symbol_id)
    if df.empty:
        return 0
    return int(df["ts"].iloc[-1])


def to_payload(df: pd.DataFrame) -> List[dict]:
    if df.empty:
        return []
    return [
        {"ts": int(r.ts), "open": float(r.open), "high": float(r.high),
         "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
        for r in df.itertuples(index=False)
    ]
