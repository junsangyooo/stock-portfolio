"""Collection sweep — every source once, into data/news.db.

The old collector looped over *themes* and fetched each theme's own source
list, so a feed covering three fields was fetched three times and its articles
stored three times. This loops over *sources*: one fetch, one row, and theme
membership is attached later per article by the enrichment pass.

It is also no longer gated on holdings. Buying or selling a stock does not
widen or narrow what gets collected — every field is always on, and the
filtering lives on the read side.

"Everything since the last cycle" comes from `source_cursor`: an item older
than the newest one we already accepted from that feed is skipped without
being parsed further. Dedup on `dedup_key` still backs this up, because feeds
lie about timestamps and some carry none at all.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from ...core.kst import KST
from .catalog import Catalog, Feed, all_keywords, load_catalog, load_sources
from .db import connect
from .sources.arxiv import fetch_arxiv
from .sources.og_image import enrich_images
from .sources.rss import fetch_rss

log = logging.getLogger(__name__)

# Short latin keywords (AI, ESS, HBM, LFP) need letter boundaries — a bare
# substring match fires on 'ai' inside "Taiwan" and "daily".
_ASCII_SHORT = 4


def _keyword_hit(title: str, summary: str, keywords: list[str]) -> bool:
    text = f"{title} {summary}".lower()
    for k in keywords:
        if k.isascii() and len(k) <= _ASCII_SHORT:
            if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text):
                return True
        elif k in text:
            return True
    return False


def _published_iso(raw: Optional[str]) -> Optional[str]:
    """Feed timestamp (RFC822 or ISO) → ISO, or None when unparseable."""
    if not raw:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _cursors() -> dict[str, Optional[str]]:
    with connect() as c:
        return {r["source_id"]: r["last_fetched_at"]
                for r in c.execute(
                    "SELECT source_id, last_fetched_at FROM source_cursor")}


def _known_keys(keys: list[str]) -> set[str]:
    """Which of these dedup keys we already hold. Chunked — SQLite caps the
    number of bound parameters, and a busy feed can exceed it."""
    have: set[str] = set()
    with connect() as c:
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            marks = ",".join("?" * len(part))
            have.update(r[0] for r in c.execute(
                f"SELECT dedup_key FROM signals WHERE dedup_key IN ({marks})",
                part).fetchall())
    return have


def _mark(source_id: str, *, newest: Optional[str], ok: bool,
          error: Optional[str], now_iso: str) -> None:
    with connect() as c:
        if ok:
            c.execute(
                "INSERT INTO source_cursor (source_id, last_fetched_at, "
                "last_ok_at, last_error, n_consecutive_fail) "
                "VALUES (?,?,?,NULL,0) ON CONFLICT(source_id) DO UPDATE SET "
                "last_fetched_at = MAX(COALESCE(excluded.last_fetched_at,''), "
                "                      COALESCE(last_fetched_at,'')), "
                "last_ok_at = excluded.last_ok_at, last_error = NULL, "
                "n_consecutive_fail = 0",
                (source_id, newest, now_iso))
        else:
            c.execute(
                "INSERT INTO source_cursor (source_id, last_error, "
                "n_consecutive_fail) VALUES (?,?,1) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "last_error = excluded.last_error, "
                "n_consecutive_fail = n_consecutive_fail + 1",
                (source_id, (error or "")[:300]))
        c.commit()


def collect_one(feed: Feed, keywords: list[str], cursor: Optional[str],
                *, now: datetime, max_per_source: int = 40,
                timeout: float = 15.0) -> dict:
    """Fetch one feed and store what is new. Never raises."""
    stat = {"source": feed.source_id, "written": 0, "filtered": 0,
            "stale": 0, "dup": 0, "error": None}
    now_iso = now.isoformat()
    try:
        if feed.kind == "rss":
            items = fetch_rss(feed.url, feed.source_id, timeout=timeout)
        elif feed.kind == "arxiv":
            # the arXiv API is slower than RSS; give it more headroom
            items = fetch_arxiv(feed.query, feed.source_id,
                                timeout=max(timeout, 30))
        else:
            return stat                        # 'x' (social) — no adapter yet
    except Exception as e:  # noqa: BLE001 — one dead feed is not a dead sweep
        stat["error"] = str(e)[:200]
        log.warning("collect %s failed: %s", feed.source_id, e)
        _mark(feed.source_id, newest=None, ok=False, error=stat["error"],
              now_iso=now_iso)
        return stat

    items = items[:max_per_source]
    fresh, newest = [], cursor or ""
    for it in items:
        pub = _published_iso(it.published)
        # An item the feed dates at or before our cursor was seen last cycle.
        # Undated items fall through to the dedup check rather than being
        # dropped — plenty of feeds omit the field entirely.
        if cursor and pub and pub <= cursor:
            stat["stale"] += 1
            continue
        if feed.broad and keywords and not _keyword_hit(it.title, it.summary,
                                                        keywords):
            stat["filtered"] += 1
            continue
        fresh.append((it, pub))
        if pub and pub > newest:
            newest = pub

    if fresh:
        known = _known_keys([it.dedup_key() for it, _ in fresh])
        before = len(fresh)
        fresh = [(it, pub) for it, pub in fresh if it.dedup_key() not in known]
        stat["dup"] = before - len(fresh)

    if fresh:
        # thumbnail scrape for the ones the feed left imageless; dedup above
        # means each article passes here exactly once
        enrich_images([it for it, _ in fresh], max_fetch=len(fresh))
        collected_on = now.strftime("%Y-%m-%d")
        with connect() as c:
            c.executemany(
                "INSERT OR IGNORE INTO signals "
                "(dedup_key, url, title, body, source_id, source_group, "
                " lead_lag, image, published_at, fetched_at, collected_on) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(it.dedup_key(), it.url or None, it.title, it.summary or None,
                  feed.source_id, feed.group, feed.lead_lag, it.image, pub,
                  now_iso, collected_on) for it, pub in fresh])
            c.commit()
        stat["written"] = len(fresh)

    _mark(feed.source_id, newest=newest or None, ok=True, error=None,
          now_iso=now_iso)
    return stat


def collect_all(*, catalog: Optional[Catalog] = None,
                now: Optional[datetime] = None,
                max_per_source: int = 40, timeout: float = 15.0) -> dict:
    """Sweep every source in sources.json once."""
    catalog = catalog or load_catalog()
    now = now or datetime.now(KST)
    keywords = all_keywords(catalog)
    cursors = _cursors()

    stats = []
    for feed in load_sources():
        stats.append(collect_one(feed, keywords, cursors.get(feed.source_id),
                                 now=now, max_per_source=max_per_source,
                                 timeout=timeout))
    return {
        "date": now.strftime("%Y-%m-%d"),
        "sources": len(stats),
        "written": sum(s["written"] for s in stats),
        "filtered": sum(s["filtered"] for s in stats),
        "stale": sum(s["stale"] for s in stats),
        "dup": sum(s["dup"] for s in stats),
        "failed": [s["source"] for s in stats if s["error"]],
        "detail": stats,
    }
