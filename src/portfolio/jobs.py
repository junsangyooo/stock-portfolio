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


def held() -> list[str]:
    """Symbols this book holds (qty > 0), across every account."""
    return sorted(all_symbol_ids())


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

    symbols = held()
    print(json.dumps({"held": len(symbols)}), file=sys.stderr)

    extra: list[str] = []
    if args.workers is not None:
        extra += ["--workers", str(args.workers)]
    if args.limit is not None:
        extra += ["--limit", str(args.limit)]
    if args.force:
        extra += ["--force"]

    jobs = {
        "fundamental": ("src.market.pipeline.fundamental_job", []),
        "etf": ("src.market.pipeline.etf_job", []),
        "analysis": ("src.agents.verdict_job", extra),
    }
    todo = list(jobs) if args.job == "all" else [args.job]
    rc = 0
    for name in todo:
        module, ex = jobs[name]
        rc |= _run_module(module, symbols, ex)
    return rc


if __name__ == "__main__":
    sys.exit(main())
