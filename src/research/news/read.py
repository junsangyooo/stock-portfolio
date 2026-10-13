"""Read side of the qualitative pipeline — every query against news.db.

Seven call sites used to reach into the jsonl tree and state.json directly,
each with its own idea of what "this theme's state" meant. They all come here
now, so the shape is defined once and a schema change is one file.

The three views:

    feed()         the landing page's news + social cards and 「오늘의 요약」
    theme_state()  what a theme looks like right now — for the judge, the
                   account page's news tab and the analysis
    read_items()   raw articles for one theme, for the company-name miner

`theme_state` replaces state.json. Its slow-moving half (horizon summary) is
the weekly roll-up, its fast half (recent developments) is a window over the
articles themselves, and sentiment/watchlist come from the daily overview.
`last_shift` is no longer written by a model — a change of sentiment direction
is visible in the stored trend history, so it is computed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

from ...core.kst import KST
from .catalog import load_catalog
from .db import connect

_HORIZON_DAYS = 7      # window of "recent developments"
_RECENT_CAP = 15
_ACTIVE_DAYS = 3       # a theme is live if it produced an overview this recently


# ── helpers ─────────────────────────────────────────────────────────────────
def published_iso(published) -> Optional[str]:
    """Normalize a feed 'published' string (RFC822 or ISO) to ISO-8601."""
    if not published or not isinstance(published, str):
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(published).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _favicon(u: str) -> Optional[str]:
    """Source favicon — the last-resort thumbnail when an item has no image."""
    try:
        host = urlparse(u or "").netloc
    except ValueError:
        host = ""
    return f"https://www.google.com/s2/favicons?domain={host}&sz=64" if host else None


def _loads(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default
    return v if isinstance(v, type(default)) else default


def _days_back(n: int, now: Optional[datetime] = None) -> list[str]:
    d = (now or datetime.now(KST)).date()
    return [(d - timedelta(days=i)).isoformat() for i in range(n)]


# ── themes with something to say ────────────────────────────────────────────
def active_themes(days: int = _ACTIVE_DAYS,
                  now: Optional[datetime] = None) -> list[str]:
    """theme_ids that produced an overview in the last `days`.

    Replaces the holdings-driven `live_themes`: collection no longer depends on
    what anybody owns, so "live" now means "this field actually had news",
    which is the question every caller was really asking.
    """
    since = _days_back(days, now)[-1]
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT theme_id FROM theme_daily WHERE date >= ? "
            "ORDER BY theme_id", (since,)).fetchall()]


# ── theme state (replaces state.json) ───────────────────────────────────────
def _recent_developments(theme_id: str, now: datetime) -> list[dict]:
    since = _days_back(_HORIZON_DAYS, now)[-1]
    with connect() as c:
        rows = c.execute(
            "SELECT s.collected_on, s.summary, s.title, s.lead_lag, "
            "       s.importance, s.novelty, s.url "
            "FROM signals s JOIN signal_themes st USING (signal_id) "
            "WHERE st.theme_id = ? AND s.collected_on >= ? "
            "  AND s.enriched_at IS NOT NULL "
            "ORDER BY s.importance DESC, s.collected_on DESC LIMIT ?",
            (theme_id, since, _RECENT_CAP)).fetchall()
    return [{
        "date": r["collected_on"],
        "point": r["summary"] or r["title"],
        "lead_lag": r["lead_lag"],
        "weight": round((r["importance"] or 3) / 5.0, 2),
        "novelty": bool(r["novelty"]),
        "url": r["url"],
    } for r in rows]


def _last_shift(theme_id: str) -> str:
    """The most recent date the sentiment direction changed.

    The old state file had a model write this sentence and carry it forward.
    The trend is stored per day now, so the change is a fact in the table
    rather than something to be remembered.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT date, sentiment_trend FROM theme_daily "
            "WHERE theme_id = ? AND sentiment_trend IS NOT NULL "
            "ORDER BY date DESC LIMIT 30", (theme_id,)).fetchall()
    for newer, older in zip(rows, rows[1:]):
        if newer["sentiment_trend"] != older["sentiment_trend"]:
            return (f"{newer['date']}: {older['sentiment_trend']}"
                    f"→{newer['sentiment_trend']}")
    return ""


def theme_state(theme_id: str, now: Optional[datetime] = None) -> dict:
    """Everything known about one field right now. Always returns a dict —
    an untouched theme yields the empty shape rather than None."""
    now = now or datetime.now(KST)
    with connect() as c:
        day = c.execute(
            "SELECT date, overview, sentiment_score, sentiment_trend, watchlist "
            "FROM theme_daily WHERE theme_id = ? ORDER BY date DESC LIMIT 1",
            (theme_id,)).fetchone()
        week = c.execute(
            "SELECT week_start, summary FROM theme_weekly WHERE theme_id = ? "
            "ORDER BY week_start DESC LIMIT 1", (theme_id,)).fetchone()
        month = c.execute(
            "SELECT month, summary FROM theme_monthly WHERE theme_id = ? "
            "ORDER BY month DESC LIMIT 1", (theme_id,)).fetchone()

    # The weekly roll-up is the long memory. Before the first Sunday there is
    # none, so today's bullets stand in rather than leaving the judge blank.
    horizon = (week["summary"] if week else
               " ".join(_loads(day["overview"], []) if day else []))
    score = day["sentiment_score"] if day else None
    return {
        "theme_id": theme_id,
        "updated": day["date"] if day else None,
        "horizon_summary": horizon or "",
        "recent_developments": _recent_developments(theme_id, now),
        "watchlist": _loads(day["watchlist"], []) if day else [],
        "sentiment": {"score": score if score is not None else 0.0,
                      "trend": (day["sentiment_trend"] if day else None) or "flat"},
        "last_shift": _last_shift(theme_id),
        "week_summary": week["summary"] if week else "",
        "month_summary": month["summary"] if month else "",
    }


# ── landing feed ────────────────────────────────────────────────────────────
def _summary_rows(theme_ids: list[str], name_kr: dict,
                  now: datetime) -> list[dict]:
    """Per theme, the newest overview from today or yesterday — before the dawn
    batch today's row does not exist yet."""
    dates = _days_back(2, now)
    out = []
    with connect() as c:
        for tid in theme_ids:
            row = c.execute(
                "SELECT date, overview FROM theme_daily WHERE theme_id = ? "
                "AND date IN (?,?) ORDER BY date DESC LIMIT 1",
                (tid, *dates)).fetchone()
            if not row:
                continue
            bullets = _loads(row["overview"], [])
            if bullets:
                out.append({"theme": tid, "theme_kr": name_kr.get(tid, tid),
                            "date": row["date"], "bullets": bullets})
    return out


def feed(limit: int = 60, days: int = 3,
         now: Optional[datetime] = None) -> dict:
    """The landing page's cards.

    news = lagging media, social = coincident chatter. arXiv (leading) is left
    out: it feeds per-symbol evaluation, not headlines.

    One article carries every theme it belongs to, so the badge list is a join
    instead of the merge-by-url pass the jsonl layout needed.
    """
    now = now or datetime.now(KST)
    cat = load_catalog()
    name_kr = {t.theme_id: t.name_kr for t in cat.themes.values()}
    since = _days_back(days, now)[-1]

    # One query per lane, not one query split afterwards: news outnumbers
    # social most days, so a shared LIMIT hands the whole budget to news and
    # the social column comes back empty even though items exist.
    lanes: dict[str, list] = {}
    with connect() as c:
        for lane in ("lagging", "coincident"):
            lanes[lane] = c.execute(
                "SELECT signal_id, url, title, source_id, lead_lag, "
                "       published_at, fetched_at, image, summary, importance "
                "FROM signals WHERE collected_on >= ? AND lead_lag = ? "
                "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
                (since, lane, limit)).fetchall()
        ids = [r["signal_id"] for rows in lanes.values() for r in rows]
        themes: dict[int, list[dict]] = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            marks = ",".join("?" * len(part))
            for t in c.execute(
                    f"SELECT signal_id, theme_id FROM signal_themes "
                    f"WHERE signal_id IN ({marks}) ORDER BY theme_id", part):
                themes.setdefault(t["signal_id"], []).append(
                    {"id": t["theme_id"],
                     "kr": name_kr.get(t["theme_id"], t["theme_id"])})

    news, social = [], []
    for r in lanes["lagging"] + lanes["coincident"]:
        th = themes.get(r["signal_id"], [])
        card = {
            "theme": th[0]["id"] if th else None,
            "theme_kr": th[0]["kr"] if th else None,
            "themes": th,
            "title": r["title"], "url": r["url"] or "",
            "source": r["source_id"], "lead_lag": r["lead_lag"],
            "published": r["published_at"],
            "published_iso": r["published_at"],
            "fetched_at": r["fetched_at"],
            "importance": r["importance"],
            "gist": r["summary"],
            "image": r["image"],
            "favicon": _favicon(r["url"] or ""),
        }
        (social if r["lead_lag"] == "coincident" else news).append(card)

    live = active_themes(now=now) or sorted(cat.themes)
    return {
        "news": news[:limit],
        "social": social[:limit],
        "summary": _summary_rows(live, name_kr, now),
        "themes": [{"id": t, "name_kr": name_kr.get(t, t)} for t in live],
        "yyyymm": now.strftime("%Y%m"),
    }


# ── raw articles, for the company-name miner ────────────────────────────────
def read_items(theme_id: str, days: int = 14,
               now: Optional[datetime] = None) -> list[dict]:
    """One theme's articles over the last `days`, newest first."""
    since = _days_back(max(days, 1), now)[-1]
    with connect() as c:
        rows = c.execute(
            "SELECT s.title, s.body, s.summary, s.url, s.collected_on "
            "FROM signals s JOIN signal_themes st USING (signal_id) "
            "WHERE st.theme_id = ? AND s.collected_on >= ? "
            "ORDER BY s.collected_on DESC, s.signal_id DESC",
            (theme_id, since)).fetchall()
    return [{"title": r["title"], "summary": r["body"] or r["summary"] or "",
             "url": r["url"], "date": r["collected_on"]} for r in rows]
