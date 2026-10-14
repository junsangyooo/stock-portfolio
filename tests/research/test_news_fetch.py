"""What counts as a delivered feed, and what the per-sweep cap loses."""
from __future__ import annotations

import io
import urllib.error
from datetime import datetime, timedelta

import pytest

from src.core.kst import KST
from src.research.news import collect_all
from src.research.news.catalog import Feed, load_sources
from src.research.news.fetch import rss
from src.research.news.fetch.base import RawItem

RSS = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
       '<item><title>a</title><link>http://x/1</link></item></channel></rss>')
WALL = ('<html><head><META NAME="robots" CONTENT="noindex,nofollow">'
        '<script src="/_Incapsula_Resource?x"></script></head></html>')


def _serve(monkeypatch, body=None, code=None):
    def _get(url, ua, timeout):
        if code:
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO())
        return body.encode()
    monkeypatch.setattr(rss, "_get", _get)


def test_a_feed_is_read(monkeypatch):
    _serve(monkeypatch, RSS)
    assert [i.title for i in rss.fetch_rss("http://x", "s")] == ["a"]


@pytest.mark.parametrize("body", [WALL, "<html><body>blocked</body></html>", "", "{}"])
def test_a_page_that_is_not_a_feed_is_a_failure(monkeypatch, body):
    """recyclingtoday answered 200 with an Incapsula page for a month and read
    as a quiet source."""
    _serve(monkeypatch, body)
    with pytest.raises(rss.NotAFeed):
        rss.fetch_rss("http://x", "s")


def test_not_modified_is_nothing_new_not_a_failure(monkeypatch):
    _serve(monkeypatch, code=304)
    assert rss.fetch_rss("http://x", "s") == []


def test_forbidden_twice_still_raises(monkeypatch):
    _serve(monkeypatch, code=403)
    with pytest.raises(urllib.error.HTTPError):
        rss.fetch_rss("http://x", "s")


def test_the_cap_counts_the_new_items_it_drops(monkeypatch):
    now = datetime(2026, 10, 14, 17, 0, tzinfo=KST)
    items = [RawItem(source_id="s1", kind="rss", title=f"t{i}", url=f"http://x/{i}",
                     published=(now - timedelta(minutes=i)).isoformat(), summary="")
             for i in range(5)]
    monkeypatch.setattr(collect_all, "fetch_rss", lambda *a, **k: items)
    monkeypatch.setattr(collect_all, "enrich_images", lambda items, **kw: 0)
    feed = Feed(source_id="s1", kind="rss", group="news", url="http://x", max_items=3)
    st = collect_all.collect_one(feed, [], (now - timedelta(days=1)).isoformat(), now=now)
    assert st["over"] == 2 and st["written"] == 3


def test_sources_file_reads_per_source_caps():
    feeds = {f.source_id: f for f in load_sources()}
    assert feeds["hk_finance"].max_items == 100
    assert feeds["aitimes"].max_items is None
    assert "fiercebiotech" not in feeds and "recyclingtoday" not in feeds
    assert "B0000502" in feeds["bok_press"].url
