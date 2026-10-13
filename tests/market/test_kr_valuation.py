"""Unit tests for KR valuation derived from DART + OHLCV — pure, no network."""
from src.market import kr_valuation as kv


def test_build_from_fy():
    rows = [{"period": "2024FY", "net_income": 1000.0, "eps": 10.0,
             "total_equity": 8000.0}]
    closes = [(1, 50.0), (2, 100.0)]
    pts = kv.build(rows, closes)
    assert len(pts) == 2
    # shares = 1000/10 = 100; bps = 8000/100 = 80
    assert pts[0].per == 5.0          # 50/10
    assert pts[1].per == 10.0         # 100/10
    assert pts[0].pbr == 0.625        # 50/80
    assert pts[1].pbr == 1.25         # 100/80
    assert pts[0].market_cap == 5000.0   # 50*100
    assert pts[1].market_cap == 10000.0
    assert pts[0].eps == 10.0         # annual EPS passthrough


def test_quarterly_eps_annualized_estimate():
    rows = [{"period": "2024Q3", "net_income": 250.0, "eps": 2.5,
             "total_equity": 8000.0}]
    closes = [(1, 50.0)]
    pts = kv.build(rows, closes)
    # shares = 250/2.5 = 100; annual_eps = 2.5*4 = 10; bps = 80
    assert pts[0].per == 5.0          # 50/10
    assert pts[0].pbr == 0.625
    assert pts[0].eps == 10.0


def test_prefers_fy_eps_over_quarterly():
    rows = [
        {"period": "2024Q3", "net_income": 250.0, "eps": 2.5, "total_equity": 8000.0},
        {"period": "2024FY", "net_income": 1000.0, "eps": 9.0, "total_equity": 8000.0},
    ]
    pts = kv.build(rows, [(1, 90.0)])
    assert pts[0].per == 10.0         # 90 / 9 (FY EPS, not 2.5*4)


def test_missing_data_guards():
    assert kv.build([], [(1, 50.0)]) == []
    assert kv.build([{"period": "2024FY"}], []) == []
    # no eps → shares None → per None, mcap None
    pts = kv.build([{"period": "2024FY", "total_equity": 8000.0}], [(1, 50.0)])
    assert pts[0].per is None and pts[0].market_cap is None


def test_dps_and_div_yield():
    rows = [{"period": "2024FY", "net_income": 1000.0, "eps": 10.0,
             "total_equity": 8000.0}]
    pts = kv.build(rows, [(1, 50.0), (2, 100.0)], dps=2.0)
    assert pts[0].dps == 2.0
    assert pts[0].div_yield == 4.0     # 2/50*100 (percent, pykrx DIV convention)
    assert pts[1].div_yield == 2.0     # 2/100*100
    # no dps → both stay None (previous behavior)
    pts = kv.build(rows, [(1, 50.0)])
    assert pts[0].dps is None and pts[0].div_yield is None


def test_zero_close_skipped():
    rows = [{"period": "2024FY", "net_income": 1000.0, "eps": 10.0,
             "total_equity": 8000.0}]
    pts = kv.build(rows, [(1, 0.0), (2, 50.0)])
    assert len(pts) == 1 and pts[0].ts == 2
