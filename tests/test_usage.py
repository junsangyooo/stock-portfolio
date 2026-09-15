"""The token ledger.

The weekly subscription ran out once with no way to say where it went. These
pin the two things that has to mean: every call leaves a line, and no failure
of the ledger can take down the call it was measuring.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.core import paths, usage
from src.core.kst import KST
from src.research import llm_backend

# Captured at import, before conftest replaces it — these tests exercise the
# real subprocess wrapper with a fake `subprocess.run`.
_REAL_CALL = llm_backend.ClaudeCliBackend.call

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=KST)


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    return tmp_path / "logs" / usage.FILENAME


def _backend(stdout: str, code: int = 0, seen: list | None = None):
    b = llm_backend.ClaudeCliBackend.__new__(llm_backend.ClaudeCliBackend)
    b.bin, b.model, b.stage = "claude", "sonnet", "analyze"

    def _run(argv, **kw):
        if seen is not None:
            seen.append(argv)
        return type("R", (), {"returncode": code, "stdout": stdout,
                              "stderr": ""})()
    return b, _run


_ENVELOPE = json.dumps({
    "result": "판정 결과", "num_turns": 1, "total_cost_usd": 0.0863,
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 18495,
              "cache_read_input_tokens": 50743, "output_tokens": 6800},
})


# ── the ledger ──────────────────────────────────────────────────────────────

def test_a_call_leaves_one_line_with_its_own_numbers(log_file, monkeypatch):
    b, run = _backend(_ENVELOPE, seen=(seen := []))
    monkeypatch.setattr(llm_backend.subprocess, "run", run)
    assert _REAL_CALL(b, "sys", "user") == "판정 결과"
    assert "--output-format" in seen[0] and "json" in seen[0]

    row = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert row["stage"] == "analyze" and row["ok"] is True
    assert row["cache_create"] == 18495 and row["cache_read"] == 50743
    assert row["out"] == 6800 and row["cost_usd"] == 0.0863


def test_an_envelope_we_cannot_read_still_returns_the_answer(log_file, monkeypatch):
    """Losing the accounting for one call must not lose the generation."""
    b, run = _backend("그냥 텍스트 답")
    monkeypatch.setattr(llm_backend.subprocess, "run", run)
    assert _REAL_CALL(b, "sys", "user") == "그냥 텍스트 답"
    row = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert row["envelope"] == "unreadable" and row["out"] == 0


def test_a_failed_call_is_recorded_before_it_raises(log_file, monkeypatch):
    """The week the limit ran out, the only trace was a job log. Now the
    refusal itself is a row."""
    b, run = _backend("You've hit your weekly limit", code=1)
    monkeypatch.setattr(llm_backend.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        _REAL_CALL(b, "sys", "user")
    row = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert row["ok"] is False and "weekly limit" in row["error"]


def test_the_ledger_never_breaks_the_run(tmp_path, monkeypatch):
    """An unwritable log is a lost measurement, not a lost cycle."""
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "file" / "logs")
    (tmp_path / "file").write_text("not a directory", encoding="utf-8")
    usage.record("analyze", "sonnet", usage={"output_tokens": 10})  # must not raise


# ── the report ──────────────────────────────────────────────────────────────

def _rows(*specs):
    for stage, day, out, cost, ok in specs:
        usage.record(stage, "sonnet", usage={"output_tokens": out},
                     cost_usd=cost, ok=ok, elapsed_s=60)
        # rewrite the timestamp: record stamps "now", these want a history
    path = usage.path()
    lines = path.read_text(encoding="utf-8").splitlines()
    out_lines = []
    for line, spec in zip(lines, specs):
        r = json.loads(line)
        r["ts"] = f"{spec[1]}T09:00:00+09:00"
        out_lines.append(json.dumps(r, ensure_ascii=False))
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def test_the_report_totals_by_stage_and_by_day():
    _rows(("analyze", "2026-09-15", 6800, 0.09, True),
          ("analyze", "2026-09-15", 7000, 0.09, True),
          ("plan", "2026-09-14", 30000, 0.40, False),
          ("news:enrich", "2026-09-10", 2000, 0.02, True))
    rep = usage.report(days=3, now=NOW)               # 09-13 onwards
    assert rep["total"]["calls"] == 3                 # 09-10 falls outside
    assert rep["by_stage"]["analyze"]["out"] == 13800
    assert rep["by_stage"]["plan"]["failed"] == 1
    assert list(rep["by_day"]) == ["2026-09-14", "2026-09-15"]
    # most expensive stage first — that is what the report is read for
    assert list(rep["by_stage"])[0] == "plan"


def test_a_torn_line_costs_one_call_not_the_file(log_file):
    usage.record("analyze", "sonnet", usage={"output_tokens": 5})
    with log_file.open("a", encoding="utf-8") as f:
        f.write("{반쪽짜리 줄\n")
    usage.record("plan", "sonnet", usage={"output_tokens": 7})
    assert usage.report(days=7)["total"]["calls"] == 2


def test_an_empty_ledger_reports_nothing_rather_than_failing():
    assert usage.report(days=7)["total"]["calls"] == 0
