"""Daily → week/month/year resampling (on-demand)."""
from __future__ import annotations

import pandas as pd

_RULES = {"week": "W-FRI", "month": "MS", "year": "YS"}


def resample(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty or period not in _RULES:
        return df
    df = df.copy()
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.set_index("dt")
    agg = df.resample(_RULES[period]).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    # Never divide raw astype("int64") output: to_datetime(unit="s") keeps
    # second resolution on pandas 3.x but was promoted to nanoseconds on
    # 2.x, so a fixed //10**9 silently maps every bar to 1970 on one of
    # them. .timestamp() is unit-agnostic and these frames are tiny.
    agg["ts"] = agg.index.map(lambda t: int(t.timestamp()))
    agg = agg.reset_index(drop=True)
    return agg[["ts", "open", "high", "low", "close", "volume"]]
