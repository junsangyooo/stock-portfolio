"""portfolio.health — job summaries from the logs, and nothing that names a holding."""
from __future__ import annotations

import json

from src.core import paths
from src.portfolio import health
from src.portfolio.store import account_store, holding_store

PRETTY = json.dumps({"date": "2026-10-14", "total": 22, "ok": 21, "results": [{"x": 1}]}, indent=2)
NEWS = ('[06:00:02] collect …\ncollect fiercebiotech failed: 403\n'
        '[06:01:04]   {"date": "2026-10-14", "sources": 53, "failed": ["fiercebiotech"]}\n'
        '[06:55:10]   {"date": "2026-10-14", "collect": {"failed": ["fiercebiotech"]}, '
        '"daily": {"built": 23, "failed": []}}\n')


def test_summaries_take_whole_objects_not_nested_ones():
    found = health._summaries("noise\n" + PRETTY + "\n")
    assert found == [json.loads(PRETTY)]
    assert len(health._summaries(NEWS)) == 2


def test_problems_and_counts():
    assert health._problems(json.loads(PRETTY)) == ["ok 21/22"]
    last = health._summaries(NEWS)[-1]
    assert health._problems(last) == ["collect.failed"]
    assert health._counts_only({"quotes_stale": ["KR:1", "KR:2"], "n": 3}) == \
        {"quotes_stale": 2, "n": 3}


def test_job_reads_the_rotated_log_when_today_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    (tmp_path / "snapshot.log").write_text("")
    (tmp_path / "snapshot.log.1").write_text(
        '{"accounts_ok": ["a"], "accounts_failed": [], "quotes_stale": ["KR:005930"]}\n')
    j = health.job("snapshot", "snapshot.log", 14, 1)
    assert j["status"] == "problem" and j["problems"] == ["quotes_stale"]
    assert j["summaries"] == [{"accounts_ok": 1, "accounts_failed": 0, "quotes_stale": 1}]
    assert health.job("analysis", "analysis.log", 96, 1)["status"] == "none"


def test_report_names_no_holding(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(paths, "UNIVERSE_DIR", tmp_path)
    account_store.create("alice", "alice", "GENERAL")
    holding_store.upsert("alice", "KR:005930", qty=1, avg_cost=1)
    r = health.report()
    assert r["market"]["held"] == 1 and r["market"]["bars"]["missing"] == 1
    assert "KR:005930" not in json.dumps(r, default=str)


def test_a_run_that_stopped_after_its_opening_line_is_a_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    (tmp_path / "etf.log").write_text('{"etf": 22}\nTraceback ...\n')
    assert health.job("etf", "etf.log", 26, 1)["problems"] == ["끝 요약 없음"]


def test_server_paths_keep_only_the_file_name():
    out = health._counts_only({"news": {"path": "/home/ubuntu/x/data/backup/news.db.wed"}})
    assert out == {"news": {"path": "news.db.wed"}}
