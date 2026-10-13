"""Sector/industry tag fetcher (yfinance .info).

One cheap call per symbol, used only to label a held symbol with its GICS-style
sector + industry. Works for US tickers directly and for KR via the universe's
yf_ticker (e.g. '005930.KS'). Network-flaky by nature → always returns a dict
with None fields on failure rather than raising.
"""
from __future__ import annotations

import yfinance as yf  # type: ignore

from .yfinance_source import _yf_ticker


def fetch(symbol_id: str) -> dict:
    """Return {sector, industry} (either may be None). Never raises."""
    ticker = _yf_ticker(symbol_id)
    try:
        info = yf.Ticker(ticker).info or {}
        return {"sector": info.get("sector") or None,
                "industry": info.get("industry") or None}
    except Exception:  # noqa: BLE001 — network/parse flakiness is expected
        return {"sector": None, "industry": None}
