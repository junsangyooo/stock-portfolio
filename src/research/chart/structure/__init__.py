"""Structure analysis (category 5): swing extrema engine + S/R, trendlines,
fibonacci, pivots, chart/candle patterns, gaps, divergence, recent move.

`extrema` is the shared foundation; the other modules build on it.
`detect_structure` bundles them into the `structure` block of chart facts.
"""
from __future__ import annotations

import pandas as pd


def detect_structure(df: pd.DataFrame) -> dict:
    """Run all structure detectors on a daily frame -> context dict.

    Structure is context, not a vote: nearest levels, breakouts and patterns
    that the analysis reads next to the indicator facts.
    """
    from . import levels, trendline, patterns, divergence, recent_move  # local import avoids any cycle

    return {
        "support_resistance": levels.support_resistance(df),
        "fibonacci": levels.fibonacci(df),
        "pivots": levels.pivot_points(df, kind="classic"),
        "trendlines": trendline.fit_trendlines(df),
        "candles": patterns.detect_candles(df),
        "gap": patterns.detect_gap(df),
        "chart_patterns": patterns.detect_double_top_bottom(df),
        "divergence": divergence.detect_divergence(df),
        "recent_move": recent_move.detect_recent_move(df),
    }
