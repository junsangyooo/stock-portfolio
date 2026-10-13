"""RSS 2.0 / RDF / Atom parsing + fetching (stdlib only).

``parse_feed`` is pure (string in, RawItem list out) so it is unit-tested
without network. ``fetch_rss`` adds the HTTP I/O. Handles the three common
feed dialects by matching item/entry elements regardless of namespace.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from .base import RawItem

# A named bot UA gets 429/403 from reddit, cloudflare-fronted feeds
# (marinelog, voxelmatters) and bls.gov. Feeds are public pages served
# to any browser; identifying as one is what makes them reachable.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_UA_BOT = "portfolio-dashboard/1.0 (+news-agent)"
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_IMG_IN_HTML = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.I)


def _local(tag: str) -> str:
    """Strip XML namespace: '{http://...}item' -> 'item'."""
    return tag.rsplit("}", 1)[-1].lower()


def _is_image(url: Optional[str], typ: Optional[str]) -> bool:
    """Heuristic: an element's url attr points to an image (by MIME or extension)."""
    if not url:
        return False
    if typ and "image" in typ.lower():
        return True
    return url.split("?", 1)[0].lower().endswith(_IMG_EXT)


def _parse_entry(node: ET.Element, source_id: str, kind: str) -> Optional[RawItem]:
    title = url = url_fallback = published = summary = image = ""
    for c in node:
        ln = _local(c.tag)
        text = "".join(c.itertext()).strip()
        if ln == "title" and not title:
            title = text
        elif ln == "link":
            # RSS: <link>url</link> ; Atom: <link href="url" .../>. The first
            # link wins, whatever its rel.
            href = c.get("href")
            if href and not url:
                url = href
            elif text and not url:
                url = text
        elif ln in ("pubdate", "published", "updated", "date") and not published:
            published = text
        elif ln in ("description", "summary", "content"):
            if text and not summary:
                summary = text
            # media:content / atom <content> may carry an image url attribute
            if not image and _is_image(c.get("url"), c.get("type") or c.get("medium")):
                image = c.get("url")
        elif ln == "thumbnail":  # media:thumbnail — always an image
            if c.get("url") and not image:
                image = c.get("url")
        elif ln == "enclosure":  # RSS <enclosure url=... type=image/*>
            if not image and _is_image(c.get("url"), c.get("type")):
                image = c.get("url")
        elif ln in ("guid", "id") and not url_fallback:
            url_fallback = text
    if not image and summary:  # fallback: first <img> embedded in the HTML summary
        m = _IMG_IN_HTML.search(summary)
        if m and m.group(1).startswith("http"):
            image = m.group(1)
    if not title:
        return None
    if image:  # feeds embed &amp;/&#038; entities in query strings (electrek)
        image = html.unescape(image)
    title = html.unescape(title)  # einfomax double-encodes (&quot; in titles)
    return RawItem(source_id=source_id, kind=kind, title=title,
                   url=url or url_fallback, published=published or None,
                   summary=summary, image=image or None)


def parse_feed(xml_text: str, source_id: str, kind: str = "rss") -> list[RawItem]:
    """Parse RSS/RDF/Atom XML into RawItems. Returns [] on parse error."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[RawItem] = []
    for node in root.iter():
        if _local(node.tag) in ("item", "entry"):
            it = _parse_entry(node, source_id, kind)
            if it:
                items.append(it)
    return items


def _get(url: str, ua: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_rss(url: str, source_id: str, kind: str = "rss",
              timeout: float = 15.0) -> list[RawItem]:
    """Fetch + parse a feed. Raises urllib errors on network failure."""
    try:
        data = _get(url, _UA, timeout)
    except urllib.error.HTTPError as e:
        # The two camps pull opposite ways: cloudflare-fronted feeds refuse
        # a named bot, hankyung refuses a browser. One retry covers both
        # without a per-source UA column.
        if e.code != 403:
            raise
        data = _get(url, _UA_BOT, timeout)
    return parse_feed(data.decode("utf-8", errors="replace"), source_id, kind)
