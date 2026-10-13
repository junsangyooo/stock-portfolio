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

    def test_removed_kinds_are_not_routes(self):
        """Per-domain verdicts went away on 2026-09-01: no generic {kind}
        route serves a well-formed 'nothing here' that reads as 'not analyzed
        yet' and sends the UI polling forever."""
        for path in ("/api/analysis/bogus/KR/005930",
                     "/api/analysis/fundamental/KR/005930",
                     "/api/analysis/chart/KR/005930/status"):
            self.assertEqual(self.c.get(path).status_code, 404, path)

    def test_the_fundamental_tab_reads_its_own_facts_route(self):
        body = self.c.get("/api/analysis/fundamental/KR/005930/facts").json()
        self.assertNotIn("error", body)
        self.assertEqual(body.get("symbol_id"), "KR:005930")

    def test_only_the_analysis_is_runnable(self):
        self.assertEqual(self.c.post("/api/analysis/chart/KR/005930/run").status_code, 404)
        body = self.c.get("/api/analysis/analysis/KR/005930/status").json()
        self.assertEqual(body["state"], "idle")

    def test_stored_analysis_is_read_from_the_kind_route(self):
        """One route for the stored result; there is no /verdict twin."""
        body = self.c.get("/api/analysis/analysis/KR/005930").json()
        self.assertEqual(body["kind"], "analysis")
        self.assertFalse(body["exists"])
        r = self.c.get("/api/analysis/analysis/KR/005930/verdict")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
