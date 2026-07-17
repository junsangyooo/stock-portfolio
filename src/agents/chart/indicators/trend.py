"""Trend (category 1) indicator FACTS: EMA alignment, MACD, ADX/DMI, Supertrend,
Ichimoku. Each reports an objective state — no buy/sell judgment.
"""
from __future__ import annotations

from ..engine import last_closed_date
from ..schema import IndicatorFact
from . import _ta
from ._common import enough, last_float, na_fact, prev_float

_CAT = "trend"


def ema_alignment(df, tf) -> IndicatorFact:
    """EMA 20/50/200 stack + price position. state: bull_stack|bear_stack|mixed."""
    if not enough(df, 200):
        return na_fact("ema_alignment", _CAT, tf, df, "need>=200 bars")
    c = df["close"]
    e20, e50, e200 = (last_float(_ta.ema(c, n)) for n in (20, 50, 200))
    price = last_float(c)
    if None in (e20, e50, e200, price):
        return na_fact("ema_alignment", _CAT, tf, df, "nan ema")
    if e20 > e50 > e200:
        state = "bull_stack"
    elif e20 < e50 < e200:
        state = "bear_stack"
    else:
        state = "mixed"
    return IndicatorFact("ema_alignment", _CAT, tf, state,
                         value=round((price - e20) / e20 * 100, 3),
                         evidence={"ema20": round(e20, 4), "ema50": round(e50, 4),
                                   "ema200": round(e200, 4), "price": round(price, 4),
                                   "price_above_ema20": price > e20,
                                   "dist_to_ema20_pct": round((price - e20) / e20 * 100, 3)},
                         asof=last_closed_date(df))


def macd_cross(df, tf) -> IndicatorFact:
    """MACD (12/26/9). state: golden_cross|dead_cross (last bar) else
    above_signal|below_signal; plus zero-line position in evidence."""
    if not enough(df, 35):
        return na_fact("macd", _CAT, tf, df, "need>=35 bars")
    line, sig, hist = _ta.macd(df["close"], 12, 26, 9)
    macd_v, signal_v, hist_v = last_float(line), last_float(sig), last_float(hist)
    pmacd, psignal = prev_float(line), prev_float(sig)
    if None in (macd_v, signal_v):
        return na_fact("macd", _CAT, tf, df, "macd nan")
    crossed_up = pmacd is not None and psignal is not None and pmacd <= psignal and macd_v > signal_v
    crossed_dn = pmacd is not None and psignal is not None and pmacd >= psignal and macd_v < signal_v
    if crossed_up:
        state = "golden_cross"
    elif crossed_dn:
        state = "dead_cross"
    elif macd_v > signal_v:
        state = "above_signal"
    else:
        state = "below_signal"
    return IndicatorFact("macd", _CAT, tf, state, value=round(macd_v, 4),
                         evidence={"macd": round(macd_v, 4), "signal": round(signal_v, 4),
                                   "hist": round(hist_v, 4) if hist_v is not None else None,
                                   "above_zero": macd_v > 0},
                         asof=last_closed_date(df))


def adx_dmi(df, tf) -> IndicatorFact:
    """ADX/DMI(14). state: strong_trend|weak_trend|no_trend; di_direction in evidence."""
    if not enough(df, 30):
        return na_fact("adx_dmi", _CAT, tf, df, "need>=30 bars")
    adx_s, plus_s, minus_s = _ta.adx(df["high"], df["low"], df["close"], 14)
    adx, dmp, dmn = last_float(adx_s), last_float(plus_s), last_float(minus_s)
    if None in (adx, dmp, dmn):
        return na_fact("adx_dmi", _CAT, tf, df, "adx nan")
    if adx >= 25:
        state = "strong_trend"
    elif adx < 20:
        state = "no_trend"
    else:
        state = "weak_trend"
    return IndicatorFact("adx_dmi", _CAT, tf, state, value=round(adx, 2),
                         evidence={"adx": round(adx, 2), "+di": round(dmp, 2),
                                   "-di": round(dmn, 2),
                                   "di_direction": "up" if dmp > dmn else "down"},
                         asof=last_closed_date(df))


def supertrend(df, tf) -> IndicatorFact:
    """Supertrend (ATR 10, mult 3). state: up|down."""
    if not enough(df, 20):
        return na_fact("supertrend", _CAT, tf, df, "need>=20 bars")
    d = last_float(_ta.supertrend(df["high"], df["low"], df["close"], 10, 3.0))
    if d is None:
        return na_fact("supertrend", _CAT, tf, df, "supertrend nan")
    return IndicatorFact("supertrend", _CAT, tf, "up" if d > 0 else "down",
                         evidence={"direction": "up" if d > 0 else "down"},
                         asof=last_closed_date(df))


def ichimoku(df, tf) -> IndicatorFact:
    """Ichimoku (9/26/52). state: above_cloud|below_cloud|in_cloud."""
    if not enough(df, 60):
        return na_fact("ichimoku", _CAT, tf, df, "need>=60 bars")
    span_a, span_b = _ta.ichimoku(df["high"], df["low"], df["close"])
    sa, sb = last_float(span_a), last_float(span_b)
    price = last_float(df["close"])
    if None in (sa, sb, price):
        return na_fact("ichimoku", _CAT, tf, df, "span nan")
    top, bot = max(sa, sb), min(sa, sb)
    state = "above_cloud" if price > top else "below_cloud" if price < bot else "in_cloud"
    return IndicatorFact("ichimoku", _CAT, tf, state, value=round(price, 4),
                         evidence={"price": round(price, 4), "cloud_top": round(top, 4),
                                   "cloud_bottom": round(bot, 4)},
                         asof=last_closed_date(df))


INDICATORS = [ema_alignment, macd_cross, adx_dmi, supertrend, ichimoku]
