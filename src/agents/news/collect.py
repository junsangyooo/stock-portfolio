"""L3 collect — fetch theme sources into data/signals/news/{theme}/{yyyymm}.jsonl.

Theme-level (not symbol-level): same-theme stocks share one collection (D2).
Dedup by url (or title hash) against the existing monthly jsonl so re-runs are
idempotent. lead/lag tag is derived from the source group (D5):
  research -> leading | social -> coincident | news -> lagging
  (macro theme overrides all to 'backbone').
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from ...core.kst import KST as _KST
from typing import Optional

from ...core.paths import signals_news_path
from .catalog import Theme
from .sources.arxiv import fetch_arxiv
from .sources.og_image import enrich_images
from .sources.rss import fetch_rss


_LEAD_LAG = {"research": "leading", "social": "coincident", "news": "lagging"}

# short latin keywords (AI, ESS, HBM, LFP ...) need word boundaries — a bare
# substring match would fire on e.g. 'ai' in "Taiwan"/"daily".
_ASCII_SHORT = 4


def _theme_keywords(theme: Theme) -> list[str]:
    kws = theme.keywords or {}
    return [k.lower() for k in (kws.get("ko") or []) + (kws.get("en") or []) if k]


def _keyword_hit(title: str, summary: str, keywords: list[str]) -> bool:
    """True if any theme keyword occurs in title+summary (NO LLM filter)."""
    text = f"{title} {summary}".lower()
    for k in keywords:
        if k.isascii() and len(k) <= _ASCII_SHORT:
            # letter boundaries only: blocks 'ai' in "Taiwan"/"daily" but
            # still matches model numbers like "HBM4"
            if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text):
                return True
        elif k in text:
            return True
    return False


def _lead_lag(theme_id: str, group: str) -> str:
    if theme_id == "macro":
        return "backbone"
    return _LEAD_LAG.get(group, "lagging")


def _record_key(rec: dict) -> str:
    url = (rec.get("url") or "").strip()
    if url:
        return url
    return "t:" + hashlib.sha1((rec.get("title") or "").strip().encode()).hexdigest()


def _load_existing_keys(path) -> set[str]:
    keys: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                keys.add(_record_key(json.loads(line)))
            except json.JSONDecodeError:
                continue
    return keys


def collect_theme(theme: Theme, *, max_per_source: int = 30,
                  timeout: float = 15.0, now: Optional[datetime] = None,
                  max_og_fetch: int = 60) -> dict:
    """Collect one theme's sources into its monthly jsonl. Returns stats dict."""
    now = now or datetime.now(_KST)
    yyyymm = now.strftime("%Y%m")
    fetched_at = now.isoformat()
    path = signals_news_path(theme.theme_id, yyyymm)
    path.parent.mkdir(parents=True, exist_ok=True)

    seen = _load_existing_keys(path)
    written = 0
    filtered = 0
    errors: list[dict] = []
    new_items: list[tuple] = []  # (RawItem, lead_lag)
    keywords = _theme_keywords(theme)

    for group, srcs in (theme.sources or {}).items():
        ll = _lead_lag(theme.theme_id, group)
        for s in srcs or []:
            kind = s.get("kind")
            try:
                if kind == "rss":
                    items = fetch_rss(s["url"], s["id"], timeout=timeout)
                elif kind == "arxiv":
                    # arXiv API is slower than RSS; give it more headroom.
                    items = fetch_arxiv(s["query"], s["id"], timeout=max(timeout, 30))
                else:
                    continue  # 'x' (social) -> N4
            except Exception as e:  # network/parse failures are non-fatal
                errors.append({"source": s.get("id"), "error": str(e)[:160]})
                continue
            # full-site feeds (rss/allArticle.xml etc.) dump off-topic articles
            # into the theme — keep only keyword-matching items for those.
            broad = bool(s.get("broad")) and bool(keywords)
            for it in items[:max_per_source]:
                k = it.dedup_key()
                if k in seen:
                    continue
                if broad and not _keyword_hit(it.title, it.summary, keywords):
                    filtered += 1
                    continue
                seen.add(k)
                new_items.append((it, ll))
                written += 1

    # B-tier thumbnail: og:image scrape for new items the feed left imageless.
    # Dedup means each item passes here exactly once — no cache needed.
    og_filled = enrich_images([it for it, _ in new_items],
                              max_fetch=max_og_fetch) if new_items else 0

    if new_items:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(
                json.dumps(it.to_record(theme.theme_id, ll, fetched_at),
                           ensure_ascii=False)
                for it, ll in new_items) + "\n")

    return {"theme": theme.theme_id, "written": written, "filtered": filtered,
            "og_filled": og_filled, "errors": errors, "path": str(path)}
