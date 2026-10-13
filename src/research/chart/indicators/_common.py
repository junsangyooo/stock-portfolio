"""Shared helpers for indicator plugins (fact extraction only)."""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from ..engine import last_closed_date
from ..schema import Category, IndicatorFact, STATE_NO_DATA, Timeframe


def prev_float(series: pd.Series, back: int = 1) -> Optional[float]:
    """Value `back` bars before the last, as float, or None (missing or NaN)."""
    if series is None or len(series) <= back:
        return None
    try:
        f = float(series.iloc[-1 - back])
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def last_float(series: pd.Series) -> Optional[float]:
    """Last value of a series as float, or None (empty or NaN)."""
    return prev_float(series, 0)


def enough(df: pd.DataFrame, n: int) -> bool:
    """True if df has at least n rows."""
    return df is not None and len(df) >= n


def na_fact(indicator: str, category: Category, tf: Timeframe,
            df: pd.DataFrame, reason: str) -> IndicatorFact:
    """A 'no_data' fact used when the indicator can't be computed."""
    return IndicatorFact(
        indicator=indicator, category=category, timeframe=tf,
        state=STATE_NO_DATA, value=None, evidence={"reason": reason},
        asof=last_closed_date(df) if df is not None else None,
    )
