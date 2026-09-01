"""og:image scraping (B-tier thumbnail) — NO LLM, stdlib only.

Fetches the first ~64KB of an article page and extracts the
``og:image`` (or ``twitter:image`` fallback) meta tag. Used by collect
to enrich items whose RSS feed carried no media image (most KR press).
Items are collected exactly once (dedup), so no cache is needed.
"""
from __future__ import annotations

import html
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

_UA = "portfolio-dashboard/1.0 (+news-agent)"
_MAX_BYTES = 65536  # og tags live in <head>; never read the whole page

# <meta property="og:image" content="..."> with attributes in either order.
_OG_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']', re.I),
]


def extract_og_image(html_text: str) -> Optional[str]:
    """Pure extraction: og:image -> twitter:image, unescaped, http(s) only."""
    for pat in _OG_PATTERNS:
        m = pat.search(html_text)
        if not m:
            continue
        url = html.unescape(m.group(1).strip())
        if url.startswith(("http://", "https://")):
            return url
    return None


def fetch_og_image(url: str, timeout: float = 5.0) -> Optional[str]:
    """GET the article page (first 64KB) and extract its og:image.

    Every failure (network, HTTP error, parse) returns None — thumbnail
    enrichment must never break collection.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            head = resp.read(_MAX_BYTES)
        return extract_og_image(head.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 — enrichment is best-effort by design
        return None


def enrich_images(items: list, *, max_fetch: int = 60, workers: int = 8,
                  timeout: float = 5.0) -> int:
    """Fill ``item.image`` in place for items lacking one. Returns #filled.

    Caps at ``max_fetch`` fetches per call so an abnormal burst of new
    items cannot stall the batch. Serial worst case (60 x 5s) is avoided
    with a small thread pool; the 06:00 cron tolerates a few minutes.
    """
    targets = [it for it in items if not it.image and it.url][:max_fetch]
    if not targets:
        return 0
    filled = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for it, img in zip(targets, pool.map(
                lambda i: fetch_og_image(i.url, timeout=timeout), targets)):
            if img:
                it.image = img
                filled += 1
    return filled
