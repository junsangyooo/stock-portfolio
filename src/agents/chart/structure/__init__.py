"""Structure analysis (category 5): swing extrema engine + S/R, trendlines,
fibonacci, pivots, chart/candle patterns, gaps.

`extrema` is the shared foundation; `levels`, `trendline`, `patterns` build on
it. `detect_structure` bundles them into one context dict for the ChartAgent.
"""
from __future__ import annotations

import pandas as pd


def detect_structure(df: pd.DataFrame, tf: str = "1d", pivot_kind: str = "classic") -> dict:
    """Run all structure detectors on a single-timeframe frame -> context dict.

    Structure does NOT vote in the 4-category aggregate; the ChartAgent (C4)
    uses this as context (nearest levels, breakouts, patterns) when forming and
    explaining the final verdict.
    """
    from . import levels, trendline, patterns, divergence, recent_move  # local import avoids any cycle

    return {
        "support_resistance": levels.support_resistance(df),
        "fibonacci": levels.fibonacci(df),
        "pivots": levels.pivot_points(df, kind=pivot_kind),
        "trendlines": trendline.fit_trendlines(df),
        "candles": patterns.detect_candles(df),
        "gap": patterns.detect_gap(df),
        "chart_patterns": patterns.detect_double_top_bottom(df),
        "divergence": divergence.detect_divergence(df),
        "recent_move": recent_move.detect_recent_move(df),
    }
