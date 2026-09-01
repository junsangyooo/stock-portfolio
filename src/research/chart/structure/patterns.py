"""Candlestick patterns, gaps, and basic chart patterns (category 5).

Detectors return directional pattern events (bullish/bearish) with the bar they
occur on. Candlestick reversal patterns are only meaningful with a prior trend,
so single/multi-candle reversals report direction and the caller (ChartAgent)
combines them with trend context. Chart-pattern geometry beyond double top/
bottom (head&shoulders, triangles, measured-move targets) is a later enhancement.
"""
from __future__ import annotations

import pandas as pd

from . import extrema
from ..engine import last_closed_date

_DOJI_BODY = 0.1      # body <= 10% of range
_LONG_SHADOW = 2.0    # shadow >= 2x body


def _parts(row):
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    rng = h - l
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return o, h, l, c, rng, body, upper, lower


def detect_candles(df: pd.DataFrame) -> list[dict]:
    """Detect candlestick patterns ending at the last bar.

    Returns a list of {pattern, direction, at} for patterns present on the last
    1-3 candles. `direction` is bullish / bearish / indecision.
    """
    if df is None or len(df) < 3:
        return []
    out = []
    last_idx = len(df) - 1
    asof = last_closed_date(df)

    o, h, l, c, rng, body, upper, lower = _parts(df.iloc[-1])
    if rng <= 0:
        return []

    # ── single-candle ──
    if body <= rng * _DOJI_BODY:
        out.append({"pattern": "doji", "direction": "indecision", "at": asof})
    if body > 0 and lower >= _LONG_SHADOW * body and upper <= body:
        out.append({"pattern": "hammer", "direction": "bullish", "at": asof})
    if body > 0 and upper >= _LONG_SHADOW * body and lower <= body:
        out.append({"pattern": "shooting_star", "direction": "bearish", "at": asof})
    if rng > 0 and body >= rng * 0.95:
        out.append({"pattern": "marubozu",
                    "direction": "bullish" if c > o else "bearish", "at": asof})

    # ── two-candle (prev, last) ──
    po, ph, pl, pc, prng, pbody, pu, plo = _parts(df.iloc[-2])
    prev_bear = pc < po
    prev_bull = pc > po
    cur_bull = c > o
    cur_bear = c < o
    # engulfing
    if prev_bear and cur_bull and c >= po and o <= pc:
        out.append({"pattern": "bullish_engulfing", "direction": "bullish", "at": asof})
    if prev_bull and cur_bear and o >= pc and c <= po:
        out.append({"pattern": "bearish_engulfing", "direction": "bearish", "at": asof})
    # harami (prev body engulfs current body)
    if prev_bear and cur_bull and o >= pc and c <= po:
        out.append({"pattern": "bullish_harami", "direction": "bullish", "at": asof})
    if prev_bull and cur_bear and o <= pc and c >= po:
        out.append({"pattern": "bearish_harami", "direction": "bearish", "at": asof})

    # ── three-candle stars ──
    o3, h3, l3, c3, r3, b3, u3, lo3 = _parts(df.iloc[-3])   # oldest
    mid_body = pbody
    mid_small = prng > 0 and mid_body <= prng * 0.4
    if c3 < o3 and mid_small and cur_bull and c > (o3 + c3) / 2:
        out.append({"pattern": "morning_star", "direction": "bullish", "at": asof})
    if c3 > o3 and mid_small and cur_bear and c < (o3 + c3) / 2:
        out.append({"pattern": "evening_star", "direction": "bearish", "at": asof})

    return out


def detect_gap(df: pd.DataFrame, atr_window: int = 14) -> dict:
    """Detect a gap on the last bar (open vs prior high/low), normalised by ATR."""
    if df is None or len(df) < atr_window + 2:
        return {"gap": None, "reason": "insufficient bars"}
    prev = df.iloc[-2]
    last = df.iloc[-1]
    prev_high, prev_low = float(prev["high"]), float(prev["low"])
    open_ = float(last["open"])

    # simple ATR (Wilder-ish via rolling mean of true range)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(atr_window).mean().iloc[-1])
    if atr <= 0:
        return {"gap": None, "reason": "atr zero"}

    if open_ > prev_high:
        size = (open_ - prev_high)
        return {"gap": {"type": "gap_up", "size": round(size, 4),
                        "atr_mult": round(size / atr, 2)}, "asof": last_closed_date(df)}
    if open_ < prev_low:
        size = (prev_low - open_)
        return {"gap": {"type": "gap_down", "size": round(size, 4),
                        "atr_mult": round(size / atr, 2)}, "asof": last_closed_date(df)}
    return {"gap": None, "asof": last_closed_date(df)}


def detect_double_top_bottom(df: pd.DataFrame, order: int = 5,
                             tol_pct: float = 0.03) -> list[dict]:
    """Detect double top / bottom from the two most recent same-side swings.

    Two swing highs within `tol_pct` -> double top (bearish); two swing lows ->
    double bottom (bullish). 3% tolerance follows the StockCharts price filter.
    """
    if df is None or len(df) < 2 * order + 2:
        return []
    sh, sl = extrema.find_swings(df["high"].values, df["low"].values, order=order)
    out = []
    asof = last_closed_date(df)
    if len(sh) >= 2:
        (_, p1), (_, p2) = sh[-2], sh[-1]
        if p1 > 0 and abs(p1 - p2) / p1 <= tol_pct:
            out.append({"pattern": "double_top", "direction": "bearish",
                        "level": round((p1 + p2) / 2, 4), "at": asof})
    if len(sl) >= 2:
        (_, p1), (_, p2) = sl[-2], sl[-1]
        if p1 > 0 and abs(p1 - p2) / p1 <= tol_pct:
            out.append({"pattern": "double_bottom", "direction": "bullish",
                        "level": round((p1 + p2) / 2, 4), "at": asof})
    return out
