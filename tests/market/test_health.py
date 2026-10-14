"""market.health — counts over the symbols it is handed."""
from __future__ import annotations

import time

from src.market import health
from src.market.source.base import OHLCVBar, Quote
from src.market.store import ohlcv_store, quote_store


def test_bars_and_quotes_counted_late_and_missing():
    now = int(time.time())
    ohlcv_store.write("KR:000001", [OHLCVBar(ts=now - 86400, open=1, high=1, low=1, close=1, volume=1)])
    ohlcv_store.write("KR:000002", [OHLCVBar(ts=now - 10 * 86400, open=1, high=1, low=1, close=1, volume=1)])
    quote_store.set_many([Quote("KR:000001", 1.0, 1.0, now)])
    h = health.symbols(["KR:000001", "KR:000002", "KR:000003"])
    assert h["bars"] == {"n": 3, "missing": 1, "late": 1, "oldest": h["bars"]["oldest"]}
    assert h["bars"]["oldest"] >= 9
    assert h["quotes"]["missing"] >= 2 and h["quotes"]["n"] == 3


def test_no_symbols_is_empty_not_an_error():
    h = health.symbols([])
    assert h["bars"]["n"] == 0 and h["filings"]["newest"] is None
