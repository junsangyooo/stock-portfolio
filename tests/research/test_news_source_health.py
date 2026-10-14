"""read.source_health — what the sources dashboard calls each source."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.core.kst import KST
from src.research.news import read
from src.research.news.collect_all import MAX_PER_SOURCE
from src.research.news.db import connect

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=KST)


def _day(k):
    return (NOW.date() - timedelta(days=k)).isoformat()


def _signals(c, source, day, n, fetched, tagged=True, start=0):
    for i in range(n):
        c.execute("INSERT INTO signals (dedup_key, title, source_id, source_group, lead_lag, "
                  "fetched_at, collected_on, enriched_at) VALUES (?, 't', ?, 'news', 'lagging', ?, ?, 'x')",
                  (f"{source}{day}{start + i}", source, fetched, day))
        if tagged:
            sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO signal_themes (signal_id, theme_id) VALUES (?, 'macro')", (sid,))


def _cursor(c, source, fails=0, error=None):
    c.execute("INSERT INTO source_cursor (source_id, last_ok_at, last_error, n_consecutive_fail) "
              "VALUES (?, '2026-10-14', ?, ?)", (source, error, fails))


def _by_id(**kw):
    return {s["source_id"]: s for s in read.source_health(now=NOW, **kw)["sources"]}


def test_statuses():
    with connect() as c:
        _cursor(c, "aitimes"); _signals(c, "aitimes", _day(1), 3, "a1")
        _cursor(c, "geeknews", fails=1, error="HTTP Error 500")
        _signals(c, "geeknews", _day(2), 2, "g1")
        _cursor(c, "statnews", fails=38, error="HTTP Error 403: Forbidden")
        _cursor(c, "bok_press")                                   # 200s, nothing in it
        _signals(c, "bok_press", _day(20), 1, "b1")
        _cursor(c, "mk_stock")
        _signals(c, "mk_stock", _day(0), MAX_PER_SOURCE, "h1")    # a sweep at the cap
        _cursor(c, "hk_finance")                                  # its own cap is 100
        _signals(c, "hk_finance", _day(0), MAX_PER_SOURCE, "h2")
        c.commit()
    s = _by_id()
    assert s["aitimes"]["status"] == "ok" and s["aitimes"]["n_7d"] == 3
    assert s["geeknews"]["status"] == "failing"
    assert s["statnews"]["status"] == "dead" and "403" in s["statnews"]["last_error"]
    assert s["bok_press"]["status"] == "quiet" and s["bok_press"]["n_30d"] == 1
    assert s["mk_stock"]["status"] == "capped" and s["mk_stock"]["capped_sweeps"] == 1
    assert s["hk_finance"]["status"] == "ok" and s["hk_finance"]["cap"] == 100
    assert s["3dpi"]["status"] == "never"
    assert len(s) == len(read.source_health(now=NOW)["sources"])   # every configured source


def test_daily_series_and_untagged_share():
    with connect() as c:
        _cursor(c, "aitimes")
        _signals(c, "aitimes", _day(0), 2, "a1")
        _signals(c, "aitimes", _day(3), 2, "a2", tagged=False)
        c.commit()
    s = _by_id(days=30)["aitimes"]
    assert len(s["daily"]) == 30 and s["daily"][-1] == 2 and s["daily"][-4] == 2
    assert s["untagged_share"] == 0.5 and s["n_1d"] == 2


def test_pipeline_health_counts_the_queue():
    with connect() as c:
        c.execute("INSERT INTO signals (dedup_key, title, source_id, source_group, lead_lag, "
                  "fetched_at, collected_on) VALUES ('k', 't', 'aitimes', 'news', 'lagging', 'x', ?)",
                  (_day(0),))
        c.commit()
    p = read.pipeline_health(now=NOW)
    assert p["pending_enrich"] == 1 and p["n_themes"] >= 23
