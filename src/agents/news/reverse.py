"""Theme → symbols (reverse of mapping.py).

`mapping.py` answers "which themes does this symbol belong to?" — the direction
the holdings pipeline needs. The discovery agent needs the opposite: given a
theme that is moving, which listed companies are in it?

Two sources, because neither alone is enough:

  tagged     holding_themes reversed. Precise, but only ever contains symbols
             somebody already holds — useless for finding something new.
  mentioned  company names recovered from the theme's own news corpus
             (data/signals/news/{theme}/{yyyymm}.jsonl). This is the mechanical
             form of "read today's news, notice a company, go look it up".

Matching is literal substring against the universe's display names, longest
name first, with matched spans masked so "한화" inside "한화에어로스페이스"
does not count twice. No LLM, no new dependency.

Deliberate floor: Hangul names shorter than 3 chars and English names shorter
than 4 are skipped — two-letter names collide with ordinary prose far more
often than they identify the company. The cost is that a handful of small caps
(한창, 삼양) can never be found this way; the returned `note` says so.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

from ...core.kst import KST
from ...core.paths import signals_news_path

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


def _months_back(days: int) -> list[str]:
    """yyyymm strings covering `days` back from today (KST), newest first."""
    now = datetime.now(KST)
    seen, out = set(), []
    for d in (now, now - timedelta(days=days)):
        ym = d.strftime("%Y%m")
        if ym not in seen:
            seen.add(ym)
            out.append(ym)
    return out


def _published_dt(item: dict) -> Optional[datetime]:
    """Always tz-aware. Feeds mix offsets with bare local timestamps, and a
    naive one anywhere in the list poisons the whole sort."""
    for key in ("published", "fetched_at"):
        raw = item.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=KST)
    return None


def read_items(theme_id: str, days: int) -> list[dict]:
    """Raw news/RSS/arXiv items for a theme within the window, newest first."""
    cutoff = datetime.now(KST) - timedelta(days=days)
    items: list[dict] = []
    for ym in _months_back(days):
        path = signals_news_path(theme_id, ym)
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    it = json.loads(line)
                except json.JSONDecodeError:
                    continue
                dt = _published_dt(it)
                if dt is None or dt.astimezone(KST) < cutoff:
                    continue
                it["_dt"] = dt
                items.append(it)
    items.sort(key=lambda x: x["_dt"], reverse=True)
    return items


def _name_entries() -> list[tuple[str, str]]:
    """(display_name, symbol_id) for every universe entry worth matching,
    longest name first so the masking pass prefers the specific name."""
    from ...universe import search as uni_search
    uni_search._ensure_loaded()  # noqa: SLF001 — module-level cache, no public warm
    entries: list[tuple[str, str]] = []
    for it in uni_search._items:  # noqa: SLF001
        sid = it.get("symbol_id")
        if not sid:
            continue
        for key, floor in (("name_kr", _MIN_LEN_KR), ("name_en", _MIN_LEN_EN)):
            name = (it.get(key) or "").strip()
            if len(name) >= floor and name not in _STOPNAMES:
                entries.append((name, sid))
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
    """holding_themes reversed — symbols somebody already mapped to this theme."""
    from ...portfolio.store.db import connect
    with connect() as conn:
        rows = conn.execute(
            "SELECT symbol_id, relation, weight FROM holding_themes "
            "WHERE theme_id = ? ORDER BY relation, symbol_id", (theme_id,)
        ).fetchall()
    return [{"symbol_id": r["symbol_id"], "relation": r["relation"],
             "weight": float(r["weight"])} for r in rows]


def _held_symbols() -> set[str]:
    from ...portfolio.store.db import connect
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol_id FROM holdings WHERE qty > 0").fetchall()
    return {r[0] for r in rows}


def symbols_for_theme(theme_id: str, days: int = 7, limit: int = 30) -> dict:
    """Companies attached to a theme, from tagging and from its news corpus.

    Returns tagged[] (precise, holdings-derived) and mentioned[] (discovery,
    ranked by mention count) — the caller decides which to trust.
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
    held = _held_symbols()

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
        "note": (f"tagged = holding_themes 역조회(보유 기반, 정밀). "
                 f"mentioned = 최근 {days}일 뉴스 본문에서 종목명 literal 매칭"
                 f"(발굴용, 동명이의 오탐 가능). "
                 f"한글 {_MIN_LEN_KR}자·영문 {_MIN_LEN_EN}자 미만 종목명은 "
                 f"오탐이 많아 매칭에서 제외됨."),
    }
