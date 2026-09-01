"""Cron entry for the collection cycle — collect → enrich → daily overview.

Runs twice a day. On Sunday evening the weekly roll-up follows the daily
overview in the same process, and on the last Sunday of the month the monthly
roll-up follows that: the long-memory cycles read the layer the run just
finished writing, so chaining them here is what stops a month from missing its
own last week.

    python -m src.research.news.cycle                    # collect + enrich + daily
    python -m src.research.news.cycle --no-collect       # re-enrich / rebuild only
    python -m src.research.news.cycle --rollup weekly    # force a roll-up
    python -m src.research.news.cycle --date 2026-08-27

Exit code is non-zero only when the run produced nothing at all — a dead feed
or a failed theme is reported, not fatal.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from ...core.kst import KST
from ...core.notify import discord
from . import collect_all, daily, enrich, rollup
from .catalog import load_catalog


def _log(msg: str) -> None:
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def _notify(date: str, out: dict) -> None:
    """Never raises — a failed notification must not fail the run."""
    try:
        c, e, d = out.get("collect", {}), out.get("enrich", {}), out.get("daily", {})
        bad = bool(c.get("failed") or e.get("failed") or d.get("failed"))
        lines = [
            f"- collect: written **{c.get('written', 0)}** "
            f"(stale {c.get('stale', 0)} / dup {c.get('dup', 0)} / "
            f"filtered {c.get('filtered', 0)})",
            f"- enrich: **{e.get('enriched', 0)}**/{e.get('pending', 0)} "
            f"in {e.get('batches', 0)} batches, failed {e.get('failed', 0)}"
            + (f", backlog {e['backlog']:,}" if e.get("backlog") else ""),
            f"- daily: **{d.get('built', 0)}** themes "
            f"(empty {d.get('empty', 0)})",
        ]
        for key in ("weekly", "monthly"):
            if key in out:
                r = out[key]
                lines.append(f"- {key}: **{r.get('built', 0)}** themes "
                             f"(empty {r.get('empty', 0)})")
        if c.get("failed"):
            lines.append(f"- ⚠️ dead feeds: {', '.join(c['failed'])}")
        discord.send_embed(
            title=f"{'🚨' if bad else '📰'} news cycle {date}",
            description="\n".join(lines)[:4000],
            color=0xE74C3C if bad else 0x2E7D4F)
    except Exception as exc:  # noqa: BLE001
        _log(f"notify failed (ignored): {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today KST)")
    ap.add_argument("--no-collect", action="store_true",
                    help="skip crawling; enrich and rebuild what is stored")
    ap.add_argument("--no-enrich", action="store_true")
    ap.add_argument("--enrich-limit", type=int, default=None,
                    help="articles per run (0 = no cap; default enrich._RUN_CAP)")
    ap.add_argument("--theme", default=None, help="daily overview for one theme")
    ap.add_argument("--rollup", choices=["none", "auto", "weekly", "monthly"],
                    default="auto",
                    help="auto = weekly on Sunday, monthly on the last Sunday")
    args = ap.parse_args()

    now = datetime.now(KST)
    date = args.date or now.strftime("%Y-%m-%d")
    catalog = load_catalog()
    out: dict = {"date": date}

    if not args.no_collect:
        _log("collect …")
        out["collect"] = collect_all.collect_all(catalog=catalog, now=now)
        _log(f"  {json.dumps({k: v for k, v in out['collect'].items() if k != 'detail'}, ensure_ascii=False)}")

    if not args.no_enrich:
        _log("enrich …")
        out["enrich"] = enrich.run(
            catalog=catalog,
            **({"limit": args.enrich_limit} if args.enrich_limit is not None else {}))
        _log(f"  {json.dumps(out['enrich'], ensure_ascii=False)}")

    _log("daily overview …")
    out["daily"] = daily.run(date, catalog=catalog, theme=args.theme)
    _log(f"  {json.dumps(out['daily'], ensure_ascii=False)}")

    today = now.date()
    do_weekly = args.rollup == "weekly" or (
        args.rollup == "auto" and today.weekday() == 6)
    do_monthly = args.rollup == "monthly" or (
        args.rollup == "auto" and rollup.is_last_sunday_of_month(today))
    if do_weekly:
        _log("weekly roll-up …")
        out["weekly"] = rollup.run_weekly(catalog=catalog)
        _log(f"  {json.dumps(out['weekly'], ensure_ascii=False)}")
    if do_monthly:
        _log("monthly roll-up …")
        out["monthly"] = rollup.run_monthly(catalog=catalog)
        _log(f"  {json.dumps(out['monthly'], ensure_ascii=False)}")

    _notify(date, out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    produced = (out.get("collect", {}).get("written", 0)
                + out.get("enrich", {}).get("enriched", 0)
                + out.get("daily", {}).get("built", 0))
    return 0 if produced else 1


if __name__ == "__main__":
    sys.exit(main())
