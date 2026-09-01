"""Price vs oscillator divergence detection (structure category).

Regular & hidden divergence between price swings and RSI / MACD-histogram — the
single most-cited reversal/continuation signal that the point-in-time indicator
facts (oversold/overbought states) cannot express. Compares the last two
CONFIRMED swing lows (bullish family) / highs (bearish family) in price against
the oscillator's value at those same bars.

  regular bullish : price lower-low   + osc higher-low   -> reversal up
  hidden  bullish : price higher-low  + osc lower-low    -> continuation up
  regular bearish : price higher-high + osc lower-high   -> reversal down
  hidden  bearish : price lower-high  + osc higher-high  -> continuation down

Honest by construction: the last `order` bars are never confirmable as swings
(no right-side neighbours -> avoids look-ahead), and a clear price + oscillator
difference is required, so quiet/short series return []. Divergence is a
SIGNAL, not a certainty — the rulebook (§B9/§E8) tells the judge to treat it so.
"""
from __future__ import annotations

import math

import pandas as pd

from . import extrema
from ..engine import last_closed_date
from ..indicators import _ta

_PRICE_EPS = 0.004   # min relative price difference between the two swings (0.4%)


def _osc_at(series: pd.Series, idx: int):
    """Oscillator value at a positional bar index, or None (NaN/out-of-range)."""
    try:
        v = float(series.iloc[idx])
    except (TypeError, ValueError, IndexError):
        return None
    return None if math.isnan(v) else v


def _classify(p1: float, p2: float, o1: float, o2: float,
              side: str, osc_eps: float):
    """side='low' (bullish family) | 'high' (bearish family).

    p1/o1 = older swing, p2/o2 = newer swing. Returns a divergence type string
    or None when the price/oscillator moves are too small or not divergent.
    """
    if p1 <= 0:
        return None
    price_diff = (p2 - p1) / p1
    if abs(price_diff) < _PRICE_EPS or abs(o2 - o1) < osc_eps:
        return None
    if side == "low":
        if price_diff < 0 and o2 > o1:
            return "regular_bullish"      # price LL, osc HL
        if price_diff > 0 and o2 < o1:
            return "hidden_bullish"       # price HL, osc LL
    else:  # high
        if price_diff > 0 and o2 < o1:
            return "regular_bearish"      # price HH, osc LH
        if price_diff < 0 and o2 > o1:
            return "hidden_bearish"       # price LH, osc HH
    return None


def _scan(df: pd.DataFrame, name: str, osc: pd.Series, order: int,
          last_idx: int, asof) -> list[dict]:
    """Scan the last two swing lows/highs for `osc` divergence vs price."""
    sh, sl = extrema.find_swings(df["high"].values, df["low"].values, order=order)
    # Dynamic threshold so RSI (0-100) and the tiny MACD histogram both work:
    # 2% of the oscillator's recent typical magnitude.
    scale = float(osc.tail(60).abs().mean() or 0.0)
    osc_eps = max(scale * 0.02, 1e-9)
    found: list[dict] = []

    for swings, side, direction in ((sl, "low", "bullish"), (sh, "high", "bearish")):
        if len(swings) < 2:
            continue
        (i1, p1), (i2, p2) = swings[-2], swings[-1]
        o1, o2 = _osc_at(osc, i1), _osc_at(osc, i2)
        if o1 is None or o2 is None:
            continue
        kind = _classify(p1, p2, o1, o2, side, osc_eps)
        if kind:
            found.append({
                "indicator": name,
                "type": kind,
                "direction": direction,
                "price_points": [round(p1, 4), round(p2, 4)],
                "osc_points": [round(o1, 2), round(o2, 2)],
                "bars_ago": last_idx - i2,   # recency of the newer swing
                "asof": asof,
            })
    return found


def detect_divergence(df: pd.DataFrame, order: int = 5) -> list[dict]:
    """Detect price vs RSI / MACD-hist divergence on the most recent swings.

    Needs enough bars for both swing confirmation and MACD warm-up. Returns a
    (possibly empty) list of divergence events, one per oscillator at most.
    """
    if df is None or len(df) < 2 * order + 35:
        return []
    asof = last_closed_date(df)
    last_idx = len(df) - 1
    rsi = _ta.rsi(df["close"], 14)
    _, _, hist = _ta.macd(df["close"], 12, 26, 9)
    out: list[dict] = []
    out += _scan(df, "rsi", rsi, order, last_idx, asof)
    out += _scan(df, "macd", hist, order, last_idx, asof)
    return out
