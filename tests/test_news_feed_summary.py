"""An empty digest (nothing collected that day) must not hide the previous
day's '오늘의 요약' bullets — the batch writes a file either way."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from src.agents.news.feed import build as news_feed

_KST = timezone(timedelta(hours=9))


def _setup(tmp_path, monkeypatch):
    from src.agents.news import feed as F
    from src.agents.news.catalog import load_catalog
    from src.core import paths as P
    monkeypatch.setattr(P, "signals_news_path",
                        lambda theme, ym: tmp_path / "sig" / theme / f"{ym}.jsonl")
    monkeypatch.setattr(P, "analysis_news_daily_path",
                        lambda theme, d: tmp_path / "ana" / theme / f"{d}.json")
    cat = load_catalog()
    monkeypatch.setattr(F, "live_themes", lambda c: [cat.get("semiconductor")])
    now = datetime.now(_KST)
    return (now.strftime("%Y-%m-%d"),
            (now - timedelta(days=1)).strftime("%Y-%m-%d"))


def _write(tmp_path, date, payload):
    p = tmp_path / "ana" / "semiconductor" / f"{date}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_empty_today_digest_falls_back_to_yesterday(tmp_path, monkeypatch):
    today, yday = _setup(tmp_path, monkeypatch)
    _write(tmp_path, today, {"date": today, "n_raw": 0, "overview": [], "items": []})
    _write(tmp_path, yday, {"date": yday, "overview": ["어제 반도체 흐름"],
                            "items": [{"url": "https://ex.com/y", "importance": 5}]})

    out = news_feed(limit=50)
    assert len(out["summary"]) == 1
    assert out["summary"][0]["bullets"] == ["어제 반도체 흐름"]
    assert out["summary"][0]["date"] == yday, "the section must show its real date"


def test_todays_overview_wins_when_present(tmp_path, monkeypatch):
    today, yday = _setup(tmp_path, monkeypatch)
    _write(tmp_path, today, {"date": today, "overview": ["오늘"], "items": []})
    _write(tmp_path, yday, {"date": yday, "overview": ["어제"], "items": []})

    out = news_feed(limit=50)
    assert [s["bullets"] for s in out["summary"]] == [["오늘"]]


def test_no_digest_at_all_yields_no_section(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert news_feed(limit=50)["summary"] == []
