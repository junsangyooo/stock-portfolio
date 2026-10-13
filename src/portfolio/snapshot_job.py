"""Daily snapshot job — record per-account totals even on no-visit days.

WHY: snapshots are written lazily (service.aggregate on account-detail view),
so a day without a dashboard visit leaves a gap in the assets chart. This job
gap-fills daily OHLCV and refreshes quotes for every held symbol once, then
runs the same aggregate per account — which persists today's snapshot through
the existing lazy path (snapshot_store.maybe_snapshot_today). A later visit
the same day simply rewrites the row with fresher numbers.

The OHLCV gap-fill also keeps charts/sparklines current: without it daily
bars only advance when someone clicks refresh in the UI (observed stale for
10+ days, 2026-07-13).

Designed for cron 21:20 UTC (06:20 KST, right after US close, before the
verdict batch). Exits 0 on partial success (at least one account recorded),
non-zero only if every account failed — same convention as the other jobs.

Usage:
  python -m src.portfolio.snapshot_job
"""
from __future__ import annotations

import json
import sys

from ..core import kst
from ..market.pipeline import gap_filler
from ..market.source.registry import for_symbol
from ..market.store import quote_store
from . import service
from .store import account_store, holding_store, snapshot_store

_QUOTE_MAX_AGE_S = 600  # cron context: anything from the last 10min is fine


def refresh_quotes(symbol_ids: set[str]) -> tuple[int, list[str]]:
    """Fetch a fresh quote for each symbol (deduped across accounts).

    Returns (n_fresh, stale) — the symbols we could not price are named, not
    swallowed. A silent failure here does not look like one: the previous
    quote stays in the cache and every valuation keeps using it, so a dead
    source shows up as a plausible number rather than a missing one.
    """
    n = 0
    stale: list[str] = []
    for sid in sorted(symbol_ids):
        if quote_store.get(sid, max_age_s=_QUOTE_MAX_AGE_S) is not None:
            n += 1
            continue
        src = for_symbol(sid)
        if src is None:
            stale.append(f"{sid}: no source")
            continue
        try:
            q = src.get_quote(sid)
        except Exception as e:  # noqa: BLE001 — one dead source is not the job
            stale.append(f"{sid}: {str(e)[:80]}")
            continue
        if q is None:
            stale.append(f"{sid}: source returned nothing")
            continue
        quote_store.set_quote(q)
        n += 1
    return n, stale


def main() -> int:
    accounts = account_store.list_all()
    held = set(holding_store.all_symbol_ids())
    filled = gap_filler.fill_many(sorted(held))  # keep charts/sparklines current
    quoted, quote_stale = refresh_quotes(held)

    ok, failed = [], []
    for acc in accounts:
        aid = acc["account_id"]
        try:
            res = service.aggregate(aid)  # writes today's snapshot internally
            if not res.get("totals"):
                ok.append(aid)  # empty account — nothing to snapshot
            # aggregate() swallows snapshot errors — verify the row landed
            elif snapshot_store.has_for(kst.today(), aid):
                ok.append(aid)
            else:
                failed.append({"account_id": aid, "error": "snapshot row missing"})
        except Exception as e:
            failed.append({"account_id": aid, "error": str(e)})

    print(json.dumps({
        "accounts_ok": ok, "accounts_failed": failed,
        "symbols": len(held), "quotes_fresh": quoted,
        "quotes_stale": quote_stale,
        "ohlcv_bars_added": sum(n for n in filled.values() if n > 0),
        "ohlcv_errors": [s for s, n in filled.items() if n < 0],
    }, ensure_ascii=False))
    return 0 if ok or not accounts else 1


if __name__ == "__main__":
    sys.exit(main())
