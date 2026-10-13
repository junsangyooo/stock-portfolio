"""C0 — extrema engine: swing detection + level clustering."""
import unittest

from src.research.chart.structure import extrema


class TestFindSwings(unittest.TestCase):
    def test_detects_local_max_min(self):
        highs = [1, 2, 3, 2, 1, 2, 5, 2, 1]
        lows = [5, 4, 3, 4, 5, 4, 1, 4, 5]
        sh, sl = extrema.find_swings(highs, lows, order=2)
        hi_idx = [i for i, _ in sh]
        lo_idx = [i for i, _ in sl]
        self.assertIn(2, hi_idx)   # value 3 peak
        self.assertIn(6, hi_idx)   # value 5 peak
        self.assertIn(2, lo_idx)   # value 3 trough
        self.assertIn(6, lo_idx)   # value 1 trough

    def test_edges_not_confirmed(self):
        # last `order` bars can't be swings (no right neighbours) -> no look-ahead
        highs = [1, 2, 3, 4, 5]   # monotonic rising, last bar highest but unconfirmed
        sh, _ = extrema.find_swings(highs, highs, order=2)
        self.assertNotIn(4, [i for i, _ in sh])

    def test_empty(self):
        sh, sl = extrema.find_swings([], [], order=3)
        self.assertEqual(sh, [])
        self.assertEqual(sl, [])


class TestClusterLevels(unittest.TestCase):
    def test_merges_within_tolerance(self):
        swings = [(0, 100.0), (1, 100.2), (2, 105.0)]
        levels = extrema.cluster_levels(swings, tolerance_pct=0.003)  # 0.3%
        self.assertEqual(len(levels), 2)
        # 100.0 & 100.2 merge (0.2% apart), 105 separate
        self.assertAlmostEqual(levels[0]["price"], 100.1, places=4)
        self.assertEqual(levels[0]["tests"], 2)
        self.assertEqual(levels[1]["tests"], 1)

    def test_sorted_ascending(self):
        swings = [(0, 50.0), (1, 10.0), (2, 30.0)]
        levels = extrema.cluster_levels(swings, tolerance_pct=0.001)
        prices = [lv["price"] for lv in levels]
        self.assertEqual(prices, sorted(prices))

    def test_empty(self):
        self.assertEqual(extrema.cluster_levels([]), [])


class TestNearest(unittest.TestCase):
    def setUp(self):
        self.levels = [{"price": 90.0, "tests": 2, "last_idx": 1},
                       {"price": 100.0, "tests": 3, "last_idx": 5},
                       {"price": 110.0, "tests": 1, "last_idx": 8}]

    def test_nearest_above_below(self):
        self.assertEqual(extrema.nearest_above(95, self.levels)["price"], 100.0)
        self.assertEqual(extrema.nearest_below(95, self.levels)["price"], 90.0)

    def test_none_when_no_level(self):
        self.assertIsNone(extrema.nearest_above(999, self.levels))
        self.assertIsNone(extrema.nearest_below(1, self.levels))


if __name__ == "__main__":
    unittest.main()
