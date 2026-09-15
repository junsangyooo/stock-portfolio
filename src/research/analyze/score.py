"""Were the stored analyses right? Deterministic, no LLM, no account.

Nothing scored them until now, which made every prompt change an argument
about taste: more counter-case, heavier news, a previous verdict in the
input — all plausible, none checkable. This reads what was stored, reads what
the price did afterwards, and puts a number on it.

Three questions, kept apart because they fail differently:

  direction     did a buy go up and a sell go down, over 5/10/20 trading days
  coordinates   did price reach the target or break the invalidation first
  contract      how often the answer carried the parts the prompt demands —
                a real counter-case, conditions with a number in them, a
                stop level at all

    python -m src.research.analyze.score                # everything stored
    python -m src.research.analyze.score --since 2026-09-01 --json

An analysis is only scored once the market has had the full horizon to answer
it; younger ones are counted as pending rather than as misses.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from typing import Iterable, Optional

from ...core import paths
from ...core.kst import KST
from ..verdict import KIND

HORIZONS = (5, 10, 20)          # trading days
FLAT_PCT = 3.0                  # a neutral call is right while price stays here
_HAS_NUMBER = re.compile(r"\d")

_BULL = ("buy", "strong_buy")
_BEAR = ("sell", "strong_sell")

# What each label claims, so a hit rate can be read against how often the
# market did that anyway.
_CLAIM = {"buy": "up", "strong_buy": "up", "sell": "down",
          "strong_sell": "down", "neutral": "flat"}


def _to_date(ts) -> date:
    return datetime.fromtimestamp(int(ts), KST).date()


def _bars(symbol_id: str, n: int = 240) -> list[tuple]:
    """(date, high, low, close) ascending — the price side of the answer."""
    from ...market.store import ohlcv_store
    df = ohlcv_store.read(symbol_id, n=n)
    if df.empty:
        return []
    df = df.sort_values("ts")
    return [(_to_date(r.ts), float(r.high), float(r.low), float(r.close))
            for r in df.itertuples()]


def _stored(symbol_ids: Optional[Iterable[str]] = None) -> list[tuple]:
    """(symbol_id, body) for every stored analysis, oldest first."""
    root = paths.ANALYSIS_DIR / KIND
    wanted = set(symbol_ids) if symbol_ids else None
    out = []
    for d in sorted(root.glob("*")) if root.exists() else []:
        sid = d.name.replace("_", ":", 1)
        if wanted and sid not in wanted:
            continue
        for f in sorted(d.glob("*.json")):
            try:
                out.append((sid, json.loads(f.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def _forward(bars: list[tuple], day: date, horizon: int) -> Optional[dict]:
    """What the market did in the `horizon` trading days after `day`.

    None while the window is still open — an unfinished answer is pending, not
    wrong, and counting it as a miss would make every recent batch look bad.
    """
    idx = next((i for i, b in enumerate(bars) if b[0] >= day), None)
    if idx is None or idx + horizon >= len(bars):
        return None
    base = bars[idx][3]
    window = bars[idx + 1: idx + 1 + horizon]
    if not base or not window:
        return None
    return {"base": base, "ret_pct": (window[-1][3] / base - 1) * 100,
            "high": max(b[1] for b in window), "low": min(b[2] for b in window)}


def _direction_hit(label: str, ret_pct: float) -> Optional[bool]:
    """None for a label that makes no directional claim we can grade."""
    if label in _BULL:
        return ret_pct > 0
    if label in _BEAR:
        return ret_pct < 0
    if label == "neutral":
        return abs(ret_pct) <= FLAT_PCT
    return None


def _levels_outcome(body: dict, fwd: dict) -> Optional[str]:
    """Which coordinate the price reached first — the planner's whole premise.

    'both' means the window touched the target and the stop; the bars do not
    say which came first, so it is reported rather than guessed.
    """
    lv = body.get("levels") or {}
    stop = (lv.get("invalidation") or {}).get("price")
    targets = [t.get("price") for t in (lv.get("targets") or [])
               if isinstance(t, dict) and t.get("price")]
    if not stop and not targets:
        return None
    hit_stop = bool(stop and fwd["low"] <= stop)
    hit_target = bool(targets and fwd["high"] >= min(targets))
    if hit_stop and hit_target:
        return "both"
    return "stop" if hit_stop else "target" if hit_target else "neither"


def _contract(body: dict) -> dict:
    """The parts the prompt demands, counted rather than assumed."""
    counter = (body.get("counter") or {}).get("case_ko") or ""
    conds = [c for s in (body.get("outlook") or {}).get("scenarios") or []
             for c in (s.get("confirm") or []) + (s.get("invalidate") or [])]
    lv = body.get("levels") or {}
    return {
        "counter_thin": len(counter.strip()) < 30,
        "conds": len(conds),
        "conds_checkable": sum(1 for c in conds if _HAS_NUMBER.search(c)),
        "no_invalidation": not (lv.get("invalidation") or {}).get("price"),
        "no_targets": not (lv.get("targets") or []),
    }


def report(symbol_ids: Optional[Iterable[str]] = None, *,
           since: Optional[str] = None,
           horizons: Iterable[int] = HORIZONS) -> dict:
    """The scorecard. Every number here is measured, none is a model's own."""
    horizons = tuple(horizons)
    graded = {h: [] for h in horizons}
    levels = {h: Counter() for h in horizons}
    pending = Counter()
    contract, labels, n_ok, n_facts = Counter(), Counter(), 0, 0
    bars_cache: dict[str, list] = {}

    for sid, body in _stored(symbol_ids):
        day = body.get("date")
        if since and (day or "") < since:
            continue
        if body.get("status") != "ok":
            n_facts += 1
            continue
        n_ok += 1
        labels[body.get("label")] += 1
        for k, v in _contract(body).items():
            contract[k] += int(v) if isinstance(v, bool) else v
        d = datetime.strptime(day, "%Y-%m-%d").date() if day else None
        if d is None:
            continue
        if sid not in bars_cache:
            bars_cache[sid] = _bars(sid)
        for h in horizons:
            fwd = _forward(bars_cache[sid], d, h)
            if fwd is None:
                pending[h] += 1
                continue
            hit = _direction_hit(body.get("label"), fwd["ret_pct"])
            graded[h].append({
                "symbol_id": sid, "date": day, "label": body.get("label"),
                "confidence": body.get("confidence"),
                "effort": body.get("effort") or "default",
                "ret_pct": round(fwd["ret_pct"], 2), "hit": hit,
            })
            out = _levels_outcome(body, fwd)
            if out:
                levels[h][out] += 1

    def _rate(rows) -> Optional[float]:
        judged = [r for r in rows if r["hit"] is not None]
        return round(sum(r["hit"] for r in judged) / len(judged), 3) if judged else None

    def _baseline(rows) -> dict:
        """What the tape did to everything, regardless of what we said.

        Without it a scorecard reads a falling market as skill: every `sell`
        is right when everything falls. The number that means something is
        the gap between a label's hit rate and its claim's base rate.
        """
        n = len(rows) or 1
        return {"up": round(sum(r["ret_pct"] > 0 for r in rows) / n, 3),
                "down": round(sum(r["ret_pct"] < 0 for r in rows) / n, 3),
                "flat": round(sum(abs(r["ret_pct"]) <= FLAT_PCT for r in rows) / n, 3)}

    def _by(rows, key, base=None) -> dict:
        out: dict = {}
        for r in rows:
            out.setdefault(key(r), []).append(r)
        res = {}
        for k, v in sorted(out.items(), key=lambda kv: str(kv[0])):
            row = {"n": len(v), "hit_rate": _rate(v),
                   "mean_ret_pct": round(sum(x["ret_pct"] for x in v) / len(v), 2)}
            claim = base and _CLAIM.get(k)
            if claim and row["hit_rate"] is not None:
                row["edge"] = round(row["hit_rate"] - base[claim], 3)
            res[k] = row
        return res

    base = {h: _baseline(graded[h]) for h in horizons}
    return {
        "analyses": {"ok": n_ok, "facts_only": n_facts},
        "labels": dict(labels),
        "contract": dict(contract),
        "horizons": {
            h: {
                "graded": len(graded[h]), "pending": pending[h],
                "hit_rate": _rate(graded[h]),
                "baseline": base[h],
                "by_label": _by(graded[h], lambda r: r["label"], base[h]),
                "by_confidence": _by(
                    graded[h], lambda r: f"{int((r['confidence'] or 0) * 10) / 10:.1f}"),
                # The reasoning budget is stored on each result, so the
                # cheap ones and the expensive ones can be compared here
                # rather than argued about.
                "by_effort": _by(graded[h], lambda r: r["effort"]),
                "levels_first": dict(levels[h]),
            } for h in horizons
        },
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="comma-separated; default all")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD")
    ap.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS))
    args = ap.parse_args()
    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else None)
    out = report(syms, since=args.since,
                 horizons=[int(h) for h in args.horizons.split(",")])
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
