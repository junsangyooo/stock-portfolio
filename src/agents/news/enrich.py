"""Per-article enrichment — summary, importance, novelty and themes.

One call covers all four for a batch of articles, because they are the same
reading. Measured on this box (479 articles, the real daily volume):

    local Qwen3-4B, summary only, batch 20   20.3 s/item   2h42m  + 1h03m themes
    Claude Haiku,   all four,     batch 80    2.26 s/item      18m, 0 dropped

The bottleneck on the local model is prefill, not generation, so its cost is
linear in input length and splitting the work into cheaper passes does not
help — scoring alone still took 1h21m. Whole-corpus enrichment is simply not
something a 4B model on four ARM cores can do twice a day. It runs on the CLI
(subscription, so §7's no-paid-API rule is untouched) and the local model keeps
the weekly and monthly summaries, which are one call per theme.

Batches are the unit of failure: a batch that fails or comes back unparseable
leaves its articles unenriched, and the next run picks them up.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from ...core.kst import KST
from ..llm_backend import FAST_MODEL, ClaudeCliBackend, _parse_json
from .catalog import Catalog, load_catalog
from .db import connect

log = logging.getLogger(__name__)

_BATCH = 80          # measured flat from 20 to 80; larger is untested
_BODY_TRUNC = 400    # chars of the source's own summary fed to the model

_SYSTEM = ("너는 투자 정보 큐레이터다. 주어진 기사 목록을 빠짐없이 평가한다. "
           "JSON만 출력한다.")


def _prompt(items: list[dict], theme_ids: list[str]) -> str:
    listing = "\n".join(
        f"[{i}] {r['title']}\n    {(r['body'] or '')[:_BODY_TRUNC]}"
        for i, r in enumerate(items))
    return (
        f"기사 {len(items)}건:\n\n{listing}\n\n"
        "각 건마다 다음 넷을 매겨라.\n"
        "1) s: 한국어 한 문장 요약\n"
        "2) imp: 투자 판단 중요도 1~5\n"
        "3) nov: 새로운 사실이면 true, 이미 알려진 것의 반복이면 false\n"
        "4) t: 해당 분야(복수 가능, 없으면 빈 배열)\n\n"
        f"분야 목록: {', '.join(theme_ids)}\n\n"
        '출력(JSON): {"r":[{"i":0,"s":"한문장","imp":3,"nov":true,"t":["ai_sw"]}]}\n'
        "- i는 위 번호. 전 건 빠짐없이. JSON만."
    )


def pending(limit: int = 0, date: Optional[str] = None) -> list[dict]:
    """Articles collected but not yet enriched, newest first."""
    sql = ("SELECT signal_id, title, body FROM signals "
           "WHERE enriched_at IS NULL")
    args: list = []
    if date:
        sql += " AND collected_on = ?"
        args.append(date)
    sql += " ORDER BY collected_on DESC, signal_id DESC"
    if limit > 0:
        sql += " LIMIT ?"
        args.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _apply(items: list[dict], parsed: list[dict], valid: set[str],
           now_iso: str) -> int:
    """Write one batch's results. Returns how many articles were enriched."""
    rows, theme_rows = [], []
    for e in parsed:
        i = e.get("i")
        if not isinstance(i, int) or not (0 <= i < len(items)):
            continue
        sid = items[i]["signal_id"]
        try:
            imp = max(1, min(5, int(e.get("imp", 3))))
        except (TypeError, ValueError):
            imp = 3
        rows.append((str(e.get("s") or "")[:300], imp,
                     1 if e.get("nov") else 0, now_iso, sid))
        for t in (e.get("t") or []):
            if t in valid:
                theme_rows.append((sid, t))
    if not rows:
        return 0
    with connect() as c:
        c.executemany(
            "UPDATE signals SET summary=?, importance=?, novelty=?, "
            "enriched_at=? WHERE signal_id=?", rows)
        if theme_rows:
            c.executemany(
                "INSERT OR IGNORE INTO signal_themes (signal_id, theme_id) "
                "VALUES (?,?)", theme_rows)
        c.commit()
    return len(rows)


def run(*, catalog: Optional[Catalog] = None, date: Optional[str] = None,
        limit: int = 0, batch: int = _BATCH, backend=None,
        timeout_s: float = 600) -> dict:
    """Enrich everything outstanding (optionally just one collection date)."""
    catalog = catalog or load_catalog()
    theme_ids = sorted(catalog.themes)
    valid = set(theme_ids)
    todo = pending(limit=limit, date=date)
    if not todo:
        return {"pending": 0, "enriched": 0, "batches": 0, "failed": 0}

    backend = backend or ClaudeCliBackend(model=FAST_MODEL)
    now_iso = datetime.now(KST).isoformat()
    enriched = batches = failed = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        try:
            raw = backend.call(_SYSTEM, _prompt(chunk, theme_ids),
                               timeout_s=timeout_s, model=FAST_MODEL)
            parsed = _parse_json(raw).get("r") or []
        except Exception as e:  # noqa: BLE001 — the batch is the unit of failure
            log.warning("enrich batch %d failed: %s", i // batch, str(e)[:200])
            failed += len(chunk)
            continue
        got = _apply(chunk, parsed, valid, now_iso)
        if got < len(chunk):
            # left unenriched on purpose: the next run retries them rather than
            # stamping an article the model never actually looked at
            log.warning("enrich batch %d returned %d/%d", i // batch, got,
                        len(chunk))
        enriched += got
        batches += 1
    return {"pending": len(todo), "enriched": enriched, "batches": batches,
            "failed": failed}


if __name__ == "__main__":   # manual: python -m src.agents.news.enrich
    import sys
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run(), ensure_ascii=False, indent=2))
    sys.exit(0)
