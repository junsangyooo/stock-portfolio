"""CLI entry-point for daily universe refresh.

Usage:
  python -m src.universe.refresh_job [--region kr|us|all]

Designed for cron — exits 0 on partial success (at least one region built),
non-zero only if everything failed.
"""
from __future__ import annotations

import argparse
import json
import sys

from .builder import refresh


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="all", choices=("all", "kr", "us"))
    args = ap.parse_args()

    summary = refresh(region=args.region)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not summary.get("regions"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
