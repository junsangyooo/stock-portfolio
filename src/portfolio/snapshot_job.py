"""Daily snapshot job — the record of each account's assets for the day.

The scheduled closes write the day's snapshot; a page view only fills a day
they have not written yet (service.aggregate → write_if_absent). This job
gap-fills daily OHLCV and refreshes quotes for every held symbol once, then
records each account (service.record_snapshot), replacing whatever a view or
the earlier close wrote — so the day's row is the latest close, not the last
visit.

The OHLCV gap-fill also keeps charts/sparklines current: without it daily
bars only advance when someone clicks refresh in the UI (observed stale for
10+ days, 2026-07-13).

Runs twice (deploy/portfolio.cron): 06:20 KST after the US close and 16:35
KST after the KR close. Exits 0 on partial success (at least one account
recorded), non-zero only if every account failed — same convention as the
other jobs.

Usage:
  python -m src.portfolio.snapshot_job
"""
from __future__ import annotations

import json
import sys

from ..market import quotes
from ..market.pipeline import gap_filler
from . import service
from .store import account_store, holding_store

_QUOTE_MAX_AGE_S = 600  # cron context: anything from the last 10min is fine


def main() -> int:
    accounts = account_store.list_all()
    held = set(holding_store.all_symbol_ids())
    filled = gap_filler.fill_many(sorted(held))  # keep charts/sparklines current
    # The symbols we could not price are named, not swallowed: the previous
    # quote stays in the cache, so a dead source would otherwise show up as a
    # plausible number in today's row rather than a missing one.
    stale: dict = {}
    warmed = quotes.warm(sorted(held), _QUOTE_MAX_AGE_S, failed=stale)

    ok, failed, partial = [], [], []
    for acc in accounts:
        aid = acc["account_id"]
        try:
            res = service.record_snapshot(aid)
            if res["missing"]:
                partial.append(aid)  # a priceless holding: that currency is not written
            else:
                ok.append(aid)       # every currency written (or an empty account)
        except Exception as e:
            failed.append({"account_id": aid, "error": str(e)})

    print(json.dumps({
        "accounts_ok": ok, "accounts_failed": failed,
        "accounts_without_snapshot": partial,
        "symbols": len(held),
        "quotes_fresh": sum(1 for q in warmed.values() if q is not None),
        "quotes_stale": [f"{sid}: {why}" for sid, why in sorted(stale.items())],
        "ohlcv_bars_added": sum(n for n in filled.values() if n > 0),
        "ohlcv_errors": [s for s, n in filled.items() if n < 0],
    }, ensure_ascii=False))
    return 0 if ok or not accounts else 1


if __name__ == "__main__":
    sys.exit(main())
