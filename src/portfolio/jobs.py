"""Cron entry for everything the human accounts need kept warm.

The shared layers — market data, research, analysis — do not know that
accounts exist. They take a symbol list and work on it. This is the other half
of that arrangement: the one place that knows which symbols this book cares
about, handing them over.

That direction is the whole point. An account may read a shared resource; a
shared resource may never read an account. Otherwise the same analysis has to
fork per book, and market.db's nightly jobs quietly stop covering whatever
quant holds and portfolio does not.

    python -m src.portfolio.jobs fundamental
    python -m src.portfolio.jobs etf
    python -m src.portfolio.jobs analysis [--workers 2] [--limit N]
    python -m src.portfolio.jobs all
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from .store.holding_store import all_symbol_ids
from .store.plan_store import all_active_symbol_ids


def held() -> list[str]:
    """Symbols this book holds (qty > 0), across every account."""
    return sorted(all_symbol_ids())


def to_analyze(*, force: bool = False) -> list[str]:
    """The symbols an analysis call is spent on today — held or under a live
    plan, and only those something changed on.

    What counts as change is decided by `analyze.due_among` from stored market
    data (a price through a coordinate, a new filing, a naming article, a 5%
    move, age). This book only says which symbols it cares about and which of
    them carry a plan, so the shared layer never reads an account.
    """
    from ..research import analyze
    symbols = sorted(set(held()) | set(all_active_symbol_ids()))
    if force:
        return symbols
    return analyze.due_among(symbols, hot=all_active_symbol_ids())


def _run_module(module: str, symbols: list[str], extra: list[str]) -> int:
    if not symbols:
        print(f"{module}: no held symbols — nothing to do")
        return 0
    return subprocess.run(
        [sys.executable, "-m", module, "--symbols", ",".join(symbols), *extra],
        check=False).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["fundamental", "etf", "analysis", "all"])
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    extra: list[str] = []
    if args.workers is not None:
        extra += ["--workers", str(args.workers)]
    if args.limit is not None:
        extra += ["--limit", str(args.limit)]
    if args.force:
        extra += ["--force"]

    # Market data is deterministic and cheap, so it still covers every held
    # symbol. Only the model call is narrowed to what changed.
    jobs = {
        "fundamental": ("src.market.pipeline.fundamental_job", [], held),
        "etf": ("src.market.pipeline.etf_job", [], held),
        "analysis": ("src.research.verdict_job", extra,
                     lambda: to_analyze(force=args.force)),
    }
    todo = list(jobs) if args.job == "all" else [args.job]
    rc = 0
    for name in todo:
        module, ex, pick = jobs[name]
        symbols = pick()
        print(json.dumps({name: len(symbols)}), file=sys.stderr)
        rc |= _run_module(module, symbols, ex)
    return rc


if __name__ == "__main__":
    sys.exit(main())
