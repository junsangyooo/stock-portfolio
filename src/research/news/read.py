"""Read side of the qualitative pipeline — every query against news.db.

Seven call sites used to reach into the jsonl tree and state.json directly,
each with its own idea of what "this theme's state" meant. They all come here
now, so the shape is defined once and a schema change is one file.

The three views:

    feed()         the landing page's news + social cards and 「오늘의 요약」
    theme_state()  what a theme looks like right now — for the account page's
                   news tab and the analysis
    read_items()   raw articles for one theme, for the company-name miner

and for the news tab and the chat agent: symbol_news() (a symbol's themes),
search() / article() (the whole archive, untagged included),
theme_history() (daily/weekly/monthly rows) and catalog() (themes, sources).
reverse.py's title scans read through important_titles() / titles_naming().

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

from ...core import kst
from .catalog import load_catalog
from .db import connect

_HORIZON_DAYS = 7      # window of "recent developments"
_RECENT_CAP = 15
_ACTIVE_DAYS = 3       # a theme is live if it produced an overview this recently


# ── helpers ─────────────────────────────────────────────────────────────────
def window_start(days: int, now: Optional[datetime] = None) -> str:
    """First KST day of a `days`-long window ending today: 'YYYY-MM-DD'.
    Compared with `>=` against collected_on / date, so today counts as day 1."""
    d = (now or kst.now()).date() - timedelta(days=max(days - 1, 0))
    return d.isoformat()


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


# ── themes with something to say ────────────────────────────────────────────
def active_themes(days: int = _ACTIVE_DAYS,
                  now: Optional[datetime] = None) -> list[str]:
    """theme_ids that produced an overview in the last `days`.

    Replaces the holdings-driven `live_themes`: collection no longer depends on
    what anybody owns, so "live" now means "this field actually had news",
    which is the question every caller was really asking.
    """
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT theme_id FROM theme_daily WHERE date >= ? "
            "ORDER BY theme_id", (window_start(days, now),)).fetchall()]


# ── theme state (replaces state.json) ───────────────────────────────────────
def _recent_developments(c, theme_id: str, now: datetime) -> list[dict]:
    rows = c.execute(
        "SELECT s.collected_on, s.summary, s.title, s.lead_lag, "
        "       s.importance, s.novelty, s.url "
        "FROM signals s JOIN signal_themes st USING (signal_id) "
        "WHERE st.theme_id = ? AND s.collected_on >= ? "
        "  AND s.enriched_at IS NOT NULL "
        "ORDER BY s.importance DESC, s.collected_on DESC LIMIT ?",
        (theme_id, window_start(_HORIZON_DAYS, now), _RECENT_CAP)).fetchall()
    return [{
        "date": r["collected_on"],
        "point": r["summary"] or r["title"],
        "lead_lag": r["lead_lag"],
        "weight": round((r["importance"] or 3) / 5.0, 2),
        "novelty": bool(r["novelty"]),
        "url": r["url"],
    } for r in rows]


def _last_shift(c, theme_id: str) -> str:
    """The most recent date the sentiment direction changed.

    The old state file had a model write this sentence and carry it forward.
    The trend is stored per day now, so the change is a fact in the table
    rather than something to be remembered.
    """
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
        recent = _recent_developments(c, theme_id, now or kst.now())
        last_shift = _last_shift(c, theme_id)

    # The weekly roll-up is the long memory. Before the first Sunday there is
    # none, so today's bullets stand in rather than leaving the reader blank.
    horizon = (week["summary"] if week else
               " ".join(_loads(day["overview"], []) if day else []))
    score = day["sentiment_score"] if day else None
    return {
        "theme_id": theme_id,
        "updated": day["date"] if day else None,
        "horizon_summary": horizon or "",
        "recent_developments": recent,
        "watchlist": _loads(day["watchlist"], []) if day else [],
        "sentiment": {"score": score if score is not None else 0.0,
                      "trend": (day["sentiment_trend"] if day else None) or "flat"},
        "last_shift": last_shift,
        "week_summary": week["summary"] if week else "",
        "month_summary": month["summary"] if month else "",
    }


# ── landing feed ────────────────────────────────────────────────────────────
def _summary_rows(theme_ids: list[str], name_kr: dict,
                  now: datetime) -> list[dict]:
    """Per theme, the newest overview from today or yesterday — before the dawn
    batch today's row does not exist yet."""
    today = now.date()
    with connect() as c:
        # ascending, so today's row overwrites yesterday's in the dict
        newest = {r["theme_id"]: r for r in c.execute(
            "SELECT theme_id, date, overview FROM theme_daily "
            "WHERE date IN (?,?) ORDER BY date",
            (today.isoformat(), (today - timedelta(days=1)).isoformat()))}
    out = []
    for tid in theme_ids:
        row = newest.get(tid)
        bullets = _loads(row["overview"], []) if row else []
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
    now = now or kst.now()
    cat = load_catalog()
    name_kr = {t.theme_id: t.name_kr for t in cat.themes.values()}
    since = window_start(days, now)

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
    with connect() as c:
        rows = c.execute(
            "SELECT s.title, s.body, s.summary, s.url, s.collected_on "
            "FROM signals s JOIN signal_themes st USING (signal_id) "
            "WHERE st.theme_id = ? AND s.collected_on >= ? "
            "ORDER BY s.collected_on DESC, s.signal_id DESC",
            (theme_id, window_start(days, now))).fetchall()
    return [{"title": r["title"], "summary": r["body"] or r["summary"] or "",
             "url": r["url"], "date": r["collected_on"]} for r in rows]


def important_titles(days: int, min_importance: int) -> list[tuple]:
    """(title, collected_on) of every article at or above `min_importance`
    inside the window — the corpus reverse.recent_mentions scans."""
    with connect() as c:
        return [tuple(r) for r in c.execute(
            "SELECT title, collected_on FROM signals "
            "WHERE collected_on >= ? AND importance >= ?",
            (window_start(days), min_importance))]


def titles_naming(names: list[str], days: int) -> list[dict]:
    """Articles inside the window whose title contains any of `names`,
    newest first — reverse.mention_items' candidates."""
    hit = " OR ".join("instr(title, ?) > 0" for _ in names)
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT signal_id, title, url, source_id, collected_on, importance "
            f"FROM signals WHERE collected_on >= ? AND ({hit}) "
            "ORDER BY collected_on DESC, signal_id DESC",
            [window_start(days), *names])]


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


def _page(limit, offset) -> tuple[int, int]:
    return max(1, min(_PAGE_MAX, int(limit))), max(0, int(offset))


def _next_offset(offset: int, returned: int, total: int) -> Optional[int]:
    return offset + returned if offset + returned < total else None


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
    limit, offset = _page(limit, offset)
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
            "next_offset": _next_offset(offset, len(items), total),
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
    limit, offset = _page(limit, offset)
    cond = " AND ".join(where)
    with connect() as c:
        total = c.execute(f"SELECT count(*) FROM {table} WHERE {cond}", args).fetchone()[0]
        rows = c.execute(f"SELECT {cols} FROM {table} WHERE {cond} "
                         f"ORDER BY {key} DESC LIMIT ? OFFSET ?",
                         args + [limit, offset]).fetchall()
    items = [{**dict(r), **{k: _loads(r[k], []) for k in json_cols}}
             for r in rows]
    return {"theme_id": theme_id, "kind": kind, "total": total, "offset": offset,
            "next_offset": _next_offset(offset, len(items), total),
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


# ── source health (the sources dashboard) ───────────────────────────────────
# Per source: every sweep's result (collect_runs), what it delivered today and
# over the last RUNS_SHOWN sweeps, how much of it mattered, and the fields it
# mainly covers. The cursor alone cannot judge a source: it forgets a failure
# the moment the next fetch succeeds, and a feed that answers 200 with
# nothing new reads as a success there.
RUNS_SHOWN = 14         # two sweeps a day → a week
FLAKY_FAILS = 3         # failures among the shown sweeps that make a source flaky
QUIET_MIN_DAYS = 3      # never call a source quiet sooner than this
GAP_DAYS = 90           # history the usual gap between new items is read from
RARE_30D = 20           # fewer items in 30 days: too few to judge its value
IMPORTANT = 4           # importance (1–5) from which an item counts as important
MAIN_SHARE = 0.3        # a field is a source's main one at this share of its items
REVIEW_SHARE = 0.07     # important share at or below this marks a removal candidate …
REVIEW_MIN_MAIN = 3     # … only in a field with at least this many main sources
REVIEW_MIN_DAYS = 7     # and only with items on this many of the 30 days (a source
                        # whose address was just fixed has one busy day, not a record)


def _quiet_after(days: list[str]) -> int:
    """How many days without a new item is unusual for this source: the 90th
    percentile of its gaps between days with items, at least QUIET_MIN_DAYS.
    BEA publishes every week or so and must not read as broken on day four."""
    ds = sorted({datetime.fromisoformat(d).date() for d in days})
    gaps = sorted((b - a).days for a, b in zip(ds, ds[1:]))
    if not gaps:
        return QUIET_MIN_DAYS
    return max(QUIET_MIN_DAYS, gaps[min(len(gaps) - 1, int(len(gaps) * 0.9))])


def _source_status(cur: dict, runs: list, last_day: Optional[str],
                   quiet_after: int, now: datetime) -> tuple[str, str]:
    fetched = cur.get("last_fetched_at")
    if fetched:
        try:
            t = datetime.fromisoformat(fetched)
            t = t.replace(tzinfo=kst.KST) if t.tzinfo is None else t
            if t > now:
                return ("collect_error", f"받은 글 시각이 미래({t.astimezone(kst.KST):%m/%d})로 "
                        "저장돼 새 글을 건너뛰고 있어요")
        except ValueError:
            pass
    seen = [r for r in runs if r is not None]
    if not cur and not seen:
        return "never", "아직 수집 기록이 없어요"
    fails = cur.get("n_consecutive_fail") or 0
    if fails:
        return "failing", f"{fails}번 연속 실패"
    n_fail = sum(not r["ok"] for r in seen)
    if n_fail >= FLAKY_FAILS:
        return "flaky", f"최근 {len(seen)}번 중 {n_fail}번 실패"
    if not last_day:
        return "quiet", "받은 글이 아직 없어요"
    gap = (now.date() - datetime.fromisoformat(last_day).date()).days
    if gap > quiet_after:
        return "quiet", f"{gap}일째 새 글 없음 · 평소에는 {quiet_after}일 안에 와요"
    return "ok", ""


def _counts(c, cond: str, args: list) -> dict:
    """Per source: items collected, enriched, important and untagged."""
    return {r["source_id"]: dict(r) for r in c.execute(
        "SELECT s.source_id, count(*) collected, count(s.enriched_at) enriched, "
        f"coalesce(sum(s.importance >= {IMPORTANT}), 0) important, "
        "coalesce(sum(s.enriched_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "
        "signal_themes t WHERE t.signal_id = s.signal_id)), 0) untagged "
        f"FROM signals s WHERE {cond} GROUP BY s.source_id", args)}


def _flags(rows: list[dict]) -> dict:
    """Notes beside a source — material for a decision, never a verdict.
    `rare`: too few items to judge. `only`: the one main source of a field.
    `review`: low important share in a field with enough main sources, one
    per field (removing two at once would leave the field short)."""
    main: dict = {}
    for r in rows:
        for t in r["themes"]["main"]:
            main.setdefault(t, []).append(r)
    out: dict = {r["source_id"]: [] for r in rows}
    for r in rows:
        if r["d30"]["n"] < RARE_30D:
            out[r["source_id"]].append({"kind": "rare"})
    for t, rs in main.items():
        if len(rs) == 1:
            out[rs[0]["source_id"]].append({"kind": "only", "theme": t})
            continue
        if len(rs) < REVIEW_MIN_MAIN:
            continue
        cands = [r for r in rs if r["group"] == "news" and r["d30"]["n"] >= RARE_30D
                 and sum(1 for n in r["d30"]["daily"] if n) >= REVIEW_MIN_DAYS
                 and r["d30"]["important"] / r["d30"]["n"] <= REVIEW_SHARE
                 and not any(f["kind"] == "review" for f in out[r["source_id"]])]
        if cands:
            r = min(cands, key=lambda x: (x["d30"]["important"] / x["d30"]["n"], x["d30"]["n"]))
            out[r["source_id"]].append({"kind": "review", "theme": t,
                                        "from": len(rs), "to": len(rs) - 1})
    return out


def source_health(days: int = 30, now: Optional[datetime] = None) -> dict:
    """Every configured source, field coverage and what falls outside the
    fields — the read side of the sources dashboard. Counts and titles only;
    nothing here knows a holding."""
    from .catalog import load_sources
    from .collect_all import MAX_PER_SOURCE
    now = now or kst.now()
    today = now.date().isoformat()
    since = window_start(days, now)
    day_keys = [(now.date() - timedelta(days=i)).isoformat()
                for i in range(days - 1, -1, -1)]
    feeds = load_sources()
    with connect() as c:
        cur = {r["source_id"]: dict(r) for r in c.execute(
            "SELECT source_id, last_fetched_at, last_ok_at, last_error, "
            "n_consecutive_fail FROM source_cursor")}
        run_ats = [r[0] for r in c.execute(
            "SELECT DISTINCT run_at FROM collect_runs ORDER BY run_at DESC LIMIT ?",
            (RUNS_SHOWN,))][::-1]
        runs: dict = {}
        if run_ats:
            for r in c.execute("SELECT * FROM collect_runs WHERE run_at >= ?", (run_ats[0],)):
                runs.setdefault(r["source_id"], {})[r["run_at"]] = {
                    "at": r["run_at"], "ok": bool(r["ok"]), "error": r["error"],
                    "written": r["written"], "filtered": r["filtered"], "over": r["over"]}
        failed_days: dict = {}
        for r in c.execute("SELECT source_id, substr(run_at, 1, 10) d FROM collect_runs "
                           "WHERE run_at >= ? GROUP BY 1, 2 HAVING max(ok) = 0", (since,)):
            failed_days[r["source_id"]] = failed_days.get(r["source_id"], 0) + 1
        per_day: dict = {}
        for r in c.execute("SELECT source_id, collected_on, count(*) n FROM signals "
                           "WHERE collected_on >= ? GROUP BY 1, 2",
                           (window_start(GAP_DAYS, now),)):
            per_day.setdefault(r["source_id"], {})[r["collected_on"]] = r["n"]
        c_today = _counts(c, "collected_on = ?", [today])
        # The week is the shown sweeps, not calendar days: from 06:00 to 17:00
        # the oldest shown sweep is a 17:00 one, and counting its whole day
        # would take that morning's items in as if collected outside a sweep.
        # fetched_at is the sweep's own clock (collect_all's now).
        week_cond = ("s.fetched_at >= ?", [run_ats[0]]) if run_ats else ("collected_on = ?", [today])
        c_week = _counts(c, *week_cond)
        c_30 = _counts(c, "collected_on >= ?", [since])
        themes30: dict = {}
        for r in c.execute("SELECT s.source_id, t.theme_id, count(*) n FROM signals s "
                           "JOIN signal_themes t ON t.signal_id = s.signal_id "
                           "WHERE s.collected_on >= ? GROUP BY 1, 2", (since,)):
            themes30.setdefault(r["source_id"], {})[r["theme_id"]] = r["n"]
        theme_n30 = {r[0]: r[1] for r in c.execute(
            "SELECT t.theme_id, count(*) FROM signal_themes t JOIN signals s "
            "ON s.signal_id = t.signal_id WHERE s.collected_on >= ? GROUP BY 1", (since,))}
        examples = [dict(r) for r in c.execute(
            "SELECT s.signal_id, s.title, s.source_id FROM signals s "
            f"WHERE {week_cond[0]} AND s.enriched_at IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM signal_themes t WHERE t.signal_id = s.signal_id) "
            "ORDER BY s.signal_id DESC LIMIT 3", week_cond[1])]

    zero = {"collected": 0, "enriched": 0, "important": 0, "untagged": 0}
    out = []
    for f in feeds:
        sid = f.source_id
        c_ = cur.get(sid, {})
        mine = runs.get(sid, {})
        shown = [mine.get(at) for at in run_ats]
        today_runs = [r for r in shown if r and r["at"][:10] == today]
        days_with = sorted(per_day.get(sid, {}))
        daily = [per_day.get(sid, {}).get(d, 0) for d in day_keys]
        n30 = c_30.get(sid, zero)
        tagged = themes30.get(sid, {})
        dist = sorted(((t, round(n / n30["enriched"], 3)) for t, n in tagged.items()
                       if n30["enriched"]), key=lambda x: -x[1])
        main = [t for t, p in dist[:2] if p >= MAIN_SHARE] or [t for t, _ in dist[:1]]
        wk = c_week.get(sid, zero)
        written7 = sum(r["written"] for r in shown if r)
        status, reason = _source_status(c_, shown, days_with[-1] if days_with else None,
                                        _quiet_after(days_with), now)
        ok_days = days - failed_days.get(sid, 0)
        out.append({
            "source_id": sid, "kind": f.kind, "group": f.group, "broad": f.broad,
            "url": f.url, "cap": f.max_items or MAX_PER_SOURCE,
            "status": status, "reason": reason,
            "last_ok_at": c_.get("last_ok_at"), "last_error": c_.get("last_error"),
            "n_consecutive_fail": c_.get("n_consecutive_fail") or 0,
            "last_item_on": days_with[-1] if days_with else None,
            "runs": shown,
            "today": {"written": sum(r["written"] for r in today_runs),
                      "filtered": sum(r["filtered"] for r in today_runs),
                      **{k: c_today.get(sid, zero)[k] for k in zero}},
            "week": {"written": written7,
                     "filtered": sum(r["filtered"] for r in shown if r),
                     "failed": sum(not r["ok"] for r in shown if r),
                     # items stored outside the scheduled sweeps (a manual run)
                     "outside": max(0, wk["collected"] - written7) if run_ats else 0,
                     **{k: wk[k] for k in zero}},
            "d30": {"n": n30["collected"], "important": n30["important"],
                    "enriched": n30["enriched"], "daily": daily,
                    "usual_per_day": round(n30["collected"] / ok_days, 1) if ok_days > 0 else None},
            "themes": {"main": main, "dist": dist[:6]},
        })
    flags = _flags(out)
    for r in out:
        r["flags"] = flags[r["source_id"]]
    main_of: dict = {}
    for r in out:
        for t in r["themes"]["main"]:
            main_of.setdefault(t, []).append(r["source_id"])
    return {
        "days": day_keys, "runs": run_ats, "today": today, "cap": MAX_PER_SOURCE,
        "sources": out,
        "themes": [{"theme_id": t.theme_id, "name_kr": t.name_kr,
                    "n30": theme_n30.get(t.theme_id, 0),
                    "main_sources": main_of.get(t.theme_id, [])}
                   for t in load_catalog().themes.values()],
        "unclassified": {
            "filtered": sum(r["week"]["filtered"] for r in out),
            "untagged": sum(r["week"]["untagged"] for r in out),
            "enriched": sum(r["week"]["enriched"] for r in out),
            "examples": examples},
    }


def pipeline_health(now: Optional[datetime] = None) -> dict:
    """Where the stages after collection stand: today's items and how many
    are summarized, the enrichment queue, the newest daily/weekly/monthly
    summaries, and how many themes the newest day covered."""
    now = now or kst.now()
    with connect() as c:
        today = c.execute("SELECT count(*), count(enriched_at) FROM signals "
                          "WHERE collected_on = ?", (now.date().isoformat(),)).fetchone()
        pending = c.execute("SELECT count(*) FROM signals WHERE enriched_at IS NULL "
                            "AND collected_on >= ?", (window_start(7, now),)).fetchone()[0]
        last_enriched = c.execute("SELECT max(enriched_at) FROM signals").fetchone()[0]
        day = c.execute("SELECT date, count(*) n FROM theme_daily "
                        "GROUP BY date ORDER BY date DESC LIMIT 1").fetchone()
        week = c.execute("SELECT week_start, max(built_at) FROM theme_weekly "
                         "GROUP BY week_start ORDER BY week_start DESC LIMIT 1").fetchone()
        month = c.execute("SELECT max(month) FROM theme_monthly").fetchone()[0]
    return {"collected_today": today[0], "enriched_today": today[1],
            "pending_enrich": pending, "last_enriched_at": last_enriched,
            "daily_date": day["date"] if day else None,
            "daily_themes": day["n"] if day else 0,
            "n_themes": len(load_catalog().themes),
            "weekly_start": week[0] if week else None,
            "weekly_built_at": week[1] if week else None, "monthly": month}
