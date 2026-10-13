"""Read side of the analysis — the counterpart to run.py.

run() writes; this reads. Without it a caller that wants "is there an analysis
for this symbol today, and if not make one" has to know the storage layout,
which means every caller reinvents the same glob. The news pipeline already
works this way (research/news/read.py) and seven call sites go through it.

Results live at data/analysis/analysis/{SYMBOL}/{YYYYMMDD}.json, one per
symbol per day, written by verdict.persist. Nothing here knows about an
account: the same stored result serves every account.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from ...core import paths
from .store import KIND  # noqa: F401 — re-exported by the package

log = logging.getLogger(__name__)

# Fields a caller wants for a badge or a shortlist — small enough to fetch for
# a hundred symbols at once.
_SUMMARY_FIELDS = ("label", "score", "confidence", "status", "summary_ko")


def _dir(symbol_id: str):
    # Read the root at call time — binding it at import would make the path
    # unredirectable, which is exactly how a test ends up writing into the
    # real data tree (src/market/store/db.py hit the same thing).
    return paths.ANALYSIS_DIR / KIND / symbol_id.replace(":", "_")


def _as_date(stem: str) -> str:
    """'20260901' → '2026-09-01'; anything else passes through."""
    return f"{stem[:4]}-{stem[4:6]}-{stem[6:]}" \
        if len(stem) == 8 and stem.isdigit() else stem


@lru_cache(maxsize=256)
def _read_at(path: str, mtime_ns: int) -> Optional[dict]:
    # Keyed by mtime so a rewrite (same path, new mtime) is a miss: a stored
    # result is 14~19KB (measured over 244 of them) and summary() for a
    # hundred symbols would otherwise parse all of it on every badge refresh.
    p = Path(path)
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # A truncated write would otherwise read as "not analyzed yet" and
        # blank the symbol with no trace of why.
        log.error("analysis %s unreadable: %s", p, e)
        return None
    body.setdefault("date", _as_date(p.stem))
    return body


def _read(path) -> Optional[dict]:
    try:
        body = _read_at(str(path), path.stat().st_mtime_ns)
    except OSError as e:
        log.error("analysis %s unreadable: %s", path, e)
        return None
    # A copy: the cached dict is shared across callers.
    return dict(body) if body is not None else None


def latest_date(symbol_id: str) -> Optional[str]:
    """Newest stored date, WITHOUT parsing the body.

    A result carries the whole chart fact set; a badge row only needs to know
    whether one exists and how old it is, so a hundred symbols must not cost a
    hundred json.loads.
    """
    d = _dir(symbol_id)
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    return _as_date(files[-1].stem) if files else None


def latest(symbol_id: str) -> Optional[dict]:
    """Newest stored analysis, or None."""
    d = _dir(symbol_id)
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    return _read(files[-1]) if files else None


def on(symbol_id: str, date: str) -> Optional[dict]:
    """The analysis for one date ('YYYY-MM-DD' or 'YYYYMMDD'), or None."""
    p = _dir(symbol_id) / f"{date.replace('-', '')}.json"
    return _read(p) if p.exists() else None


def summary(symbol_ids: Iterable[str]) -> dict:
    """{symbol_id: {label, score, confidence, status, summary_ko, date, coords}}.

    Symbols with nothing stored appear with `date: None` rather than being
    dropped — a caller asking about twenty symbols wants twenty answers, and
    "never analyzed" is an answer.
    """
    out: dict = {}
    for sid in symbol_ids:
        body = latest(sid)
        if body is None:
            out[sid] = {"date": None, "coords": None,
                        **{f: None for f in _SUMMARY_FIELDS}}
            continue
        out[sid] = {"date": body.get("date"), "coords": _coords(body),
                    **{f: body.get(f) for f in _SUMMARY_FIELDS}}
    return out


def _coords(body: dict) -> dict:
    """The analysis's own stop, first target and entry zone — what a card
    states against a holding in place of a plan. Missing ones are None."""
    lv = body.get("levels") or {}
    ez = lv.get("entry_zone") or {}
    t = (lv.get("targets") or [None])[0]
    return {"stop": (lv.get("invalidation") or {}).get("price"),
            "target": t.get("price") if isinstance(t, dict) else None,
            "entry_low": ez.get("low"), "entry_high": ez.get("high")}
