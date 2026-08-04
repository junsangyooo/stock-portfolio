"""US + KR-via-yfinance fallback adapter.

For 'KR:005930' we look up yf_ticker from the universe (e.g. '005930.KS').
For 'US:AAPL' we just pass code directly.
"""
from __future__ import annotations

import logging

import time
from typing import List, Optional

import yfinance as yf  # type: ignore

from .base import Source, OHLCVBar, Quote, ValuationPoint, FinancialRow
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


log = logging.getLogger(__name__)


class YFinanceSource(Source):
    name = "yfinance"

    def get_daily(self, symbol_id: str, start: str, end: str) -> List[OHLCVBar]:
        ticker = _yf_ticker(symbol_id)
        try:
            t = yf.Ticker(ticker)
            df = t.history(start=start, end=end, interval="1d", auto_adjust=False)
        except Exception as e:  # noqa: BLE001 — yfinance is scrape-flaky
            log.warning("yfinance %s failed: %s", symbol_id, e)
            return []
        if df is None or df.empty:
            return []
        bars: List[OHLCVBar] = []
        for ts_pd, row in df.iterrows():
            dt = ts_pd.to_pydatetime()
            bars.append(OHLCVBar(
                ts=int(dt.timestamp()),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0) or 0),
            ))
        return bars

    def get_intraday(self, symbol_id: str, interval: str = "5m",
                     period: str = "5d") -> List[OHLCVBar]:
        ticker = _yf_ticker(symbol_id)
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, interval=interval, auto_adjust=False)
        except Exception as e:  # noqa: BLE001 — yfinance is scrape-flaky
            log.warning("yfinance %s failed: %s", symbol_id, e)
            return []
        if df is None or df.empty:
            return []
        bars: List[OHLCVBar] = []
        for ts_pd, row in df.iterrows():
            dt = ts_pd.to_pydatetime()
            bars.append(OHLCVBar(
                ts=int(dt.timestamp()),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0) or 0),
            ))
        return bars

    def get_valuation(self, symbol_id: str, start: str = "",
                      end: str = "") -> List[ValuationPoint]:
        """L1 valuation snapshot from .info. yfinance gives only the CURRENT
        snapshot (no history), so we return one point stamped now. The store
        appends it, building a series across fetch dates over time."""
        ticker = _yf_ticker(symbol_id)
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as e:  # noqa: BLE001 — yfinance is scrape-flaky
            log.warning("yfinance %s failed: %s", symbol_id, e)
            return []
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
        except Exception as e:  # noqa: BLE001 — yfinance is scrape-flaky
            log.warning("yfinance %s failed: %s", symbol_id, e)
            return []

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

        # union of all period-end columns across the three statements
        cols = []
        for df in (inc, bs, cf):
            if df is not None and not df.empty:
                cols.extend(list(df.columns))
        cols = sorted(set(cols), reverse=True)[: years * 4]

        rows: List[FinancialRow] = []
        for col in cols:
            try:
                dt = col.to_pydatetime()
            except AttributeError:
                continue
            q = (dt.month - 1) // 3 + 1
            row = FinancialRow(
                period=f"{dt.year}Q{q}", fs_div="CFS",
                report_date=dt.strftime("%Y-%m-%d"), source="yfinance",
                revenue=_val(inc, "revenue", col),
                gross_profit=_val(inc, "gross_profit", col),
                operating_income=_val(inc, "operating_income", col),
                net_income=_val(inc, "net_income", col),
                total_assets=_val(bs, "total_assets", col),
                total_liabilities=_val(bs, "total_liabilities", col),
                total_equity=_val(bs, "total_equity", col),
                current_assets=_val(bs, "current_assets", col),
                current_liabilities=_val(bs, "current_liabilities", col),
                operating_cf=_val(cf, "operating_cf", col),
                investing_cf=_val(cf, "investing_cf", col),
                financing_cf=_val(cf, "financing_cf", col),
            )
            rows.append(row)
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
                    return None
                price = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else price
            return Quote(symbol_id=symbol_id, price=price, prev_close=prev,
                         ts=int(time.time()))
        except Exception as e:  # noqa: BLE001 — yfinance is scrape-flaky
            log.warning("yfinance %s failed: %s", symbol_id, e)
            return None
