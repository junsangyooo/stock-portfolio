"""Unit tests for DART parsing helpers — pure, no network."""
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
