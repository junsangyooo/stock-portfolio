"""KR valuation (L1) derived from DART financials (L2) + OHLCV close.

pykrx's PER/PBR/market_cap endpoints are dead at the KRX source (only OHLCV
survives), and yfinance has no KR fundamental coverage. So for KR we compute
valuation ourselves from the authoritative DART data we already fetch, plus
the OHLCV close series:

    shares     = net_income / EPS        (any period with both; shares are
                                          constant within a fiscal year)
    annual_EPS = latest FY 기본주당이익   (or latest quarterly EPS × 4 estimate)
    BPS        = latest total_equity / shares
    PER(date)  = close(date) / annual_EPS
    PBR(date)  = close(date) / BPS
    mcap(date) = close(date) × shares

Simplification (MVP): a single latest annual_EPS / BPS / shares is applied
across the close series, so PER/PBR move with price (the useful signal);
quarterly-TTM rolling is a future refinement. Pure & pandas-free → testable.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .source.base import ValuationPoint

Num = Optional[float]


def _is_fy(period: str) -> bool:
    return isinstance(period, str) and period.endswith("FY")


def _shares(rows: Sequence[dict]) -> Num:
    """net_income / EPS from the most recent period having both (eps != 0)."""
    for r in reversed(rows):                       # rows sorted oldest→newest
        ni, eps = r.get("net_income"), r.get("eps")
        if ni is not None and eps not in (None, 0):
            return ni / eps
    return None


def _annual_eps(rows: Sequence[dict]) -> Tuple[Num, bool]:
    """Returns (annual_eps, is_estimate). Prefer latest FY EPS; else latest
    quarterly EPS × 4 as an annualized estimate."""
    for r in reversed(rows):
        if _is_fy(r.get("period", "")) and r.get("eps") not in (None, 0):
            return r["eps"], False
    for r in reversed(rows):
        if r.get("eps") not in (None, 0):
            return r["eps"] * 4, True
    return None, False


def _latest_equity(rows: Sequence[dict]) -> Num:
    for r in reversed(rows):
        if r.get("total_equity") is not None:
            return r["total_equity"]
    return None


def build(rows: Sequence[dict],
          closes: Sequence[Tuple[int, float]],
          dps: Num = None) -> List[ValuationPoint]:
    """rows = financials dicts (oldest→newest); closes = [(ts, close), ...];
    dps = latest-FY common-stock cash DPS from the DART dividend disclosure
    (same single-latest-value simplification as annual_eps/BPS)."""
    if not rows or not closes:
        return []
    shares = _shares(rows)
    annual_eps, _est = _annual_eps(rows)
    equity = _latest_equity(rows)
    bps = (equity / shares) if (equity is not None and shares) else None

    points: List[ValuationPoint] = []
    for ts, close in closes:
        if close is None or close <= 0:
            continue
        per = (close / annual_eps) if (annual_eps and annual_eps > 0) else None
        pbr = (close / bps) if (bps and bps > 0) else None
        mcap = (close * shares) if shares else None
        div_yield = (dps / close * 100) if dps else None   # percent (DIV)
        points.append(ValuationPoint(
            ts=int(ts), per=per, pbr=pbr, psr=None,
            eps=annual_eps, bps=bps, dps=dps, div_yield=div_yield,
            market_cap=mcap,
        ))
    return points
