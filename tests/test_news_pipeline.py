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

from src.research.news import collect_all, cycle, daily, enrich, read, rollup
from src.research.news.catalog import Feed, load_catalog, load_sources
from src.research.news.db import connect
from src.research.news.sources.base import RawItem
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


def test_a_title_wrapped_in_markup_survives_parsing():
    """fiercebiotech wraps every <title> in an <a>; reading only ``.text``
    left the title empty and the item was dropped as untitled."""
    from src.research.news.sources.rss import parse_feed
    xml = ("<rss version='2.0'><channel><item>"
           "<title><a href='/x'>Alnylam silencer sees silver linings</a></title>"
           "<link>http://f/x</link></item></channel></rss>")
    items = parse_feed(xml, "fiercebiotech")
    assert [i.title for i in items] == ["Alnylam silencer sees silver linings"]


def test_theme_pairs_normalises_what_the_model_returns():
    """The tagger may answer with pairs, bare ids, or nonsense in either slot."""
    from src.research.news.enrich import _theme_pairs
    valid = {"ai_sw", "macro", "bio"}
    got = list(_theme_pairs(
        [["ai_sw", 0.9], "macro", ["nope", 1.0], ["bio", "x"], ["ai_sw", 5]],
        valid))
    assert got == [("ai_sw", 0.9), ("macro", 1.0), ("bio", 1.0), ("ai_sw", 1.0)]
    assert list(_theme_pairs(None, valid)) == []


def test_the_tagger_is_shown_definitions_and_told_none_is_allowed():
    """Bare ids made the model pick the nearest-sounding label and made macro
    a drain; the definitions and the explicit opt-out are the fix."""
    from src.research.news.enrich import _prompt
    cat = load_catalog()
    p = _prompt([{"title": "t", "body": "b"}], cat)
    assert cat.themes["semiconductor"].definition in p
    assert "빈 배열" in p
    assert "(연관 신호용" not in p   # the dead active/shadow vocabulary


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


def test_a_first_sweep_leaves_the_feeds_back_catalogue_behind(monkeypatch, no_images):
    """No cursor means nothing bounds how far back the first fetch reaches.
    BEA's feed is a rolling archive back to 2019, and all of it arrived
    stamped with today's collected_on — which is the date `daily` reads."""
    old = _item("2019 release", "http://a", published="Wed, 18 Dec 2019 08:30:00 +0000")
    new = _item("this week", "http://b", published="Mon, 24 Aug 2026 08:30:00 +0000")
    stat = _collect(monkeypatch, [old, new], cursor=None)
    assert stat["written"] == 1 and stat["stale"] == 1
    assert [r["title"] for r in _stored()] == ["this week"]


def test_the_age_floor_applies_only_to_a_feed_we_have_never_fetched(monkeypatch,
                                                                    no_images):
    """Once a cursor exists it is the bound, and a feed that back-dates a
    correction must not be silently dropped by a second rule."""
    old = _item("backdated", "http://a", published="Wed, 18 Dec 2019 08:30:00 +0000")
    stat = _collect(monkeypatch, [old], cursor="2019-01-01T00:00:00+00:00")
    assert stat["written"] == 1


def test_an_undated_item_survives_the_first_sweep_floor(monkeypatch, no_images):
    stat = _collect(monkeypatch, [_item("no date", "http://a")], cursor=None)
    assert stat["written"] == 1


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
    out = enrich.run(now=NOW, backend=be)
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
    enrich.run(now=NOW, backend=be)
    with connect() as c:
        themes = [r[0] for r in c.execute(
            "SELECT theme_id FROM signal_themes WHERE signal_id=?", (ids[0],))]
    assert themes == ["semiconductor"]


def test_importance_is_clamped(monkeypatch):
    _seed(monkeypatch, 1)
    enrich.run(now=NOW, backend=_Backend({"r": [{"i": 0, "s": "x", "imp": 99, "t": []}]}))
    with connect() as c:
        assert c.execute("SELECT importance FROM signals").fetchone()[0] == 5


def test_an_article_the_model_skipped_stays_pending(monkeypatch):
    """It must be retried, not stamped as enriched with nothing in it."""
    _seed(monkeypatch, 2)
    enrich.run(now=NOW, backend=_Backend({"r": [{"i": 0, "s": "x", "imp": 3, "t": []}]}))
    assert len(enrich.pending(now=NOW)) == 1


def test_a_failed_batch_leaves_everything_pending(monkeypatch):
    _seed(monkeypatch, 2)

    class _Boom:
        def call(self, *a, **kw):
            raise RuntimeError("cli exit 1")
    out = enrich.run(now=NOW, backend=_Boom())
    assert out["enriched"] == 0 and out["failed"] == 2
    assert len(enrich.pending(now=NOW)) == 2


def test_enrichment_is_not_repeated(monkeypatch):
    _seed(monkeypatch, 1)
    be = _Backend({"r": [{"i": 0, "s": "x", "imp": 3, "t": []}]})
    enrich.run(now=NOW, backend=be)
    second = enrich.run(now=NOW, backend=be)
    assert second["pending"] == 0 and len(be.calls) == 1


def test_the_weekend_enriches_on_the_local_model():
    at = lambda day, hour: datetime(2026, 9, day, hour, 0, tzinfo=KST)
    assert not cycle.local_enrich(at(11, 17))   # Fri 17:00 — Claude
    assert cycle.local_enrich(at(12, 6))        # Sat 06:00 — local
    assert cycle.local_enrich(at(13, 17))       # Sun 17:00 — local
    assert not cycle.local_enrich(at(14, 6))    # Mon 06:00 — Claude


def test_local_enrichment_batches_to_fit_the_on_host_context(monkeypatch):
    _seed(monkeypatch, 25)
    seen = []

    class _Local:
        def __init__(self, max_tokens, stage="?"):
            seen.append(("max_tokens", max_tokens))

        def call(self, system, user, model=None):
            seen.append(("batch", user.count("\n    ")))
            raise RuntimeError("local llama HTTP 500")

    monkeypatch.setattr(enrich, "LocalLlamaBackend", _Local)
    out = enrich.run(now=NOW, local=True)
    assert seen[0] == ("max_tokens", enrich._LOCAL_MAX_TOKENS)
    assert [n for k, n in seen if k == "batch"] == [10, 10, 5]
    # no fallback: a failed local batch waits for the next run
    assert out["failed"] == 25 and len(enrich.pending(now=NOW)) == 25


# ── daily overview ──────────────────────────────────────────────────────────

def test_daily_overview_reads_only_enriched_items(monkeypatch):
    _seed(monkeypatch, 1)
    assert daily.items_for("semiconductor", "2026-08-27") == []
    enrich.run(now=NOW, backend=_Backend(
        {"r": [{"i": 0, "s": "칩 수요", "imp": 5, "t": ["semiconductor"]}]}))
    got = daily.items_for("semiconductor", "2026-08-27")
    assert len(got) == 1 and got[0]["summary"] == "칩 수요"


def test_daily_overview_is_written_and_rebuildable(monkeypatch):
    _seed(monkeypatch, 1)
    enrich.run(now=NOW, backend=_Backend(
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
    enrich.run(now=NOW, backend=_Backend(
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
    enrich.run(now=NOW, backend=_Backend(
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


# ── read side: landing feed ─────────────────────────────────────────────────

def _enriched(monkeypatch, items, themes_by_index, *, day=None):
    """Collect `items`, then enrich them with the given theme assignment."""
    monkeypatch.setattr(collect_all, "enrich_images", lambda its, **kw: 0)
    monkeypatch.setattr(collect_all, "fetch_rss",
                        lambda url, sid, timeout=15.0: list(items))
    collect_all.collect_one(_feed(), [], None, now=day or NOW)
    # window_days=0: this seeds fixtures at fixed past dates, and the freshness
    # window is measured against the real clock.
    enrich.run(now=NOW, window_days=0, backend=_Backend({"r": [
        {"i": i, "s": f"요약{i}", "imp": 5 - i, "nov": i == 0, "t": t}
        for i, t in enumerate(themes_by_index)]}))


def test_published_iso_parses_both_dialects():
    assert read.published_iso("Sat, 11 Jul 2026 09:00:00 +0900") \
        == "2026-07-11T09:00:00+09:00"
    assert read.published_iso("2026-07-11T00:00:00Z") == "2026-07-11T00:00:00+00:00"
    assert read.published_iso("not a date") is None
    assert read.published_iso(None) is None


def test_one_article_carries_every_theme_it_belongs_to(monkeypatch):
    """The jsonl layout needed a merge-by-url pass for this; it is a join now."""
    _enriched(monkeypatch, [_item("A", "http://a")],
              [["semiconductor", "ai_sw"]])
    out = read.feed(now=NOW)
    assert len(out["news"]) == 1
    assert {t["id"] for t in out["news"][0]["themes"]} == {"semiconductor", "ai_sw"}


def test_the_feed_splits_news_from_social_and_drops_research(monkeypatch):
    monkeypatch.setattr(collect_all, "enrich_images", lambda its, **kw: 0)
    for group, title in (("news", "N"), ("social", "S"), ("research", "R")):
        monkeypatch.setattr(
            collect_all, "fetch_rss",
            lambda url, sid, timeout=15.0, t=title: [_item(t, f"http://{t}")])
        collect_all.collect_one(_feed(source_id=group, group=group), [], None,
                                now=NOW)
    out = read.feed(now=NOW)
    assert [c["title"] for c in out["news"]] == ["N"]
    assert [c["title"] for c in out["social"]] == ["S"]


def test_the_feed_carries_the_gist_and_importance(monkeypatch):
    _enriched(monkeypatch, [_item("A", "http://a")], [["semiconductor"]])
    card = read.feed(now=NOW)["news"][0]
    assert card["gist"] == "요약0" and card["importance"] == 5
    assert card["favicon"] and card["source"] == "s1"


def test_an_unenriched_article_still_shows_without_a_gist(monkeypatch):
    """Collection and enrichment fail independently; the feed must survive the
    window between them."""
    monkeypatch.setattr(collect_all, "enrich_images", lambda its, **kw: 0)
    _collect(monkeypatch, [_item("A", "http://a")])
    card = read.feed(now=NOW)["news"][0]
    assert card["title"] == "A" and card["gist"] is None


def test_the_summary_section_falls_back_to_yesterday(monkeypatch):
    """Before the dawn batch today's overview does not exist yet."""
    yesterday = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    with connect() as c:
        c.execute("INSERT INTO theme_daily (theme_id, date, overview, n_items, "
                  "built_at) VALUES ('semiconductor',?,?,1,'t')",
                  (yesterday, json.dumps(["어제의 반도체"])))
        c.commit()
    out = read.feed(now=NOW)
    assert [s["bullets"] for s in out["summary"]] == [["어제의 반도체"]]


def test_todays_overview_wins_over_yesterdays():
    with connect() as c:
        for d, b in ((NOW - timedelta(days=1), "어제"), (NOW, "오늘")):
            c.execute("INSERT INTO theme_daily (theme_id, date, overview, "
                      "n_items, built_at) VALUES ('semiconductor',?,?,1,'t')",
                      (d.strftime("%Y-%m-%d"), json.dumps([b])))
        c.commit()
    assert read.feed(now=NOW)["summary"][0]["bullets"] == ["오늘"]


def test_no_overview_at_all_yields_no_summary_section():
    assert read.feed(now=NOW)["summary"] == []


# ── read side: theme state (replaces state.json) ────────────────────────────

def test_theme_state_of_an_untouched_theme_is_the_empty_shape():
    st = read.theme_state("semiconductor", now=NOW)
    assert st["horizon_summary"] == "" and st["recent_developments"] == []
    assert st["sentiment"] == {"score": 0.0, "trend": "flat"}
    assert st["watchlist"] == [] and st["last_shift"] == ""


def test_theme_state_pulls_sentiment_and_watchlist_from_the_day(monkeypatch):
    _enriched(monkeypatch, [_item("A", "http://a")], [["semiconductor"]])
    daily.run("2026-08-27", theme="semiconductor", backend=_Backend(
        {"overview": ["불렛"], "sentiment": {"score": -0.4, "trend": "deteriorating"},
         "watchlist": ["관세"]}))
    st = read.theme_state("semiconductor", now=NOW)
    assert st["sentiment"] == {"score": -0.4, "trend": "deteriorating"}
    assert st["watchlist"] == ["관세"] and st["updated"] == "2026-08-27"


def test_the_weekly_summary_becomes_the_long_memory(monkeypatch):
    _enriched(monkeypatch, [_item("A", "http://a")], [["semiconductor"]])
    daily.run("2026-08-27", theme="semiconductor",
              backend=_Backend({"overview": ["오늘 불렛"]}))
    # before the first Sunday the day's bullets stand in
    assert read.theme_state("semiconductor", now=NOW)["horizon_summary"] == "오늘 불렛"
    with connect() as c:
        c.execute("INSERT INTO theme_weekly (theme_id, week_start, summary, "
                  "bullets, n_days, built_at) VALUES "
                  "('semiconductor','2026-08-24','한 주의 흐름','[]',5,'t')")
        c.commit()
    st = read.theme_state("semiconductor", now=NOW)
    assert st["horizon_summary"] == "한 주의 흐름"
    assert st["week_summary"] == "한 주의 흐름"


def test_recent_developments_stop_at_the_horizon(monkeypatch):
    inside = NOW - timedelta(days=3)
    outside = NOW - timedelta(days=30)
    for when, title in ((inside, "recent"), (outside, "ancient")):
        _enriched(monkeypatch, [_item(title, f"http://{title}")],
                  [["semiconductor"]], day=when)
    points = {d["point"] for d in
              read.theme_state("semiconductor", now=NOW)["recent_developments"]}
    assert points == {"요약0"}          # only the in-window article survives
    assert len(points) == 1


def test_last_shift_is_computed_not_remembered():
    """The old state file had a model write this sentence; the trend history
    makes it a fact."""
    with connect() as c:
        for d, trend in (("2026-08-25", "flat"), ("2026-08-26", "flat"),
                         ("2026-08-27", "improving")):
            c.execute("INSERT INTO theme_daily (theme_id, date, overview, "
                      "sentiment_trend, n_items, built_at) "
                      "VALUES ('semiconductor',?,'[]',?,1,'t')", (d, trend))
        c.commit()
    assert read.theme_state("semiconductor", now=NOW)["last_shift"] \
        == "2026-08-27: flat→improving"


def test_active_themes_are_the_ones_with_recent_news():
    with connect() as c:
        c.execute("INSERT INTO theme_daily (theme_id, date, overview, n_items, "
                  "built_at) VALUES ('semiconductor','2026-08-27','[]',1,'t')")
        c.execute("INSERT INTO theme_daily (theme_id, date, overview, n_items, "
                  "built_at) VALUES ('space','2026-01-01','[]',1,'t')")
        c.commit()
    assert read.active_themes(now=NOW) == ["semiconductor"]


def test_a_busy_news_day_does_not_starve_the_social_column(monkeypatch):
    """A shared LIMIT handed the whole budget to news and left social empty."""
    monkeypatch.setattr(collect_all, "enrich_images", lambda its, **kw: 0)
    monkeypatch.setattr(
        collect_all, "fetch_rss",
        lambda url, sid, timeout=15.0: [_item(f"N{i}", f"http://n{i}")
                                        for i in range(30)])
    collect_all.collect_one(_feed(), [], None, now=NOW)
    monkeypatch.setattr(
        collect_all, "fetch_rss",
        lambda url, sid, timeout=15.0: [_item("S", "http://s")])
    collect_all.collect_one(_feed(source_id="soc", group="social"), [], None,
                            now=NOW)
    out = read.feed(limit=5, now=NOW)
    assert len(out["news"]) == 5
    assert [c["title"] for c in out["social"]] == ["S"]


def test_the_enrich_window_can_be_pinned_to_a_clock(monkeypatch):
    """Reading the wall clock with no override is how this suite rotted: the
    fixture sits at a fixed date, and one morning it fell out of the 7-day
    window and fifteen tests went red with no code change."""
    from datetime import timedelta
    monkeypatch.setattr(collect_all, "enrich_images", lambda its, **kw: 0)
    monkeypatch.setattr(collect_all, "fetch_rss",
                        lambda url, sid, timeout=15.0: [_item("t", "http://t")])
    collect_all.collect_one(_feed(), [], None, now=NOW)
    assert len(enrich.pending(now=NOW)) == 1
    assert enrich.pending(now=NOW + timedelta(days=60)) == []


def test_an_enrich_run_is_capped_and_takes_the_newest_first(monkeypatch):
    """The migration left tens of thousands behind; an uncapped pass would
    spend hours on old news before touching today's."""
    monkeypatch.setattr(collect_all, "enrich_images", lambda its, **kw: 0)
    monkeypatch.setattr(
        collect_all, "fetch_rss",
        lambda url, sid, timeout=15.0: [_item(f"T{i}", f"http://{i}")
                                        for i in range(5)])
    collect_all.collect_one(_feed(), [], None, now=NOW)
    be = _Backend({"r": [{"i": i, "s": f"s{i}", "imp": 3, "t": []}
                         for i in range(2)]})
    out = enrich.run(now=NOW, limit=2, batch=2, backend=be)
    assert out["enriched"] == 2 and out["backlog"] == 3
    with connect() as c:
        done = [r[0] for r in c.execute(
            "SELECT title FROM signals WHERE enriched_at IS NOT NULL "
            "ORDER BY signal_id")]
    assert done == ["T3", "T4"], "newest first"


def test_enrichment_ignores_articles_older_than_anything_reads(monkeypatch):
    """Summaries are only read inside a 7-day horizon, so a two-month-old
    article is not a backlog — it is work nobody would look at."""
    monkeypatch.setattr(collect_all, "enrich_images", lambda its, **kw: 0)
    for when, title in ((NOW, "fresh"), (NOW - timedelta(days=60), "ancient")):
        monkeypatch.setattr(
            collect_all, "fetch_rss",
            lambda url, sid, timeout=15.0, t=title: [_item(t, f"http://{t}")])
        collect_all.collect_one(_feed(), [], None, now=when)
    monkeypatch.setattr(enrich, "_since", lambda days, now=None:
                        (NOW - timedelta(days=days - 1)).strftime("%Y-%m-%d"))
    assert [r["title"] for r in enrich.pending(now=NOW)] == ["fresh"]
    # the window can be lifted on purpose, for a hand-run backfill
    assert len(enrich.pending(now=NOW, window_days=0)) == 2
