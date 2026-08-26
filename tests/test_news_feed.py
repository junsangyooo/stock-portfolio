"""Tests for /api/analysis/news/feed — cross-theme dedup, published_iso,
importance join. Paths are monkeypatched into tmp_path (no real data/)."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from src.agents.news.feed import build as news_feed, published_iso as _published_iso

_KST = timezone(timedelta(hours=9))


def test_published_iso_rfc822():
    assert _published_iso("Sat, 11 Jul 2026 09:00:00 +0900") \
        == "2026-07-11T09:00:00+09:00"


def test_published_iso_isoformat():
    assert _published_iso("2026-07-11T00:00:00Z") == "2026-07-11T00:00:00+00:00"


def test_published_iso_garbage_none():
    assert _published_iso("not a date") is None
    assert _published_iso(None) is None
    assert _published_iso("") is None


def _write_jsonl(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs)
                    + "\n", encoding="utf-8")


def test_feed_merges_cross_theme_and_joins_importance(tmp_path, monkeypatch):
    from src.agents.news import feed as F
    from src.agents.news.catalog import load_catalog
    from src.core import paths as P
    monkeypatch.setattr(P, "signals_news_path",
                        lambda theme, ym: tmp_path / "sig" / theme / f"{ym}.jsonl")
    monkeypatch.setattr(P, "analysis_news_daily_path",
                        lambda theme, d: tmp_path / "ana" / theme / f"{d}.json")
    # the live set is holdings-driven (DB); pin it so the feed is tested alone
    cat = load_catalog()
    monkeypatch.setattr(F, "live_themes",
                        lambda c: [cat.get("battery"), cat.get("semiconductor")])

    now = datetime.now(_KST)
    ym = now.strftime("%Y%m")
    today = now.strftime("%Y-%m-%d")
    base = {"kind": "rss", "published": "Sat, 11 Jul 2026 09:00:00 +0900",
            "summary": "", "lead_lag": "lagging", "fetched_at": now.isoformat()}

    # same URL surfaces in two themes (shared thelec feed); only one carries image
    _write_jsonl(tmp_path / "sig" / "battery" / f"{ym}.jsonl", [
        {**base, "theme": "battery", "source_id": "thelec_battery",
         "title": "공유 기사", "url": "https://ex.com/1", "image": None},
    ])
    _write_jsonl(tmp_path / "sig" / "semiconductor" / f"{ym}.jsonl", [
        {**base, "theme": "semiconductor", "source_id": "thelec_semi",
         "title": "공유 기사", "url": "https://ex.com/1",
         "image": "https://cdn.ex.com/x.jpg"},
    ])
    # today's battery digest stars that URL
    dig = tmp_path / "ana" / "battery" / f"{today}.json"
    dig.parent.mkdir(parents=True, exist_ok=True)
    dig.write_text(json.dumps(
        {"items": [{"url": "https://ex.com/1", "importance": 5,
                    "summary": "한 문장 요약"}]}, ensure_ascii=False))

    # yesterday's semiconductor digest carries the overview bullets
    ydig = tmp_path / "ana" / "semiconductor" / \
        f"{(now - timedelta(days=1)).strftime('%Y-%m-%d')}.json"
    ydig.parent.mkdir(parents=True, exist_ok=True)
    ydig.write_text(json.dumps({"date": "old", "overview": ["어제 요약"],
                                "items": []}, ensure_ascii=False))

    out = news_feed(limit=50)
    assert [s["bullets"] for s in out["summary"]] == [["어제 요약"]]
    hits = [n for n in out["news"] if n["url"] == "https://ex.com/1"]
    assert len(hits) == 1, "cross-theme duplicate must merge into one card"
    n = hits[0]
    assert {t["id"] for t in n["themes"]} == {"battery", "semiconductor"}
    assert n["image"] == "https://cdn.ex.com/x.jpg"  # merged from the dup
    assert n["importance"] == 5
    # the digest one-liner rides along so cards can be read without leaving
    assert n["gist"] == "한 문장 요약"
    assert n["published_iso"] == "2026-07-11T09:00:00+09:00"


def test_unselected_article_has_no_gist(tmp_path, monkeypatch):
    """Only digest-selected items carry a summary; the rest must be None so
    the card renders without an empty line."""
    from src.agents.news import feed as F
    from src.agents.news.catalog import load_catalog
    from src.core import paths as P
    monkeypatch.setattr(P, "signals_news_path",
                        lambda theme, ym: tmp_path / "sig" / theme / f"{ym}.jsonl")
    monkeypatch.setattr(P, "analysis_news_daily_path",
                        lambda theme, d: tmp_path / "ana" / theme / f"{d}.json")
    cat = load_catalog()
    monkeypatch.setattr(F, "live_themes", lambda c: [cat.get("battery")])

    now = datetime.now(_KST)
    _write_jsonl(tmp_path / "sig" / "battery" / f"{now.strftime('%Y%m')}.jsonl", [
        {"kind": "rss", "lead_lag": "lagging", "theme": "battery",
         "source_id": "electrek", "title": "비선정 기사",
         "url": "https://ex.com/9", "published": None,
         "fetched_at": now.isoformat()},
    ])
    out = news_feed(limit=50)
    assert [n["gist"] for n in out["news"]] == [None]
