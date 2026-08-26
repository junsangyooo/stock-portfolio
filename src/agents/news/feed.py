"""Landing-page qualitative feed — the read side of the collect step.

Groups the latest collected signals into news (lagging media) and social
(coincident, e.g. Reddit/X). arXiv (leading) is excluded: it feeds per-symbol
evaluate, not the headline feed. Items sharing one URL across themes merge
into a single card carrying every theme badge, and the latest daily digest's
importance (1~5) is joined so the front can star key articles — both without
any extra LLM cost.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from ...core import paths
from ...core.kst import KST
from .catalog import load_catalog
from .mapping import live_themes


def published_iso(published) -> Optional[str]:
    """Normalize a feed 'published' string (RFC822 or ISO) to ISO-8601."""
    if not published or not isinstance(published, str):
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(published).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _iso_epoch(iso) -> Optional[float]:
    """ISO string -> epoch seconds (None-safe). Naive times assumed UTC."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _favicon(u: str) -> Optional[str]:
    """Source favicon (C-tier thumbnail fallback) via Google's favicon service
    — used when an item carries no media image."""
    try:
        host = urlparse(u or "").netloc
    except ValueError:
        host = ""
    return f"https://www.google.com/s2/favicons?domain={host}&sz=64" if host else None


def _read_digests(themes, now_kst: datetime) -> tuple[dict[str, int], list[dict]]:
    """Per theme, the newest available daily digest (today, else yesterday —
    before the dawn batch today's file doesn't exist yet).

    Returns (importance by url, one-line gist by url, per-theme overview
    bullets). All three come off the same file read: the overview and the
    per-article gist are written by the digest LLM call itself, so neither the
    '오늘의 요약' section nor the card summary costs extra inference.

    A theme with nothing collected still gets a digest file, just an empty one.
    That must not hide yesterday's summary, so the overview keeps looking back
    while importance stops at the newest file it finds.
    """
    dates = [now_kst.strftime("%Y-%m-%d"),
             (now_kst - timedelta(days=1)).strftime("%Y-%m-%d")]
    importance: dict[str, int] = {}
    gist: dict[str, str] = {}
    summary: list[dict] = []
    for t in themes:
        newest = True
        for d in dates:
            p = paths.analysis_news_daily_path(t.theme_id, d)
            if not p.exists():
                continue
            try:
                dig = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                break
            if newest:
                for di in dig.get("items", []):
                    u = (di.get("url") or "").strip()
                    if u:
                        importance[u] = max(importance.get(u, 0),
                                            int(di.get("importance") or 0))
                        if di.get("summary") and u not in gist:
                            gist[u] = str(di["summary"])
                newest = False
            bullets = dig.get("overview") or []
            if bullets:
                summary.append({"theme": t.theme_id, "theme_kr": t.name_kr,
                                "date": dig.get("date") or d, "bullets": bullets})
                break
    return importance, gist, summary


def build(limit: int = 60) -> dict:
    """Assemble the landing feed from this month's collected signals."""
    now_kst = datetime.now(KST)
    yyyymm = now_kst.strftime("%Y%m")
    cat = load_catalog()
    themes = live_themes(cat)
    name_kr = {t.theme_id: t.name_kr for t in cat.themes.values()}
    importance, gist, summary = _read_digests(themes, now_kst)

    news_by_key: dict[str, dict] = {}
    social_by_key: dict[str, dict] = {}
    for t in themes:
        p = paths.signals_news_path(t.theme_id, yyyymm)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ll = r.get("lead_lag")
            if ll == "coincident":
                bucket = social_by_key
            elif ll in ("lagging", "backbone"):
                bucket = news_by_key
            else:
                continue  # leading (arxiv) skipped for the landing feed
            url = (r.get("url") or "").strip()
            key = url or f"t:{r.get('title')}"
            prev = bucket.get(key)
            if prev is not None:
                # same article surfaced by another theme: merge instead of
                # duplicating the card (shared feeds -> up to 3x repeats)
                if not any(th["id"] == t.theme_id for th in prev["themes"]):
                    prev["themes"].append(
                        {"id": t.theme_id,
                         "kr": name_kr.get(t.theme_id, t.theme_id)})
                if not prev.get("image") and r.get("image"):
                    prev["image"] = r.get("image")
                if not prev.get("published_iso"):
                    prev["published_iso"] = published_iso(r.get("published"))
                continue
            bucket[key] = {
                "theme": t.theme_id, "theme_kr": name_kr.get(t.theme_id, t.theme_id),
                "themes": [{"id": t.theme_id,
                            "kr": name_kr.get(t.theme_id, t.theme_id)}],
                "title": r.get("title"), "url": url,
                "source": r.get("source_id"), "lead_lag": ll,
                "published": r.get("published"),
                "published_iso": published_iso(r.get("published")),
                "fetched_at": r.get("fetched_at"),
                "importance": importance.get(url),   # digest join (may be None)
                "gist": gist.get(url),               # digest one-liner (may be None)
                "image": r.get("image"),             # A: RSS media thumbnail
                "favicon": _favicon(url),            # C: source favicon fallback
            }

    def _sort_key(x):
        return (_iso_epoch(x.get("published_iso"))
                or _iso_epoch(x.get("fetched_at")) or 0.0)

    return {
        "news": sorted(news_by_key.values(), key=_sort_key, reverse=True)[:limit],
        "social": sorted(social_by_key.values(), key=_sort_key, reverse=True)[:limit],
        "summary": summary,
        "themes": [{"id": t.theme_id, "name_kr": t.name_kr} for t in themes],
        "yyyymm": yyyymm,
    }
