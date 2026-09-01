"""CLI for the comprehensive analysis.

    python -m src.research.analyze KR:005930 [--force-chart] [--dry]

`--dry` prints the assembled prompt and makes no call — the cheapest way to
see what the model is actually being shown.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import engine, inputs, prompt


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol_id")
    ap.add_argument("--name", default=None)
    ap.add_argument("--force-chart", action="store_true",
                    help="recompute chart facts even if today's row exists")
    ap.add_argument("--dry", action="store_true",
                    help="print the assembled prompt, make no call")
    args = ap.parse_args()

    if args.dry:
        env = inputs.build(args.symbol_id, args.name,
                           force_chart=args.force_chart)
        text = prompt.build(env)
        print(f"# domains: {[(d['id'], d['status']) for d in env['domains']]}",
              file=sys.stderr)
        print(f"# prompt chars: {len(text):,}", file=sys.stderr)
        print(text)
        return 0

    out = engine.analyze(args.symbol_id, args.name,
                         force_chart=args.force_chart)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
