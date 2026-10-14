"""A source that fails says so (ops notes §11 소스 실패): no [] or None for an error."""
from __future__ import annotations

import pandas as pd
import pytest

from src.market.pipeline import gap_filler
from src.market.source import pykrx_source, yfinance_source
from src.market.source.base import SourceError


def _boom(*a, **k):
    raise ConnectionError("KRX down")


def test_pykrx_failures_raise(monkeypatch):
    monkeypatch.setattr(pykrx_source.pkx, "get_market_ohlcv_by_date", _boom)
    src = pykrx_source.PykrxSource()
    with pytest.raises(SourceError, match="KRX down"):
        src.get_daily("KR:005930", "2026-10-01", "2026-10-14")
    with pytest.raises(SourceError):
        src.get_quote("KR:005930")


def test_pykrx_quote_without_bars_is_a_failure(monkeypatch):
    monkeypatch.setattr(pykrx_source.pkx, "get_market_ohlcv_by_date",
                        lambda *a, **k: pd.DataFrame())
    with pytest.raises(SourceError, match="no bars"):
        pykrx_source.PykrxSource().get_quote("KR:005930")


def test_an_empty_range_is_still_just_empty(monkeypatch):
    """A holiday has no bars; that is an answer, not a failure."""
    monkeypatch.setattr(pykrx_source.pkx, "get_market_ohlcv_by_date",
                        lambda *a, **k: pd.DataFrame())
    assert pykrx_source.PykrxSource().get_daily("KR:005930", "2026-10-03", "2026-10-03") == []


def test_yfinance_failures_raise(monkeypatch):
    class T:
        def __init__(self, *a):
            pass

        def history(self, **k):
            raise ConnectionError("rate limited")
        info = property(lambda self: _boom())
        fast_info = property(lambda self: _boom())
    monkeypatch.setattr(yfinance_source.yf, "Ticker", T)
    src = yfinance_source.YFinanceSource()
    for call in (lambda: src.get_daily("US:AAPL", "2026-10-01", "2026-10-14"),
                 lambda: src.get_quote("US:AAPL"),
                 lambda: src.get_valuation("US:AAPL")):
        with pytest.raises(SourceError):
            call()


def test_gap_fill_marks_the_failed_symbol(monkeypatch):
    class Src:
        def get_daily(self, *a):
            raise SourceError("down")
    monkeypatch.setattr(gap_filler, "for_symbol", lambda sid: Src())
    assert gap_filler.fill_many(["KR:000001"]) == {"KR:000001": -1}


def test_refresh_names_no_symbol(monkeypatch):
    from src.portfolio import service
    from src.portfolio.store import account_store, holding_store
    account_store.create("a", "a", "GENERAL")
    holding_store.upsert("a", "KR:000001", qty=1, avg_cost=1)
    monkeypatch.setattr(gap_filler, "fill_many", lambda sids: {s: -1 for s in sids})
    from src.market import quotes
    monkeypatch.setattr(quotes, "warm", lambda sids, max_age_s: {s: None for s in sids})
    out = service.refresh_held()
    assert out == {"ok": True, "symbols": 1, "bars_added": 0, "failed": 1, "quotes_fresh": 0}
