"""Read side of the qualitative pipeline — every query against news.db.

Seven call sites used to reach into the jsonl tree and state.json directly,
each with its own idea of what "this theme's state" meant. They all come here
now, so the shape is defined once and a schema change is one file.

The three views:

    feed()         the landing page's news + social cards and 「오늘의 요약」
    theme_state()  what a theme looks like right now — for the judge, the
                   account page's news tab and the analysis
    read_items()   raw articles for one theme, for the company-name miner

and for the news tab and the chat agent: symbol_news() (a symbol's themes),
search() / article() (the whole archive, untagged included),
theme_history() (daily/weekly/monthly rows) and catalog() (themes, sources).

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


# ── one symbol's news (the news tab and the chat agent) ─────────────────────
def symbol_news(symbol_id: str, include_macro: bool = False,
                mentions: bool = False) -> dict:
    """The news connected to a symbol: its mapped themes' state and their
    recent developments, newest first. No LLM.

    The news tab leaves `macro` out — background too generic for a list of
    connected items. The chat agent asks for it: it may see every collected
    article, and a rate decision is news about every holding. `mentions`
    adds the articles that name the symbol in their title (reverse.py).
    """
    from ...market.store import theme_store

    cat = load_catalog()
    rows = [(r["theme_id"], r["relation"], r["weight"])
            for r in theme_store.get(symbol_id)]

    themes_out, devs, seen = [], [], set()
    for theme_id, relation, weight in rows:
        if relation == "macro" and not include_macro:
            continue
        st = theme_state(theme_id)
        ct = cat.get(theme_id)
        nm = ct.name_kr if ct else theme_id
        themes_out.append({
            "theme_id": theme_id, "name_kr": nm, "relation": relation,
            "weight": weight, "sentiment": st.get("sentiment", {}),
            "watchlist": st.get("watchlist", []) if relation == "primary" else [],
        })
        for d in st.get("recent_developments", []):
            k = (d.get("url") or "").strip() or (d.get("point") or "")
            if not k or k in seen:
                continue
            seen.add(k)
            devs.append({
                "theme": theme_id, "theme_kr": nm,
                "date": d.get("date"), "point": d.get("point"),
                "lead_lag": d.get("lead_lag"), "weight": d.get("weight"),
                "url": d.get("url"),
            })
    devs.sort(key=lambda x: (x.get("date") or "", x.get("weight") or 0), reverse=True)
    out = {"symbol_id": symbol_id, "mapped": bool(rows),
           "themes": themes_out, "developments": devs[:30]}
    if mentions:
        from .reverse import mention_items
        out["mentions"] = mention_items(symbol_id)
    return out


# ── the whole archive (chat agent) ──────────────────────────────────────────
_PAGE_MAX = 50


def _themes_of(c, signal_ids: list[int]) -> dict:
    if not signal_ids:
        return {}
    out: dict = {}
    q = ",".join("?" * len(signal_ids))
    for r in c.execute(
            f"SELECT signal_id, theme_id, confidence FROM signal_themes "
            f"WHERE signal_id IN ({q}) ORDER BY confidence DESC", signal_ids):
        out.setdefault(r["signal_id"], []).append(
            {"theme_id": r["theme_id"], "confidence": r["confidence"]})
    return out


def search(q: Optional[str] = None, theme: Optional[str] = None,
           since: Optional[str] = None, until: Optional[str] = None,
           source: Optional[str] = None, min_importance: Optional[int] = None,
           limit: int = 20, offset: int = 0) -> dict:
    """Every stored article, filtered — untagged ones included.

    `q` is a literal substring of title, summary or body. `theme='none'`
    asks for the articles no theme took. `since`/`until` are KST days
    against `collected_on`, both inclusive. Newest first; `total` and
    `offset` let a caller page through.
    """
    where, args = [], []
    if q:
        where.append("(instr(s.title, ?) > 0 OR instr(coalesce(s.summary, ''), ?) > 0 "
                     "OR instr(coalesce(s.body, ''), ?) > 0)")
        args += [q, q, q]
    if theme == "none":
        where.append("NOT EXISTS (SELECT 1 FROM signal_themes t "
                     "WHERE t.signal_id = s.signal_id)")
    elif theme:
        where.append("EXISTS (SELECT 1 FROM signal_themes t "
                     "WHERE t.signal_id = s.signal_id AND t.theme_id = ?)")
        args.append(theme)
    if since:
        where.append("s.collected_on >= ?")
        args.append(since)
    if until:
        where.append("s.collected_on <= ?")
        args.append(until)
    if source:
        where.append("s.source_id = ?")
        args.append(source)
    if min_importance is not None:
        where.append("s.importance >= ?")
        args.append(int(min_importance))
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(_PAGE_MAX, int(limit)))
    offset = max(0, int(offset))
    with connect() as c:
        total = c.execute(f"SELECT count(*) FROM signals s {cond}", args).fetchone()[0]
        rows = c.execute(
            "SELECT s.signal_id, s.title, s.summary, s.url, s.source_id, "
            "       s.collected_on, s.published_at, s.importance, s.novelty "
            f"FROM signals s {cond} "
            "ORDER BY s.collected_on DESC, s.signal_id DESC LIMIT ? OFFSET ?",
            args + [limit, offset]).fetchall()
        themes = _themes_of(c, [r["signal_id"] for r in rows])
    items = [{**dict(r), "themes": themes.get(r["signal_id"], [])} for r in rows]
    return {"total": total, "offset": offset, "returned": len(items),
            "next_offset": offset + len(items) if offset + len(items) < total else None,
            "items": items}


def article(signal_id: int) -> Optional[dict]:
    """Every stored column of one article, with its themes and confidences."""
    with connect() as c:
        r = c.execute("SELECT * FROM signals WHERE signal_id = ?",
                      (int(signal_id),)).fetchone()
        if r is None:
            return None
        return {**dict(r), "themes": _themes_of(c, [r["signal_id"]]).get(r["signal_id"], [])}


_HISTORY = {
    "daily": ("theme_daily", "date",
              "date, overview, sentiment_score, sentiment_trend, watchlist, n_items",
              ("overview", "watchlist")),
    "weekly": ("theme_weekly", "week_start", "week_start, summary, bullets, n_days",
               ("bullets",)),
    "monthly": ("theme_monthly", "month", "month, summary, bullets, n_weeks",
                ("bullets",)),
}


def theme_history(theme_id: str, kind: str = "daily",
                  since: Optional[str] = None, until: Optional[str] = None,
                  limit: int = 30, offset: int = 0) -> dict:
    """One theme's daily, weekly or monthly summaries, newest first.
    `since`/`until` compare against that table's key (a day, a Monday,
    'YYYY-MM')."""
    if kind not in _HISTORY:
        raise ValueError(f"kind must be one of {sorted(_HISTORY)}")
    table, key, cols, json_cols = _HISTORY[kind]
    where, args = ["theme_id = ?"], [theme_id]
    if since:
        where.append(f"{key} >= ?")
        args.append(since)
    if until:
        where.append(f"{key} <= ?")
        args.append(until)
    limit = max(1, min(_PAGE_MAX, int(limit)))
    offset = max(0, int(offset))
    cond = " AND ".join(where)
    with connect() as c:
        total = c.execute(f"SELECT count(*) FROM {table} WHERE {cond}", args).fetchone()[0]
        rows = c.execute(f"SELECT {cols} FROM {table} WHERE {cond} "
                         f"ORDER BY {key} DESC LIMIT ? OFFSET ?",
                         args + [limit, offset]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        for k in json_cols:
            d[k] = _loads(d.get(k), [])
        items.append(d)
    return {"theme_id": theme_id, "kind": kind, "total": total, "offset": offset,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
            "items": items}


def catalog() -> dict:
    """The themes with their definitions, and every source with where its
    reading stands (last item taken, last good fetch, consecutive failures)."""
    from .catalog import load_sources
    cat = load_catalog()
    with connect() as c:
        cur = {r["source_id"]: dict(r) for r in c.execute(
            "SELECT source_id, last_fetched_at, last_ok_at, last_error, "
            "n_consecutive_fail FROM source_cursor")}
    return {
        "themes": [{"theme_id": t.theme_id, "name_kr": t.name_kr,
                    "definition": t.definition, "always_on": t.always_on,
                    "related": list(t.related)}
                   for t in cat.themes.values()],
        "sources": [{"source_id": f.source_id, "kind": f.kind, "group": f.group,
                     "lead_lag": f.lead_lag, "broad": f.broad,
                     **{k: v for k, v in cur.get(f.source_id, {}).items()
                        if k != "source_id"}}
                    for f in load_sources()],
    }
