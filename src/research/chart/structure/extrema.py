"""Swing point (local extrema) detection + level clustering.

This is the shared 1st building block for ALL structure analysis: support/
resistance, trendlines, fibonacci anchors, and chart-pattern pivots all run on
top of swing highs/lows.

`order` is the sensitivity dial: a bar is a swing high if it is strictly the
maximum within +/- `order` neighbours. Daily 5-10, weekly 3-5 are practical
starting points. The last `order` bars can never be confirmed as swings (their
right-side neighbours don't exist yet) — this is correct and avoids look-ahead.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from scipy.signal import argrelextrema

Swing = Tuple[int, float]  # (bar_index, price)


def find_swings(
    highs, lows, order: int = 5
) -> Tuple[List[Swing], List[Swing]]:
    """Detect swing highs (on highs) and swing lows (on lows).

    Returns (swing_highs, swing_lows), each a list of (index, price) sorted by
    index. Uses scipy.argrelextrema with the given comparison order.
    """
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    if h.size == 0 or l.size == 0:
        return [], []

    hi_idx = argrelextrema(h, np.greater, order=order)[0]
    lo_idx = argrelextrema(l, np.less, order=order)[0]

    swing_highs = [(int(i), float(h[i])) for i in hi_idx]
    swing_lows = [(int(i), float(l[i])) for i in lo_idx]
    return swing_highs, swing_lows


def frame_swings(df, order: int = 5) -> Tuple[List[Swing], List[Swing]]:
    """`find_swings` on an OHLCV frame's high/low columns."""
    return find_swings(df["high"].values, df["low"].values, order=order)


def cluster_levels(
    swings: List[Swing], tolerance_pct: float = 0.003
) -> List[dict]:
    """Collapse nearby swing prices into single levels.

    Two swings within `tolerance_pct` of the running cluster anchor merge into
    one level. Returns levels sorted ascending by price, each:
        {"price": mean, "tests": count, "last_idx": max index in cluster}
    `tests` (touch count) is the level's strength — more tests = stronger S/R.
    """
    groups: List[List[Swing]] = []
    for idx, price in sorted(swings, key=lambda s: s[1]):
        if groups:
            cur = groups[-1]
            anchor = sum(p for _, p in cur) / len(cur)
            if anchor > 0 and abs(price - anchor) / anchor <= tolerance_pct:
                cur.append((idx, price))
                continue
        groups.append([(idx, price)])
    clusters = [{"price": sum(p for _, p in g) / len(g), "tests": len(g),
                 "last_idx": max(i for i, _ in g)} for g in groups]
    return sorted(clusters, key=lambda c: c["price"])


def nearest_above(price: float, levels: List[dict]) -> Optional[dict]:
    """Nearest level strictly above `price` (nearest resistance), or None."""
    return next((lv for lv in levels if lv["price"] > price), None)


def nearest_below(price: float, levels: List[dict]) -> Optional[dict]:
    """Nearest level strictly below `price` (nearest support), or None."""
    below = [lv for lv in levels if lv["price"] < price]
    return below[-1] if below else None
