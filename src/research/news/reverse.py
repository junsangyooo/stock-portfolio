"""Theme → symbols (reverse of mapping.py).

`mapping.py` answers "which themes does this symbol belong to?" — the direction
the holdings pipeline needs. The discovery agent needs the opposite: given a
theme that is moving, which listed companies are in it?

Two sources, because neither alone is enough:

  tagged     symbol_themes reversed. Precise, but only ever contains symbols
             somebody already mapped — useless for finding something new.
  mentioned  company names recovered from the theme's own news corpus
             (news.db, via read.read_items). This is the mechanical form of
             "read today's news, notice a company, go look it up".

Matching is literal substring against the universe's display names, longest
name first, with matched spans masked so "한화" inside "한화에어로스페이스"
does not count twice. No LLM, no new dependency.

Deliberate floor: Hangul names shorter than 3 chars and English names shorter
than 4 are skipped — two-letter names collide with ordinary prose far more
often than they identify the company. The cost is that a handful of small caps
(한창, 삼양) can never be found this way; the returned `note` says so.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional


_MIN_LEN_KR = 3
_MIN_LEN_EN = 4

# Names that are ordinary words first and tickers second. A literal matcher
# cannot tell them apart, and each one poisons every theme it appears in.
_STOPNAMES = {
    "한국전자", "대한제강", "코리아", "에스와이", "이에스", "디에스", "아이티",
    "네이처", "에이치", "케이씨", "제이씨", "에이스", "우리기술",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_HANGUL = ("가", "힣")

# Korean glues a particle straight onto the noun — 삼성전자'가', 하이닉스'는' —
# so "reject any Hangul neighbour" would throw away most real mentions. These
# are the syllables a particle can start with; any other Hangul means the name
# ran into a compound word instead (디바이스 inside 디바이스솔루션).
_PARTICLE_HEADS = set("가이은는을를의에도와과로만라고며등및측부까처보한서야께짜")


def _is_hangul(ch: str) -> bool:
    return _HANGUL[0] <= ch <= _HANGUL[1]


def _boundary_ok(corpus: str, i: int, n: int, hangul_name: bool) -> bool:
    """True when corpus[i:i+n] stands as a name, not as part of a longer word.

    Catches the two real failures seen in production: '디바이스' matching inside
    '디바이스솔루션' and 'DGI' matching inside 'DGIST'.
    """
    prev_ch = corpus[i - 1] if i > 0 else ""
    next_ch = corpus[i + n] if i + n < len(corpus) else ""
    if prev_ch and (_is_hangul(prev_ch) or prev_ch.isalnum()):
        return False
    if not next_ch:
        return True
    if hangul_name:
        return not _is_hangul(next_ch) or next_ch in _PARTICLE_HEADS
    return not (next_ch.isascii() and next_ch.isalnum())


def _strip_html(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", s or ""))


def read_items(theme_id: str, days: int) -> list[dict]:
    """Raw articles for a theme within the window, newest first.

    Thin wrapper over read.read_items: the corpus moved into news.db, and the
    month-file walk plus the tz-aware sort it needed went with it — the date
    axis is a stored column now.
    """
    from .read import read_items as _read_items
    return _read_items(theme_id, days=days)


# Legal suffixes news prose never carries: the universe says "Broadcom Inc"
# where every article says "Broadcom". Stripped variants are matched alongside
# the registered name. Applied word-by-word from the tail, so "Class A" and
# "Inc." both fall off "Alphabet Inc. Class A".
_EN_SUFFIXES = {"inc", "inc.", "incorporated", "corp", "corp.", "corporation",
                "co", "co.", "company", "ltd", "ltd.", "limited", "plc",
                "sa", "s.a.", "nv", "n.v.", "ag", "se",
                "a", "b", "c", "class"}


def _strip_en_suffix(name: str) -> str:
    words = name.split()
    while len(words) > 1 and words[-1].lower().rstrip(",") in _EN_SUFFIXES:
        words.pop()
    return " ".join(words)


def _name_entries() -> list[tuple[str, str]]:
    """(display_name, symbol_id) for every universe entry worth matching,
    longest name first so the masking pass prefers the specific name.

    US coverage needs two extra entry kinds (cycle #1: discovery could name
    AVGO/MRVL from the news but literal match found nothing):
      - the suffix-stripped English name ("Broadcom" for "Broadcom Inc")
      - the ticker itself, 4+ chars — prose that says "AVGO" means the stock,
        and the latin boundary check already rejects DGIST-style supersets.
    """
    from ...universe import search as uni_search
    uni_search._ensure_loaded()  # noqa: SLF001 — module-level cache, no public warm
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, sid: str) -> None:
        if not name or name in _STOPNAMES or (name, sid) in seen:
            return
        floor = _MIN_LEN_KR if _is_hangul(name[0]) else _MIN_LEN_EN
        if len(name) >= floor:
            seen.add((name, sid))
            entries.append((name, sid))

    for it in uni_search._items:  # noqa: SLF001
        sid = it.get("symbol_id")
        if not sid:
            continue
        for key in ("name_kr", "name_en"):
            name = (it.get(key) or "").strip()
            if not name:
                continue
            add(name, sid)
            if not _is_hangul(name[0]):
                add(_strip_en_suffix(name), sid)
        if sid.startswith("US:"):
            ticker = sid.split(":", 1)[1]
            if ticker.isalpha() and ticker.isupper():
                add(ticker, sid)
    entries.sort(key=lambda e: len(e[0]), reverse=True)
    return entries


def _count_masked(corpus: str, entries: Iterable[tuple[str, str]]) -> dict[str, int]:
    """Literal counts over one corpus, longest name first, matched spans masked.

    Masking is what stops '한화' from scoring on every '한화에어로스페이스'.
    """
    taken = bytearray(len(corpus))
    hits: dict[str, int] = {}
    for name, sid in entries:
        n = len(name)
        hangul_name = _is_hangul(name[0])
        start, found = 0, 0
        while True:
            i = corpus.find(name, start)
            if i < 0:
                break
            start = i + n
            if any(taken[i:i + n]):
                continue
            if not _boundary_ok(corpus, i, n, hangul_name):
                continue
            taken[i:i + n] = b"\x01" * n
            found += 1
        if found:
            hits[sid] = hits.get(sid, 0) + found
    return hits


def _tagged(theme_id: str) -> list[dict]:
    """symbol_themes reversed — symbols somebody already mapped to this theme."""
    from ...market.store import theme_store
    return theme_store.symbols_for(theme_id)



def symbols_for_theme(theme_id: str, days: int = 7, limit: int = 30,
                      held: Optional[Iterable[str]] = None) -> dict:
    """Companies attached to a theme, from tagging and from its news corpus.

    Returns tagged[] (precise, mapping-derived) and mentioned[] (discovery,
    ranked by mention count) — the caller decides which to trust.

    `held` is passed in, never looked up. This module knows nothing about
    accounts; whichever book is asking marks its own positions, and two books
    asking the same question get the same corpus with their own flags.
    """
    from ...universe import search as uni_search
    from .catalog import load_catalog

    catalog = load_catalog()
    theme = catalog.get(theme_id)
    if theme is None:
        return {"error": f"unknown theme_id {theme_id!r}",
                "known_themes": sorted(catalog.themes)}

    items = read_items(theme_id, days)
    corpus = "\n".join(
        f"{it.get('title', '')} {_strip_html(it.get('summary', ''))}"
        for it in items)

    counts = _count_masked(corpus, _name_entries()) if corpus else {}
    held = set(held or ())

    mentioned = []
    for sid, cnt in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]:
        info = uni_search.get(sid) or {}
        name = info.get("name_kr") or info.get("name_en") or sid
        urls = [it.get("url") for it in items
                if name in f"{it.get('title', '')} "
                           f"{_strip_html(it.get('summary', ''))}"][:3]
        mentioned.append({
            "symbol_id": sid,
            "name": name,
            "market": info.get("market"),
            "is_etf": bool(info.get("is_etf")),
            "mentions": cnt,
            "held": sid in held,
            "urls": [u for u in urls if u],
        })

    tagged = _tagged(theme_id)
    for row in tagged:
        info = uni_search.get(row["symbol_id"]) or {}
        row["name"] = info.get("name_kr") or info.get("name_en") or row["symbol_id"]
        row["held"] = row["symbol_id"] in held

    return {
        "theme_id": theme_id,
        "name_kr": theme.name_kr,
        "days": days,
        "items_scanned": len(items),
        "tagged": tagged,
        "mentioned": mentioned,
        "note": (f"tagged = symbol_themes 역조회(보유 기반, 정밀). "
                 f"mentioned = 최근 {days}일 뉴스 본문에서 종목명 literal 매칭"
                 f"(발굴용, 동명이의 오탐 가능). "
                 f"한글 {_MIN_LEN_KR}자·영문 {_MIN_LEN_EN}자 미만 종목명은 "
                 f"오탐이 많아 매칭에서 제외됨."),
    }
