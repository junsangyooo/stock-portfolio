"""Per-symbol daily valuation parquet store (L1).

Path: data/market/valuation/{MARKET}/{code}.parquet
Index-free wide table, one row per date:
    ts, per, pbr, psr, eps, bps, dps, div_yield, market_cap

Mirrors ohlcv_store: append + dedup on ts. KR (pykrx) fills historical series
natively; US (yfinance.info) appends a single snapshot row per fetch date.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pandas as pd

from ...core.paths import ensure_dirs, valuation_path, split_symbol
from ..source.base import ValuationPoint

COLS = ["ts", "per", "pbr", "psr", "eps", "bps", "dps", "div_yield",
        "market_cap"]


def _path(symbol_id: str) -> Path:
    market, code = split_symbol(symbol_id)
    p = valuation_path(market, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write(symbol_id: str, points: Iterable[ValuationPoint]) -> int:
    """Append + dedup on ts (newer wins). Returns number of NEW rows."""
    ensure_dirs()
    points = list(points)
    if not points:
        return 0
    new_df = pd.DataFrame([
        {"ts": p.ts, "per": p.per, "pbr": p.pbr, "psr": p.psr, "eps": p.eps,
         "bps": p.bps, "dps": p.dps, "div_yield": p.div_yield,
         "market_cap": p.market_cap}
        for p in points
    ])
    path = _path(symbol_id)
    if path.exists():
        old = pd.read_parquet(path)
        merged = pd.concat([old, new_df], ignore_index=True)
        # newer row (later in concat) wins on ts collision
        merged = (merged.drop_duplicates("ts", keep="last")
                  .sort_values("ts").reset_index(drop=True))
        added = len(merged) - len(old)
    else:
        merged = new_df.drop_duplicates("ts", keep="last").sort_values("ts")
        merged = merged.reset_index(drop=True)
        added = len(merged)
    merged.to_parquet(path, index=False)
    return max(added, 0)


def read(symbol_id: str, n: int = 0) -> pd.DataFrame:
    path = _path(symbol_id)
    if not path.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_parquet(path).sort_values("ts").reset_index(drop=True)
    if n > 0:
        df = df.tail(n).reset_index(drop=True)
    return df


def latest(symbol_id: str) -> dict:
    """Most recent valuation row as a dict (or empty dict)."""
    df = read(symbol_id, n=1)
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {c: (None if pd.isna(row[c]) else float(row[c])) for c in COLS}


def to_payload(df: pd.DataFrame) -> List[dict]:
    if df.empty:
        return []
    out: List[dict] = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        out.append({k: (None if pd.isna(v) else (int(v) if k == "ts" else float(v)))
                    for k, v in d.items()})
    return out
