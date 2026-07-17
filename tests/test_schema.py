"""IndicatorFact schema — objective fact, no judgment."""
import unittest

from src.agents.chart.schema import IndicatorFact, STATE_NO_DATA


class TestIndicatorFact(unittest.TestCase):
    def test_basic_fact(self):
        f = IndicatorFact("rsi", "momentum", "1d", "oversold", value=28.4,
                          evidence={"rsi": 28.4}, asof="2026-05-24")
        self.assertEqual(f.state, "oversold")
        self.assertEqual(f.value, 28.4)
        self.assertTrue(f.has_data)

    def test_no_data(self):
        f = IndicatorFact("rsi", "momentum", "1d", STATE_NO_DATA)
        self.assertFalse(f.has_data)

    def test_to_dict_has_no_judgment_fields(self):
        f = IndicatorFact("macd", "trend", "1w", "golden_cross", value=0.3,
                          asof="2026-05-24")
        d = f.to_dict()
        self.assertEqual(d["state"], "golden_cross")
        self.assertEqual(d["category"], "trend")
        # objective fact only — no score/label/confidence/vote
        for forbidden in ("score", "label", "confidence", "vote", "signal", "strength"):
            self.assertNotIn(forbidden, d)


if __name__ == "__main__":
    unittest.main()
