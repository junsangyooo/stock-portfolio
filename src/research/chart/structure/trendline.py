"""Trendlines / channels (category 5 — structure).

A support trendline is fit (least-squares) through recent swing lows, a
resistance trendline through recent swing highs. We report slope, the line's
current value, and whether the last close broke through. Simple polyfit
approach (pytrendline-style exhaustive search is a later enhancement).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import extrema
from ..engine import last_closed_date


def _fit(points: list[tuple[int, float]]):
    """Least-squares line through (index, price) points. Returns (slope, intercept)."""
    if len(points) < 2:
        return None
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope), float(intercept)


def fit_trendlines(df: pd.DataFrame, order: int = 5, max_points: int = 4) -> dict:
    """Fit support (swing lows) and resistance (swing highs) trendlines.

    Uses the most recent `max_points` swings on each side. Reports slope sign
    (rising/falling), the line value at the last bar, and break detection.
    """
    if df is None or len(df) < 2 * order + 2:
        return {"support": None, "resistance": None, "reason": "insufficient bars"}

    sh, sl = extrema.frame_swings(df, order)
    last_x = len(df) - 1
    price = float(df["close"].iloc[-1])

    def _describe(points, side):
        pts = points[-max_points:]
        fit = _fit(pts)
        if fit is None:
            return None
        slope, intercept = fit
        val = slope * last_x + intercept
        broke = None
        if side == "support" and price < val:
            broke = "break_down"
        elif side == "resistance" and price > val:
            broke = "break_up"
        return {
            "slope": round(slope, 6),
            "direction": "rising" if slope > 0 else "falling" if slope < 0 else "flat",
            "value_now": round(val, 4),
            "touches": len(pts),
            "break": broke,
        }

    return {
        "support": _describe(sl, "support"),
        "resistance": _describe(sh, "resistance"),
        "price": round(price, 4),
        "asof": last_closed_date(df),
    }
