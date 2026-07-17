"""Nightly verdict warm batch — pre-bake chart + judge verdicts for holdings.

WHY: verdicts are on-demand (UI button) and take 307~390s each, so coverage
sat at 3/27 held symbols and every first click meant a ~6min wait. This job
walks every held symbol (all accounts, qty>0) and runs chart → judge per
symbol, so the morning dashboard serves same-day verdicts instantly (the GET
.../verdict endpoints just read the persisted files).

Ordering matters: chart first, so judge's cached_verdicts.chart is fresh
(≤24h window). Fundamental/news verdicts stay on-demand — judge reads their
live facts / theme state directly.

Cache: a symbol×kind whose file for today already exists is skipped (24h
cache-hit; --force overrides). Failures are isolated per symbol; a summary of
failed symbols (and any judge run missing holding_context) is sent to Discord
(DISCORD_WEBHOOK_URL). Exit 0 on partial success, non-zero only if every
symbol failed (cron convention, same as news.daily_job).

Designed for cron 21:30 UTC (06:30 KST), after the news/fundamental/etf
batches. ~27 symbols × 2 LLM calls × ~6min ≈ 5h sequential; --workers 2
(default) halves that so the run finishes before the 09:00 KST open.

Usage:
  python -m src.agents.verdict_job                          # all holdings
  python -m src.agents.verdict_job --symbols KR:005930,US:AAPL
  python -m src.agents.verdict_job --kinds judge --limit 3 --workers 1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ..core.paths import analysis_path, ensure_dirs

_KST = timezone(timedelta(hours=9))

# Single-verdict latency measured up to 390s (commit 5cf47f6 widened the API
# timeouts to 480 for the same reason) — batch uses the same ceiling.
_TIMEOUT_S = 480.0


def _load_webhook_env() -> None:
    """cron runs with a minimal env — pull DISCORD_WEBHOOK_URL from the
    secrets file when unset (same fallback pattern as dart_source)."""
    if os.getenv("DISCORD_WEBHOOK_URL"):
        return
    for env in (Path.home() / ".claude" / "secrets" / ".env",
                Path.home() / "dotclaude" / "secrets" / ".env"):
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            m = re.match(r"\s*DISCORD_WEBHOOK_URL\s*=\s*(\S+)", line)
            if m:
                os.environ["DISCORD_WEBHOOK_URL"] = m.group(1).strip()
                return


def _held_symbols() -> list[str]:
    """Every symbol held in ANY account (qty>0) — verdicts are per-symbol,
    account-agnostic, so duplicates across accounts collapse."""
    from ..portfolio.store.db import connect
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol_id FROM holdings WHERE qty > 0 "
            "ORDER BY symbol_id").fetchall()
    return [r[0] for r in rows]


def _name(symbol_id: str) -> str:
    try:
        from ..universe import search as uni_search
        item = uni_search.get(symbol_id)
        return (item or {}).get("name_kr") or symbol_id
    except Exception:  # noqa: BLE001 — a missing name never blocks a verdict
        return symbol_id


def run_symbol(symbol_id: str, kinds: tuple, date: str, *, force: bool) -> dict:
    """chart → judge for one symbol, each skipped on same-day cache-hit.
    Never raises — failures land in the returned summary."""
    out: dict = {"symbol": symbol_id, "kinds": {}, "errors": []}
    name = _name(symbol_id)
    for kind in kinds:
        path = analysis_path(kind, symbol_id, date.replace("-", ""))
        if path.exists() and not force:
            out["kinds"][kind] = "cached"
            continue
        t0 = time.monotonic()
        try:
            if kind == "chart":
                from .chart.evaluate import evaluate_symbol as run  # lazy: spawns claude
            else:
                from .judge.evaluate import evaluate_symbol as run
            res = run(symbol_id, name=name, date=date, timeout_s=_TIMEOUT_S)
            status = res.get("status", "ok")
            out["kinds"][kind] = status
            if kind == "judge":
                # Past incident: a 삼성전자 verdict shipped without
                # holding_context — batch must prove it's attached.
                out["holding_ctx"] = bool(res.get("holding_context"))
            if status != "ok":
                out["errors"].append(f"{kind}:{status}")
        except Exception as e:  # noqa: BLE001 — isolate per-symbol failures
            out["kinds"][kind] = "error"
            out["errors"].append(f"{kind}:{str(e)[:150]}")
        print(f"[{datetime.now(_KST):%H:%M:%S}] {symbol_id} {kind} -> "
              f"{out['kinds'][kind]} ({time.monotonic() - t0:.0f}s)", flush=True)
    return out


def _notify_failures(date: str, results: list[dict], n_total: int) -> None:
    failed = [r for r in results if r["errors"]]
    no_ctx = [r["symbol"] for r in results if r.get("holding_ctx") is False]
    if not failed and not no_ctx:
        return
    _load_webhook_env()
    from ..core.notify import discord
    lines = [f"verdict 배치 {date} — {n_total}종목 중 실패/폴백 {len(failed)}"]
    for r in failed[:15]:
        lines.append(f"- {r['symbol']}: {'; '.join(r['errors'])[:120]}")
    if len(failed) > 15:
        lines.append(f"… 외 {len(failed) - 15}종목")
    if no_ctx:
        lines.append(f"holding_context 누락: {', '.join(no_ctx[:10])}")
    discord.send("⚠️ **verdict-batch**\n" + "\n".join(lines), key="verdict_batch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None,
                    help="comma-separated symbol_ids (default: all holdings)")
    ap.add_argument("--kinds", default="chart,judge",
                    help="comma-separated subset of chart,judge (in run order)")
    ap.add_argument("--limit", type=int, default=None, help="cap symbol count")
    ap.add_argument("--workers", type=int, default=2,
                    help="parallel symbols (each symbol stays chart→judge sequential)")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even when today's verdict file exists")
    args = ap.parse_args()

    ensure_dirs()
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    bad = [k for k in kinds if k not in ("chart", "judge")]
    if bad or not kinds:
        print(f"invalid --kinds {args.kinds!r} (allowed: chart,judge)")
        return 2

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = _held_symbols()
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        print("no held symbols — nothing to do")
        return 0

    date = datetime.now(_KST).strftime("%Y-%m-%d")
    t0 = time.monotonic()
    print(f"verdict batch {date} — {len(symbols)} symbols, kinds={','.join(kinds)}, "
          f"workers={args.workers}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        results = list(ex.map(
            lambda s: run_symbol(s, kinds, date, force=args.force), symbols))

    ok = [r for r in results if not r["errors"]]
    summary = {
        "date": date,
        "total": len(results),
        "ok": len(ok),
        "failed": [{"symbol": r["symbol"], "errors": r["errors"]}
                   for r in results if r["errors"]],
        "cached": sum(1 for r in results
                      if all(v == "cached" for v in r["kinds"].values())),
        "no_holding_ctx": [r["symbol"] for r in results
                           if r.get("holding_ctx") is False],
        "elapsed_min": round((time.monotonic() - t0) / 60, 1),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    _notify_failures(date, results, len(symbols))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
