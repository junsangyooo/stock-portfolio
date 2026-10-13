"""Account aggregate service.

Combines holdings + live quotes (market store) + cash balances + universe
meta into the rendered portfolio shape consumed by the dashboard frontend.
"""
from __future__ import annotations

from ..market.store import ohlcv_store, quote_store
from ..universe import search as uni_search
from .store import account_store, balance_store, holding_store, snapshot_store


def aggregate(account_id: str, *, spark_n: int = 30) -> dict:
    """Build the rich portfolio shape for one account."""
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
        ccy = meta.get("currency") or acc["base_currency"]
        q = quote_store.get(sid, max_age_s=quote_store.STALE_S)
        price = q.price if q else None
        prev = q.prev_close if q else None
        qty = h.get("qty")
        avg = h.get("avg_cost")

        t = totals.setdefault(ccy, {"eval": 0.0, "cost": 0.0, "pnl": 0.0,
                                     "n_held": 0, "n_tracked": 0})
        t["n_tracked"] += 1
        if price is not None and qty is not None and qty > 0:
            ev = price * qty
            t["eval"] += ev
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
        t = totals.setdefault(ccy, {"eval": 0.0, "cost": 0.0, "pnl": 0.0,
                                     "n_held": 0, "n_tracked": 0})
        t["cash"] = amt

    for ccy, t in totals.items():
        t.setdefault("cash", 0.0)
        t["total_assets"] = t["eval"] + t["cash"]
        t["pnl_pct"] = (t["pnl"] / t["cost"] * 100) if t["cost"] > 0 else 0.0

    # lazy daily snapshot
    try:
        snapshot_store.maybe_snapshot_today(account_id, totals)
    except Exception:
        pass

    return {
        "exists": True,
        "account": acc,
        "holdings": out_holdings,
        "totals": totals,
        "balances": cash,
        "n": len(out_holdings),
    }


def refresh_held() -> dict:
    """Gap-fill bars and warm quotes for every symbol any account holds — the
    ↻ button. Market data is per-symbol, so it covers the whole book, not the
    visible account, and returns nothing account-specific."""
    from ..market import quotes
    from ..market.pipeline import gap_filler
    sids = sorted(holding_store.all_symbol_ids())
    filled = gap_filler.fill_many(sids)
    fresh = sum(1 for q in quotes.warm(sids, max_age_s=10).values() if q is not None)
    return {"ok": True, "symbols": len(sids), "filled": filled, "quotes_fresh": fresh}
