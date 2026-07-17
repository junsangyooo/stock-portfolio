"""Multi-timeframe engine — load daily OHLCV, derive weekly/monthly, guard
against look-ahead bias (D6).

We store ONLY daily bars (src/market/store/ohlcv_store). Weekly/monthly are
derived on the fly via the existing resampler. The single most important rule
(D6, docs/chart-analysis-design.md): a signal must never be computed on an
in-progress (not-yet-closed) weekly/monthly bar — that is peeking at the
future. `drop_incomplete_bar` enforces this.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from ...market.pipeline import resampler
from ...market.store import ohlcv_store

# Our timeframe labels -> resampler period names.
TF_TO_PERIOD = {"1w": "week", "1mo": "month"}
TIMEFRAMES = ("1d", "1w", "1mo")


def load_ohlcv(symbol_id: str, n: int = 0) -> pd.DataFrame:
    """Load daily OHLCV for a symbol (cols: ts, open, high, low, close, volume)."""
    return ohlcv_store.read(symbol_id, n)


def to_timeframe(df_daily: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample a daily frame to the requested timeframe.

    tf == "1d" returns the frame unchanged; "1w"/"1mo" delegate to the existing
    resampler (open=first, high=max, low=min, close=last, volume=sum).
    """
    if tf == "1d":
        return df_daily.reset_index(drop=True)
    period = TF_TO_PERIOD.get(tf)
    if period is None:
        raise ValueError(f"unknown timeframe {tf!r} (valid: {TIMEFRAMES})")
    return resampler.resample(df_daily, period)


def drop_incomplete_bar(
    df: pd.DataFrame, tf: str, now: Optional[datetime] = None
) -> pd.DataFrame:
    """Drop the last bar if it belongs to the current (still-open) week/month.

    Daily bars are treated as closed (the stored close is final). For weekly /
    monthly bars, if the last bar's calendar week / month equals `now`'s, the
    period has not ended yet -> drop it so no signal can use future-peeking data.
    """
    if tf == "1d" or df.empty:
        return df.reset_index(drop=True)
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    last_ts = int(df["ts"].iloc[-1])
    last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)

    if tf == "1w":
        in_progress = last_dt.isocalendar()[:2] == now.isocalendar()[:2]
    elif tf == "1mo":
        in_progress = (last_dt.year, last_dt.month) == (now.year, now.month)
    else:
        in_progress = False

    if in_progress:
        return df.iloc[:-1].reset_index(drop=True)
    return df.reset_index(drop=True)


def last_closed_date(df: pd.DataFrame) -> Optional[str]:
    """YYYY-MM-DD of the last (closed) bar, or None if empty."""
    if df.empty:
        return None
    last_ts = int(df["ts"].iloc[-1])
    return datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")


def cache_key(
    symbol_id: str, tf: str, indicator: str, params: dict, last_closed: Optional[str]
) -> str:
    """Deterministic cache key. Past closed bars are immutable, so a key keyed on
    the last closed bar date is stable until a new bar closes (D6 caching)."""
    pstr = ",".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{symbol_id}|{tf}|{indicator}|{pstr}|{last_closed}"
