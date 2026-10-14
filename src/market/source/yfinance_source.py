"""US + KR-via-yfinance fallback adapter.

For 'KR:005930' we look up yf_ticker from the universe (e.g. '005930.KS').
For 'US:AAPL' we just pass code directly.
"""
from __future__ import annotations

import time
from typing import List, Optional

import yfinance as yf  # type: ignore

from .base import (FinancialRow, OHLCVBar, Quote, Source, SourceError,
                   ValuationPoint)
from ...core.paths import split_symbol
from ...universe import search as uni_search


# yfinance statement line-item label → standardized field. First hit wins.
_FIN_ALIASES = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported"],
    "net_income": ["Net Income", "Net Income Common Stockholders"],
    "total_assets": ["Total Assets"],
    "total_liabilities": ["Total Liabilities Net Minority Interest",
                          "Total Liabilities"],
    "total_equity": ["Stockholders Equity",
                     "Total Equity Gross Minority Interest"],
    "current_assets": ["Current Assets", "Total Current Assets"],
    "current_liabilities": ["Current Liabilities", "Total Current Liabilities"],
    "operating_cf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "investing_cf": ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"],
    "financing_cf": ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"],
}


def _yf_ticker(symbol_id: str) -> str:
    """Map portfolio symbol_id → yfinance ticker string.
    Prefers the cached yf_ticker from universe; falls back to code (US) or
    suffix guess (KR)."""
    market, code = split_symbol(symbol_id)
    item = uni_search.get(symbol_id)
    if item and item.get("yf_ticker"):
        return item["yf_ticker"]
    if market == "US":
        return code
    return f"{code}.KS"  # safe default for KR


def _history(symbol_id: str, **kw) -> List[OHLCVBar]:
    """Ticker.history → bars; [] when the range has none. A failed call
    raises SourceError (yfinance is scrape-flaky — and that has to show)."""
    ticker = _yf_ticker(symbol_id)
    try:
        df = yf.Ticker(ticker).history(auto_adjust=False, **kw)
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"yfinance history {symbol_id}: {e}") from e
    if df is None or df.empty:
        return []
    return [OHLCVBar(ts=int(ts_pd.to_pydatetime().timestamp()),
                     open=float(row["Open"]), high=float(row["High"]),
                     low=float(row["Low"]), close=float(row["Close"]),
                     volume=float(row.get("Volume", 0) or 0))
            for ts_pd, row in df.iterrows()]


class YFinanceSource(Source):
    name = "yfinance"

    def get_daily(self, symbol_id: str, start: str, end: str) -> List[OHLCVBar]:
        return _history(symbol_id, start=start, end=end, interval="1d")

    def get_intraday(self, symbol_id: str, interval: str = "5m",
                     period: str = "5d") -> List[OHLCVBar]:
        return _history(symbol_id, period=period, interval=interval)

    def get_valuation(self, symbol_id: str, start: str = "",
                      end: str = "") -> List[ValuationPoint]:
        """L1 valuation snapshot from .info. yfinance gives only the CURRENT
        snapshot (no history), so we return one point stamped now. The store
        appends it, building a series across fetch dates over time."""
        ticker = _yf_ticker(symbol_id)
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"yfinance info {symbol_id}: {e}") from e
        if not info:
            return []

        def g(*keys):
            for k in keys:
                v = info.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            return None

        return [ValuationPoint(
            ts=int(time.time()),
            per=g("trailingPE", "forwardPE"),
            pbr=g("priceToBook"),
            psr=g("priceToSalesTrailing12Months"),
            eps=g("trailingEps", "forwardEps"),
            bps=g("bookValue"),
            dps=g("dividendRate"),
            div_yield=g("dividendYield"),
            market_cap=g("marketCap"),
        )]

    def get_financials(self, symbol_id: str, years: int = 4) -> List[FinancialRow]:
        """L2 statements from yfinance quarterly income / balance / cashflow.
        Merges the three by period (column = period-end date)."""
        ticker = _yf_ticker(symbol_id)
        try:
            t = yf.Ticker(ticker)
            inc = t.quarterly_financials
            bs = t.quarterly_balance_sheet
            cf = t.quarterly_cashflow
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"yfinance statements {symbol_id}: {e}") from e

        def _val(df, field, col):
            if df is None or df.empty or col not in df.columns:
                return None
            for label in _FIN_ALIASES.get(field, []):
                if label in df.index:
                    v = df.loc[label, col]
                    try:
                        f = float(v)
                        return f if f == f else None  # NaN guard
                    except (TypeError, ValueError):
                        return None
            return None

        # each standardized field comes from one statement
        stmt_of = {
            **dict.fromkeys(("revenue", "gross_profit", "operating_income",
                             "net_income"), inc),
            **dict.fromkeys(("total_assets", "total_liabilities", "total_equity",
                             "current_assets", "current_liabilities"), bs),
            **dict.fromkeys(("operating_cf", "investing_cf", "financing_cf"), cf),
        }
        # union of all period-end columns across the three statements
        cols = sorted({c for df in (inc, bs, cf)
                       if df is not None and not df.empty for c in df.columns},
                      reverse=True)[: years * 4]

        rows: List[FinancialRow] = []
        for col in cols:
            try:
                dt = col.to_pydatetime()
            except AttributeError:
                continue
            rows.append(FinancialRow(
                period=f"{dt.year}Q{(dt.month - 1) // 3 + 1}", fs_div="CFS",
                report_date=dt.strftime("%Y-%m-%d"), source="yfinance",
                **{f: _val(df, f, col) for f, df in stmt_of.items()}))
        return rows

    def get_quote(self, symbol_id: str) -> Optional[Quote]:
        ticker = _yf_ticker(symbol_id)
        try:
            t = yf.Ticker(ticker)
            fi = getattr(t, "fast_info", None) or {}
            price = float(fi.get("last_price") or fi.get("lastPrice") or 0)
            prev = float(fi.get("previous_close") or fi.get("previousClose") or 0)
            if price <= 0 or prev <= 0:
                df = t.history(period="5d", interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    raise SourceError(f"yfinance quote {symbol_id}: no price and no recent bars")
                price = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else price
            return Quote(symbol_id=symbol_id, price=price, prev_close=prev,
                         ts=int(time.time()))
        except SourceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"yfinance quote {symbol_id}: {e}") from e
