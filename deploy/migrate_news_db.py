"""One-shot: theme-keyed jsonl + daily digests → data/news.db.

    data/signals/news/{theme}/{yyyymm}.jsonl        → signals + signal_themes
    data/analysis/news/{theme}/daily/{date}.json    → theme_daily (`overview`)
      …and that file's `items[]`                    → the signals' enrichment

The jsonl tree stored one copy of an article per theme it belonged to, so this
merges on the dedup key: an article that appeared under three themes becomes
one `signals` row with three `signal_themes` rows. That collapse is the
migration's whole point, so the report prints both numbers.

Old articles are NOT re-enriched. Only the 5~8 items a day the old digest
happened to select carry a summary/importance/novelty; everything else keeps
NULL. Backfilling by inference would mean re-reading tens of thousands of
articles to reconstruct a judgement nobody is waiting for.

`state.json` is not migrated: it is a rolling 7-day window, so there is no
history in it to move.

    ssh oracle-instance "cd ~/portfolio-dashboard && .venv/bin/python \\
        deploy/migrate_news_db.py"
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.research.news.db import connect                       # noqa: E402
from src.core import paths                                   # noqa: E402
from src.core.kst import KST                                 # noqa: E402

SIGNALS = paths.SIGNALS_DIR / "news"
DIGESTS = paths.ANALYSIS_DIR / "news"

# The old records carried lead_lag directly; 'backbone' was the macro theme's
# override of it. Theme membership already says an article is macro, so the
# lane collapses back to what its source group implies.
_LANE = {"backbone": "lagging"}
_GROUP = {"leading": "research", "coincident": "social", "lagging": "news",
          "backbone": "news"}


def _dedup_key(rec: dict) -> str:
    url = (rec.get("url") or "").strip()
    if url:
        return url
    return "t:" + hashlib.sha1(
        (rec.get("title") or "").strip().encode("utf-8")).hexdigest()


def migrate_signals(c) -> dict:
    """jsonl → signals (one row per article) + signal_themes (one per theme)."""
    if not SIGNALS.exists():
        return {"files": 0, "records": 0, "articles": 0, "memberships": 0}
    files = records = 0
    articles: dict[str, dict] = {}
    memberships: set[tuple] = set()
    for theme_dir in sorted(SIGNALS.iterdir()):
        if not theme_dir.is_dir():
            continue
        for f in sorted(theme_dir.glob("*.jsonl")):
            files += 1
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                records += 1
                key = _dedup_key(rec)
                theme = rec.get("theme") or theme_dir.name
                memberships.add((key, theme))
                if key in articles:
                    # keep the earliest sighting; fill blanks from later copies
                    prev = articles[key]
                    if not prev.get("image") and rec.get("image"):
                        prev["image"] = rec["image"]
                    if (rec.get("fetched_at") or "") < (prev.get("fetched_at") or ""):
                        prev["fetched_at"] = rec["fetched_at"]
                    continue
                articles[key] = dict(rec)

    rows = []
    for key, rec in articles.items():
        fetched = rec.get("fetched_at") or ""
        ll = rec.get("lead_lag") or "lagging"
        rows.append((
            key, (rec.get("url") or "").strip() or None,
            rec.get("title") or "(제목 없음)", rec.get("summary") or None,
            rec.get("source_id") or "unknown",
            _GROUP.get(ll, "news"), _LANE.get(ll, ll), rec.get("image"),
            rec.get("published"), fetched, fetched[:10] or "1970-01-01"))
    c.executemany(
        "INSERT OR IGNORE INTO signals (dedup_key, url, title, body, "
        "source_id, source_group, lead_lag, image, published_at, fetched_at, "
        "collected_on) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()

    id_of = {r["dedup_key"]: r["signal_id"] for r in c.execute(
        "SELECT dedup_key, signal_id FROM signals")}
    c.executemany(
        "INSERT OR IGNORE INTO signal_themes (signal_id, theme_id) VALUES (?,?)",
        [(id_of[k], t) for k, t in memberships if k in id_of])
    c.commit()
    return {"files": files, "records": records, "articles": len(articles),
            "memberships": len(memberships)}


def migrate_digests(c) -> dict:
    """daily digest json → theme_daily.overview, and its selected items back
    onto the articles they describe."""
    if not DIGESTS.exists():
        return {"digests": 0, "overviews": 0, "enriched": 0}
    id_of = {r["dedup_key"]: r["signal_id"] for r in c.execute(
        "SELECT dedup_key, signal_id FROM signals")}
    built_at = datetime.now(KST).isoformat()
    digests = overviews = 0
    enrich_rows = []
    for theme_dir in sorted(DIGESTS.iterdir()):
        daily_dir = theme_dir / "daily"
        if not daily_dir.is_dir():
            continue
        for f in sorted(daily_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            digests += 1
            date = d.get("date") or f.stem
            ov = [str(b) for b in (d.get("overview") or []) if str(b).strip()]
            if ov:
                c.execute(
                    "INSERT OR REPLACE INTO theme_daily (theme_id, date, "
                    "overview, n_items, built_at) VALUES (?,?,?,?,?)",
                    (theme_dir.name, date,
                     json.dumps(ov, ensure_ascii=False),
                     len(d.get("items") or []), built_at))
                overviews += 1
            for it in (d.get("items") or []):
                sid = id_of.get((it.get("url") or "").strip())
                if sid is None:
                    continue
                enrich_rows.append((
                    str(it.get("summary") or "")[:300],
                    max(1, min(5, int(it.get("importance") or 3))),
                    1 if it.get("novelty") else 0, built_at, sid))
    c.executemany(
        "UPDATE signals SET summary=?, importance=?, novelty=?, enriched_at=? "
        "WHERE signal_id=? AND enriched_at IS NULL", enrich_rows)
    c.commit()
    return {"digests": digests, "overviews": overviews,
            "enriched": len(enrich_rows)}


def main() -> int:
    print(f"signals tree : {SIGNALS}")
    print(f"digest tree  : {DIGESTS}")
    print(f"target       : {paths.NEWS_DB_PATH}\n")
    with connect() as c:
        s = migrate_signals(c)
        print(f"  jsonl        files={s['files']} records={s['records']:,}")
        print(f"  → signals    {s['articles']:,} articles "
              f"(the tree held {s['records']:,} copies)")
        print(f"  → memberships{s['memberships']:,} theme rows")
        d = migrate_digests(c)
        print(f"  digests      {d['digests']} files")
        print(f"  → theme_daily{d['overviews']} overviews")
        print(f"  → enriched   {d['enriched']:,} articles carry a summary "
              f"(the rest stay NULL on purpose)")
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("signals", "signal_themes", "theme_daily")}
    print(f"\n  news.db: {counts}")
    ok = counts["signals"] == s["articles"] and \
        counts["signal_themes"] == s["memberships"]
    if not ok:
        print("MISMATCH — nothing was deleted; fix and re-run.")
        return 1
    print("ok — counts agree. The jsonl tree is untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
