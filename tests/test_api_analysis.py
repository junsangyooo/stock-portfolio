"""API: /api/analysis/chart returns live facts; graceful when no data."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import analysis


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(analysis.router)
    return TestClient(app)


class TestAnalysisAPI(unittest.TestCase):
    def setUp(self):
        self.c = _client()

    def test_chart_returns_facts_shape(self):
        # No local OHLCV -> still 200 with a well-formed fact document (has_data False).
        r = self.c.get("/api/analysis/chart/KR/005930")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["kind"], "chart")
        self.assertEqual(body["symbol_id"], "KR:005930")
        self.assertIn("timeframes", body)
        self.assertIn("1d", body["timeframes"])
        self.assertIn("queried_at", body)
        self.assertNotIn("error", body)

    def test_chart_no_judgment_fields(self):
        body = self.c.get("/api/analysis/chart/US/AAPL").json()
        for forbidden in ("verdict", "label", "score", "risk_flags"):
            self.assertNotIn(forbidden, body)

    def test_unknown_kind(self):
        body = self.c.get("/api/analysis/bogus/KR/005930").json()
        self.assertIn("error", body)

    def test_fundamental_falls_back_to_file(self):
        # no file -> exists False, but well-formed (does not call chart_agent)
        body = self.c.get("/api/analysis/fundamental/KR/005930").json()
        self.assertEqual(body["kind"], "fundamental")
        self.assertIn("exists", body)


if __name__ == "__main__":
    unittest.main()
