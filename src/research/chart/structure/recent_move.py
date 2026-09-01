"""Recent swing move quantifier (structure category).

Supplies the facts the rulebook's §E risk patterns need but the point-in-time
indicators cannot express: how big was the PRIOR decline, how big / old /
volume-backed is the CURRENT rebound, and where price sits versus the decline's
fibonacci retracement. These turn dead-cat-bounce (E1), blow-off-top (E4) and
capitulation (E5) from an LLM guess into a data-grounded read.

Look-ahead safe by construction: it anchors to the last CONFIRMED swing high
(the last `order` bars are never confirmable swings, see extrema) and measures
the rebound up to the CURRENT close (the present, never a future bar).
"""
from __future__ import annotations

import pandas as pd

from . import extrema
from ..engine import last_closed_date


def detect_recent_move(df: pd.DataFrame, order: int = 5) -> dict:
    """Quantify the most recent decline leg + the rebound off its low.

    Returns {} fields only when a confirmed swing high with a subsequent low
    exists; otherwise a {"reason": ...} stub (treated as 'no data' downstream).
    """
    if df is None or len(df) < 2 * order + 5:
        return {"reason": "insufficient bars"}

    sh, sl = extrema.find_swings(df["high"].values, df["low"].values, order=order)
    if not sh:
        return {"reason": "no swing high"}

    close = float(df["close"].iloc[-1])
    last_idx = len(df) - 1

    # Last confirmed swing high → the lowest low AFTER it = the decline leg.
    hi_idx, hi = sh[-1]
    lows_after = df["low"].values[hi_idx + 1:]
    if len(lows_after) == 0:
        return {"reason": "swing high is the last bar"}
    lo_rel = int(lows_after.argmin())
    lo_idx = hi_idx + 1 + lo_rel
    lo = float(lows_after[lo_rel])

    decline_pct = round((hi - lo) / hi * 100, 2) if hi else None       # +ve = drop size
    rebound_pct = round((close - lo) / lo * 100, 2) if lo else None    # off-the-low rally
    rebound_bars = last_idx - lo_idx

    # Rebound vs decline average volume. ratio < 1 = rally on weak volume
    # (the classic dead-cat-bounce tell).
    vol = df["volume"].values
    dvol = vol[hi_idx: lo_idx + 1]
    rvol = vol[lo_idx: last_idx + 1]
    vol_ratio = None
    if len(dvol) and len(rvol) and float(dvol.mean()) > 0:
        vol_ratio = round(float(rvol.mean()) / float(dvol.mean()), 2)

    # Where does price sit vs the decline's fib retracement? (recovery check)
    rng = hi - lo
    fib_recovery = {}
    if rng > 0:
        for r in (0.382, 0.5, 0.618):
            lvl = lo + rng * r
            fib_recovery[f"{r:.3f}"] = {"price": round(lvl, 4),
                                        "price_above": close > lvl}

    return {
        "swing_high": round(hi, 4),
        "swing_low": round(lo, 4),
        "close": round(close, 4),
        "decline_from_high_pct": decline_pct,
        "rebound_from_low_pct": rebound_pct,
        "rebound_bars": rebound_bars,
        "rebound_vol_ratio": vol_ratio,     # < 1 = rebound NOT backed by volume
        "fib_recovery": fib_recovery,       # 38.2 / 50 / 61.8 recovered?
        "asof": last_closed_date(df),
    }
