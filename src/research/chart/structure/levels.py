"""Support/Resistance, Fibonacci, Pivot Points (category 5 — structure).

These detect price LEVELS (not directional votes). The ChartAgent (C4) consumes
them as context: nearest support/resistance, fib retracement zone, pivot grid,
and recent breakouts (confirmed by volume_spike). All build on the swing
extrema engine.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from . import extrema
from ..engine import last_closed_date

# Standard fib retracement ratios (50% is Dow, not fib, but conventionally used).
FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def support_resistance(df: pd.DataFrame, order: int = 5,
                       tol_pct: float = 0.005) -> dict:
    """Detect S/R levels from swing highs/lows, clustered by proximity.

    Returns nearest support/resistance to the last close, all levels, and any
    recent break (close beyond a level). Levels with more `tests` are stronger.
    """
    if df is None or len(df) < 2 * order + 2:
        return {"levels": [], "nearest_support": None, "nearest_resistance": None,
                "recent_break": None, "reason": "insufficient bars"}

    sh, sl = extrema.find_swings(df["high"].values, df["low"].values, order=order)
    res_levels = extrema.cluster_levels(sh, tolerance_pct=tol_pct)
    sup_levels = extrema.cluster_levels(sl, tolerance_pct=tol_pct)
    all_levels = extrema.cluster_levels(sh + sl, tolerance_pct=tol_pct)

    price = float(df["close"].iloc[-1])
    nearest_r = extrema.nearest_above(price, res_levels)
    nearest_s = extrema.nearest_below(price, sup_levels)

    # recent break: did the last close cross a level the prior close hadn't?
    recent_break = None
    if len(df) >= 2:
        prev = float(df["close"].iloc[-2])
        for lv in all_levels:
            p = lv["price"]
            if prev <= p < price:
                recent_break = {"type": "break_up", "level": round(p, 4), "tests": lv["tests"]}
                break
            if prev >= p > price:
                recent_break = {"type": "break_down", "level": round(p, 4), "tests": lv["tests"]}
                break

    def _fmt(lv: Optional[dict]) -> Optional[dict]:
        if lv is None:
            return None
        return {"price": round(lv["price"], 4), "tests": lv["tests"],
                "distance_pct": round((lv["price"] - price) / price * 100, 3)}

    return {
        "levels": [{"price": round(l["price"], 4), "tests": l["tests"]} for l in all_levels],
        "nearest_support": _fmt(nearest_s),
        "nearest_resistance": _fmt(nearest_r),
        "recent_break": recent_break,
        "price": round(price, 4),
        "asof": last_closed_date(df),
    }


def fibonacci(df: pd.DataFrame, order: int = 5) -> dict:
    """Fibonacci retracement of the most recent significant swing leg.

    Picks the last swing high and last swing low; the leg direction (up if the
    low precedes the high) determines whether levels are pullback supports
    (uptrend) or rebound resistances (downtrend).
    """
    if df is None or len(df) < 2 * order + 2:
        return {"levels": {}, "reason": "insufficient bars"}
    sh, sl = extrema.find_swings(df["high"].values, df["low"].values, order=order)
    if not sh or not sl:
        return {"levels": {}, "reason": "no swings"}

    hi_idx, hi = sh[-1]
    lo_idx, lo = sl[-1]
    rng = hi - lo
    if rng <= 0:
        return {"levels": {}, "reason": "degenerate range"}

    uptrend = lo_idx < hi_idx   # low came first -> recent leg is up
    levels = {}
    for r in FIB_RATIOS:
        # uptrend: retrace down from high; downtrend: retrace up from low
        lvl = hi - rng * r if uptrend else lo + rng * r
        levels[f"{r:.3f}"] = round(lvl, 4)

    return {
        "direction": "up" if uptrend else "down",
        "swing_high": round(hi, 4),
        "swing_low": round(lo, 4),
        "levels": levels,
        "asof": last_closed_date(df),
    }


def pivot_points(df: pd.DataFrame, kind: str = "classic") -> dict:
    """Pivot points from the LAST closed bar's HLC. Deterministic (no detection).

    kind: classic | fibonacci | camarilla | woodie.
    """
    if df is None or df.empty:
        return {"reason": "no data"}
    last = df.iloc[-1]
    h, l, c = float(last["high"]), float(last["low"]), float(last["close"])
    rng = h - l

    if kind == "classic":
        pp = (h + l + c) / 3
        out = {"PP": pp, "R1": 2 * pp - l, "S1": 2 * pp - h,
               "R2": pp + rng, "S2": pp - rng,
               "R3": h + 2 * (pp - l), "S3": l - 2 * (h - pp)}
    elif kind == "fibonacci":
        pp = (h + l + c) / 3
        out = {"PP": pp, "R1": pp + 0.382 * rng, "S1": pp - 0.382 * rng,
               "R2": pp + 0.618 * rng, "S2": pp - 0.618 * rng,
               "R3": pp + rng, "S3": pp - rng}
    elif kind == "camarilla":
        out = {"PP": (h + l + c) / 3,
               "R1": c + rng * 1.0833, "S1": c - rng * 1.0833,
               "R2": c + rng * 1.1666, "S2": c - rng * 1.1666,
               "R3": c + rng * 1.25, "S3": c - rng * 1.25,
               "R4": c + rng * 1.5, "S4": c - rng * 1.5}
    elif kind == "woodie":
        pp = (h + l + 2 * c) / 4
        out = {"PP": pp, "R1": 2 * pp - l, "S1": 2 * pp - h,
               "R2": pp + rng, "S2": pp - rng}
    else:
        return {"reason": f"unknown kind {kind!r}"}

    return {"kind": kind, **{k: round(v, 4) for k, v in out.items()},
            "asof": last_closed_date(df)}
