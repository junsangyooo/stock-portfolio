"""Tests for og:image extraction (pure) + collect keyword filter (pure)."""
from __future__ import annotations

from src.agents.news.sources.og_image import extract_og_image
from src.agents.news.collect_all import _keyword_hit


def test_og_image_property_first():
    h = '<head><meta property="og:image" content="https://x.com/a.jpg"></head>'
    assert extract_og_image(h) == "https://x.com/a.jpg"


def test_og_image_content_first():
    h = '<meta content="https://x.com/b.png" property="og:image"/>'
    assert extract_og_image(h) == "https://x.com/b.png"


def test_og_image_twitter_fallback():
    h = '<meta name="twitter:image" content="https://x.com/c.webp">'
    assert extract_og_image(h) == "https://x.com/c.webp"


def test_og_image_prefers_og_over_twitter():
    h = ('<meta name="twitter:image" content="https://x.com/tw.jpg">'
         '<meta property="og:image" content="https://x.com/og.jpg">')
    assert extract_og_image(h) == "https://x.com/og.jpg"


def test_og_image_unescapes_entities():
    h = '<meta property="og:image" content="https://x.com/a.jpg?q=82&#038;strip=all">'
    assert extract_og_image(h) == "https://x.com/a.jpg?q=82&strip=all"


def test_og_image_rejects_non_http():
    h = '<meta property="og:image" content="data:image/png;base64,AAAA">'
    assert extract_og_image(h) is None


def test_og_image_none_when_absent():
    assert extract_og_image("<html><body>no meta</body></html>") is None


# ── keyword filter (broad full-site feeds) ──────────────────────────

_KWS = [k.lower() for k in
        ["반도체", "HBM", "파운드리", "semiconductor", "foundry", "ASIC"]]


def test_keyword_hit_korean_substring():
    assert _keyword_hit("삼성 반도체 투자 확대", "", _KWS)


def test_keyword_hit_english_case_insensitive():
    assert _keyword_hit("TSMC Foundry expansion", "", _KWS)


def test_keyword_hit_in_summary():
    assert _keyword_hit("아무 제목", "HBM4 양산 계획 발표", _KWS)


def test_keyword_miss_offtopic():
    assert not _keyword_hit("테일러 스위프트 결혼식에 300억", "연예 뉴스", _KWS)


def test_short_ascii_keyword_needs_word_boundary():
    kws = ["ai"]
    assert not _keyword_hit("Taiwan daily report", "", kws)  # 'ai' inside words
    assert _keyword_hit("AI 반도체 시대", "", kws)
    assert _keyword_hit("the AI boom", "", kws)


def test_rss_image_and_title_unescaped():
    from src.agents.news.sources.rss import parse_feed
    xml = ('<?xml version="1.0"?><rss version="2.0"><channel><item>'
           '<title>&amp;quot;호평&amp;quot; 데뷔</title>'
           '<link>https://ex.com/1</link>'
           '<enclosure url="https://ex.com/a.jpg?q=82&amp;#038;strip=all" type="image/jpeg"/>'
           '</item></channel></rss>')
    items = parse_feed(xml, "einfomax")
    assert items[0].title == '"호평" 데뷔'
    assert items[0].image == "https://ex.com/a.jpg?q=82&strip=all"
