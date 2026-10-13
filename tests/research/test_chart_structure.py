"""C2 — structure: S/R, fibonacci, pivots, trendlines, candles, gaps, patterns."""
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.research.chart import structure
from src.research.chart.structure import levels, trendline, patterns


def _df(rows: list[dict]) -> pd.DataFrame:
    start = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp())
    for i, r in enumerate(rows):
        r.setdefault("ts", start + i * 86400)
        r.setdefault("volume", 1000)
    return pd.DataFrame(rows)


def _ohlc(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def _uptrend(n=120):
    rng = np.random.default_rng(3)
    close = 100 + np.arange(n) * 0.5 + rng.normal(0, 0.3, n)
    ts = [int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp()) + i * 86400 for i in range(n)]
    return pd.DataFrame({"ts": ts, "open": close, "high": close + 0.5,
                         "low": close - 0.5, "close": close, "volume": np.full(n, 1000.0)})


class TestPivots(unittest.TestCase):
    def test_classic_formula(self):
        df = _df([_ohlc(95, 110, 90, 100)])
        p = levels.pivot_points(df, "classic")
        self.assertAlmostEqual(p["PP"], 100.0)
        self.assertAlmostEqual(p["R1"], 110.0)   # 2*PP - L
        self.assertAlmostEqual(p["S1"], 90.0)    # 2*PP - H
        self.assertAlmostEqual(p["R2"], 120.0)   # PP + (H-L)
        self.assertAlmostEqual(p["S2"], 80.0)

    def test_camarilla_has_r4(self):
        df = _df([_ohlc(95, 110, 90, 100)])
        p = levels.pivot_points(df, "camarilla")
        self.assertIn("R4", p)


class TestFibonacci(unittest.TestCase):
    def test_uptrend_levels(self):
        fib = levels.fibonacci(_uptrend(), order=5)
        self.assertEqual(fib["direction"], "up")
        self.assertEqual(len(fib["levels"]), 5)
        # 0.618 level must lie between swing low and high
        v = fib["levels"]["0.618"]
        self.assertGreater(v, fib["swing_low"])
        self.assertLess(v, fib["swing_high"])


class TestSupportResistance(unittest.TestCase):
    def test_returns_levels_and_nearest(self):
        sr = levels.support_resistance(_uptrend(), order=5)
        self.assertIn("levels", sr)
        self.assertIn("price", sr)
        # in an uptrend there should be support below
        self.assertTrue(sr["nearest_support"] is None or
                        sr["nearest_support"]["price"] <= sr["price"])

    def test_insufficient(self):
        sr = levels.support_resistance(_df([_ohlc(1, 2, 0.5, 1.5)]), order=5)
        self.assertEqual(sr["levels"], [])


class TestTrendlines(unittest.TestCase):
    def test_uptrend_support_rising(self):
        # rising trend WITH oscillation so swing lows actually form
        n = 120
        i = np.arange(n)
        close = 100 + i * 0.5 + 4 * np.sin(i * 0.4)
        ts = [int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp()) + k * 86400 for k in range(n)]
        df = pd.DataFrame({"ts": ts, "open": close, "high": close + 0.5,
                           "low": close - 0.5, "close": close, "volume": np.full(n, 1000.0)})
        t = trendline.fit_trendlines(df, order=3)
        self.assertIsNotNone(t["support"])
        self.assertEqual(t["support"]["direction"], "rising")


class TestCandles(unittest.TestCase):
    def test_hammer(self):
        df = _df([_ohlc(12, 12.1, 11.9, 12), _ohlc(11, 11.1, 10.9, 11),
                  _ohlc(10, 10.1, 8.0, 9.8)])   # long lower shadow
        names = [p["pattern"] for p in patterns.detect_candles(df)]
        self.assertIn("hammer", names)

    def test_bullish_engulfing(self):
        df = _df([_ohlc(12, 12.1, 11.9, 12), _ohlc(10, 10.1, 9.4, 9.5),   # bear
                  _ohlc(9.4, 10.2, 9.3, 10.1)])                            # bull engulfs
        out = patterns.detect_candles(df)
        names = [p["pattern"] for p in out]
        self.assertIn("bullish_engulfing", names)
        self.assertTrue(all(p["direction"] in ("bullish", "bearish", "indecision") for p in out))

    def test_doji(self):
        df = _df([_ohlc(10, 10.5, 9.5, 10), _ohlc(10, 10.5, 9.5, 10),
                  _ohlc(10, 10.5, 9.5, 10.02)])   # tiny body, big range
        names = [p["pattern"] for p in patterns.detect_candles(df)]
        self.assertIn("doji", names)


class TestGap(unittest.TestCase):
    def test_gap_up(self):
        rows = [_ohlc(10, 10.5, 9.5, 10) for _ in range(20)]
        rows.append(_ohlc(12, 12.5, 11.8, 12.3))   # opens well above prior high 10.5
        g = patterns.detect_gap(_df(rows))
        self.assertIsNotNone(g["gap"])
        self.assertEqual(g["gap"]["type"], "gap_up")


class TestDoubleTopBottom(unittest.TestCase):
    def test_double_top(self):
        # two peaks ~equal with a trough between, order=2
        seq = [10, 11, 15, 11, 10, 11, 15.2, 11, 10]
        rows = [_ohlc(v, v + 0.2, v - 0.2, v) for v in seq]
        out = patterns.detect_double_top_bottom(_df(rows), order=2, tol_pct=0.03)
        names = [p["pattern"] for p in out]
        self.assertIn("double_top", names)


class TestDetectStructure(unittest.TestCase):
    def test_bundle_keys(self):
        s = structure.detect_structure(_uptrend())
        for k in ("support_resistance", "fibonacci", "pivots", "trendlines",
                  "candles", "gap", "chart_patterns"):
            self.assertIn(k, s)


if __name__ == "__main__":
    unittest.main()
