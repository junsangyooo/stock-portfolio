"""Unit tests for DART parsing helpers — pure, no network."""
import pytest

from src.market.source.base import FinancialRow
from src.market.source.dart_source import (_parse_amount, _pick_common_dps,
                                           derive_quarter_cf, REPRT)


def test_parse_amount():
    assert _parse_amount("1,234,567") == 1234567.0
    assert _parse_amount("-2,000") == -2000.0
    assert _parse_amount("") is None
    assert _parse_amount("-") is None
    assert _parse_amount(None) is None
    assert _parse_amount("abc") is None
    assert _parse_amount(5000) == 5000.0


def test_reprt_period_labels():
    assert REPRT["11013"] == "Q1"
    assert REPRT["11012"] == "Q2"
    assert REPRT["11014"] == "Q3"
    assert REPRT["11011"] == "FY"


def _row(period, ocf=None, icf=None, fcf=None, fs_div="CFS"):
    return FinancialRow(period=period, fs_div=fs_div, operating_cf=ocf,
                        investing_cf=icf, financing_cf=fcf)


def test_derive_quarter_cf_differences_cumulatives():
    # DART interim CF is cumulative: Q1=10, H1=25, 9M=60. FY is annual.
    rows = [
        _row("2025Q1", ocf=10.0, icf=-2.0, fcf=-1.0),
        _row("2025Q2", ocf=25.0, icf=-5.0, fcf=-4.0),
        _row("2025Q3", ocf=60.0, icf=-9.0, fcf=-6.0),
        _row("2025FY", ocf=100.0, icf=-20.0, fcf=-10.0),
    ]
    derive_quarter_cf(rows)
    assert rows[0].operating_cf == 10.0            # Q1: cum == quarter
    assert rows[1].operating_cf == 25.0 - 10.0     # Q2 = H1 − Q1
    assert rows[1].investing_cf == -5.0 - (-2.0)
    assert rows[1].financing_cf == -4.0 - (-1.0)
    assert rows[2].operating_cf == 60.0 - 25.0     # Q3 = 9M − H1
    assert rows[3].operating_cf == 100.0           # FY: annual, untouched


def test_derive_quarter_cf_missing_prior_quarter():
    # No Q1 row → Q2 cumulative cannot be quarterized → None (not left cum).
    rows = [_row("2025Q2", ocf=25.0), _row("2025Q3", ocf=60.0)]
    derive_quarter_cf(rows)
    assert rows[0].operating_cf is None
    assert rows[1].operating_cf == 35.0            # Q3 still has Q2 cum


def test_derive_quarter_cf_fs_div_mismatch():
    # CFS/OFS cumulatives must not be mixed in a difference.
    rows = [_row("2025Q1", ocf=10.0, fs_div="OFS"),
            _row("2025Q2", ocf=25.0, fs_div="CFS")]
    derive_quarter_cf(rows)
    assert rows[1].operating_cf is None


def test_derive_quarter_cf_years_independent():
    rows = [_row("2024Q1", ocf=7.0), _row("2025Q2", ocf=25.0)]
    derive_quarter_cf(rows)
    assert rows[0].operating_cf == 7.0
    assert rows[1].operating_cf is None            # 2025Q1 absent


def test_pick_common_dps():
    recs = [
        {"se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "100"},
        {"se": "주당 현금배당금(원)", "stock_knd": "우선주", "thstrm": "1,669"},
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,668"},
    ]
    assert _pick_common_dps(recs) == 1668.0
    assert _pick_common_dps([{"se": "주당 현금배당금(원)",
                              "stock_knd": "보통주", "thstrm": "-"}]) is None
    assert _pick_common_dps([]) is None


# ── 2026-10-14: totals by IFRS id, filing date, honest failures ────────────

def test_equity_and_liabilities_total_is_not_taken_for_liabilities():
    """'자본과부채총계' contains '부채총계' and comes first in the filing;
    383 of 884 stored rows had total assets as total liabilities."""
    from src.market.source.dart_source import match_accounts
    rows = [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계", "thstrm_amount": "100"},
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAndLiabilities", "account_nm": "자본과부채총계", "thstrm_amount": "100"},
        {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "account_nm": "부채총계", "thstrm_amount": "60"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계", "thstrm_amount": "40"},
    ]
    f = match_accounts(rows)
    assert (f["total_assets"], f["total_liabilities"], f["total_equity"]) == (100, 60, 40)


def test_a_report_is_dated_by_its_receipt_number():
    from src.market.source.dart_source import DartSource
    row = DartSource()._parse([{"sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "5",
                                 "rcept_no": "20260318001622"}], 2025, "11011")
    assert row.filed_at == "2026-03-18" and row.period == "2025FY"


@pytest.mark.parametrize("status,expect", [("013", None), ("010", "raise"), ("020", "raise")])
def test_no_such_report_is_none_any_other_status_raises(monkeypatch, status, expect):
    from src.market.source import dart_source
    monkeypatch.setattr(dart_source, "_api_key", lambda: "k")
    monkeypatch.setattr(dart_source.requests, "get", lambda *a, **k: type(
        "R", (), {"json": lambda self: {"status": status, "message": "m"}})())
    ds = dart_source.DartSource()
    if expect == "raise":
        with pytest.raises(dart_source.DartError):
            ds._finstate("c", 2025, "11011", "CFS")
    else:
        assert ds._finstate("c", 2025, "11011", "CFS") is None


def test_a_rows_own_filing_date_is_stored():
    from src.market.source.base import FinancialRow
    from src.market.store import financials_store
    financials_store.write("KR:000001", [FinancialRow(period="2025FY", source="dart",
                                                       revenue=1, filed_at="2026-03-18")])
    assert financials_store.latest_filed_at(["KR:000001"]) == {"KR:000001": "2026-03-18"}
    financials_store.write("KR:000001", [FinancialRow(period="2025FY", source="dart", revenue=2)])
    assert financials_store.latest_filed_at(["KR:000001"]) == {"KR:000001": "2026-03-18"}
