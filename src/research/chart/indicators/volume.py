"""Volume (category 4) indicator FACTS: OBV trend, CMF, + volume_spike helper.
Objective states only — no buy/sell judgment.
"""
from __future__ import annotations

from ..engine import last_closed_date
from ..schema import IndicatorFact
from . import _ta
from ._common import enough, last_float, na_fact

_CAT = "volume"


def obv_trend(df, tf) -> IndicatorFact:
    """OBV vs its 20-EMA. state: rising|falling."""
    if not enough(df, 25):
        return na_fact("obv_trend", _CAT, tf, df, "need>=25 bars")
    obv = _ta.obv(df["close"], df["volume"])
    cur, base = last_float(obv), last_float(_ta.ema(obv, 20))
    if None in (cur, base):
        return na_fact("obv_trend", _CAT, tf, df, "obv nan")
    return IndicatorFact("obv_trend", _CAT, tf, "rising" if cur > base else "falling",
                         value=round(cur, 2),
                         evidence={"obv": round(cur, 2), "obv_ema20": round(base, 2)},
                         asof=last_closed_date(df))


def cmf(df, tf) -> IndicatorFact:
    """Chaikin Money Flow(21). state: accumulation(>0.05)|distribution(<-0.05)|neutral.
    Note: ignores gaps (design doc category 4)."""
    if not enough(df, 22):
        return na_fact("cmf", _CAT, tf, df, "need>=22 bars")
    cur = last_float(_ta.cmf(df["high"], df["low"], df["close"], df["volume"], 21))
    if cur is None:
        return na_fact("cmf", _CAT, tf, df, "cmf nan")
    state = "accumulation" if cur > 0.05 else "distribution" if cur < -0.05 else "neutral"
    return IndicatorFact("cmf", _CAT, tf, state, value=round(cur, 4),
                         evidence={"cmf": round(cur, 4)}, asof=last_closed_date(df))


def volume_spike(df, tf, lookback: int = 20, mult: float = 1.5) -> dict:
    """Breakout-confirmation FACT (not in INDICATORS): whether the last bar's
    volume exceeds `mult` x its `lookback`-bar average."""
    if not enough(df, lookback + 1):
        return {"spike": False, "reason": "insufficient bars"}
    v = df["volume"]
    avg = float(v.iloc[-lookback - 1: -1].mean())
    last = float(v.iloc[-1])
    ratio = (last / avg) if avg > 0 else 0.0
    return {"spike": ratio >= mult, "ratio": round(ratio, 2),
            "last": last, "avg": round(avg, 2), "mult": mult}


INDICATORS = [obv_trend, cmf]
