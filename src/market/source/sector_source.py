"""Sector/industry tag fetcher (yfinance .info).

One cheap call per symbol, used only to label a held symbol with its GICS-style
sector + industry. Works for US tickers directly and for KR via the universe's
yf_ticker (e.g. '005930.KS'). Network-flaky by nature → always returns a dict
with None fields on failure rather than raising.
"""
from __future__ import annotations

from typing import Optional

import yfinance as yf  # type: ignore

from ...universe import search as uni_search
from ...core.paths import split_symbol


def _yf_ticker(symbol_id: str) -> str:
    market, code = split_symbol(symbol_id)
    item = uni_search.get(symbol_id)
    if item and item.get("yf_ticker"):
        return item["yf_ticker"]
    if market == "US":
        return code
    return f"{code}.KS"


def fetch(symbol_id: str) -> dict:
    """Return {sector, industry} (either may be None). Never raises."""
    ticker = _yf_ticker(symbol_id)
    sector: Optional[str] = None
    industry: Optional[str] = None
    try:
        info = yf.Ticker(ticker).info or {}
        sector = info.get("sector") or None
        industry = info.get("industry") or None
    except Exception:  # noqa: BLE001 — network/parse flakiness is expected
        pass
    return {"sector": sector, "industry": industry}
