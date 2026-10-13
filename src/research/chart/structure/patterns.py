"""Candlestick patterns, gaps, and basic chart patterns (category 5).

Detectors return directional pattern events (bullish/bearish) with the bar they
occur on. Candlestick reversal patterns are only meaningful with a prior trend,
so single/multi-candle reversals report direction and the analysis reads them
next to the trend facts. Chart-pattern geometry beyond double top/bottom
(head&shoulders, triangles, measured-move targets) is a later enhancement.
"""
from __future__ import annotations

import pandas as pd

from . import extrema
from ..engine import last_closed_date
from ..indicators import _ta

_DOJI_BODY = 0.1      # body <= 10% of range
_LONG_SHADOW = 2.0    # shadow >= 2x body


def _parts(row):
    """(open, close, range, body, upper shadow, lower shadow) of one candle."""
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    return o, c, h - l, abs(c - o), h - max(o, c), min(o, c) - l


def detect_candles(df: pd.DataFrame) -> list[dict]:
    """Detect candlestick patterns ending at the last bar.

    Returns a list of {pattern, direction, at} for patterns present on the last
    1-3 candles. `direction` is bullish / bearish / indecision.
    """
    if df is None or len(df) < 3:
        return []
    o, c, rng, body, upper, lower = _parts(df.iloc[-1])
    if rng <= 0:
        return []
    asof = last_closed_date(df)
    out = []

    def add(pattern: str, direction: str) -> None:
        out.append({"pattern": pattern, "direction": direction, "at": asof})

    # ── single-candle ──
    if body <= rng * _DOJI_BODY:
        add("doji", "indecision")
    if body > 0 and lower >= _LONG_SHADOW * body and upper <= body:
        add("hammer", "bullish")
    if body > 0 and upper >= _LONG_SHADOW * body and lower <= body:
        add("shooting_star", "bearish")
    if body >= rng * 0.95:
        add("marubozu", "bullish" if c > o else "bearish")

    # ── two-candle (prev, last) ──
    po, pc, prng, pbody, _, _ = _parts(df.iloc[-2])
    prev_bear, prev_bull = pc < po, pc > po
    cur_bull, cur_bear = c > o, c < o
    # engulfing
    if prev_bear and cur_bull and c >= po and o <= pc:
        add("bullish_engulfing", "bullish")
    if prev_bull and cur_bear and o >= pc and c <= po:
        add("bearish_engulfing", "bearish")
    # harami (prev body engulfs current body)
    if prev_bear and cur_bull and o >= pc and c <= po:
        add("bullish_harami", "bullish")
    if prev_bull and cur_bear and o <= pc and c >= po:
        add("bearish_harami", "bearish")

    # ── three-candle stars ──
    o3, c3, *_ = _parts(df.iloc[-3])   # oldest
    mid_small = prng > 0 and pbody <= prng * 0.4
    if c3 < o3 and mid_small and cur_bull and c > (o3 + c3) / 2:
        add("morning_star", "bullish")
    if c3 > o3 and mid_small and cur_bear and c < (o3 + c3) / 2:
        add("evening_star", "bearish")

    return out


def detect_gap(df: pd.DataFrame, atr_window: int = 14) -> dict:
    """Detect a gap on the last bar (open vs prior high/low), normalised by ATR."""
    if df is None or len(df) < atr_window + 2:
        return {"gap": None, "reason": "insufficient bars"}
    prev_high, prev_low = float(df["high"].iloc[-2]), float(df["low"].iloc[-2])
    open_ = float(df["open"].iloc[-1])

    # simple ATR (rolling mean of true range, not Wilder)
    tr = _ta.true_range(df["high"].astype(float), df["low"].astype(float),
                        df["close"].astype(float))
    atr = float(tr.rolling(atr_window).mean().iloc[-1])
    if atr <= 0:
        return {"gap": None, "reason": "atr zero"}

    asof = last_closed_date(df)
    if open_ > prev_high:
        kind, size = "gap_up", open_ - prev_high
    elif open_ < prev_low:
        kind, size = "gap_down", prev_low - open_
    else:
        return {"gap": None, "asof": asof}
    return {"gap": {"type": kind, "size": round(size, 4),
                    "atr_mult": round(size / atr, 2)}, "asof": asof}


def detect_double_top_bottom(df: pd.DataFrame, order: int = 5,
                             tol_pct: float = 0.03) -> list[dict]:
    """Detect double top / bottom from the two most recent same-side swings.

    Two swing highs within `tol_pct` -> double top (bearish); two swing lows ->
    double bottom (bullish). 3% tolerance follows the StockCharts price filter.
    """
    if df is None or len(df) < 2 * order + 2:
        return []
    sh, sl = extrema.frame_swings(df, order)
    asof = last_closed_date(df)
    out = []
    for swings, pattern, direction in ((sh, "double_top", "bearish"),
                                       (sl, "double_bottom", "bullish")):
        if len(swings) < 2:
            continue
        (_, p1), (_, p2) = swings[-2], swings[-1]
        if p1 > 0 and abs(p1 - p2) / p1 <= tol_pct:
            out.append({"pattern": pattern, "direction": direction,
                        "level": round((p1 + p2) / 2, 4), "at": asof})
    return out
