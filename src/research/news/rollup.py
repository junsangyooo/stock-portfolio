"""Long-memory cycles — weekly and monthly per-theme summaries.

Same shape, one layer apart:

    weekly   Sunday evening, right after the collection run.
             Reads that week's theme_daily rows (Mon–Sun KST).
    monthly  last Sunday of the month, right after the weekly run.
             Reads that month's theme_weekly rows.

Each is one call per theme over text that is already compressed, which is the
workload the on-host model exists for — no batching, no rush, once a week.

Compression only flows one way. The weekly cycle never re-reads articles and
the monthly cycle never re-reads days: if something was dropped a layer below,
it is gone, and that is the mechanism keeping long memory a fixed size instead
of an ever-growing pile.
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date, datetime, timedelta
from typing import Optional

from ...core.kst import KST
from ...core.llm import FAST_MODEL, _parse_json, local_or_claude
from .catalog import Catalog, load_catalog
from .db import connect

log = logging.getLogger(__name__)

_MAX_BULLETS = 10

_SYSTEM = ("너는 특정 산업 분야를 장기 추적하는 애널리스트다. 짧은 기간의 "
           "요약들을 받아 더 긴 기간의 흐름으로 압축한다. JSON만 출력한다.")


# ── period arithmetic ───────────────────────────────────────────────────────
def week_start_of(d: _date) -> _date:
    """The Monday of d's week (KST)."""
    return d - timedelta(days=d.weekday())


def last_complete_week(now: Optional[datetime] = None) -> _date:
    """Monday of the week that ends on the most recent Sunday.

    Run on Sunday evening, "this week" is the week that is ending now, so the
    Monday six days back. Run on any other day it is the previous full week.
    """
    d = (now or datetime.now(KST)).date()
    return week_start_of(d) if d.weekday() == 6 else week_start_of(d) - timedelta(days=7)


def is_last_sunday_of_month(d: _date) -> bool:
    return d.weekday() == 6 and (d + timedelta(days=7)).month != d.month


# ── weekly ──────────────────────────────────────────────────────────────────
def _daily_rows(theme_id: str, start: _date, end: _date) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT date, overview, n_items FROM theme_daily "
            "WHERE theme_id = ? AND date BETWEEN ? AND ? ORDER BY date",
            (theme_id, start.isoformat(), end.isoformat())).fetchall()]


def _weekly_rows(theme_id: str, month: str) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT week_start, summary, bullets FROM theme_weekly "
            "WHERE theme_id = ? AND week_start LIKE ? ORDER BY week_start",
            (theme_id, f"{month}-%")).fetchall()]


def _prompt(theme_name: str, span: str, unit: str, blocks: list[str]) -> str:
    body = "\n\n".join(blocks)
    return (
        f"분야: {theme_name}\n기간: {span}\n\n{unit} 요약들:\n\n{body}\n\n"
        f"위 {unit} 요약들을 이 기간 전체의 흐름 하나로 압축하라.\n"
        "- summary: 3~5문장. 무엇이 어떻게 움직였고 왜 중요한지.\n"
        "- bullets: 기억해 둘 사실·전개 최대 10개, 각 1~2문장.\n"
        "- 날짜별 나열 금지. 반복된 이야기는 하나로 묶어라.\n"
        '출력(JSON): {"summary": "...", "bullets": ["...", "..."]}'
    )


def _call(prompt: str, stage: str, backend) -> dict:
    if backend is not None:                       # tests inject their own
        raw = backend.call(_SYSTEM, prompt, model=FAST_MODEL)
    else:
        raw, _ = local_or_claude(_SYSTEM, prompt, claude_model=FAST_MODEL,
                                 stage=stage, validate=_parse_json)
    data = _parse_json(raw)
    bl = data.get("bullets") or []
    return {
        "summary": str(data.get("summary") or "").strip()[:2000],
        "bullets": [str(b).strip()[:300] for b in bl if str(b).strip()][:_MAX_BULLETS]
        if isinstance(bl, list) else [],
    }


def build_week(theme_id: str, theme_name: str, start: _date,
               backend=None) -> Optional[dict]:
    end = start + timedelta(days=6)
    rows = _daily_rows(theme_id, start, end)
    blocks = []
    for r in rows:
        try:
            bullets = json.loads(r["overview"])
        except (json.JSONDecodeError, TypeError):
            continue
        if bullets:
            blocks.append(f"[{r['date']}]\n" + "\n".join(f"- {b}" for b in bullets))
    if not blocks:
        return None
    out = _call(_prompt(theme_name, f"{start} ~ {end}", "일별", blocks),
                f"weekly:{theme_id}", backend)
    if not out["summary"]:
        return None
    return {"theme_id": theme_id, "week_start": start.isoformat(),
            "n_days": len(blocks), **out}


def build_month(theme_id: str, theme_name: str, month: str,
                backend=None) -> Optional[dict]:
    rows = _weekly_rows(theme_id, month)
    blocks = []
    for r in rows:
        try:
            bullets = json.loads(r["bullets"] or "[]")
        except (json.JSONDecodeError, TypeError):
            bullets = []
        block = f"[{r['week_start']} 주]\n{r['summary']}"
        if bullets:
            block += "\n" + "\n".join(f"- {b}" for b in bullets)
        blocks.append(block)
    if not blocks:
        return None
    out = _call(_prompt(theme_name, month, "주간", blocks),
                f"monthly:{theme_id}", backend)
    if not out["summary"]:
        return None
    return {"theme_id": theme_id, "month": month, "n_weeks": len(blocks), **out}


# ── persistence ─────────────────────────────────────────────────────────────
def write_week(row: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO theme_weekly (theme_id, week_start, summary, bullets, "
            "n_days, built_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(theme_id, week_start) DO UPDATE SET "
            "summary=excluded.summary, bullets=excluded.bullets, "
            "n_days=excluded.n_days, built_at=excluded.built_at",
            (row["theme_id"], row["week_start"], row["summary"],
             json.dumps(row["bullets"], ensure_ascii=False), row["n_days"],
             datetime.now(KST).isoformat()))
        c.commit()


def write_month(row: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO theme_monthly (theme_id, month, summary, bullets, "
            "n_weeks, built_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(theme_id, month) DO UPDATE SET "
            "summary=excluded.summary, bullets=excluded.bullets, "
            "n_weeks=excluded.n_weeks, built_at=excluded.built_at",
            (row["theme_id"], row["month"], row["summary"],
             json.dumps(row["bullets"], ensure_ascii=False), row["n_weeks"],
             datetime.now(KST).isoformat()))
        c.commit()


# ── runners ─────────────────────────────────────────────────────────────────
def run_weekly(start: Optional[_date] = None, *,
               catalog: Optional[Catalog] = None, backend=None) -> dict:
    catalog = catalog or load_catalog()
    start = start or last_complete_week()
    built, empty, failed = 0, 0, []
    for t in catalog.themes.values():
        try:
            row = build_week(t.theme_id, t.name_kr, start, backend=backend)
        except Exception as e:  # noqa: BLE001
            log.warning("weekly %s failed: %s", t.theme_id, str(e)[:200])
            failed.append(t.theme_id)
            continue
        if row is None:
            empty += 1
            continue
        write_week(row)
        built += 1
    return {"cycle": "weekly", "week_start": start.isoformat(),
            "built": built, "empty": empty, "failed": failed}


def run_monthly(month: Optional[str] = None, *,
                catalog: Optional[Catalog] = None, backend=None) -> dict:
    catalog = catalog or load_catalog()
    month = month or datetime.now(KST).strftime("%Y-%m")
    built, empty, failed = 0, 0, []
    for t in catalog.themes.values():
        try:
            row = build_month(t.theme_id, t.name_kr, month, backend=backend)
        except Exception as e:  # noqa: BLE001
            log.warning("monthly %s failed: %s", t.theme_id, str(e)[:200])
            failed.append(t.theme_id)
            continue
        if row is None:
            empty += 1
            continue
        write_month(row)
        built += 1
    return {"cycle": "monthly", "month": month, "built": built,
            "empty": empty, "failed": failed}
