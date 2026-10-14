"""The sources dashboard's data: is every source and scheduled job delivering?

Composes the shared layers' own health reads with the two things only this
app knows — which symbols are held (market facts are checked for those) and
which jobs it schedules (deploy/portfolio.cron, each with its own log).

Any account's session can open the dashboard, so nothing here names a
holding: market facts are counts, and a job summary's lists (stale symbols,
failed accounts) are reduced to their length.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Optional

from ..core import kst, paths
from ..market import health as market_health
from ..research.news import read as news
from ..universe import search as universe
from .store import holding_store

# (job, log file, hours between runs before it counts as late, summaries kept,
# a key only the run's final summary has, hours a run may take) — the
# schedule in deploy/portfolio.cron. The news cycle prints a summary per stage
# on the way; only its last one carries "collect". A run without its final
# summary is "running" until its ceiling, then a run that died (measured: a
# news cycle 67–85 min, an analysis batch of 20 under an hour).
JOBS = (
    ("news", "news.log", 14, 1, "collect", 3),
    ("backup", "backup.log", 26, 1, None, 1),
    ("fundamental", "fundamental.log", 26, 1, None, 1),
    ("etf", "etf.log", 26, 1, None, 1),
    ("snapshot", "snapshot.log", 14, 1, None, 1),
    ("screener", "screener.log", 26, 1, None, 1),
    ("universe", "universe.log", 26, 1, None, 1),
    ("analysis", "analysis.log", 4 * 24, 1, None, 3),   # weekdays only
)
# Keys whose non-empty value means something in the run went wrong.
_BAD = ("failed", "accounts_failed", "accounts_without_snapshot", "quotes_stale", "ohlcv_errors",
        "kept_previous", "errors", "error")


def _summaries(text: str) -> list:
    """JSON objects a job printed as its summary: a `{` at the start of a
    line, or after the news cycle's `[hh:mm:ss]` prefix. Nested objects of a
    pretty-printed summary start indented, so they are never taken alone."""
    dec, out, pos = json.JSONDecoder(), [], 0
    for line in text.splitlines(keepends=True):
        start = 0 if line.startswith("{") else (
            line.find("{") if line.startswith("[") and "]" in line[:12] else -1)
        if start >= 0:
            try:
                obj, _ = dec.raw_decode(text[pos + start:])
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError:
                pass
        pos += len(line)
    return out


def _counts_only(v):
    """Lists become their length; a server path keeps only its file name."""
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        return {k: _counts_only(x) for k, x in v.items()}
    if isinstance(v, str) and v.startswith("/"):
        return os.path.basename(v)
    return v


def _problems(summary: dict) -> list:
    found = []

    def walk(d, path=""):
        for k, v in d.items():
            if k in _BAD and v not in (None, [], {}, 0, ""):
                found.append(f"{path}{k}")
            elif isinstance(v, dict):
                walk(v, f"{path}{k}.")
        if isinstance(d.get("total"), int) and isinstance(d.get("ok"), int) \
                and d["ok"] < d["total"]:
            found.append(f"{path}ok {d['ok']}/{d['total']}")
    walk(summary)
    return found


def _last_run(text: str) -> tuple[Optional[float], Optional[str]]:
    """When the last run began — the `@run <ISO>` line deploy/portfolio.cron
    writes before each job — and what the log holds after it (None: no such
    line, a log from before the marker)."""
    at = text.rfind("@run ")
    while at > 0 and text[at - 1] != "\n":
        at = text.rfind("@run ", 0, at)
    if at < 0:
        return None, None
    line, _, rest = text[at:].partition("\n")
    try:
        return datetime.fromisoformat(line[5:].strip()).timestamp(), rest
    except ValueError:
        return None, rest


def job(name: str, log: str, late_h: float, keep: int,
        final: Optional[str] = None, max_run_h: float = 1) -> dict:
    """The newest summaries a job logged (today's file, else yesterday's
    rotated copy), whether its last run is still going, and whether that run
    is older than its schedule.

    The run time is the job's own `@run` line. A log without one falls back
    to the live file's mtime; the rotated copy's mtime is the rotation time
    (09:00 KST for every job), so there the time is unknown."""
    for path in (paths.LOGS_DIR / log, paths.LOGS_DIR / f"{log}.1"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ran, rest = _last_run(text)
        if not ran:
            ran = os.path.getmtime(path) if path.name == log and text else None
        found = _summaries(text)
        if not found and not ran:
            continue
        age_h = (time.time() - ran) / 3600 if ran else None
        if rest is not None:
            tail = _summaries(rest)
            done = bool(tail) and len(tail[-1]) > 1 and (final is None or final in tail[-1])
        else:
            done = bool(found)
        last = found[-keep:]
        problems = [p for s in last for p in _problems(s)]
        # Some jobs open with a one-key line ({"etf": 22}); a run that dies
        # before its summary leaves only that, which must not read as ok.
        if last and len(last[-1]) == 1:
            problems.append("끝 요약 없음")
        if not done and age_h is not None and age_h <= max_run_h:
            status = "running"
        elif not done and "끝 요약 없음" not in problems:
            problems.append("끝 요약 없음")
            status = "problem"
        else:
            status = ("late" if age_h is not None and age_h > late_h
                      else "problem" if problems else "ok")
        return {"job": name, "status": status, "problems": problems,
                "written_at": (datetime.fromtimestamp(ran, kst.KST).isoformat()
                               if ran else None),
                "age_h": round(age_h, 1) if age_h is not None else None,
                "summaries": [_counts_only(s) for s in last]}
    return {"job": name, "status": "none", "problems": [], "written_at": None,
            "age_h": None, "summaries": []}


def _file_age_h(p) -> Optional[float]:
    try:
        return round((time.time() - os.path.getmtime(p)) / 3600, 1)
    except OSError:
        return None


def report() -> dict:
    held = holding_store.all_symbol_ids()
    uni = universe.stats()
    return {
        "asof": kst.now().isoformat(),
        "news": news.source_health(),
        "pipeline": news.pipeline_health(),
        "market": {"held": len(held), **market_health.symbols(held),
                   "screener": market_health.screener()},
        "universe": {"total": uni["total"], "by_region": uni["by_region"],
                     "kr_age_h": _file_age_h(paths.UNIVERSE_DIR / "kr.json"),
                     "us_age_h": _file_age_h(paths.UNIVERSE_DIR / "us.json")},
        "jobs": [job(*j) for j in JOBS],
    }
