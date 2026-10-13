"""Every model call, written down: tokens in, tokens out, what it cost.

The subscription has a weekly ceiling and nothing measured where it went. The
first time it ran out (2026-09-15) the only evidence was a job log full of
"You've hit your weekly limit" — after the fact, with no way to say which
stage had spent it.

The numbers here are not estimates. `claude -p --output-format json` reports
its own usage per call, and llama.cpp reports its own; both are copied
verbatim into `data/logs/usage.jsonl`, one line per call. No model reads this
file and no model writes it.

    python -m src.core.usage                  # last 7 days by stage
    python -m src.core.usage --days 30 --json

A line is appended, never rewritten, so concurrent cron jobs can each write
their own (one short line per call, which POSIX append keeps whole).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from . import kst, paths

FILENAME = "usage.jsonl"


def path():
    """Resolved per call, not at import — a test redirects `paths.LOGS_DIR`."""
    return paths.LOGS_DIR / FILENAME


def record(stage: str, model: str, *, backend: str = "claude_cli",
           usage: Optional[dict] = None, cost_usd: Optional[float] = None,
           elapsed_s: Optional[float] = None, ok: bool = True,
           error: Optional[str] = None, extra: Optional[dict] = None) -> None:
    """Append one call's accounting. Never raises — a ledger of spending must
    not be able to take down the thing it is measuring."""
    u = usage or {}

    def _n(*keys) -> int:
        for k in keys:
            v = u.get(k)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 0
        return 0

    row = {
        "ts": kst.now().isoformat(timespec="seconds"),
        "stage": stage, "backend": backend, "model": model, "ok": bool(ok),
        "in": _n("input_tokens", "prompt_tokens"),
        "cache_create": _n("cache_creation_input_tokens"),
        "cache_read": _n("cache_read_input_tokens"),
        "out": _n("output_tokens", "completion_tokens"),
        "cost_usd": round(float(cost_usd), 6) if cost_usd is not None else None,
        "elapsed_s": round(float(elapsed_s), 1) if elapsed_s is not None else None,
    }
    if error:
        row["error"] = str(error)[:200]
    if extra:
        row.update(extra)
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def rows(days: int = 7, now: Optional[datetime] = None) -> list[dict]:
    """The lines inside the window, oldest first."""
    since = ((now or kst.now()).date() - timedelta(days=days - 1)).isoformat()
    out = []
    p = path()
    if not p.exists():
        return out
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue            # a torn line is one call, not the file
            if str(r.get("ts", ""))[:10] >= since:
                out.append(r)
    return out


def report(days: int = 7, now: Optional[datetime] = None) -> dict:
    """Totals per stage and per day — what a person reads to see where the
    week went."""
    def _blank() -> dict:
        return {"calls": 0, "failed": 0, "in": 0, "cache_create": 0,
                "cache_read": 0, "out": 0, "cost_usd": 0.0, "elapsed_s": 0.0}

    by_stage: dict = defaultdict(_blank)
    by_day: dict = defaultdict(_blank)
    total = _blank()
    for r in rows(days, now):
        for bucket in (by_stage[r.get("stage", "?")],
                       by_day[str(r.get("ts", ""))[:10]], total):
            bucket["calls"] += 1
            bucket["failed"] += 0 if r.get("ok", True) else 1
            for k in ("in", "cache_create", "cache_read", "out"):
                bucket[k] += int(r.get(k) or 0)
            bucket["cost_usd"] += float(r.get("cost_usd") or 0)
            bucket["elapsed_s"] += float(r.get("elapsed_s") or 0)
    for b in list(by_stage.values()) + list(by_day.values()) + [total]:
        b["cost_usd"] = round(b["cost_usd"], 4)
        b["elapsed_s"] = round(b["elapsed_s"], 1)
    return {"days": days, "total": total,
            "by_stage": dict(sorted(by_stage.items(),
                                    key=lambda kv: -kv[1]["cost_usd"])),
            "by_day": dict(sorted(by_day.items()))}


def _table(title: str, block: dict) -> str:
    head = (f"{title:<22}{'calls':>7}{'fail':>6}{'new-in':>10}{'cached':>10}"
            f"{'out':>9}{'usd':>9}{'min':>7}")
    lines = [head, "-" * len(head)]
    for k, v in block.items():
        lines.append(f"{k[:22]:<22}{v['calls']:>7}{v['failed']:>6}"
                     f"{v['in'] + v['cache_create']:>10,}{v['cache_read']:>10,}"
                     f"{v['out']:>9,}{v['cost_usd']:>9.2f}"
                     f"{v['elapsed_s'] / 60:>7.0f}")
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = report(args.days)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    print(_table(f"stage (최근 {args.days}일)", rep["by_stage"]))
    print()
    print(_table("day", rep["by_day"]))
    t = rep["total"]
    print(f"\n합계 {t['calls']}콜 (실패 {t['failed']}) · "
          f"새 입력 {t['in'] + t['cache_create']:,} · 캐시 {t['cache_read']:,} · "
          f"출력 {t['out']:,} 토큰 · ${t['cost_usd']:.2f} · "
          f"{t['elapsed_s'] / 3600:.1f}시간")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
