"""Momentum (category 2) indicator FACTS: RSI, Stochastic, CCI, Williams %R, MFI.
Objective states only (oversold / overbought / neutral) — no buy/sell judgment.
"""
from __future__ import annotations

from ..engine import last_closed_date
from ..schema import IndicatorFact
from . import _ta
from ._common import enough, last_float, na_fact, prev_float

_CAT = "momentum"


def _osc_state(cur: float, low: float, high: float) -> str:
    if cur < low:
        return "oversold"
    if cur > high:
        return "overbought"
    return "neutral"


def _osc_fact(name: str, key: str, df, tf, series, low: float, high: float,
              nan_reason: str) -> IndicatorFact:
    """A single-line oscillator: last value, previous value, rising, state."""
    cur, prv = last_float(series), prev_float(series)
    if cur is None:
        return na_fact(name, _CAT, tf, df, nan_reason)
    return IndicatorFact(name, _CAT, tf, _osc_state(cur, low, high), value=round(cur, 2),
                         evidence={key: round(cur, 2), "prev": round(prv, 2) if prv else None,
                                   "rising": (prv is not None and cur > prv)},
                         asof=last_closed_date(df))


def rsi(df, tf) -> IndicatorFact:
    """RSI(14). state: oversold(<30)|overbought(>70)|neutral."""
    if not enough(df, 15):
        return na_fact("rsi", _CAT, tf, df, "need>=15 bars")
    return _osc_fact("rsi", "rsi", df, tf, _ta.rsi(df["close"], 14), 30, 70, "rsi nan")


def stochastic(df, tf) -> IndicatorFact:
    """Stochastic (14,3,3). state: oversold(<20)|overbought(>80)|neutral; %K vs %D."""
    if not enough(df, 20):
        return na_fact("stochastic", _CAT, tf, df, "need>=20 bars")
    pk, pd_ = _ta.stoch(df["high"], df["low"], df["close"], 14, 3, 3)
    k, d = last_float(pk), last_float(pd_)
    if None in (k, d):
        return na_fact("stochastic", _CAT, tf, df, "stoch nan")
    return IndicatorFact("stochastic", _CAT, tf, _osc_state(k, 20, 80), value=round(k, 2),
                         evidence={"%K": round(k, 2), "%D": round(d, 2), "k_above_d": k > d},
                         asof=last_closed_date(df))


def cci(df, tf) -> IndicatorFact:
    """CCI(20). state: oversold(<-100)|overbought(>100)|neutral."""
    if not enough(df, 25):
        return na_fact("cci", _CAT, tf, df, "need>=25 bars")
    return _osc_fact("cci", "cci", df, tf,
                     _ta.cci(df["high"], df["low"], df["close"], 20), -100, 100, "cci nan")


def williams_r(df, tf) -> IndicatorFact:
    """Williams %R(14). state: oversold(<-80)|overbought(>-20)|neutral."""
    if not enough(df, 15):
        return na_fact("williams_r", _CAT, tf, df, "need>=15 bars")
    return _osc_fact("williams_r", "willr", df, tf,
                     _ta.williams_r(df["high"], df["low"], df["close"], 14),
                     -80, -20, "willr nan")


def mfi(df, tf) -> IndicatorFact:
    """Money Flow Index(14, volume-weighted). state: oversold(<20)|overbought(>80)|neutral."""
    if not enough(df, 15):
        return na_fact("mfi", _CAT, tf, df, "need>=15 bars")
    return _osc_fact("mfi", "mfi", df, tf,
                     _ta.mfi(df["high"], df["low"], df["close"], df["volume"], 14),
                     20, 80, "mfi nan")


INDICATORS = [rsi, stochastic, cci, williams_r, mfi]
