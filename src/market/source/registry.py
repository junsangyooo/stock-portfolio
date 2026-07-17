"""Pick the right Source adapter for a symbol."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from .base import Source
from .pykrx_source import PykrxSource
from .yfinance_source import YFinanceSource
from ...core.paths import split_symbol


@lru_cache(maxsize=4)
def _pykrx() -> PykrxSource:
    return PykrxSource()


@lru_cache(maxsize=4)
def _yfinance() -> YFinanceSource:
    return YFinanceSource()


def for_symbol(symbol_id: str) -> Optional[Source]:
    if ":" not in symbol_id:
        return None
    market, _ = split_symbol(symbol_id)
    if market == "KR":
        return _pykrx()
    return _yfinance()


def yfinance_for(symbol_id: str) -> YFinanceSource:
    """Force-yfinance, used for intraday on KR symbols (pykrx has no
    intraday)."""
    return _yfinance()
