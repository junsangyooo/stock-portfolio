"""Volatility (category 3) indicator FACTS: Bollinger %B, TTM Squeeze, Donchian.
Objective states only — no buy/sell judgment.
"""
from __future__ import annotations

from ..engine import last_closed_date
from ..schema import IndicatorFact
from . import _ta
from ._common import enough, last_float, na_fact

_CAT = "volatility"


def bbands_pctb(df, tf) -> IndicatorFact:
    """Bollinger %B (20,2). state: above_upper(>1)|below_lower(<0)|inside."""
    if not enough(df, 20):
        return na_fact("bbands_pctb", _CAT, tf, df, "need>=20 bars")
    _, _, _, pctb_s, bw_s = _ta.bbands(df["close"], 20, 2.0)
    pctb, bw = last_float(pctb_s), last_float(bw_s)
    if pctb is None:
        return na_fact("bbands_pctb", _CAT, tf, df, "bbands nan")
    state = "above_upper" if pctb > 1 else "below_lower" if pctb < 0 else "inside"
    return IndicatorFact("bbands_pctb", _CAT, tf, state, value=round(pctb, 4),
                         evidence={"pctB": round(pctb, 4),
                                   "bandwidth": round(bw, 4) if bw is not None else None},
                         asof=last_closed_date(df))


def ttm_squeeze(df, tf) -> IndicatorFact:
    """TTM Squeeze. state: squeeze_on|fired_up|fired_down (momentum direction)."""
    if not enough(df, 30):
        return na_fact("ttm_squeeze", _CAT, tf, df, "need>=30 bars")
    mom_s, sq_s = _ta.squeeze(df["high"], df["low"], df["close"], 20, 2.0, 20, 1.5)
    mom = last_float(mom_s)
    if mom is None:
        return na_fact("ttm_squeeze", _CAT, tf, df, "squeeze nan")
    squeezed = bool(sq_s.iloc[-1]) if len(sq_s) else False
    if squeezed:
        state = "squeeze_on"
    else:
        state = "fired_up" if mom > 0 else "fired_down"
    return IndicatorFact("ttm_squeeze", _CAT, tf, state, value=round(mom, 4),
                         evidence={"momentum": round(mom, 4), "squeeze_on": squeezed},
                         asof=last_closed_date(df))


def donchian_breakout(df, tf) -> IndicatorFact:
    """Donchian(20). state: breakout_up|breakout_down|inside."""
    if not enough(df, 20):
        return na_fact("donchian", _CAT, tf, df, "need>=20 bars")
    lower_s, _, upper_s = _ta.donchian(df["high"], df["low"], 20)
    upper, lower = last_float(upper_s), last_float(lower_s)
    price = last_float(df["close"])
    if None in (upper, lower, price):
        return na_fact("donchian", _CAT, tf, df, "donchian nan")
    state = "breakout_up" if price >= upper else "breakout_down" if price <= lower else "inside"
    return IndicatorFact("donchian", _CAT, tf, state, value=round(price, 4),
                         evidence={"price": round(price, 4), "upper": round(upper, 4),
                                   "lower": round(lower, 4)},
                         asof=last_closed_date(df))


INDICATORS = [bbands_pctb, ttm_squeeze, donchian_breakout]
