"""plan_run_store + planner_job — the read side and the batch.

The card grid reads one row per account, so what matters here is that the row
it reads is the right one: the newest pass that actually produced something,
never a failure, and never another account's.
"""
from __future__ import annotations

import json

import pytest

from src.portfolio import planner_job
from src.portfolio.store import account_store, db, plan_run_store


def _row(conn, account_id, date, slot, status="ok", **over):
    payload = {
        "notes_by_symbol": json.dumps({"KR:005930": {"stance": "hold",
                                                     "line_ko": "유지"}}),
        "accepted": json.dumps({"suggest": [{"plan_id": 1, "what": "익절 상향",
                                             "why": "타깃"}]}),
        "rejected": json.dumps([{"item": {}, "why": "현금 부족"}]),
        "portfolio_note": f"{date} {slot}",
    }
    payload.update(over)
    conn.execute(
        "INSERT INTO plan_runs (account_id, date, slot, status, model,"
        " context_chars, diff, accepted, rejected, notes_by_symbol,"
        " portfolio_note, elapsed_s, created_at)"
        " VALUES (?, ?, ?, ?, 'm', 100, '{}', ?, ?, ?, ?, 1.0, ?)",
        (account_id, date, slot, status, payload["accepted"],
         payload["rejected"], payload["notes_by_symbol"],
         payload["portfolio_note"], int(date.replace("-", ""))))
    conn.commit()


@pytest.fixture
def book(tmp_path, monkeypatch):
    # db.py binds DB_PATH at import, so patching core.paths does nothing and
    # the test writes into the real local book — patch the module's own name,
    # the way test_api_gate and test_keeper_alerts do.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "portfolio.db")
    monkeypatch.setattr(db, "_initialized", False)
    account_store.create("isa", "ISA", "ISA")
    account_store.create("gen", "일반", "GENERAL")
    with db.connect() as c:
        yield c


def test_latest_is_the_newest_pass_that_produced_something(book):
    _row(book, "isa", "2026-09-05", "morning")
    _row(book, "isa", "2026-09-07", "morning", status="parse_error",
         notes_by_symbol=None, accepted=None, rejected=None,
         portfolio_note=None)
    got = plan_run_store.latest("isa")
    assert got["date"] == "2026-09-05"          # not the newer failure
    assert got["notes_by_symbol"]["KR:005930"]["stance"] == "hold"
    assert got["suggest"][0]["what"] == "익절 상향" and got["n_rejected"] == 1


def test_latest_never_crosses_accounts(book):
    _row(book, "gen", "2026-09-07", "morning")
    assert plan_run_store.latest("isa") is None
    assert plan_run_store.latest("gen")["date"] == "2026-09-07"


def test_a_day_prefers_the_morning_over_the_preview(book):
    _row(book, "isa", "2026-09-07", "manual")
    _row(book, "isa", "2026-09-07", "morning")
    got = plan_run_store.on("isa", "2026-09-07")
    assert got["slot"] == "morning" and got["rejected"][0]["why"] == "현금 부족"


def test_a_day_with_only_a_preview_still_answers(book):
    _row(book, "isa", "2026-09-07", "manual")
    assert plan_run_store.on("isa", "2026-09-07")["slot"] == "manual"


def test_a_failure_is_invisible_to_the_grid_but_not_to_the_status_read(book):
    _row(book, "isa", "2026-09-07", "morning", status="llm_error")
    assert plan_run_store.latest("isa") is None
    assert plan_run_store.status_row("isa", "2026-09-07",
                                     "morning")["status"] == "llm_error"
    assert plan_run_store.dates("isa") == ["2026-09-07"]


def test_the_batch_walks_every_human_account_in_page_order(book, monkeypatch,
                                                           capsys):
    seen = []

    def _run(conn, account_id, **k):
        seen.append((account_id, k["slot"], k["dry"]))
        return {"status": "ok", "account_id": account_id, "date": "2026-09-07",
                "n_add": 0, "n_modify": 0, "n_abandon": 0, "n_suggest": 0,
                "n_rejected": 0, "elapsed_s": 1.0, "applied": True}

    monkeypatch.setattr(planner_job.planner, "run", _run)
    sent = []
    monkeypatch.setattr(planner_job.discord, "send",
                        lambda t, **k: sent.append(t) or True)
    monkeypatch.setattr("sys.argv", ["planner_job"])
    assert planner_job.main() == 0
    assert seen == [("isa", "morning", False), ("gen", "morning", False)]
    assert len(sent) == 1 and "isa" in sent[0] and "gen" in sent[0]


def test_one_account_falling_over_does_not_stop_the_rest(book, monkeypatch):
    def _run(conn, account_id, **k):
        if account_id == "isa":
            raise RuntimeError("claude CLI AUTH error")
        return {"status": "ok", "account_id": account_id, "date": "d",
                "n_add": 0, "n_modify": 0, "n_abandon": 0, "n_suggest": 0,
                "n_rejected": 0, "elapsed_s": 1.0, "applied": True}

    monkeypatch.setattr(planner_job.planner, "run", _run)
    sent = []
    monkeypatch.setattr(planner_job.discord, "send",
                        lambda t, **k: sent.append(t) or True)
    monkeypatch.setattr("sys.argv", ["planner_job"])
    assert planner_job.main() == 1              # non-zero: cron must notice
    assert "llm_error" in sent[0] and "gen" in sent[0]


def test_dry_runs_the_manual_slot(book, monkeypatch):
    seen = []
    monkeypatch.setattr(planner_job.planner, "run",
                        lambda conn, a, **k: seen.append(k) or {
                            "status": "ok", "account_id": a, "date": "d",
                            "n_add": 0, "n_modify": 0, "n_abandon": 0,
                            "n_suggest": 0, "n_rejected": 0, "elapsed_s": 1.0,
                            "applied": False})
    monkeypatch.setattr(planner_job.discord, "send", lambda t, **k: True)
    monkeypatch.setattr("sys.argv", ["planner_job", "--dry", "--account", "isa"])
    assert planner_job.main() == 0
    assert seen == [{"slot": "manual", "model": planner_job.MODEL, "dry": True}]
