"""Where a symbol's numbers sit among its peers.

PER 12 is not cheap or expensive on its own — it is one or the other relative
to the companies it competes with. Until the whole universe was in market.db
that comparison was impossible, so every valuation judgment was made against
remembered absolutes ("PER under 15 is cheap"), which is how a Korean utility
and a US software company end up judged by the same yardstick.

Peers = same `sector` (from the sector table), same market. Falls back to the
whole market when the sector is unknown or too thin to mean anything — and
says so, because a percentile computed against 4 companies is noise wearing a
number.
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


def _latest_ts(c) -> Optional[int]:
    row = c.execute("SELECT MAX(ts) FROM valuation").fetchone()
    return int(row[0]) if row and row[0] else None


def percentiles(symbol_id: str) -> dict:
    """This symbol's valuation percentile within its peer group.

    Returns {metric: {value, percentile, median, n}} plus how the peer group
    was chosen. A metric the symbol has no value for is omitted rather than
    reported as zero.
    """
    market, _ = split_symbol(symbol_id)
    with connect() as c:
        ts = _latest_ts(c)
        if ts is None:
            return {"error": "valuation table is empty"}

        row = c.execute(
            f"SELECT {', '.join(_METRICS)} FROM valuation "
            "WHERE symbol_id = ? ORDER BY ts DESC LIMIT 1",
            (symbol_id,)).fetchone()
        if row is None:
            return {"error": "no valuation for this symbol"}
        mine = {m: row[m] for m in _METRICS}

        sector = (c.execute("SELECT sector FROM sector WHERE symbol_id = ?",
                            (symbol_id,)).fetchone() or {"sector": None})["sector"]

        basis, peers = _peer_ids(c, market, sector)
        if len(peers) < _MIN_PEERS:
            basis, peers = _peer_ids(c, market, None)
        if len(peers) < _MIN_PEERS:
            return {"error": f"only {len(peers)} peers", "basis": basis}

        out: dict = {}
        marks = ",".join("?" * len(peers))
        for m in _METRICS:
            if mine[m] is None:
                continue
            # Ratios are meaningless when negative (a loss-making PER) — those
            # rows are excluded rather than ranked at the cheap end.
            floor = " AND v.{0} > 0".format(m) if m in _LOWER_IS_CHEAPER else ""
            vals = [r[0] for r in c.execute(
                f"SELECT v.{m} FROM valuation v WHERE v.ts = ? "
                f"AND v.symbol_id IN ({marks}) AND v.{m} IS NOT NULL{floor}",
                [ts, *peers]).fetchall()]
            if len(vals) < _MIN_PEERS:
                continue
            vals.sort()
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
        "asof_ts": ts,
        "metrics": out,
        "note_ko": ("percentile 은 낮을수록 동종 대비 값이 작다는 뜻. "
                    "per·pbr·psr 은 낮은 쪽이 저평가, div_yield·market_cap 은 "
                    "높은 쪽이 크다. 적자(음수 비율) 종목은 순위에서 제외했다."),
    }


def _peer_ids(c, market: str, sector: Optional[str]) -> tuple[str, list[str]]:
    if sector:
        rows = c.execute(
            "SELECT symbol_id FROM sector WHERE sector = ? AND symbol_id LIKE ?",
            (sector, f"{market}:%")).fetchall()
        return f"{market} · {sector}", [r[0] for r in rows]
    rows = c.execute(
        "SELECT DISTINCT symbol_id FROM valuation WHERE symbol_id LIKE ?",
        (f"{market}:%",)).fetchall()
    return f"{market} 전체", [r[0] for r in rows]
