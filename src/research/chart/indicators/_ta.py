"""Pure numpy/pandas technical-indicator math — NO external TA library.

Replaces pandas-ta (which can't install on Oracle: 0.4.x needs py>=3.12, 0.3.x
breaks on numpy>=2). Everything here is deterministic and dependency-free.

Implementation traps handled (see chart-analysis-design.md):
  - RSI/MFI use Wilder smoothing (alpha = 1/n), NOT a normal EMA.
  - ATR/ADX use Wilder (RMA) smoothing.
  - CCI uses mean ABSOLUTE deviation, not standard deviation.
  - Bollinger uses population std (ddof=0).
  - EMA uses adjust=False (recursive form), matching standard charts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── moving averages ─────────────────────────────────────────────────────────
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (a.k.a. RMA): alpha = 1/n."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


# ── trend ────────────────────────────────────────────────────────────────────
def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high, low, close, n: int = 14) -> pd.Series:
    return _rma(true_range(high, low, close), n)


def adx(high, low, close, n: int = 14):
    """Return (adx, plus_di, minus_di), all Wilder-smoothed."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    tr_n = _rma(true_range(high, low, close), n)
    plus_di = 100 * _rma(plus_dm, n) / tr_n
    minus_di = 100 * _rma(minus_dm, n) / tr_n
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_line = _rma(dx.fillna(0), n)
    return adx_line, plus_di, minus_di


def supertrend(high, low, close, n: int = 10, multiplier: float = 3.0) -> pd.Series:
    """Return a direction Series: +1 uptrend, -1 downtrend."""
    hl2 = (high + low) / 2.0
    atr_n = atr(high, low, close, n)
    upper = hl2 + multiplier * atr_n
    lower = hl2 - multiplier * atr_n

    c = close.to_numpy()
    ub = upper.to_numpy()
    lb = lower.to_numpy()
    direction = np.ones(len(c))           # default up
    f_up = ub.copy()
    f_lo = lb.copy()
    for i in range(1, len(c)):
        # final upper band
        if ub[i] < f_up[i - 1] or c[i - 1] > f_up[i - 1]:
            f_up[i] = ub[i]
        else:
            f_up[i] = f_up[i - 1]
        # final lower band
        if lb[i] > f_lo[i - 1] or c[i - 1] < f_lo[i - 1]:
            f_lo[i] = lb[i]
        else:
            f_lo[i] = f_lo[i - 1]
        # direction
        if c[i] > f_up[i - 1]:
            direction[i] = 1
        elif c[i] < f_lo[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    return pd.Series(direction, index=close.index)


def ichimoku(high, low, close):
    """Return (span_a, span_b) Series (leading spans, shifted +26)."""
    def mid(n):
        return (high.rolling(n).max() + low.rolling(n).min()) / 2.0
    tenkan = mid(9)
    kijun = mid(26)
    span_a = ((tenkan + kijun) / 2.0).shift(26)
    span_b = mid(52).shift(26)
    return span_a, span_b


# ── momentum ──────────────────────────────────────────────────────────────────
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _rma(gain, n)              # Wilder smoothing
    avg_loss = _rma(loss, n)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def stoch(high, low, close, k: int = 14, d: int = 3, smooth_k: int = 3):
    """Return (%K, %D) for the slow/full stochastic."""
    ll = low.rolling(k).min()
    hh = high.rolling(k).max()
    fast_k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    pk = fast_k.rolling(smooth_k).mean()   # full %K
    pd_ = pk.rolling(d).mean()             # %D
    return pk, pd_


def cci(high, low, close, n: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    sma_tp = tp.rolling(n).mean()
    mad = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


def williams_r(high, low, close, n: int = 14) -> pd.Series:
    hh = high.rolling(n).max()
    ll = low.rolling(n).min()
    return -100 * (hh - close) / (hh - ll).replace(0, np.nan)


def mfi(high, low, close, volume, n: int = 14) -> pd.Series:
    tp = (high + low + close) / 3.0
    rmf = tp * volume
    pos = rmf.where(tp > tp.shift(1), 0.0)
    neg = rmf.where(tp < tp.shift(1), 0.0)
    pos_sum = pos.rolling(n).sum()
    neg_sum = neg.rolling(n).sum()
    ratio = pos_sum / neg_sum.replace(0, np.nan)
    return (100 - 100 / (1 + ratio)).fillna(100)


# ── volatility ────────────────────────────────────────────────────────────────
def bbands(close: pd.Series, n: int = 20, std: float = 2.0):
    """Return (lower, mid, upper, pctB, bandwidth). Population std (ddof=0)."""
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    upper = mid + std * sd
    lower = mid - std * sd
    width = (upper - lower)
    pctb = (close - lower) / width.replace(0, np.nan)
    bandwidth = width / mid.replace(0, np.nan) * 100
    return lower, mid, upper, pctb, bandwidth


def squeeze(high, low, close, bb_n: int = 20, bb_std: float = 2.0,
            kc_n: int = 20, kc_mult: float = 1.5):
    """TTM Squeeze. Return (momentum, squeeze_on) — squeeze_on is a bool Series.

    squeeze_on = BB entirely inside KC (low volatility, compression).
    momentum = linear-regression value of (close - midline) over kc_n bars.
    """
    lower, mid, upper, _, _ = bbands(close, bb_n, bb_std)
    atr_n = atr(high, low, close, kc_n)
    kc_mid = ema(close, kc_n)
    kc_upper = kc_mid + kc_mult * atr_n
    kc_lower = kc_mid - kc_mult * atr_n
    squeeze_on = (lower > kc_lower) & (upper < kc_upper)

    # momentum: linreg of (close - midline) where midline = avg(donchian_mid, sma)
    don_mid = (high.rolling(kc_n).max() + low.rolling(kc_n).min()) / 2.0
    midline = (don_mid + close.rolling(kc_n).mean()) / 2.0
    delta = close - midline
    mom = _linreg_last(delta, kc_n)
    return mom, squeeze_on


def _linreg_last(s: pd.Series, n: int) -> pd.Series:
    """Rolling linear-regression endpoint value (slope*x_last + intercept)."""
    x = np.arange(n)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    def _fit(y):
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / denom
        intercept = y_mean - slope * x_mean
        return slope * (n - 1) + intercept

    return s.rolling(n).apply(_fit, raw=True)


def donchian(high, low, n: int = 20):
    """Return (lower, mid, upper)."""
    upper = high.rolling(n).max()
    lower = low.rolling(n).min()
    return lower, (upper + lower) / 2.0, upper


# ── volume ────────────────────────────────────────────────────────────────────
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def cmf(high, low, close, volume, n: int = 21) -> pd.Series:
    rng = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / rng
    mfv = mfm.fillna(0) * volume
    return mfv.rolling(n).sum() / volume.rolling(n).sum().replace(0, np.nan)
