"""ChartFactCollector — facts across timeframes + structure, no judgment."""
import json
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.agents.chart import chart_agent


def _daily(close: np.ndarray) -> pd.DataFrame:
    n = len(close)
    ts = [int(datetime(2021, 1, 4, tzinfo=timezone.utc).timestamp()) + i * 86400 for i in range(n)]
    rng = np.random.default_rng(7)
    high = close + np.abs(rng.normal(0, 0.3, n)) + 0.2
    low = close - np.abs(rng.normal(0, 0.3, n)) - 0.2
    return pd.DataFrame({"ts": ts, "open": close, "high": high, "low": low,
                         "close": close, "volume": np.full(n, 1500.0)})


def _uptrend(n=400):
    rng = np.random.default_rng(7)
    return _daily(100 + np.arange(n) * 0.4 + rng.normal(0, 0.3, n))


class TestCollectFacts(unittest.TestCase):
    def test_structure_and_timeframe_keys(self):
        out = chart_agent.collect_facts("KR:TESTUP", daily_df=_uptrend(), persist=False)
        for k in ("symbol_id", "kind", "asof", "timeframes", "structure", "engine_version"):
            self.assertIn(k, out)
        self.assertEqual(out["kind"], "chart")
        for tf in ("1d", "1w", "1mo"):
            self.assertIn(tf, out["timeframes"])
            self.assertIn("indicators", out["timeframes"][tf])
            self.assertIn("volume_spike", out["timeframes"][tf])
        self.assertIn("support_resistance", out["structure"])

    def test_facts_are_objective(self):
        out = chart_agent.collect_facts("KR:TESTUP", daily_df=_uptrend(), persist=False)
        daily_inds = out["timeframes"]["1d"]["indicators"]
        self.assertTrue(out["timeframes"]["1d"]["has_data"])
        # every indicator carries a state, and NO judgment fields
        for ind in daily_inds:
            self.assertIn("state", ind)
            for forbidden in ("score", "label", "confidence", "vote", "signal"):
                self.assertNotIn(forbidden, ind)
        # no top-level verdict/label/score
        for forbidden in ("verdict", "label", "score", "risk_flags", "regime"):
            self.assertNotIn(forbidden, out)

    def test_json_serialisable(self):
        out = chart_agent.collect_facts("KR:TESTUP", daily_df=_uptrend(), persist=False)
        parsed = json.loads(json.dumps(out, default=str))
        self.assertEqual(parsed["symbol_id"], "KR:TESTUP")

    def test_empty_frame_no_crash(self):
        empty = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        out = chart_agent.collect_facts("KR:EMPTY", daily_df=empty, persist=False)
        self.assertEqual(out["structure"], {})
        self.assertFalse(out["timeframes"]["1d"]["has_data"])

    def test_uptrend_facts_reflect_trend(self):
        out = chart_agent.collect_facts("KR:TESTUP", daily_df=_uptrend(), persist=False)
        states = {i["indicator"]: i["state"] for i in out["timeframes"]["1d"]["indicators"]}
        self.assertEqual(states["ema_alignment"], "bull_stack")
        self.assertEqual(states["supertrend"], "up")


if __name__ == "__main__":
    unittest.main()
