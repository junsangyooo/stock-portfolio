"""Tests for the source-driven collection pipeline (news.db).

Covers the parts that fail silently if they are wrong: the cursor, the
keyword gate on whole-site feeds, dedup across sources, what the enrichment
pass is allowed to stamp, and the week/month arithmetic the roll-ups pick
their period from.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from src.agents.news import collect_all, daily, enrich, rollup
from src.agents.news.catalog import Feed, load_catalog, load_sources
from src.agents.news.db import connect
from src.agents.news.sources.base import RawItem
from src.core.kst import KST

NOW = datetime(2026, 8, 27, 6, 0, tzinfo=KST)


def _item(title, url, published=None, summary=""):
    return RawItem(source_id="s1", kind="rss", title=title, url=url,
                   published=published, summary=summary)


def _feed(**kw):
    base = dict(source_id="s1", kind="rss", group="news",
                url="http://x/feed", broad=False)
    base.update(kw)
    return Feed(**base)


@pytest.fixture
def no_images(monkeypatch):
    """og:image scraping is network; the collector must not need it."""
    monkeypatch.setattr(collect_all, "enrich_images", lambda items, **kw: 0)


def _collect(monkeypatch, items, *, feed=None, cursor=None, keywords=None):
    monkeypatch.setattr(collect_all, "fetch_rss",
                        lambda url, sid, timeout=15.0: list(items))
    return collect_all.collect_one(feed or _feed(), keywords or [], cursor,
                                   now=NOW)


def _stored():
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT dedup_key, title, source_id, lead_lag, collected_on, "
            "published_at FROM signals ORDER BY signal_id")]


# ── the sources registry ────────────────────────────────────────────────────

def test_sources_load_and_lead_lag_comes_from_the_group():
    feeds = load_sources()
    assert feeds, "sources.json is empty"
    lanes = {f.group: f.lead_lag for f in feeds}
    assert lanes["news"] == "lagging"
    assert lanes.get("research", "leading") == "leading"
    assert lanes.get("social", "coincident") == "coincident"


def test_every_source_id_is_unique():
    ids = [f.source_id for f in load_sources()]
    assert len(ids) == len(set(ids))


# ── collection ──────────────────────────────────────────────────────────────

def test_new_items_are_stored_once(monkeypatch, no_images):
    stat = _collect(monkeypatch, [_item("A", "http://a"), _item("B", "http://b")])
    assert stat["written"] == 2
    rows = _stored()
    assert [r["title"] for r in rows] == ["A", "B"]
    assert rows[0]["collected_on"] == "2026-08-27"
    assert rows[0]["lead_lag"] == "lagging"


def test_a_second_run_stores_nothing_new(monkeypatch, no_images):
    items = [_item("A", "http://a")]
    assert _collect(monkeypatch, items)["written"] == 1
    again = _collect(monkeypatch, items)
    assert again["written"] == 0 and again["dup"] == 1
    assert len(_stored()) == 1


def test_the_cursor_skips_items_the_feed_dates_before_it(monkeypatch, no_images):
    items = [_item("old", "http://old", published="2026-08-20T00:00:00+09:00"),
             _item("new", "http://new", published="2026-08-26T00:00:00+09:00")]
    stat = _collect(monkeypatch, items, cursor="2026-08-25T00:00:00+09:00")
    assert stat["stale"] == 1 and stat["written"] == 1
    assert [r["title"] for r in _stored()] == ["new"]


def test_an_undated_item_survives_the_cursor(monkeypatch, no_images):
    """Plenty of feeds omit the timestamp; dropping those would silently lose
    whole sources rather than a few articles."""
    stat = _collect(monkeypatch, [_item("nodate", "http://n")],
                    cursor="2026-08-25T00:00:00+09:00")
    assert stat["written"] == 1 and stat["stale"] == 0


def test_the_cursor_advances_to_the_newest_item_seen(monkeypatch, no_images):
    _collect(monkeypatch, [_item("a", "http://a",
                                 published="2026-08-26T00:00:00+09:00")])
    with connect() as c:
        row = c.execute("SELECT last_fetched_at, n_consecutive_fail "
                        "FROM source_cursor WHERE source_id='s1'").fetchone()
    assert row["last_fetched_at"].startswith("2026-08-26")
    assert row["n_consecutive_fail"] == 0


def test_a_broad_feed_keeps_only_keyword_matches(monkeypatch, no_images):
    items = [_item("삼성 HBM4 양산", "http://a"),
             _item("오늘의 날씨", "http://b")]
    stat = _collect(monkeypatch, items, feed=_feed(broad=True),
                    keywords=["hbm"])
    assert stat["written"] == 1 and stat["filtered"] == 1
    assert [r["title"] for r in _stored()] == ["삼성 HBM4 양산"]


def test_a_narrow_feed_is_not_keyword_gated(monkeypatch, no_images):
    stat = _collect(monkeypatch, [_item("오늘의 날씨", "http://b")],
                    keywords=["hbm"])
    assert stat["written"] == 1


def test_short_latin_keywords_need_letter_boundaries(monkeypatch, no_images):
    """'ai' must not match inside 'Taiwan' — the old collector's bug class."""
    items = [_item("Taiwan daily briefing", "http://a"),
             _item("AI chip demand", "http://b")]
    stat = _collect(monkeypatch, items, feed=_feed(broad=True), keywords=["ai"])
    assert stat["written"] == 1
    assert [r["title"] for r in _stored()] == ["AI chip demand"]


def test_the_same_article_from_two_sources_is_one_row(monkeypatch, no_images):
    """The old layout stored a copy per theme folder; this is the point of
    keying on the article instead."""
    _collect(monkeypatch, [_item("shared", "http://shared")])
    _collect(monkeypatch, [_item("shared", "http://shared")],
             feed=_feed(source_id="s2"))
    assert len(_stored()) == 1


def test_a_dead_feed_is_recorded_not_swallowed(monkeypatch, no_images):
    def boom(url, sid, timeout=15.0):
        raise OSError("connection refused")
    monkeypatch.setattr(collect_all, "fetch_rss", boom)
    stat = collect_all.collect_one(_feed(), [], None, now=NOW)
    assert stat["error"] and stat["written"] == 0
    with connect() as c:
        row = c.execute("SELECT last_error, n_consecutive_fail FROM "
                        "source_cursor WHERE source_id='s1'").fetchone()
    assert "connection refused" in row["last_error"]
    assert row["n_consecutive_fail"] == 1


# ── enrichment ──────────────────────────────────────────────────────────────

class _Backend:
    """Returns whatever payload it was handed, recording the prompts."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def call(self, system, user, timeout_s=0, model=None):
        self.calls.append(user)
        return json.dumps(self.payload)


def _seed(monkeypatch, n=2):
    monkeypatch.setattr(collect_all, "enrich_images", lambda items, **kw: 0)
    monkeypatch.setattr(
        collect_all, "fetch_rss",
        lambda url, sid, timeout=15.0: [_item(f"T{i}", f"http://{i}")
                                        for i in range(n)])
    collect_all.collect_one(_feed(), [], None, now=NOW)
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT signal_id FROM signals ORDER BY signal_id")]


def test_enrichment_writes_all_four_fields(monkeypatch):
    ids = _seed(monkeypatch, 1)
    be = _Backend({"r": [{"i": 0, "s": "요약", "imp": 4, "nov": True,
                          "t": ["semiconductor"]}]})
    out = enrich.run(backend=be)
    assert out["enriched"] == 1
    with connect() as c:
        row = c.execute("SELECT summary, importance, novelty, enriched_at "
                        "FROM signals WHERE signal_id=?", (ids[0],)).fetchone()
        themes = [r[0] for r in c.execute(
            "SELECT theme_id FROM signal_themes WHERE signal_id=?", (ids[0],))]
    assert row["summary"] == "요약" and row["importance"] == 4
    assert row["novelty"] == 1 and row["enriched_at"]
    assert themes == ["semiconductor"]


def test_unknown_theme_ids_are_dropped(monkeypatch):
    ids = _seed(monkeypatch, 1)
    be = _Backend({"r": [{"i": 0, "s": "x", "imp": 3, "nov": False,
                          "t": ["semiconductor", "not_a_theme"]}]})
    enrich.run(backend=be)
    with connect() as c:
        themes = [r[0] for r in c.execute(
            "SELECT theme_id FROM signal_themes WHERE signal_id=?", (ids[0],))]
    assert themes == ["semiconductor"]


def test_importance_is_clamped(monkeypatch):
    _seed(monkeypatch, 1)
    enrich.run(backend=_Backend({"r": [{"i": 0, "s": "x", "imp": 99, "t": []}]}))
    with connect() as c:
        assert c.execute("SELECT importance FROM signals").fetchone()[0] == 5


def test_an_article_the_model_skipped_stays_pending(monkeypatch):
    """It must be retried, not stamped as enriched with nothing in it."""
    _seed(monkeypatch, 2)
    enrich.run(backend=_Backend({"r": [{"i": 0, "s": "x", "imp": 3, "t": []}]}))
    assert len(enrich.pending()) == 1


def test_a_failed_batch_leaves_everything_pending(monkeypatch):
    _seed(monkeypatch, 2)

    class _Boom:
        def call(self, *a, **kw):
            raise RuntimeError("cli exit 1")
    out = enrich.run(backend=_Boom())
    assert out["enriched"] == 0 and out["failed"] == 2
    assert len(enrich.pending()) == 2


def test_enrichment_is_not_repeated(monkeypatch):
    _seed(monkeypatch, 1)
    be = _Backend({"r": [{"i": 0, "s": "x", "imp": 3, "t": []}]})
    enrich.run(backend=be)
    second = enrich.run(backend=be)
    assert second["pending"] == 0 and len(be.calls) == 1


# ── daily overview ──────────────────────────────────────────────────────────

def test_daily_overview_reads_only_enriched_items(monkeypatch):
    _seed(monkeypatch, 1)
    assert daily.items_for("semiconductor", "2026-08-27") == []
    enrich.run(backend=_Backend(
        {"r": [{"i": 0, "s": "칩 수요", "imp": 5, "t": ["semiconductor"]}]}))
    got = daily.items_for("semiconductor", "2026-08-27")
    assert len(got) == 1 and got[0]["summary"] == "칩 수요"


def test_daily_overview_is_written_and_rebuildable(monkeypatch):
    _seed(monkeypatch, 1)
    enrich.run(backend=_Backend(
        {"r": [{"i": 0, "s": "칩 수요", "imp": 5, "t": ["semiconductor"]}]}))
    cat = load_catalog()
    daily.run("2026-08-27", catalog=cat, theme="semiconductor",
              backend=_Backend({"overview": ["오늘 반도체는 …"]}))
    with connect() as c:
        row = c.execute("SELECT overview, n_items FROM theme_daily").fetchone()
    assert json.loads(row["overview"]) == ["오늘 반도체는 …"]
    # a rebuild replaces rather than duplicating
    daily.run("2026-08-27", catalog=cat, theme="semiconductor",
              backend=_Backend({"overview": ["고쳐 쓴 요약"]}))
    with connect() as c:
        rows = c.execute("SELECT overview FROM theme_daily").fetchall()
    assert len(rows) == 1 and json.loads(rows[0][0]) == ["고쳐 쓴 요약"]


def test_sentiment_and_watchlist_ride_on_the_same_call(monkeypatch):
    """They replace the rolling state file; one call must produce all four."""
    _seed(monkeypatch, 1)
    enrich.run(backend=_Backend(
        {"r": [{"i": 0, "s": "칩 수요", "imp": 5, "t": ["semiconductor"]}]}))
    be = _Backend({"overview": ["오늘 반도체는 …"],
                   "sentiment": {"score": 0.6, "trend": "improving"},
                   "watchlist": ["HBM4 양산 일정", "관세 결정"]})
    daily.run("2026-08-27", theme="semiconductor", backend=be)
    assert len(be.calls) == 1
    with connect() as c:
        row = c.execute("SELECT * FROM theme_daily").fetchone()
    assert row["sentiment_score"] == 0.6
    assert row["sentiment_trend"] == "improving"
    assert json.loads(row["watchlist"]) == ["HBM4 양산 일정", "관세 결정"]


@pytest.mark.parametrize("sent,score,trend", [
    ({"score": 9.9, "trend": "improving"}, 1.0, "improving"),   # clamped
    ({"score": -5, "trend": "improving"}, -1.0, "improving"),
    ({"score": "n/a", "trend": "improving"}, None, "improving"),
    ({"score": 0.2, "trend": "vibes"}, 0.2, None),              # unknown trend
    ("not a dict", None, None),
])
def test_bad_sentiment_is_dropped_not_guessed(monkeypatch, sent, score, trend):
    _seed(monkeypatch, 1)
    enrich.run(backend=_Backend(
        {"r": [{"i": 0, "s": "x", "imp": 3, "t": ["semiconductor"]}]}))
    row = daily.build_theme("semiconductor", "반도체", "2026-08-27",
                            backend=_Backend({"overview": ["b"],
                                              "sentiment": sent}))
    assert row["sentiment_score"] == score and row["sentiment_trend"] == trend
    assert row["overview"] == ["b"], "a bad sentiment must not cost the bullets"


def test_a_theme_with_nothing_collected_writes_no_row(monkeypatch):
    out = daily.run("2026-08-27", theme="semiconductor",
                    backend=_Backend({"overview": ["never used"]}))
    assert out["built"] == 0 and out["empty"] == 1


# ── roll-up period arithmetic ───────────────────────────────────────────────

def test_sunday_evening_rolls_up_the_week_that_is_ending():
    sunday = datetime(2026, 8, 30, 20, 0, tzinfo=KST)
    assert rollup.last_complete_week(sunday) == date(2026, 8, 24)


def test_any_other_day_rolls_up_the_previous_full_week():
    wednesday = datetime(2026, 8, 26, 20, 0, tzinfo=KST)
    assert rollup.last_complete_week(wednesday) == date(2026, 8, 17)


@pytest.mark.parametrize("d,expected", [
    (date(2026, 8, 30), True),    # last Sunday of August
    (date(2026, 8, 23), False),   # one more Sunday follows
    (date(2026, 8, 29), False),   # Saturday
])
def test_last_sunday_of_month(d, expected):
    assert rollup.is_last_sunday_of_month(d) is expected


def test_weekly_reads_the_days_and_writes_one_row():
    with connect() as c:
        for i, day in enumerate(["2026-08-24", "2026-08-25"]):
            c.execute("INSERT INTO theme_daily (theme_id, date, overview, "
                      "n_items, built_at) VALUES (?,?,?,?,?)",
                      ("semiconductor", day,
                       json.dumps([f"불렛 {i}"]), 3, "t"))
        c.commit()
    row = rollup.build_week("semiconductor", "반도체", date(2026, 8, 24),
                            backend=_Backend({"summary": "한 주 요약",
                                              "bullets": ["a", "b"]}))
    assert row["n_days"] == 2 and row["summary"] == "한 주 요약"
    rollup.write_week(row)
    with connect() as c:
        got = c.execute("SELECT week_start, bullets FROM theme_weekly").fetchone()
    assert got["week_start"] == "2026-08-24"
    assert json.loads(got["bullets"]) == ["a", "b"]


def test_monthly_reads_the_weeks_not_the_days():
    with connect() as c:
        c.execute("INSERT INTO theme_daily (theme_id, date, overview, n_items, "
                  "built_at) VALUES ('semiconductor','2026-08-03','[\"d\"]',1,'t')")
        for wk in ("2026-08-03", "2026-08-10"):
            c.execute("INSERT INTO theme_weekly (theme_id, week_start, summary, "
                      "bullets, n_days, built_at) VALUES (?,?,?,?,?,?)",
                      ("semiconductor", wk, f"{wk} 주간", '["x"]', 5, "t"))
        c.commit()
    be = _Backend({"summary": "한 달 요약", "bullets": ["m"]})
    row = rollup.build_month("semiconductor", "반도체", "2026-08", backend=be)
    assert row["n_weeks"] == 2
    assert "2026-08-03 주간" in be.calls[0] and "\"d\"" not in be.calls[0]


def test_a_month_with_no_weekly_rows_produces_nothing():
    assert rollup.build_month("semiconductor", "반도체", "2026-08",
                              backend=_Backend({"summary": "x"})) is None
