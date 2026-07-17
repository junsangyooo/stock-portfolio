"""Unit tests for L3 derived metrics — pure, no I/O, no network."""
import pytest

from src.market import fundamental_metrics as fm


def test_margins():
    assert fm.operating_margin(20, 100) == 0.2
    assert fm.net_margin(10, 100) == 0.1


def test_roe_roa():
    assert fm.roe(50, 200) == 0.25
    assert fm.roa(50, 500) == 0.1


def test_debt_ratio_korean_formula():
    # 부채 200 / 자본 100 * 100 = 200(%)
    assert fm.debt_ratio(200, 100) == 200.0


def test_current_ratio_pct():
    assert fm.current_ratio(150, 100) == 150.0


def test_none_safety():
    assert fm.operating_margin(None, 100) is None
    assert fm.net_margin(10, None) is None
    assert fm.roe(10, 0) is None          # zero denominator
    assert fm.debt_ratio(100, 0) is None


def test_yoy():
    assert fm.yoy(110, 100) == 0.1
    assert fm.yoy(90, 100) == -0.1
    # negative prior → abs() denominator so recovery reads positive
    assert fm.yoy(50, -100) == 1.5
    assert fm.yoy(100, 0) is None
    assert fm.yoy(None, 100) is None


def test_find_prior_year():
    rows = [{"period": "2023Q3"}, {"period": "2024Q3"}, {"period": "2024FY"}]
    assert fm.find_prior_year(rows, "2024Q3") == {"period": "2023Q3"}
    assert fm.find_prior_year(rows, "2024FY") is None      # no 2023FY
    assert fm.find_prior_year(rows, "") is None


def test_compute_bundle():
    latest = {"revenue": 1000, "operating_income": 200, "net_income": 100,
              "total_assets": 5000, "total_liabilities": 2000,
              "total_equity": 3000, "current_assets": 1500,
              "current_liabilities": 1000}
    prior = {"revenue": 800, "operating_income": 150}
    val = {"per": 12.5, "pbr": 1.2, "market_cap": 9_000_000}
    out = fm.compute(latest, prior_year=prior, valuation=val)

    assert out["operating_margin"] == 0.2
    assert out["net_margin"] == 0.1
    assert out["roe"] == pytest.approx(100 / 3000)
    assert out["debt_ratio"] == pytest.approx(2000 / 3000 * 100)
    assert out["current_ratio"] == 150.0
    assert out["revenue_yoy"] == 0.25
    assert out["operating_income_yoy"] == pytest.approx((200 - 150) / 150)
    # valuation passthrough
    assert out["per"] == 12.5
    assert out["pbr"] == 1.2
    assert out["market_cap"] == 9_000_000


def test_compute_empty_is_all_none():
    out = fm.compute({}, prior_year=None, valuation=None)
    for k in ("operating_margin", "roe", "debt_ratio", "revenue_yoy", "per"):
        assert out[k] is None


# ── quality of earnings / accrual ─────────────────────────────────────────
def test_quality_of_earnings():
    assert fm.quality_of_earnings(120, 100) == 1.2
    assert fm.quality_of_earnings(80, 100) == 0.8
    assert fm.quality_of_earnings(100, 0) is None
    assert fm.quality_of_earnings(None, 100) is None


def test_sloan_accrual():
    # (NI - OCF - ICF) / TA  →  (100 - 120 - (-30)) / 1000 = 0.01
    assert fm.sloan_accrual(100, 120, -30, 1000) == pytest.approx(0.01)
    assert fm.sloan_accrual(100, 120, -30, 0) is None
    assert fm.sloan_accrual(None, 120, -30, 1000) is None


# ── Altman Z (approx) ──────────────────────────────────────────────────────
def test_altman_z_safe_zone():
    latest = {"total_assets": 1000, "total_liabilities": 300,
              "total_equity": 700, "current_assets": 500,
              "current_liabilities": 200, "operating_income": 150,
              "revenue": 1200}
    out = fm.altman_z(latest, market_cap=2000)
    assert out["z"] is not None
    assert out["zone"] == "safe"          # healthy firm → Z > 3
    assert out["approx"] is True


def test_altman_z_insufficient():
    out = fm.altman_z({"total_assets": 1000}, market_cap=None)
    assert out["z"] is None and out["zone"] is None


# ── Piotroski F (partial 8/9) ──────────────────────────────────────────────
def test_piotroski_f_strong():
    latest = {"net_income": 120, "operating_cf": 150, "total_assets": 1000,
              "total_liabilities": 300, "total_equity": 700,
              "current_assets": 600, "current_liabilities": 200,
              "revenue": 1000, "gross_profit": 400}
    prior = {"net_income": 80, "operating_cf": 90, "total_assets": 1000,
             "total_liabilities": 400, "total_equity": 600,
             "current_assets": 450, "current_liabilities": 250,
             "revenue": 900, "gross_profit": 340}
    out = fm.piotroski_f(latest, prior)
    assert out["max"] == 8                 # shares item (#7) dropped
    assert out["score"] >= 7               # improving firm scores high
    assert set(out["components"]) >= {"roa_pos", "ocf_pos", "droa_pos"}


def test_piotroski_f_no_prior():
    # without prior year, delta items are None but level items still score
    latest = {"net_income": 120, "operating_cf": 150, "total_assets": 1000}
    out = fm.piotroski_f(latest, None)
    assert out["components"]["roa_pos"] is True
    assert out["components"]["droa_pos"] is None


# ── derived trend series ────────────────────────────────────────────────────
def test_derived_series_trend():
    rows = [
        {"period": "2023Q3", "revenue": 800, "operating_income": 100,
         "net_income": 60, "total_equity": 500, "total_assets": 1000,
         "total_liabilities": 500, "current_assets": 300,
         "current_liabilities": 200},
        {"period": "2024Q3", "revenue": 1000, "operating_income": 200,
         "net_income": 120, "total_equity": 600, "total_assets": 1100,
         "total_liabilities": 500, "current_assets": 400,
         "current_liabilities": 200},
    ]
    series = fm.derived_series(rows, last_n=8)
    assert len(series) == 2
    assert series[-1]["period"] == "2024Q3"
    assert series[-1]["operating_margin"] == 0.2
    # YoY uses the matching prior-year row inside the series
    assert series[-1]["revenue_yoy"] == 0.25


# ── percentile rank (valuation band) ────────────────────────────────────────
def test_percentile_rank():
    vals = [10, 12, 14, 16, 18, 20]
    assert fm.percentile_rank(vals, 16) == pytest.approx(4 / 6, abs=1e-3)  # 4/6 ≤ 16
    assert fm.percentile_rank(vals, 21) == 1.0
    assert fm.percentile_rank(vals, 9) == 0.0


def test_percentile_rank_insufficient():
    assert fm.percentile_rank([1, 2, 3], 2) is None     # < 5 points
    assert fm.percentile_rank([], 2) is None
    assert fm.percentile_rank([1, 2, 3, 4, 5], None) is None


# ── anomaly flags (input-data sanity guard) ─────────────────────────────────
def test_anomaly_flags_clean():
    # Samsung 2026Q1 (real filing): 42.75% margin must NOT be flagged.
    m = {"operating_margin": 0.4275, "net_margin": 0.3528, "roe": 0.09,
         "roa": 0.07, "revenue_yoy": 0.69}
    assert fm.anomaly_flags(m) == []


def test_anomaly_flags_implausible():
    flags = fm.anomaly_flags({"operating_margin": 0.85, "roe": 2.5})
    assert any("operating_margin" in f for f in flags)
    assert any("roe" in f for f in flags)


def test_anomaly_flags_quality_and_none_safe():
    assert fm.anomaly_flags({}) == []
    assert fm.anomaly_flags({"operating_margin": None}) == []
    flags = fm.anomaly_flags({}, quality={"qoe": 15.0})
    assert any("qoe" in f for f in flags)
