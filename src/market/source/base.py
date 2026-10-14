"""Common Source interface — all market-data providers conform to this.

symbol_id convention (new):
  'KR:005930'   — Korean equity (code as 6-digit string)
  'US:AAPL'     — US equity

Resolution to actual exchange + yfinance ticker is done via universe.search.get().
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class OHLCVBar:
    ts: int               # unix seconds at bar OPEN
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Quote:
    symbol_id: str
    price: float
    prev_close: float
    ts: int


@dataclass
class ValuationPoint:
    """L1 valuation — price-linked, one row per date."""
    ts: int                              # unix seconds at date
    per: Optional[float] = None
    pbr: Optional[float] = None
    psr: Optional[float] = None
    eps: Optional[float] = None
    bps: Optional[float] = None
    dps: Optional[float] = None
    div_yield: Optional[float] = None    # percent
    market_cap: Optional[float] = None


@dataclass
class FinancialRow:
    """L2 financial statement — one row per reporting period."""
    period: str                          # "2024Q3" | "2024FY"
    fs_div: str = "CFS"                  # CFS=consolidated, OFS=separate
    report_date: str = ""               # ISO date (period end / filing)
    source: str = ""                    # dart | yfinance | sec
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    eps: Optional[float] = None           # basic EPS as reported (기본주당이익)
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    total_equity: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    operating_cf: Optional[float] = None
    investing_cf: Optional[float] = None
    financing_cf: Optional[float] = None
    filed_at: Optional[str] = None        # ISO date the filing was received


class Source(ABC):
    name: str = "base"

    @abstractmethod
    def get_daily(self, symbol_id: str, start: str, end: str) -> List[OHLCVBar]: ...

    @abstractmethod
    def get_quote(self, symbol_id: str) -> Optional[Quote]: ...

    def get_intraday(self, symbol_id: str, interval: str = "5m",
                     period: str = "5d") -> List[OHLCVBar]:
        """Optional. Default = no intraday support."""
        return []

    def get_valuation(self, symbol_id: str, start: str,
                      end: str) -> List[ValuationPoint]:
        """Optional. L1 daily valuation series. Default = unsupported."""
        return []

    def get_financials(self, symbol_id: str, years: int = 4) -> List[FinancialRow]:
        """Optional. L2 financial statements. Default = unsupported."""
        return []
