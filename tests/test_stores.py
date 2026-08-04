"""Round-trip tests for valuation_store + financials_store.

Path functions are monkeypatched into a tmp dir so tests never touch the
real data/ tree.
"""
import pytest

from src.market.store import valuation_store, financials_store
from src.market.source.base import ValuationPoint, FinancialRow


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    def vpath(market, code):
        p = tmp_path / "valuation" / market.upper() / f"{code}.parquet"
        return p

    def fpath(market, code):
        p = tmp_path / "financials" / market.upper() / f"{code}.parquet"
        return p

    monkeypatch.setattr(valuation_store, "valuation_path", vpath)
    monkeypatch.setattr(valuation_store, "ensure_dirs", lambda: None)
    monkeypatch.setattr(financials_store, "financials_path", fpath)
    monkeypatch.setattr(financials_store, "ensure_dirs", lambda: None)
    return tmp_path


def test_valuation_write_dedup_and_latest(tmp_paths):
    sid = "KR:000000"
    n1 = valuation_store.write(sid, [
        ValuationPoint(ts=100, per=10.0, pbr=1.0),
        ValuationPoint(ts=200, per=11.0, pbr=1.1),
    ])
    assert n1 == 2
    # overlapping ts=200 (newer value) + new ts=300
    n2 = valuation_store.write(sid, [
        ValuationPoint(ts=200, per=99.0, pbr=9.9),
        ValuationPoint(ts=300, per=12.0, pbr=1.2),
    ])
    assert n2 == 1                      # only ts=300 is new
    df = valuation_store.read(sid)
    assert list(df["ts"]) == [100, 200, 300]
    # newer write wins on collision
    assert df[df["ts"] == 200]["per"].iloc[0] == 99.0
    latest = valuation_store.latest(sid)
    assert latest["ts"] == 300 and latest["per"] == 12.0


def test_financials_upsert_and_sort(tmp_paths):
    sid = "KR:000000"
    valuation_store  # noqa: keep import used
    financials_store.write(sid, [
        FinancialRow(period="2024Q3", revenue=300.0, source="dart"),
        FinancialRow(period="2024Q1", revenue=100.0, source="dart"),
        FinancialRow(period="2024FY", revenue=400.0, source="dart"),
    ])
    df = financials_store.read(sid)
    # Q1 < Q3 < FY ordering by _period_sort_key
    assert list(df["period"]) == ["2024Q1", "2024Q3", "2024FY"]

    # re-fetch 2024Q3 with restated revenue → upsert (no new period)
    added = financials_store.write(sid, [
        FinancialRow(period="2024Q3", revenue=350.0, source="dart"),
    ])
    assert added == 0
    df2 = financials_store.read(sid)
    assert df2[df2["period"] == "2024Q3"]["revenue"].iloc[0] == 350.0
    assert financials_store.rows_as_dicts(sid)[-1]["period"] == "2024FY"


def test_period_sort_key():
    assert financials_store._period_sort_key("2024Q1") == (2024, 1)
    assert financials_store._period_sort_key("2024FY") == (2024, 5)
    assert financials_store._period_sort_key("bad") == (0, 0)
