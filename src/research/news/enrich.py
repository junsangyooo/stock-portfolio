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

The queue has a freshness window as well as a cap. Enrichment is only ever
read inside a 7-day horizon — recent developments look 7 days back, the
roll-ups read theme_daily rather than articles, and the company-name miner
reads raw titles — so summarising a two-month-old article buys nothing. The
migration left ~29k of them behind; draining that backlog would have burnt an
hour of Haiku a day for eighteen days producing text no code path reads.

Enrich exactly as far back as something reads. The cap is then a ceiling that
almost never binds (a real day is ~250 articles), and exists so a burst or a
gap in the cron cannot make one cycle unbounded.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from ...core.kst import KST
from ..llm_backend import (FAST_MODEL, ClaudeCliBackend, LocalLlamaBackend,
                           _parse_json)
from .catalog import Catalog, load_catalog
from .db import connect

log = logging.getLogger(__name__)

_BATCH = 80          # measured flat from 20 to 80; larger is untested
_RUN_CAP = 800       # ~10 batches — a ceiling, not the usual load
_WINDOW_DAYS = 7     # matches read._HORIZON_DAYS: nothing reads further back
_BODY_TRUNC = 400    # chars of the source's own summary fed to the model

# The on-host model serves a 16k context and the backend's default output cap
# is 1200 tokens; a batch of 80 fits neither.
_LOCAL_BATCH = 10
_LOCAL_MAX_TOKENS = 2400

_SYSTEM = ("너는 투자 정보 큐레이터다. 주어진 기사 목록을 빠짐없이 평가한다. "
           "JSON만 출력한다.")


def _prompt(items: list[dict], catalog: Catalog) -> str:
    """The batch prompt.

    Themes arrive with their definitions, not as bare ids. A list of names
    alone is the failure mode §6.2 of ops notes already names for the analysis
    agent — the model picks the closest-sounding label instead of reading the
    text, and picks one even when none applies. It showed up here exactly as
    predicted: a story about a county farming subsidy came back tagged
    [ai_sw, macro], and macro had become the drain everything unclassifiable
    ran into. Saying "none" has to be an allowed answer, in words.
    """
    listing = "\n".join(
        f"[{i}] {r['title']}\n    {(r['body'] or '')[:_BODY_TRUNC]}"
        for i, r in enumerate(items))
    fields = "\n".join(f"- {t.theme_id}: {t.definition}"
                        for t in sorted(catalog.themes.values(),
                                        key=lambda x: x.theme_id))
    return (
        f"기사 {len(items)}건:\n\n{listing}\n\n"
        "각 건마다 다음 넷을 매겨라.\n"
        "1) s: 한국어 한 문장 요약\n"
        "2) imp: 투자 판단 중요도 1~5\n"
        "3) nov: 새로운 사실이면 true, 이미 알려진 것의 반복이면 false\n"
        "4) t: 해당 분야와 확신도 [분야id, 0.0~1.0] 목록\n\n"
        f"분야:\n{fields}\n\n"
        "분야 판정 규칙:\n"
        "- 기사가 그 분야를 실제로 다룰 때만 붙인다. 어느 분야에도 해당하지 "
        "않으면 t는 빈 배열이며, 그것이 정상적인 답이다. 가장 비슷해 보이는 "
        "것을 억지로 고르지 말 것.\n"
        "- macro는 금리·환율·통화정책·지정학처럼 전 종목에 걸리는 거시 "
        "이슈에만 쓴다. 분류가 애매한 기사를 담는 곳이 아니다.\n"
        "- 확신도는 그 분야가 기사의 실제 주제인 정도다. 곁다리로 한 번 "
        "언급된 정도면 0.3 이하로 낮춘다.\n\n"
        '출력(JSON): {"r":[{"i":0,"s":"한문장","imp":3,"nov":true,'
        '"t":[["ai_sw",0.9],["semiconductor",0.4]]}]}\n'
        "- i는 위 번호. 전 건 빠짐없이. JSON만."
    )


def _theme_pairs(raw, valid: set[str]):
    """Normalise the model's ``t`` into (theme_id, confidence) pairs.

    Accepts the bare string form too: the field used to be a plain id list,
    and a model that ignores the change should degrade to unscored tags
    rather than losing its themes. Unscored means 1.0 — the column's own
    default, and what every row written before this meant.
    """
    for e in (raw or []):
        tid, conf = (e, None) if isinstance(e, str) else (
            (e[0], e[1]) if isinstance(e, (list, tuple)) and e else (None, None))
        if tid not in valid:
            continue
        try:
            c = 1.0 if conf is None else max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            c = 1.0
        yield tid, c


def _since(days: int, now: Optional[datetime] = None) -> str:
    d = (now or datetime.now(KST)).date() - timedelta(days=max(days - 1, 0))
    return d.isoformat()


def pending(limit: int = 0, date: Optional[str] = None,
            window_days: int = _WINDOW_DAYS,
            now: Optional[datetime] = None) -> list[dict]:
    """Articles collected but not yet enriched, newest first.

    `now` exists so a caller can pin the window. Reading the wall clock with
    no way to override it is what let the test suite rot by the calendar: its
    fixture data sat at a fixed date and silently fell out of the window one
    morning, with no code change.

    Bounded by `window_days` because nothing reads an older summary. Pass 0 to
    lift the window (backfilling an old date by hand).
    """
    sql = ("SELECT signal_id, title, body FROM signals "
           "WHERE enriched_at IS NULL")
    args: list = []
    if date:
        sql += " AND collected_on = ?"
        args.append(date)
    elif window_days > 0:
        sql += " AND collected_on >= ?"
        args.append(_since(window_days, now))
    sql += " ORDER BY collected_on DESC, signal_id DESC"
    if limit > 0:
        sql += " LIMIT ?"
        args.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _backlog(date: Optional[str] = None,
             window_days: int = _WINDOW_DAYS,
             now: Optional[datetime] = None) -> int:
    """Articles still waiting *inside the window* — reported so a cap that
    never drains is visible instead of merely quiet. Articles that aged out
    are not a backlog; nothing was ever going to read them."""
    sql = "SELECT COUNT(*) FROM signals WHERE enriched_at IS NULL"
    args: list = []
    if date:
        sql += " AND collected_on = ?"
        args.append(date)
    elif window_days > 0:
        sql += " AND collected_on >= ?"
        args.append(_since(window_days, now))
    with connect() as c:
        return c.execute(sql, args).fetchone()[0]


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
        theme_rows.extend((sid, tid, conf)
                          for tid, conf in _theme_pairs(e.get("t"), valid))
    if not rows:
        return 0
    with connect() as c:
        c.executemany(
            "UPDATE signals SET summary=?, importance=?, novelty=?, "
            "enriched_at=? WHERE signal_id=?", rows)
        if theme_rows:
            c.executemany(
                "INSERT OR IGNORE INTO signal_themes "
                "(signal_id, theme_id, confidence) VALUES (?,?,?)", theme_rows)
        c.commit()
    return len(rows)


def run(*, catalog: Optional[Catalog] = None, date: Optional[str] = None,
        limit: int = _RUN_CAP, batch: int = _BATCH, backend=None,
        window_days: int = _WINDOW_DAYS, local: bool = False,
        now: Optional[datetime] = None) -> dict:
    """Enrich outstanding articles, newest first, up to `limit` (0 = no cap).

    `local` runs on the on-host model with no Claude fallback: a failed batch
    stays pending for the next run like any other failure."""
    catalog = catalog or load_catalog()
    valid = set(catalog.themes)
    todo = pending(limit=limit, date=date, window_days=window_days, now=now)
    if not todo:
        return {"pending": 0, "enriched": 0, "batches": 0, "failed": 0,
                "backlog": 0}
    backlog = _backlog(date, window_days=window_days, now=now)

    if local:
        backend = LocalLlamaBackend(max_tokens=_LOCAL_MAX_TOKENS,
                                    stage="news:enrich")
        batch = _LOCAL_BATCH
    backend = backend or ClaudeCliBackend(model=FAST_MODEL,
                                          stage="news:enrich")
    now_iso = (now or datetime.now(KST)).isoformat()
    enriched = batches = failed = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        try:
            raw = backend.call(_SYSTEM, _prompt(chunk, catalog),
                               model=FAST_MODEL)
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
            "failed": failed, "backlog": max(backlog - enriched, 0)}


def redo(days: int = _WINDOW_DAYS, date: Optional[str] = None,
         now: Optional[datetime] = None) -> dict:
    """Clear enrichment over a window so the next run redoes it.

    What this is for is a change to the theme catalog: a new theme is worth
    nothing until the articles that belong to it carry the tag, and `pending`
    skips anything already stamped. Clearing the summary along with the themes
    looks wasteful, but the batch produces all four fields in one call — a
    themes-only pass would be a second inference over the same input, which
    §6.1 of ops notes rules out for exactly this reason.

    The window is what matters, not the corpus. Nothing reads enrichment older
    than `_WINDOW_DAYS`, so re-tagging further back buys nothing and costs an
    hour of Haiku per fortnight of history.
    """
    where, args = "collected_on >= ?", [_since(days, now)]
    if date:
        where, args = "collected_on = ?", [date]
    with connect() as c:
        n = c.execute(f"SELECT count(*) FROM signals WHERE {where} "
                      "AND enriched_at IS NOT NULL", args).fetchone()[0]
        c.execute("DELETE FROM signal_themes WHERE signal_id IN "
                  f"(SELECT signal_id FROM signals WHERE {where})", args)
        c.execute(f"UPDATE signals SET enriched_at = NULL WHERE {where}", args)
        c.commit()
    return {"cleared": n, "window": date or f"{days}d"}


if __name__ == "__main__":   # manual: python -m src.research.news.enrich
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo-days", type=int, default=0,
                    help="clear this many days of enrichment first — use "
                         "after changing themes.json")
    ap.add_argument("--limit", type=int, default=_RUN_CAP)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.redo_days:
        print(json.dumps(redo(a.redo_days), ensure_ascii=False))
    print(json.dumps(run(limit=a.limit), ensure_ascii=False, indent=2))
    sys.exit(0)
