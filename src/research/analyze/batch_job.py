"""Nightly analysis warm batch — pre-bake the comprehensive analysis.

The account list shows a badge per symbol; computing it on page load would
mean a multi-minute claude call each time. This runs `analyze` once per given
symbol so the UI reads a stored result.

`--symbols` is required. This module is part of the shared research layer and
does not ask an account book what it holds — the account side schedules it
(src/portfolio/supply_job.py).

One call per symbol, all three domains in it — a domain judged alone hardens
a one-dimensional conclusion that a later merge cannot undo (see prompt.py).

Same-day results are skipped, so a re-run is cheap and a crash mid-batch is
resumed rather than repeated. Failures are collected and reported to Discord
at the end; one symbol never stops the batch.

  python -m src.research.analyze.batch_job --symbols KR:005930,US:NVDA [--workers 2]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ...core import kst
from ...core.paths import ensure_dirs


def _name(symbol_id: str) -> str:
    try:
        from ...universe import search as uni_search
        item = uni_search.get(symbol_id)
        return (item or {}).get("name_kr") or symbol_id
    except Exception:  # noqa: BLE001 — a missing name never blocks a verdict
        return symbol_id


# A root session costs about what one full prompt costs to write into cache
# ($0.15~0.22 measured), and each fork after it saves more than that — so it
# pays from the second symbol on. A one-symbol batch sends the whole prompt,
# which is what `due_among` makes the common case.
_ROOT_MIN_BATCH = 2


def run_symbol(symbol_id: str, date: str, *, force: bool,
               effort: Optional[str] = None,
               root: Optional[str] = None) -> dict:
    """One symbol's comprehensive analysis, skipped on a same-day hit.
    Never raises — failures land in the returned summary."""
    from .. import analyze
    out: dict = {"symbol": symbol_id, "status": None, "errors": []}
    if not force and analyze.on(symbol_id, date) is not None:
        out["status"] = "cached"
        return out
    t0 = time.monotonic()
    try:
        # `root` holds the dictionaries for the whole batch; this symbol's
        # call forks it and sends only its own data.
        res = analyze.run(symbol_id, _name(symbol_id), root=root,
                          effort=effort)
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
    print(f"[{kst.now():%H:%M:%S}] {symbol_id} -> {out['status']} "
          f"({time.monotonic() - t0:.0f}s)", flush=True)
    return out


def _notify_failures(date: str, results: list[dict], n_total: int) -> None:
    failed = [r for r in results if r["errors"]]
    no_ctx = [r["symbol"] for r in results if r.get("holding_ctx") is False]
    if not failed and not no_ctx:
        return
    from ...core import notify
    lines = [f"분석 배치 {date} — {n_total}종목 중 실패/폴백 {len(failed)}"]
    for r in failed[:15]:
        lines.append(f"- {r['symbol']}: {'; '.join(r['errors'])[:120]}")
    if len(failed) > 15:
        lines.append(f"… 외 {len(failed) - 15}종목")
    if no_ctx:
        lines.append(f"내용 없이 반환: {', '.join(no_ctx[:10])}")
    notify.send("⚠️ **analysis-batch**\n" + "\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True,
                    help="comma-separated symbol_ids")
    ap.add_argument("--limit", type=int, default=None, help="cap symbol count")
    ap.add_argument("--workers", type=int, default=2,
                    help="parallel symbols")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even when today's analysis exists")
    ap.add_argument("--effort", default=None,
                    help="low|medium|high|xhigh|max — the reasoning budget; "
                         "omitted = the CLI default")
    args = ap.parse_args()

    ensure_dirs()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        print("no symbols given — nothing to do")
        return 0

    date = kst.today()
    t0 = time.monotonic()
    print(f"analysis batch {date} — {len(symbols)} symbols, "
          f"workers={args.workers}", flush=True)

    from .. import analyze
    todo = [s for s in symbols if args.force or analyze.on(s, date) is None]
    # The root is made at the forks' budget: a fork whose thinking settings
    # differ from the root's does not read the cached dictionaries.
    root = (analyze.batch_root(effort=args.effort)
            if len(todo) >= _ROOT_MIN_BATCH else None)
    print(f"batch root: {'yes' if root else 'no'} ({len(todo)} to run)",
          flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        results = list(ex.map(
            lambda s: run_symbol(s, date, force=args.force, root=root,
                                 effort=args.effort),
            symbols))

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
