"""arXiv export API adapter (Research / leading signal).

The API returns Atom, so we reuse rss.parse_feed. Query syntax:
  https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate
Docs: https://info.arxiv.org/help/api/user-manual.html
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request

from .base import RawItem
from .rss import parse_feed, _UA

_API = "https://export.arxiv.org/api/query"

# arXiv API etiquette: no more than 1 request per 3 seconds. Queries used to
# fire back-to-back across themes, causing most fetch failures (429s).
_MIN_INTERVAL_S = 3.0
_RETRY_BACKOFF_S = 10.0
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def fetch_arxiv(query: str, source_id: str, max_results: int = 30,
                timeout: float = 20.0) -> list[RawItem]:
    """Fetch recent papers for an arXiv search query (newest first).

    `query` is a raw arXiv search_query expression, e.g.
    'cat:cs.AI OR cat:cs.LG' or 'all:holotomography'.
    Rate-limited module-wide (3s between calls); one retry on 429.
    """
    params = urllib.parse.urlencode({
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    })
    req = urllib.request.Request(f"{_API}?{params}",
                                 headers={"User-Agent": _UA})
    for attempt in (0, 1):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            break
        except urllib.error.HTTPError as e:
            if attempt == 0 and e.code == 429:
                time.sleep(_RETRY_BACKOFF_S)
                continue
            raise
    items = parse_feed(data.decode("utf-8", errors="replace"),
                       source_id, kind="arxiv")
    return items
