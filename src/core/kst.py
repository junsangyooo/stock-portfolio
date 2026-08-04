"""KST (UTC+9) — the clock every stored date and timestamp in this app uses.

Market data, analysis artifacts and snapshots are all keyed by the Korean
trading day, so the offset lives here once instead of being re-declared in
every module that needs it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now() -> datetime:
    return datetime.now(KST)


def today() -> str:
    """'YYYY-MM-DD' in KST — the date key for daily artifacts."""
    return now().strftime("%Y-%m-%d")
