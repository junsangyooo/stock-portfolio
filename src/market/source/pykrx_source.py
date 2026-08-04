"""KR adapter — pykrx for daily OHLCV + close-as-quote fallback.

KR valuation does NOT come from here: pykrx's fundamental endpoints stopped
returning usable data, so PER/PBR/EPS are derived from DART filings instead
(see market/kr_valuation.py).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import List, Optional

from pykrx import stock as pkx  # type: ignore

from .base import Source, OHLCVBar, Quote
from ...core.kst import KST
from ...core.paths import split_symbol

log = logging.getLogger(__name__)


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
        except Exception as e:  # noqa: BLE001 — pykrx scrapes KRX; stay soft
            log.warning("pykrx get_daily %s failed: %s", symbol_id, e)
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
        except Exception as e:  # noqa: BLE001 — pykrx scrapes KRX; stay soft
            log.warning("pykrx get_quote %s failed: %s", symbol_id, e)
            return None
        if df.empty:
            return None
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        return Quote(symbol_id=symbol_id, price=float(last["종가"]),
                     prev_close=float(prev["종가"]), ts=int(time.time()))
