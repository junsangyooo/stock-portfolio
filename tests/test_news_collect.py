"""Tests for L3 collect: feed parsing (pure) + dedup/lead-lag logic."""
from __future__ import annotations

from src.agents.news.sources.rss import parse_feed
from src.agents.news.sources.base import RawItem
from src.agents.news.collect import _lead_lag, _record_key, _load_existing_keys


_RSS2 = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Feed</title>
  <item>
    <title>배터리 신소재 발표</title>
    <link>https://example.com/a</link>
    <pubDate>Sat, 24 May 2026 09:00:00 +0900</pubDate>
    <description>요약 A</description>
  </item>
  <item>
    <title>Second</title>
    <link>https://example.com/b</link>
    <guid>https://example.com/b</guid>
  </item>
</channel></rss>"""

_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Paper One</title>
    <link href="https://arxiv.org/abs/2605.00001" rel="alternate"/>
    <published>2026-05-24T00:00:00Z</published>
    <summary>abstract one</summary>
    <id>https://arxiv.org/abs/2605.00001</id>
  </entry>
</feed>"""


def test_parse_rss2():
    items = parse_feed(_RSS2, "electrek", "rss")
    assert len(items) == 2
    a = items[0]
    assert a.title == "배터리 신소재 발표"
    assert a.url == "https://example.com/a"
    assert a.summary == "요약 A"
    assert a.source_id == "electrek" and a.kind == "rss"


def test_parse_atom_uses_link_href():
    items = parse_feed(_ATOM, "arxiv_ai", "arxiv")
    assert len(items) == 1
    assert items[0].url == "https://arxiv.org/abs/2605.00001"
    assert items[0].title == "Paper One"
    assert items[0].summary == "abstract one"


def test_parse_garbage_returns_empty():
    assert parse_feed("not xml at all", "x") == []


def test_dedup_key_prefers_url():
    it = RawItem("s", "rss", "Title", "https://u/1")
    assert it.dedup_key() == "https://u/1"


def test_dedup_key_falls_back_to_title_hash():
    it = RawItem("s", "rss", "No URL Here", "")
    k = it.dedup_key()
    assert k.startswith("t:") and len(k) > 5


def test_lead_lag_mapping():
    assert _lead_lag("battery", "research") == "leading"
    assert _lead_lag("battery", "social") == "coincident"
    assert _lead_lag("battery", "news") == "lagging"
    assert _lead_lag("macro", "news") == "backbone"  # macro override


def test_record_key_matches_rawitem(tmp_path):
    rec = RawItem("s", "rss", "T", "https://u/9").to_record("battery", "lagging", "now")
    assert _record_key(rec) == "https://u/9"


def test_load_existing_keys_roundtrip(tmp_path):
    p = tmp_path / "202605.jsonl"
    import json
    recs = [
        RawItem("s", "rss", "A", "https://u/a").to_record("battery", "lagging", "now"),
        RawItem("s", "rss", "B", "https://u/b").to_record("battery", "lagging", "now"),
    ]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    keys = _load_existing_keys(p)
    assert keys == {"https://u/a", "https://u/b"}
