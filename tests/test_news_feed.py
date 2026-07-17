"""Tests for /api/analysis/news/feed — cross-theme dedup, published_iso,
importance join. Paths are monkeypatched into tmp_path (no real data/)."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from src.api.analysis import _published_iso, news_feed

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
    from src.core import paths as P
    monkeypatch.setattr(P, "signals_news_path",
                        lambda theme, ym: tmp_path / "sig" / theme / f"{ym}.jsonl")
    monkeypatch.setattr(P, "analysis_news_daily_path",
                        lambda theme, d: tmp_path / "ana" / theme / f"{d}.json")

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
        {"items": [{"url": "https://ex.com/1", "importance": 5}]}))

    out = news_feed(limit=50)
    hits = [n for n in out["news"] if n["url"] == "https://ex.com/1"]
    assert len(hits) == 1, "cross-theme duplicate must merge into one card"
    n = hits[0]
    assert {t["id"] for t in n["themes"]} == {"battery", "semiconductor"}
    assert n["image"] == "https://cdn.ex.com/x.jpg"  # merged from the dup
    assert n["importance"] == 5
    assert n["published_iso"] == "2026-07-11T09:00:00+09:00"
