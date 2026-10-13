"""Source adapters for qualitative signal collection (L3).

  rss.py    RSS 2.0 / Atom feeds   (News, Social-via-RSS, Macro)
  arxiv.py  arXiv export API       (Research, leading signal)

All adapters emit ``RawItem`` (see base.py). Parsing is pure (testable without
network); fetching is the only I/O. Stdlib only — no feedparser dependency.
"""
