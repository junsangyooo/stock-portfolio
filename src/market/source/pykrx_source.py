"""KR adapter — pykrx for daily OHLCV + close-as-quote fallback."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from pykrx import stock as pkx  # type: ignore

from .base import Source, OHLCVBar, Quote, ValuationPoint
from ...core.paths import split_symbol

KST = timezone(timedelta(hours=9))


def _ymd(s: str) -> str:
    return s.replace("-", "")


class PykrxSource(Source):
    name = "pykrx"

    def get_daily(self, symbol_id: str, start: str, end: str) -> List[OHLCVBar]:
        market, code = split_symbol(symbol_id)
        if market != "KR":
            return []
        try:
            df = pkx.get_market_ohlcv_by_date(_ymd(start), _ymd(end), code)
        except Exception:
            return []
        bars: List[OHLCVBar] = []
        for ts_pd, row in df.iterrows():
            dt = ts_pd.to_pydatetime().replace(tzinfo=KST)
            bars.append(OHLCVBar(
                ts=int(dt.timestamp()),
                open=float(row["시가"]),
                high=float(row["고가"]),
                low=float(row["저가"]),
                close=float(row["종가"]),
                volume=float(row["거래량"]),
            ))
        return bars

    def get_valuation(self, symbol_id: str, start: str,
                      end: str) -> List[ValuationPoint]:
        """L1 daily valuation via pykrx. get_market_fundamental gives
        BPS/PER/PBR/EPS/DIV/DPS per date; get_market_cap gives 시가총액.
        PSR is not provided by KRX → left None."""
        market, code = split_symbol(symbol_id)
        if market != "KR":
            return []
        try:
            fund = pkx.get_market_fundamental(_ymd(start), _ymd(end), code)
        except Exception:
            return []
        if fund is None or fund.empty:
            return []
        try:
            cap = pkx.get_market_cap(_ymd(start), _ymd(end), code)
        except Exception:
            cap = None

        def _f(row, key):
            try:
                v = float(row[key])
                return v if v == v else None  # drop NaN
            except (KeyError, TypeError, ValueError):
                return None

        points: List[ValuationPoint] = []
        for ts_pd, row in fund.iterrows():
            dt = ts_pd.to_pydatetime().replace(tzinfo=KST)
            mcap = None
            if cap is not None and ts_pd in cap.index:
                mcap = _f(cap.loc[ts_pd], "시가총액")
            points.append(ValuationPoint(
                ts=int(dt.timestamp()),
                per=_f(row, "PER"), pbr=_f(row, "PBR"), psr=None,
                eps=_f(row, "EPS"), bps=_f(row, "BPS"),
                dps=_f(row, "DPS"), div_yield=_f(row, "DIV"),
                market_cap=mcap,
            ))
        return points

    def get_quote(self, symbol_id: str) -> Optional[Quote]:
        """pykrx has no live tick. Returns the most recent close as a
        delayed proxy so charts have something to render."""
        market, code = split_symbol(symbol_id)
        if market != "KR":
            return None
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=10)).strftime("%Y%m%d")
        try:
            df = pkx.get_market_ohlcv_by_date(start, end, code)
        except Exception:
            return None
        if df.empty:
            return None
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        return Quote(symbol_id=symbol_id, price=float(last["종가"]),
                     prev_close=float(prev["종가"]), ts=int(time.time()))
