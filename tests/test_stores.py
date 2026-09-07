"""Round-trip tests for valuation_store + financials_store.

Both now live in market.db; the autouse `market_db` fixture in conftest points
them at a throwaway file, so nothing here touches the real data/ tree.
"""
from src.market.store import valuation_store, financials_store
from src.market.source.base import ValuationPoint, FinancialRow


def test_valuation_write_dedup_and_latest():
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


def test_financials_upsert_and_sort():
    sid = "KR:000000"
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


def test_a_stampless_refetch_keeps_the_filing_date():
    """The daily backfill calls write() without a filed_at. It must not erase
    the disclosure date the event-driven fetch stamped — that column is how a
    later run tells "we already have this filing" from "the company restated".
    """
    sid = "US:0000TEST"
    financials_store.write(sid, [FinancialRow(period="2025Q1", revenue=1.0,
                                              source="edgar")],
                           filed_at="2025-05-02")
    financials_store.write(sid, [FinancialRow(period="2025Q1", revenue=2.0,
                                              source="edgar")])
    from src.market.store.db import connect
    with connect() as c:
        row = c.execute("SELECT revenue, filed_at FROM financials"
                        " WHERE symbol_id = ?", (sid,)).fetchone()
    assert row[0] == 2.0                     # the numbers still update
    assert row[1] == "2025-05-02"            # the stamp survives

    # a newer filing date does replace it
    financials_store.write(sid, [FinancialRow(period="2025Q1", revenue=3.0,
                                              source="edgar")],
                           filed_at="2025-08-01")
    with connect() as c:
        assert c.execute("SELECT filed_at FROM financials WHERE symbol_id = ?",
                         (sid,)).fetchone()[0] == "2025-08-01"
