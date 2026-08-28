"""Per-theme daily overview — "what happened in this field today".

Runs after enrichment, one call per theme over that day's one-line summaries.
The input is already compressed, so prefill is roughly a sixth of what the old
digest fed the model and this is back inside what the on-host Qwen can do;
`local_or_claude` falls through to Haiku when it is down.

One call, four outputs: the bullets the landing page renders, a sentiment
score, and the open issues worth watching. Those last two used to live in a
rolling state file with its own LLM rewrite; they are the same reading as the
bullets, so they ride along and the second long memory goes away.

Its output is also what the weekly cycle reads a week later. Nothing
downstream ever re-reads the articles — compression only flows one way.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from ...core.kst import KST
from ..llm_backend import FAST_MODEL, _parse_json, local_or_claude
from .catalog import Catalog, load_catalog
from .db import connect

log = logging.getLogger(__name__)

_MAX_ITEMS = 60      # per theme per day; ordered by importance
_MAX_BULLETS = 8
_MAX_WATCH = 5
_TRENDS = ("improving", "flat", "deteriorating")

_SYSTEM = ("너는 특정 산업 분야를 추적하는 애널리스트다. 하루치 항목을 읽고 "
           "무슨 일이 있었는지 정리한다. JSON만 출력한다.")


def items_for(theme_id: str, date: str, limit: int = _MAX_ITEMS) -> list[dict]:
    """That theme's enriched articles for one collection date, most important
    first."""
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.signal_id, s.title, s.summary, s.importance, s.novelty, "
            "       s.lead_lag, s.source_id, s.url "
            "FROM signals s JOIN signal_themes st USING (signal_id) "
            "WHERE st.theme_id = ? AND s.collected_on = ? "
            "  AND s.enriched_at IS NOT NULL "
            "ORDER BY s.importance DESC, s.signal_id DESC LIMIT ?",
            (theme_id, date, limit)).fetchall()]


def _prompt(theme_name: str, date: str, items: list[dict]) -> str:
    listing = "\n".join(
        f"- [{r['lead_lag']}/중요도{r['importance']}"
        f"{'/신규' if r['novelty'] else ''}] {r['summary'] or r['title']}"
        for r in items)
    return (
        f"분야: {theme_name}\n날짜: {date}\n\n항목 {len(items)}건:\n{listing}\n\n"
        "다음 셋을 작성하라.\n"
        "- overview: 이 분야에 오늘 무슨 일이 있었는지 불렛. 각 한국어 1~2문장. "
        "기사 제목 나열이 아니라 흐름과 의미를. 개수 제한은 없되 사소하면 빼라.\n"
        "- sentiment: 이 분야의 현재 분위기. score는 -1(매우 부정)~1(매우 긍정), "
        "trend는 improving|flat|deteriorating.\n"
        "- watchlist: 전개 중이거나 예정된, 계속 지켜봐야 할 이슈 최대 5개.\n"
        '출력(JSON): {"overview": ["불렛", "..."], '
        '"sentiment": {"score": 0.0, "trend": "flat"}, "watchlist": ["..."]}'
    )


def build_theme(theme_id: str, theme_name: str, date: str,
                backend=None, timeout_s: float = 300) -> Optional[dict]:
    """One theme's overview for one date. None when there is nothing to say."""
    items = items_for(theme_id, date)
    if not items:
        return None
    prompt = _prompt(theme_name, date, items)
    if backend is not None:                       # tests inject their own
        raw = backend.call(_SYSTEM, prompt, timeout_s=timeout_s,
                           model=FAST_MODEL)
    else:
        raw, _ = local_or_claude(_SYSTEM, prompt, claude_model=FAST_MODEL,
                                 stage=f"daily:{theme_id}",
                                 validate=_parse_json,
                                 claude_timeout_s=timeout_s)
    data = _parse_json(raw)
    # Each field is parsed independently: a malformed watchlist must not cost
    # us the bullets, and vice versa.
    ov = data.get("overview") or []
    bullets = [str(b).strip()[:300] for b in ov if str(b).strip()][:_MAX_BULLETS] \
        if isinstance(ov, list) else []
    wl = data.get("watchlist") or []
    watch = [str(w).strip()[:200] for w in wl if str(w).strip()][:_MAX_WATCH] \
        if isinstance(wl, list) else []
    sent = data.get("sentiment") if isinstance(data.get("sentiment"), dict) else {}
    try:
        score = max(-1.0, min(1.0, float(sent.get("score"))))
    except (TypeError, ValueError):
        score = None
    trend = sent.get("trend") if sent.get("trend") in _TRENDS else None
    return {"theme_id": theme_id, "date": date, "overview": bullets,
            "sentiment_score": score, "sentiment_trend": trend,
            "watchlist": watch, "n_items": len(items)}


def write(row: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO theme_daily (theme_id, date, overview, "
            "sentiment_score, sentiment_trend, watchlist, n_items, built_at) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(theme_id, date) DO UPDATE SET "
            "overview=excluded.overview, "
            "sentiment_score=excluded.sentiment_score, "
            "sentiment_trend=excluded.sentiment_trend, "
            "watchlist=excluded.watchlist, n_items=excluded.n_items, "
            "built_at=excluded.built_at",
            (row["theme_id"], row["date"],
             json.dumps(row["overview"], ensure_ascii=False),
             row.get("sentiment_score"), row.get("sentiment_trend"),
             json.dumps(row.get("watchlist") or [], ensure_ascii=False),
             row["n_items"], datetime.now(KST).isoformat()))
        c.commit()


def run(date: Optional[str] = None, *, catalog: Optional[Catalog] = None,
        theme: Optional[str] = None, backend=None) -> dict:
    """Build the day's overview for every theme that collected anything."""
    catalog = catalog or load_catalog()
    date = date or datetime.now(KST).strftime("%Y-%m-%d")
    themes = ([catalog.get(theme)] if theme
              else list(catalog.themes.values()))
    built, empty, failed = 0, 0, []
    for t in [x for x in themes if x]:
        try:
            row = build_theme(t.theme_id, t.name_kr, date, backend=backend)
        except Exception as e:  # noqa: BLE001 — one theme is not the batch
            log.warning("daily %s failed: %s", t.theme_id, str(e)[:200])
            failed.append(t.theme_id)
            continue
        if row is None:
            empty += 1
            continue
        write(row)
        built += 1
    return {"date": date, "built": built, "empty": empty, "failed": failed}
