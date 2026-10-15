"""Sector/industry tag fetcher (yfinance .info).

One cheap call per symbol, used only to label a held symbol with its GICS-style
sector + industry. Works for US tickers directly and for KR via the universe's
yf_ticker (e.g. '005930.KS').

A failed call raises SourceError (ops notes §11 소스 실패): returning None
fields made a dead source look like a symbol without a sector, and the job
then wrote that over the stored tag. None fields mean yfinance answered and
has no tag (an ETF, a fresh listing).
"""
from __future__ import annotations

import yfinance as yf  # type: ignore

from .base import SourceError
from .yfinance_source import _yf_ticker


def fetch(symbol_id: str) -> dict:
    """Return {sector, industry} (either may be None when yfinance has no
    tag). Raises SourceError when the call itself fails."""
    ticker = _yf_ticker(symbol_id)
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:  # noqa: BLE001 — network/parse: name it, do not hide it
        raise SourceError(f"yfinance info {ticker}: {str(e)[:120]}") from e
    return {"sector": info.get("sector") or None,
            "industry": info.get("industry") or None}
