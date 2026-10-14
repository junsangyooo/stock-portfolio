"""read.source_health — what the sources dashboard calls each source."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.core.kst import KST
from src.research.news import read
from src.research.news.db import connect

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=KST)


def _day(k):
    return (NOW.date() - timedelta(days=k)).isoformat()


def _signals(c, source, day, n, theme="macro", importance=3, start=0):
    for i in range(n):
        c.execute("INSERT INTO signals (dedup_key, title, source_id, source_group, lead_lag, "
                  "fetched_at, collected_on, enriched_at, importance) "
                  "VALUES (?, 't', ?, 'news', 'lagging', 'f', ?, 'x', ?)",
                  (f"{source}{day}{start + i}", source, day, importance))
        if theme:
            sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO signal_themes (signal_id, theme_id) VALUES (?, ?)", (sid, theme))


def _cursor(c, source, fails=0, error=None, fetched=None):
    c.execute("INSERT INTO source_cursor (source_id, last_fetched_at, last_ok_at, last_error, "
              "n_consecutive_fail) VALUES (?, ?, '2026-10-14', ?, ?)", (source, fetched, error, fails))


def _run(c, at, source, ok=True, written=0, filtered=0, error=None):
    c.execute("INSERT INTO collect_runs (run_at, source_id, ok, error, written, filtered) "
              "VALUES (?,?,?,?,?,?)", (at, source, ok, error, written, filtered))


def _by_id(**kw):
    return {s["source_id"]: s for s in read.source_health(now=NOW, **kw)["sources"]}


def test_statuses():
    with connect() as c:
        _cursor(c, "aitimes"); _signals(c, "aitimes", _day(1), 3)
        _cursor(c, "geeknews", fails=1, error="HTTP Error 500"); _signals(c, "geeknews", _day(2), 2)
        _cursor(c, "cleantechnica"); _signals(c, "cleantechnica", _day(0), 2)
        for i in range(4):
            _run(c, f"2026-10-1{i}T06:00:00+09:00", "cleantechnica", ok=False, error="HTTP Error 403")
        _cursor(c, "bok_press"); _signals(c, "bok_press", _day(20), 1)      # 200s, nothing new
        _cursor(c, "finextra", fetched="2026-10-21T15:00:00+00:00")         # a feed's future date
        c.commit()
    s = _by_id()
    assert s["aitimes"]["status"] == "ok"
    assert s["geeknews"]["status"] == "failing" and "500" in s["geeknews"]["last_error"]
    assert s["cleantechnica"]["status"] == "flaky" and s["cleantechnica"]["week"]["failed"] == 4
    assert s["bok_press"]["status"] == "quiet" and s["bok_press"]["d30"]["n"] == 1
    assert s["finextra"]["status"] == "collect_error" and "10/22" in s["finextra"]["reason"]
    assert s["3dpi"]["status"] == "never"
    assert len(s) == len(read.source_health(now=NOW)["sources"])   # every configured source


def test_a_weekly_source_is_not_quiet_inside_its_usual_gap():
    """BEA publishes every week or so; four days without a release is normal."""
    with connect() as c:
        _cursor(c, "bea")
        for k in (4, 12, 19, 27, 34):
            _signals(c, "bea", _day(k), 1)
        c.commit()
    assert _by_id()["bea"]["status"] == "ok"


def test_runs_today_and_the_week():
    with connect() as c:
        _cursor(c, "aitimes")
        _run(c, "2026-10-13T17:00:00+09:00", "aitimes", written=5, filtered=1)
        _run(c, "2026-10-14T06:00:00+09:00", "aitimes", written=3, filtered=2)
        _run(c, "2026-10-14T06:00:00+09:00", "geeknews", written=1)
        _signals(c, "aitimes", _day(1), 5, importance=4)
        _signals(c, "aitimes", _day(0), 3, theme=None, start=10)
        _signals(c, "aitimes", _day(0), 4, start=20)                        # a manual run
        c.commit()
    r = read.source_health(now=NOW)
    assert r["runs"] == ["2026-10-13T17:00:00+09:00", "2026-10-14T06:00:00+09:00"]
    s = {x["source_id"]: x for x in r["sources"]}
    a = s["aitimes"]
    assert a["today"] == {"written": 3, "filtered": 2, "collected": 7, "enriched": 7,
                          "important": 0, "untagged": 3}
    assert a["week"]["written"] == 8 and a["week"]["outside"] == 4 and a["week"]["important"] == 5
    assert [x["written"] for x in a["runs"]] == [5, 3]
    assert s["geeknews"]["runs"][0] is None                                 # no record that sweep
    assert r["unclassified"]["untagged"] == 3
    names = {x["theme_id"]: x["name_kr"] for x in r["themes"]}
    assert len(names) >= 23 and names["macro"]                          # the page names fields from here


def test_flags_one_removal_candidate_per_field():
    with connect() as c:
        for sid, imp in (("steeldaily", 1), ("ferrotimes", 1), ("snmnews", 4)):
            _cursor(c, sid)
            for k in range(10):
                _signals(c, sid, _day(k), 3, theme="steel", importance=imp)
        _cursor(c, "splash247")
        for k in range(10):
            _signals(c, "splash247", _day(k), 3, theme="shipbuilding")
        _cursor(c, "fed_press"); _signals(c, "fed_press", _day(1), 2, theme="regulation_fin")
        c.commit()
    s = _by_id()
    review = [x for x in s if any(f["kind"] == "review" for f in s[x]["flags"])]
    assert len(review) == 1 and review[0] in ("steeldaily", "ferrotimes")
    assert {"kind": "review", "theme": "steel", "from": 3, "to": 2} in s[review[0]]["flags"]
    assert {"kind": "only", "theme": "shipbuilding"} in s["splash247"]["flags"]
    assert {"kind": "rare"} in s["fed_press"]["flags"]
    assert s["steeldaily"]["themes"]["main"] == ["steel"]


def test_pipeline_health_counts_today_and_the_queue():
    with connect() as c:
        c.execute("INSERT INTO signals (dedup_key, title, source_id, source_group, lead_lag, "
                  "fetched_at, collected_on) VALUES ('k', 't', 'aitimes', 'news', 'lagging', 'x', ?)",
                  (_day(0),))
        c.commit()
    p = read.pipeline_health(now=NOW)
    assert p["pending_enrich"] == 1 and p["n_themes"] >= 23
    assert p["collected_today"] == 1 and p["enriched_today"] == 0


def test_the_week_starts_at_the_first_shown_sweep_not_its_day():
    """Between 06:00 and 17:00 the oldest shown sweep is a 17:00 one; that
    day's morning items must not count as collected outside a sweep."""
    with connect() as c:
        _cursor(c, "aitimes")
        _run(c, "2026-10-13T17:00:00+09:00", "aitimes", written=2)
        for i, fetched in enumerate(("2026-10-13T06:00:01+09:00", "2026-10-13T17:00:01+09:00",
                                     "2026-10-13T17:00:02+09:00")):
            c.execute("INSERT INTO signals (dedup_key, title, source_id, source_group, lead_lag, "
                      "fetched_at, collected_on, enriched_at) VALUES (?, 't', 'aitimes', 'news', "
                      "'lagging', ?, '2026-10-13', 'x')", (f"k{i}", fetched))
        c.commit()
    a = _by_id()["aitimes"]
    assert a["week"]["collected"] == 2 and a["week"]["outside"] == 0
    assert a["week"]["important"] == 0                       # all importance NULL: 0, not None


def test_themes_overview_has_every_field_with_a_daily_series():
    with connect() as c:
        c.execute("INSERT INTO theme_daily (theme_id, date, overview, sentiment_score, "
                  "sentiment_trend, n_items, built_at) VALUES ('steel', ?, '[\"a\"]', 0.4, 'up', 3, 'x')",
                  (_day(0),))
        _signals(c, "steeldaily", _day(0), 2, theme="steel")
        c.commit()
    r = read.themes_overview(days=7, now=NOW)
    t = {x["theme_id"]: x for x in r["themes"]}
    assert len(t) >= 23 and len(t["steel"]["series"]) == 7
    assert t["steel"]["today"] == {"date": _day(0), "score": 0.4, "trend": "up", "bullets": ["a"]}
    assert t["steel"]["items_today"] == 2 and t["battery"]["today"]["score"] is None
