"""Async analysis job runner — state transitions + dedup."""
import time
import unittest

from src.api import analysis_jobs


class TestAnalysisJobs(unittest.TestCase):
    def setUp(self):
        analysis_jobs._jobs.clear()

    def test_idle_when_never_started(self):
        self.assertEqual(analysis_jobs.get_status("chart", "KR:000001")["state"], "idle")

    def test_done_transition_and_result_date(self):
        analysis_jobs.start("chart", "KR:000002", lambda: {"date": "2026-06-13", "status": "ok"})
        for _ in range(50):
            st = analysis_jobs.get_status("chart", "KR:000002")
            if st["state"] == "done":
                break
            time.sleep(0.02)
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["date"], "2026-06-13")

    def test_error_transition_captures_message(self):
        def boom():
            raise RuntimeError("claude CLI timeout (420s)")
        analysis_jobs.start("judge", "KR:000003", boom)
        for _ in range(50):
            st = analysis_jobs.get_status("judge", "KR:000003")
            if st["state"] == "error":
                break
            time.sleep(0.02)
        self.assertEqual(st["state"], "error")
        self.assertIn("timeout", st["error"])

    def test_duplicate_start_is_noop_while_running(self):
        gate = {"go": False}
        def slow():
            while not gate["go"]:
                time.sleep(0.01)
            return {"date": "2026-06-13", "status": "ok"}
        r1 = analysis_jobs.start("news", "KR:000004", slow)
        r2 = analysis_jobs.start("news", "KR:000004", slow)   # while first runs
        self.assertEqual(r1["state"], "started")
        self.assertEqual(r2["state"], "running")
        gate["go"] = True
        for _ in range(50):
            if analysis_jobs.get_status("news", "KR:000004")["state"] == "done":
                break
            time.sleep(0.02)
        self.assertEqual(analysis_jobs.get_status("news", "KR:000004")["state"], "done")


if __name__ == "__main__":
    unittest.main()
