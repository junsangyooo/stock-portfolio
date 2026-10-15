"""Account aggregate service.

Combines holdings + live quotes (market store) + cash balances + universe
meta into the rendered portfolio shape consumed by the dashboard frontend,
writes the day's asset snapshot, and runs the follow-up work a newly held
symbol needs (its bars, its themes).

Snapshots: the scheduled closes (snapshot_job, 06:20 and 16:35 KST) write the
day's row and overwrite it (`record_snapshot`); a page view writes one only
when the day has none yet (`aggregate`). A currency with a priceless holding
is never written either way.
"""
from __future__ import annotations

import logging

from ..core import kst
from ..market import quotes
from ..market.pipeline import gap_filler
from ..market.store import ohlcv_store, quote_store
from ..research.news.mapping import ensure_theme_mapping
from ..universe import search as uni_search
from .store import account_store, balance_store, holding_store, snapshot_store

log = logging.getLogger(__name__)


def _blank_totals() -> dict:
    return {"eval": 0.0, "cost": 0.0, "pnl": 0.0, "n_held": 0, "n_tracked": 0,
            "n_missing": 0}


def _whole(totals: dict) -> dict:
    """The currencies whose every position has a price. A sum missing a
    holding is not that day's assets: written down, it draws a fall that
    never happened on the asset curve."""
    return {c: t for c, t in totals.items() if not t["n_missing"]}


def aggregate(account_id: str, *, spark_n: int = 30, snapshot: bool = True) -> dict:
    """Build the rich portfolio shape for one account. With `snapshot`, a
    currency that has no row for today yet gets one (write_if_absent)."""
    acc = account_store.get(account_id)
    if acc is None:
        return {"account_id": account_id, "exists": False,
                "holdings": [], "totals": {}, "balances": {}}

    rows = holding_store.list_by_account(account_id)
    out_holdings: list[dict] = []
    totals: dict[str, dict] = {}

    # sparklines (last 30 daily closes), one query for the whole account —
    # best-effort
    try:
        sparks = ohlcv_store.read_closes([h["symbol_id"] for h in rows], spark_n)
    except Exception:
        sparks = {}

    for h in rows:
        sid = h["symbol_id"]
        meta = uni_search.get(sid) or {}
        ccy = uni_search.currency_of(sid, meta)
        q = quote_store.get(sid, max_age_s=quote_store.STALE_S)
        price = q.price if q else None
        prev = q.prev_close if q else None
        qty = h.get("qty")
        avg = h.get("avg_cost")

        t = totals.setdefault(ccy, _blank_totals())
        t["n_tracked"] += 1
        if price is None and qty:
            t["n_missing"] += 1
        if price is not None and qty is not None and qty > 0:
            t["eval"] += price * qty
            if avg is not None:
                t["cost"] += avg * qty
                t["pnl"] += (price - avg) * qty
                t["n_held"] += 1

        out_holdings.append({
            "symbol_id": sid,
            "market": meta.get("market"),
            "code": meta.get("code"),
            # a per-holding override wins over the universe name
            "name_kr": h.get("name") or meta.get("name_kr"),
            "name_en": meta.get("name_en"),
            "currency": ccy,
            "sector": meta.get("sector"),
            "is_etf": meta.get("is_etf", False),
            "avg_cost": avg,
            "qty": qty,
            "note": h.get("note"),
            "added_at": h.get("added_at"),
            "sort_order": h.get("sort_order"),
            "price": price,
            "prev_close": prev,
            "quote_ts": q.ts if q else None,
            "spark": sparks.get(sid, []),
        })

    # attach cash balances
    cash = balance_store.get_all(account_id)
    for ccy, amt in cash.items():
        totals.setdefault(ccy, _blank_totals())["cash"] = amt

    for t in totals.values():
        t.setdefault("cash", 0.0)
        t["total_assets"] = t["eval"] + t["cash"]
        t["pnl_pct"] = (t["pnl"] / t["cost"] * 100) if t["cost"] > 0 else 0.0

    if snapshot:
        # a page view: fill a day the scheduled closes have not written yet
        try:
            snapshot_store.write_if_absent(kst.today(), account_id, _whole(totals))
        except Exception as e:  # noqa: BLE001 — a page read must not fail on it
            log.warning("snapshot of %s on view failed: %s", account_id, e)

    return {
        "exists": True,
        "account": acc,
        "holdings": out_holdings,
        "totals": totals,
        "balances": cash,
        "n": len(out_holdings),
    }


def record_snapshot(account_id: str) -> dict:
    """The scheduled close: write today's rows for every fully priced
    currency, replacing what a view or an earlier close wrote. Raises on a
    write failure — the job counts it. {"written": [ccy], "missing": [ccy]}"""
    agg = aggregate(account_id, spark_n=0, snapshot=False)
    totals = agg.get("totals") or {}
    whole = _whole(totals)
    snapshot_store.write(kst.today(), account_id, whole)
    return {"written": sorted(whole), "missing": sorted(set(totals) - set(whole))}


def fill_new_bars(symbol_id: str) -> tuple[int, bool]:
    """(bars added, failed) for a symbol just added to a book. The holding is
    the person's record; a price source being down must not lose it — the
    bars come with the next refresh or snapshot instead."""
    try:
        return gap_filler.fill_one(symbol_id), False
    except Exception as e:  # noqa: BLE001 — SourceError and kin
        log.warning("backfill %s on add failed: %s", symbol_id, e)
        return 0, True


def map_new_themes(symbol_id: str) -> None:
    """Tag a newly held symbol into themes (may make one model call — run it
    off the request path). The mapping is a fact about the symbol in
    market.db; removing the holding leaves it (ops notes §4)."""
    ensure_theme_mapping(symbol_id)


def after_new_holding(symbol_id: str) -> None:
    """Everything a freshly opened position needs, for a background task:
    its bars, then its themes. Never raises."""
    fill_new_bars(symbol_id)
    map_new_themes(symbol_id)


def refresh_held() -> dict:
    """Gap-fill bars and warm quotes for every symbol any account holds — the
    ↻ button. Market data is per-symbol, so it covers the whole book, not the
    visible account, and returns nothing account-specific."""
    sids = sorted(holding_store.all_symbol_ids())
    filled = gap_filler.fill_many(sids)
    fresh = sum(1 for q in quotes.warm(sids, max_age_s=10).values() if q is not None)
    # Counts, not the per-symbol map: any account's session can press ↻, and
    # the map named every account's holdings.
    return {"ok": True, "symbols": len(sids),
            "bars_added": sum(n for n in filled.values() if n > 0),
            "failed": sum(1 for n in filled.values() if n < 0),
            "quotes_fresh": fresh}
