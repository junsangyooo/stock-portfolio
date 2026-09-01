"""Nightly analysis warm batch — pre-bake the comprehensive analysis.

The account list shows a badge per symbol; computing it on page load would
mean a multi-minute claude call each time. This runs `analyze` once per given
symbol so the UI reads a stored result.

`--symbols` is required. This module is part of the shared research layer and
does not ask an account book what it holds — the account side schedules it
(src/portfolio/jobs.py).

Was chart → judge per symbol. There is one call now: judging a domain alone
hardens a one-dimensional conclusion that the later merge cannot undo, so the
domains meet inside a single call instead (src/agents/analyze/).

Same-day results are skipped, so a re-run is cheap and a crash mid-batch is
resumed rather than repeated. Failures are collected and reported to Discord
at the end; one symbol never stops the batch.

  python -m src.agents.verdict_job --symbols KR:005930,US:NVDA [--workers 2]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..core.kst import KST as _KST
from ..core.paths import analysis_path, ensure_dirs

# Hang guard, NOT error detection. At 480 this ceiling sat inside the body of
# the real latency distribution (743 chart runs: mean 352s, p90 439s, p95 461s)
# and manufactured its own failures: 98 of 99 recorded chart errors were the
# timeout itself, ~12% of runs every night, on a distribution that has not
# drifted in months. The only thing a ceiling has to catch is a claude -p that
# never returns and would otherwise stall the batch forever.
_TIMEOUT_S = 1800.0



def _name(symbol_id: str) -> str:
    try:
        from ..universe import search as uni_search
        item = uni_search.get(symbol_id)
        return (item or {}).get("name_kr") or symbol_id
    except Exception:  # noqa: BLE001 — a missing name never blocks a verdict
        return symbol_id


def run_symbol(symbol_id: str, date: str, *, force: bool) -> dict:
    """One symbol's comprehensive analysis, skipped on a same-day hit.
    Never raises — failures land in the returned summary."""
    from .analyze.run import KIND
    out: dict = {"symbol": symbol_id, "status": None, "errors": []}
    path = analysis_path(KIND, symbol_id, date.replace("-", ""))
    if path.exists() and not force:
        out["status"] = "cached"
        return out
    t0 = time.monotonic()
    try:
        from .analyze.run import analyze          # lazy: spawns claude
        res = analyze(symbol_id, _name(symbol_id), timeout_s=_TIMEOUT_S)
        out["status"] = res.get("status", "ok")
        # A held symbol whose analysis shipped without its position is the
        # 2026-06 삼성전자 incident; the batch proves the context attached.
        out["holding_ctx"] = bool((res.get("levels") or {}).get("current")
                                  or res.get("observations"))
        if out["status"] != "ok":
            out["errors"].append(out["status"])
    except Exception as e:  # noqa: BLE001 — isolate per-symbol failures
        out["status"] = "error"
        out["errors"].append(str(e)[:150])
    print(f"[{datetime.now(_KST):%H:%M:%S}] {symbol_id} -> {out['status']} "
          f"({time.monotonic() - t0:.0f}s)", flush=True)
    return out


def _notify_failures(date: str, results: list[dict], n_total: int) -> None:
    failed = [r for r in results if r["errors"]]
    no_ctx = [r["symbol"] for r in results if r.get("holding_ctx") is False]
    if not failed and not no_ctx:
        return
    from ..core.notify import discord
    lines = [f"분석 배치 {date} — {n_total}종목 중 실패/폴백 {len(failed)}"]
    for r in failed[:15]:
        lines.append(f"- {r['symbol']}: {'; '.join(r['errors'])[:120]}")
    if len(failed) > 15:
        lines.append(f"… 외 {len(failed) - 15}종목")
    if no_ctx:
        lines.append(f"내용 없이 반환: {', '.join(no_ctx[:10])}")
    discord.send("⚠️ **analysis-batch**\n" + "\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True,
                    help="comma-separated symbol_ids")
    ap.add_argument("--limit", type=int, default=None, help="cap symbol count")
    ap.add_argument("--workers", type=int, default=2,
                    help="parallel symbols")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even when today's analysis exists")
    args = ap.parse_args()

    ensure_dirs()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        print("no symbols given — nothing to do")
        return 0

    date = datetime.now(_KST).strftime("%Y-%m-%d")
    t0 = time.monotonic()
    print(f"analysis batch {date} — {len(symbols)} symbols, "
          f"workers={args.workers}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        results = list(ex.map(
            lambda s: run_symbol(s, date, force=args.force), symbols))

    ok = [r for r in results if not r["errors"]]
    summary = {
        "date": date,
        "total": len(results),
        "ok": len(ok),
        "failed": [{"symbol": r["symbol"], "errors": r["errors"]}
                   for r in results if r["errors"]],
        "cached": sum(1 for r in results if r["status"] == "cached"),
        "no_holding_ctx": [r["symbol"] for r in results
                           if r.get("holding_ctx") is False],
        "elapsed_min": round((time.monotonic() - t0) / 60, 1),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    _notify_failures(date, results, len(symbols))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
