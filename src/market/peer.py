"""Where a symbol's numbers sit among its peers.

PER 12 is not cheap or expensive on its own — it is one or the other relative
to the companies it competes with. Until the whole universe was in market.db
that comparison was impossible, so every valuation judgment was made against
remembered absolutes ("PER under 15 is cheap"), which is how a Korean utility
and a US software company end up judged by the same yardstick.

Peers = same `sector` (from the sector table), same market. Falls back to the
whole market when the sector is unknown or too thin to mean anything — and
says so, because a percentile computed against 4 companies is noise wearing a
number. Each peer contributes its own most recent valuation, not a shared
date: two writers fill this table with different coverage and pinning one
instant leaves only whichever of them wrote last.
"""
from __future__ import annotations

from typing import Optional

from ..core.paths import split_symbol
from .store.db import connect

# Below this a percentile is not worth reporting: with 12 peers each one moves
# the rank by 8 points.
_MIN_PEERS = 12

_METRICS = ("per", "pbr", "psr", "div_yield", "market_cap")

# For these, a LOW value is the cheap end; the percentile is reported as-is and
# the caller is told which direction "high" means.
_LOWER_IS_CHEAPER = {"per", "pbr", "psr"}

_STALE_DAYS = 45          # a peer whose last valuation is older than this is out


def _newest_ts(c, market: str) -> Optional[int]:
    row = c.execute("SELECT MAX(ts) FROM valuation WHERE symbol_id LIKE ?",
                    (f"{market}:%",)).fetchone()
    return int(row[0]) if row and row[0] else None


def _peer_rows(c, market: str, sector: Optional[str], floor_ts: int) -> tuple:
    """(basis, [{symbol_id, per, pbr, …}]) — each peer at ITS OWN latest date.

    Not "everyone on the newest date": two writers fill this table with
    different coverage — the nightly job refreshes the dozen symbols somebody
    holds, the bulk backfill did the whole market up to the day it ran. Pinning
    one instant leaves whichever set wrote last, which is how a market-wide
    comparison came back with fourteen companies in it.
    """
    cols = ", ".join(f"v.{m}" for m in _METRICS)
    if sector:
        scope = ("JOIN sector s ON s.symbol_id = v.symbol_id "
                 "WHERE s.sector = ? AND v.symbol_id LIKE ?")
        args = [sector, f"{market}:%"]
        basis = f"{market} · {sector}"
    else:
        scope = "WHERE v.symbol_id LIKE ?"
        args = [f"{market}:%"]
        basis = f"{market} 전체"
    rows = c.execute(
        f"SELECT v.symbol_id, {cols} FROM valuation v "
        f"JOIN (SELECT symbol_id, MAX(ts) mts FROM valuation "
        f"      WHERE ts >= ? GROUP BY symbol_id) m "
        f"  ON m.symbol_id = v.symbol_id AND m.mts = v.ts "
        f"{scope}", [floor_ts, *args]).fetchall()
    return basis, [dict(r) for r in rows]


def percentiles(symbol_id: str) -> dict:
    """This symbol's valuation percentile within its peer group.

    Returns {metric: {value, percentile, median, n}} plus how the peer group
    was chosen. A metric the symbol has no value for is omitted rather than
    reported as zero.
    """
    market, _ = split_symbol(symbol_id)
    with connect() as c:
        newest = _newest_ts(c, market)
        if newest is None:
            return {"error": f"no valuation stored for {market}"}
        floor_ts = newest - _STALE_DAYS * 86400

        row = c.execute(
            f"SELECT ts, {', '.join(_METRICS)} FROM valuation "
            "WHERE symbol_id = ? ORDER BY ts DESC LIMIT 1",
            (symbol_id,)).fetchone()
        if row is None:
            return {"error": "no valuation for this symbol"}
        mine = {m: row[m] for m in _METRICS}

        sector = (c.execute("SELECT sector FROM sector WHERE symbol_id = ?",
                            (symbol_id,)).fetchone() or {"sector": None})["sector"]

        basis, peers = _peer_rows(c, market, sector, floor_ts)
        if len(peers) < _MIN_PEERS and sector:
            basis, peers = _peer_rows(c, market, None, floor_ts)
        if len(peers) < _MIN_PEERS:
            return {"error": f"only {len(peers)} peers", "basis": basis}

        out: dict = {}
        for m in _METRICS:
            if mine[m] is None:
                continue
            # A negative ratio (a loss-making PER) is not "cheap" — those are
            # dropped rather than ranked at the bottom.
            vals = sorted(p[m] for p in peers
                          if p[m] is not None
                          and not (m in _LOWER_IS_CHEAPER and p[m] <= 0))
            if len(vals) < _MIN_PEERS:
                continue
            below = sum(1 for v in vals if v < mine[m])
            out[m] = {
                "value": round(float(mine[m]), 4),
                "percentile": round(100.0 * below / len(vals), 1),
                "median": round(vals[len(vals) // 2], 4),
                "n": len(vals),
            }

    return {
        "symbol_id": symbol_id,
        "basis": basis,
        "asof_ts": int(row["ts"]),
        "metrics": out,
        "note_ko": ("percentile 은 낮을수록 동종 대비 값이 작다는 뜻. "
                    "per·pbr·psr 은 낮은 쪽이 저평가, div_yield·market_cap 은 "
                    "높은 쪽이 크다. 적자(음수 비율) 종목은 순위에서 제외했다. "
                    "비교 대상은 각자의 최신 밸류에이션이며 45일 넘게 갱신이 "
                    "없는 종목은 제외했다."),
    }
