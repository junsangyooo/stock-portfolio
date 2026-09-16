"""Morning plan pass for the human accounts — one isolated call per book.

Every account gets its own process-local session: a fresh `claude -p` with no
--resume, its own context assembled from its own rows, its own plan_runs row.
Nothing is shared between two accounts because there is nothing to share.

Sequential on purpose. Four accounts of roughly 40k characters each is a few
minutes, and running them in order is what lets the batch send one Discord
message instead of four.

    python -m src.portfolio.planner_job                # every account, morning
    python -m src.portfolio.planner_job --account isa  # one
    python -m src.portfolio.planner_job --dry          # decide, do not apply
"""
from __future__ import annotations

import argparse
import json
import sys

from ..core.notify import discord
from ..ledger import planner
from ..research.llm_backend import SLOW_MODEL
from .store import account_store, db

# Sonnet: this is judgment, but a person reads every line of it before
# anything happens. The money path (quant) picks its own model.
MODEL = SLOW_MODEL

# The reasoning budget. Measured on the analysis call (2026-09-15): the
# default spends ~4x the output tokens of `low` for an answer of the same
# shape, but it did move the verdict. These notes are a person's reference,
# not an order — `medium` is the middle of that trade, and the biggest book
# here was taking 27 minutes a morning at the default.
EFFORT = "medium"

_BAD = ("llm_error", "parse_error", "incomplete")


def _line(r: dict) -> str:
    acct = r["account_id"]
    if r["status"] == "skipped":
        return f"- {acct}: 건너뜀 (보유·계획·현금 없음)"
    if r["status"] != "ok":
        return f"- {acct}: **{r['status']}** {r.get('error', '')[:80]}"
    return (f"- {acct}: 추가 {r['n_add']} · 수정 {r['n_modify']} · 철회 "
            f"{r['n_abandon']} · 의견 {r['n_suggest']} · 기각 {r['n_rejected']}"
            f" ({r['elapsed_s']:.0f}s"
            + ("" if r["applied"] else ", 미적용") + ")")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", help="one account id (default: every human)")
    ap.add_argument("--dry", action="store_true",
                    help="record the pass, leave the plans alone")
    args = ap.parse_args()

    accounts = [args.account] if args.account else account_store.list_ids()
    if not accounts:
        print("no human accounts — nothing to do")
        return 0

    slot = "manual" if args.dry else "morning"
    results = []
    for account_id in accounts:
        with db.connect() as conn:
            try:
                r = planner.run(conn, account_id, slot=slot, model=MODEL,
                                dry=args.dry, effort=EFFORT)
            except Exception as e:  # noqa: BLE001 — one account never stops the rest
                r = {"account_id": account_id, "status": "llm_error",
                     "error": str(e)[:200]}
        print(json.dumps(r, ensure_ascii=False), flush=True)
        results.append(r)

    body = "\n".join(_line(r) for r in results)
    bad = [r for r in results if r["status"] in _BAD]
    discord.send(f"{'🧪' if args.dry else '📋'} **계획 작성** "
                 f"{results[0].get('date', '')} — {len(results)}계좌"
                 + (f", 실패 {len(bad)}" if bad else "") + "\n" + body)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
