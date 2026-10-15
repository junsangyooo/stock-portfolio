"""deploy/portfolio.cron is the truth of this app's schedule (정의서 §1.1 규칙 6)."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
_CRON = (_DEPLOY / "portfolio.cron").read_text(encoding="utf-8")
sys.path.insert(0, str(_DEPLOY))
import install_cron  # noqa: E402

_JOBS = [l for l in _CRON.splitlines() if l.strip() and not l.startswith("#")]


def test_every_job_names_a_module_that_exists():
    mods = [m for l in _JOBS for m in re.findall(r"python -m (src\.[\w.]+)", l)]
    assert mods
    for m in mods:
        assert importlib.util.find_spec(m) is not None, m


def test_no_unescaped_percent():
    """crontab cuts a command at an unescaped % (ops notes §8)."""
    assert all(re.search(r"(?<!\\)%", l) is None for l in _JOBS)


def test_block_is_replaced_and_the_rest_kept():
    old = ("0 1 * * * other app\n"
           "# >>> portfolio (old) >>>\n0 2 * * * stale job\n# <<< portfolio <<<\n"
           "*/5 * * * * autotrade tick\n")
    new = install_cron.replace_block(old, _CRON)
    assert "stale job" not in new
    assert new.startswith("0 1 * * * other app\n")
    assert new.rstrip().endswith("autotrade tick")
    assert new.count("src.research.news.cycle_job") == 2


def test_a_crontab_without_the_block_is_refused():
    with pytest.raises(SystemExit):
        install_cron.replace_block("0 1 * * * other app\n", _CRON)


def test_the_sources_page_watches_every_scheduled_job():
    """portfolio/health.JOBS reads each job's log; a cron line it does not
    know is never checked, and a job it lists without a cron line reads as
    late forever."""
    from src.portfolio import health
    cron_logs = {m for l in _JOBS for m in re.findall(r">> data/logs/([\w.-]+\.log) 2>&1", l)}
    assert cron_logs == {j[1] for j in health.JOBS}
    # every logged job writes its @run line to the same log it appends to
    for l in _JOBS:
        logs = set(re.findall(r"data/logs/([\w.-]+\.log)", l))
        assert len(logs) <= 1, l
