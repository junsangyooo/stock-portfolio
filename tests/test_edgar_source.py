"""EDGAR parser — duration classification is the whole game.

XBRL entries mix quarter, half, nine-month and FY spans in one list; picking
by (end-start) days is what keeps quarterly rows quarter-only.
"""
from src.market.source.edgar_source import get_financials, _q_label, _iter_entries
import src.market.source.edgar_source as es
from datetime import date


def _entry(start, end, val, form="10-Q", filed="2026-01-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def test_duration_classification(monkeypatch, tmp_path):
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _entry("2025-01-01", "2025-03-31", 100),          # Q1
            _entry("2025-01-01", "2025-06-30", 220),          # H1 → skip
            _entry("2025-04-01", "2025-06-30", 120),          # Q2
            _entry("2025-01-01", "2025-12-31", 500, "10-K"),  # FY
            _entry("2025-01-01", "2025-03-31", 105, filed="2026-02-01"),  # 재공시
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _entry("2025-01-01", "2025-03-31", 10),
            _entry("2025-04-01", "2025-06-30", 12),
            _entry("2025-01-01", "2025-12-31", 50, "10-K"),
        ]}},
        "Assets": {"units": {"USD": [
            {"end": "2025-03-31", "val": 1000, "form": "10-Q", "filed": "2025-05-01"},
            {"end": "2025-06-30", "val": 1100, "form": "10-Q", "filed": "2025-08-01"},
        ]}},
    }}}
    monkeypatch.setattr(es, "cik_for", lambda sid: 999)

    class _Res:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return facts
    monkeypatch.setattr(es.requests, "get", lambda *a, **k: _Res())

    rows = get_financials("US:TEST", years=6)
    by = {r.period: r for r in rows}
    assert by["2025Q1"].revenue == 105          # latest filing wins
    assert by["2025Q1"].net_income == 10
    assert by["2025Q1"].total_assets == 1000
    assert by["2025Q2"].revenue == 120
    assert by["2025FY"].revenue == 500
    assert "2025H1" not in by                   # cumulative span dropped
    assert all(r.source == "edgar" for r in rows)


def test_q_label_calendar_quarters():
    assert _q_label(date(2026, 2, 1)) == "2026Q1"    # AVGO-style fiscal end
    assert _q_label(date(2026, 11, 2)) == "2026Q4"


def test_iter_entries_prefers_first_concept_hit():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [{"val": 1}]}},
        "SalesRevenueNet": {"units": {"USD": [{"val": 2}]}},
    }}}
    got = _iter_entries(facts, ("RevenueFromContractWithCustomerExcludingAssessedTax",
                                "Revenues", "SalesRevenueNet"))
    assert got == [{"val": 1}]
