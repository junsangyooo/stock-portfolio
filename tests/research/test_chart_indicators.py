"""Indicator plugins emit objective FACTS (state), not votes."""
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.research.chart.indicators import trend, momentum, volatility, volume, ALL_INDICATORS
from src.research.chart.schema import STATE_NO_DATA


def _df(close: np.ndarray) -> pd.DataFrame:
    n = len(close)
    ts = [int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp()) + i * 86400 for i in range(n)]
    return pd.DataFrame({"ts": ts, "open": close, "high": close + 0.3,
                         "low": close - 0.3, "close": close, "volume": np.linspace(1000, 2000, n)})


def _uptrend(n=260):
    rng = np.random.default_rng(1)
    return _df(100 + np.arange(n) * 0.6 + rng.normal(0, 0.2, n))


def _downtrend(n=260):
    rng = np.random.default_rng(2)
    return _df(300 - np.arange(n) * 0.6 + rng.normal(0, 0.2, n))


class TestTrendFacts(unittest.TestCase):
    def test_uptrend_states(self):
        df = _uptrend()
        self.assertEqual(trend.ema_alignment(df, "1d").state, "bull_stack")
        self.assertEqual(trend.supertrend(df, "1d").state, "up")
        self.assertEqual(trend.adx_dmi(df, "1d").state, "strong_trend")
        self.assertEqual(trend.adx_dmi(df, "1d").evidence["di_direction"], "up")

    def test_downtrend_states(self):
        df = _downtrend()
        self.assertEqual(trend.ema_alignment(df, "1d").state, "bear_stack")
        self.assertEqual(trend.supertrend(df, "1d").state, "down")

    def test_ichimoku_above_cloud_in_uptrend(self):
        self.assertEqual(trend.ichimoku(_uptrend(), "1d").state, "above_cloud")

    def test_insufficient_data(self):
        f = trend.ema_alignment(_uptrend(n=30), "1d")
        self.assertEqual(f.state, STATE_NO_DATA)
        self.assertFalse(f.has_data)


class TestMomentumFacts(unittest.TestCase):
    def test_states_in_valid_set(self):
        df = _uptrend()
        for fn in momentum.INDICATORS:
            f = fn(df, "1d")
            self.assertIn(f.state, ("oversold", "overbought", "neutral", STATE_NO_DATA))

    def test_rsi_evidence_has_rising(self):
        f = momentum.rsi(_uptrend(), "1d")
        self.assertIn("rising", f.evidence)


class TestVolatilityVolumeFacts(unittest.TestCase):
    def test_bbands_state(self):
        f = volatility.bbands_pctb(_uptrend(), "1d")
        self.assertIn(f.state, ("above_upper", "below_lower", "inside"))

    def test_obv_rising_in_uptrend(self):
        self.assertEqual(volume.obv_trend(_uptrend(), "1d").state, "rising")

    def test_donchian_state(self):
        f = volatility.donchian_breakout(_uptrend(), "1d")
        self.assertIn(f.state, ("breakout_up", "breakout_down", "inside"))

    def test_volume_spike_flag(self):
        df = _uptrend()
        df.loc[df.index[-1], "volume"] = 99999
        flag = volume.volume_spike(df, "1d")
        self.assertTrue(flag["spike"])


class TestAllIndicators(unittest.TestCase):
    def test_no_judgment_fields_anywhere(self):
        df = _uptrend()
        for fn in ALL_INDICATORS:
            d = fn(df, "1d").to_dict()
            for forbidden in ("score", "label", "confidence", "vote", "signal"):
                self.assertNotIn(forbidden, d)


if __name__ == "__main__":
    unittest.main()
