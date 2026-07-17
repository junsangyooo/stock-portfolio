"""P0-B — chart verdict price validation.

The LLM quotes support/resistance/target prices; _validate_price / _coerce_levels
must snap them to deterministically-extracted structure anchors (within tol) or
flag llm_only when the model invented a price with no nearby fact.
"""
import unittest

from src.agents.chart import evaluate


def _facts():
    return {
        "structure": {
            "support_resistance": {"levels": [
                {"price": 100.0, "tests": 3},
                {"price": 110.0, "tests": 2},
            ]},
            "fibonacci": {"levels": {"0.618": 105.0}},
            "pivots": {"PP": 108.0, "R1": 112.0, "S1": 96.0},
        }
    }


class TestValidatePrice(unittest.TestCase):
    def setUp(self):
        self.anchors = evaluate._anchor_prices(_facts())

    def test_anchor_extraction(self):
        prices = sorted(p for p, _ in self.anchors)
        for expect in (96.0, 100.0, 105.0, 108.0, 110.0, 112.0):
            self.assertIn(expect, prices)

    def test_snap_within_tolerance(self):
        v = evaluate._validate_price(100.5, self.anchors)   # 0.5% off 100
        self.assertTrue(v["anchored"])
        self.assertEqual(v["price"], 100.0)
        self.assertEqual(v["llm_raw"], 100.5)

    def test_flag_llm_only_when_far(self):
        v = evaluate._validate_price(130.0, self.anchors)   # nearest 112 → 16% off
        self.assertFalse(v["anchored"])
        self.assertTrue(v["llm_only"])
        self.assertEqual(v["price"], 130.0)                 # kept, not dropped

    def test_none_price(self):
        self.assertIsNone(evaluate._validate_price(None, self.anchors)["price"])

    def test_no_anchors_passthrough(self):
        v = evaluate._validate_price(123.0, [])
        self.assertFalse(v["anchored"])
        self.assertFalse(v.get("llm_only"))


class TestCoerceLevels(unittest.TestCase):
    def test_levels_validated_against_facts(self):
        raw = {
            "supports": [{"price": 100.2, "tests": 3, "why": "swing low"}],
            "resistances": [{"price": 140.0, "why": "made up"}],
            "targets": [112.1, "심리 저항 150"],
            "entry_zone": "101~103", "invalidation": "98 이탈",
        }
        out = evaluate._coerce_levels(raw, _facts())
        self.assertTrue(out["supports"][0]["anchored"])
        self.assertEqual(out["supports"][0]["price"], 100.0)
        self.assertTrue(out["resistances"][0]["llm_only"])
        # numeric target snapped to pivot R1=112; string target passed through
        self.assertTrue(out["targets"][0]["anchored"])
        self.assertEqual(out["targets"][1], "심리 저항 150")
        self.assertEqual(out["entry_zone"], "101~103")

    def test_no_facts_safe(self):
        raw = {"supports": [{"price": 100.0}], "resistances": [], "targets": []}
        out = evaluate._coerce_levels(raw, None)
        self.assertEqual(out["supports"][0]["price"], 100.0)


if __name__ == "__main__":
    unittest.main()
