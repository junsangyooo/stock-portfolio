"""P0-C — recent_move: deterministic dead-cat-bounce facts.

Builds a synthetic decline → weak-volume rebound and asserts the quantified
move facts the rulebook §E1 needs are populated and correct.
"""
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.research.chart.structure import recent_move


def _series(closes, vols):
    n = len(closes)
    ts = [int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp()) + i * 86400
          for i in range(n)]
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "ts": ts, "open": c, "high": c + 0.3, "low": c - 0.3,
        "close": c, "volume": np.asarray(vols, dtype=float),
    })


def _dead_cat_df():
    # rise 100→130 (peak @20), decline 130→104 (-20%, @35), weak rebound 104→112.
    rise = list(np.linspace(100, 130, 21))            # 0..20
    fall = list(np.linspace(130, 104, 15))[1:]        # 21..35
    bounce = list(np.linspace(104, 112, 11))[1:]      # 36..45
    closes = rise + fall + bounce
    vols = ([1000] * 21) + ([2000] * 14) + ([500] * 10)   # rebound low-volume
    return _series(closes, vols)


class TestRecentMove(unittest.TestCase):
    def test_dead_cat_facts_populated(self):
        rm = recent_move.detect_recent_move(_dead_cat_df(), order=5)
        self.assertNotIn("reason", rm)
        # decline ~20% off the 130 peak
        self.assertAlmostEqual(rm["decline_from_high_pct"], 20.0, delta=1.5)
        # rebound modest (single-digit %)
        self.assertTrue(0 < rm["rebound_from_low_pct"] < 12)
        # rebound volume well below decline volume → ratio < 1 (the tell)
        self.assertIsNotNone(rm["rebound_vol_ratio"])
        self.assertLess(rm["rebound_vol_ratio"], 1.0)
        # 50% fib of the decline NOT recovered
        self.assertIn("0.500", rm["fib_recovery"])
        self.assertFalse(rm["fib_recovery"]["0.500"]["price_above"])

    def test_insufficient_bars(self):
        df = _series([100, 101, 102], [1000, 1000, 1000])
        self.assertIn("reason", recent_move.detect_recent_move(df))

    def test_no_lookahead_uses_current_close(self):
        # rebound endpoint must be the CURRENT close, not a future bar.
        df = _dead_cat_df()
        rm = recent_move.detect_recent_move(df, order=5)
        self.assertAlmostEqual(rm["close"], float(df["close"].iloc[-1]), places=2)


if __name__ == "__main__":
    unittest.main()
