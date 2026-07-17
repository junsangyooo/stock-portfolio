"""CLI entry-point for the daily qualitative pipeline (cron 06:00 KST).

Runs collect -> digest -> state for every theme in the collection set. This is
the BATCH half of the flow (the on-demand half is per-symbol evaluate, triggered
by the UI). Theme-level work (not per-symbol): cost scales with #themes, and the
theme set is derived from holdings (see _themes_to_collect) so it shrinks/grows
with the portfolio. evaluate is intentionally NOT here — it runs when the user
clicks the analysis button.

Usage:
  python -m src.agents.news.daily_job [--theme battery] [--no-collect]

Designed for cron — exits 0 on partial success, non-zero only if every theme
failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

from ...core.notify import discord
from .catalog import load_catalog
from .collect import collect_theme
from .digest import make_daily_digest, write_daily_digest, load_signals_for_date
from .state import load_state, update_state, write_state

_KST = timezone(timedelta(hours=9))


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _themes_to_collect(cat) -> list:
    """Collection set = holdings-driven (§ holdings-driven activation).

    A theme is collected iff some current holding maps to it as ``primary``,
    plus always-on backbones (macro). Replaces the static ``active`` flag so
    adding/removing a holding grows/shrinks the news footprint automatically
    (no themes.json edit, which is git-tracked and must not mutate at runtime).
    themes.json ``active`` stays as the taxonomy of taggable themes.
    """
    from ...portfolio.store.db import connect
    from .mapping import held_primary_theme_ids

    with connect() as c:
        held = held_primary_theme_ids(c)
    return [t for t in cat.themes.values()
            if not t.shadow and (t.always_on or t.theme_id in held)]


def run_theme(theme, date: str, *, do_collect: bool = True) -> dict:
    """collect -> digest -> state for one theme. Returns a summary dict.

    The two stages fail independently: a digest/state (LLM) fatal must not
    mask a successful collect — during the 06-21~07-07 CLI-auth outage the
    log looked like collection was down when only the LLM layer was.
    """
    summary = {"theme": theme.theme_id, "collected": 0, "digested": 0,
               "state_updated": False, "errors": []}

    if do_collect:
        try:
            cstat = collect_theme(theme)
            summary["collected"] = cstat["written"]
            summary["filtered"] = cstat.get("filtered", 0)    # broad-feed keyword drops
            summary["og_filled"] = cstat.get("og_filled", 0)  # og:image enrichment hits
            summary["errors"].extend(cstat.get("errors", []))
            _log(f"[{theme.theme_id}] collect ok: written={cstat['written']} "
                 f"filtered={summary['filtered']} og_filled={summary['og_filled']} "
                 f"errors={len(cstat.get('errors', []))}")
        except Exception as e:  # noqa: BLE001
            summary["fatal"] = f"collect: {str(e)[:200]}"
            _log(f"[{theme.theme_id}] collect FATAL: {str(e)[:200]}")
            return summary

    try:
        raw = load_signals_for_date(theme.theme_id, date)
        digest = make_daily_digest(theme.theme_id, theme.name_kr, raw, date)
        write_daily_digest(digest)
        summary["digested"] = len(digest["items"])

        new_state = update_state(theme.theme_id, theme.name_kr, digest,
                                 prev_state=load_state(theme.theme_id), date=date)
        write_state(new_state)
        summary["state_updated"] = True
        summary["sentiment"] = new_state.get("sentiment")
        _log(f"[{theme.theme_id}] digest/state ok: digested={summary['digested']}")
    except Exception as e:  # noqa: BLE001
        summary["fatal"] = f"digest/state: {str(e)[:200]}"
        _log(f"[{theme.theme_id}] digest/state FATAL: {str(e)[:200]}")
    return summary


def _notify(date: str, results: list[dict], *, did_collect: bool) -> None:
    """Discord alert on fatal themes / zero collection; 1-line summary when ok.

    Never raises — notification failure must not fail the job.
    """
    try:
        fatal = [r for r in results if r.get("fatal")]
        collected = sum(r.get("collected", 0) for r in results)
        digested = sum(r.get("digested", 0) for r in results)
        n = len(results)
        if fatal or (did_collect and collected == 0):
            lines = [f"- **{r['theme']}**: {r['fatal']}" for r in fatal]
            if did_collect and collected == 0:
                lines.append("- 총 collected=0 (전 소스 무수집 — same-day 재실행이면 정상)")
            discord.send_embed(
                title=f"🚨 news daily_job {date} — fatal {len(fatal)}/{n} themes",
                description="\n".join(lines)[:4000],
                color=0xE74C3C,
                footer=f"collected={collected} digested={digested} "
                       f"(collect와 digest/state는 독립 — fatal 사유의 단계 확인)")
        else:
            discord.send(f"📰 news daily_job {date} ok — {n} themes, "
                         f"collected={collected}, digested={digested}")
    except Exception as e:  # noqa: BLE001
        _log(f"notify failed (ignored): {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=None, help="single theme_id (default: all active)")
    ap.add_argument("--no-collect", action="store_true", help="skip crawling, digest existing only")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today KST)")
    args = ap.parse_args()

    cat = load_catalog()
    date = args.date or datetime.now(_KST).strftime("%Y-%m-%d")
    themes = ([cat.get(args.theme)] if args.theme else _themes_to_collect(cat))
    themes = [t for t in themes if t]
    if not themes:
        print(json.dumps({"error": "no themes"}), file=sys.stderr)
        return 1

    results, ok = [], 0
    for t in themes:
        try:
            r = run_theme(t, date, do_collect=not args.no_collect)
        except Exception as e:  # noqa: BLE001 — backstop, report per-theme
            r = {"theme": t.theme_id, "fatal": str(e)[:200]}
        results.append(r)
        if not r.get("fatal"):
            ok += 1

    _notify(date, results, did_collect=not args.no_collect)
    print(json.dumps({"date": date, "themes": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
