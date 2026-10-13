"""Pick the right Source adapter for a symbol."""
from __future__ import annotations

from typing import Optional

from .base import Source
from .pykrx_source import PykrxSource
from .yfinance_source import YFinanceSource
from ...core.paths import split_symbol

_pykrx = PykrxSource()
_yfinance = YFinanceSource()


def for_symbol(symbol_id: str) -> Optional[Source]:
    if ":" not in symbol_id:
        return None
    return _pykrx if split_symbol(symbol_id)[0] == "KR" else _yfinance


def yfinance_for(symbol_id: str) -> YFinanceSource:
    """Force-yfinance, used for intraday on KR symbols (pykrx has no
    intraday). Takes the symbol only to read like its sibling above."""
    return _yfinance
