"""Exchange sessions: is a day a trading day, and where a market stands now.

The pages labelled a weekday holiday (한글날, Thanksgiving) as '장중' all day,
coloured every quote late and re-polled every five minutes. Nothing stored
can tell a holiday apart while it is happening — bars arrive after the close
— so the holidays are listed here, from the exchanges' published calendars.

The list covers whole years. `covered(year)` is checked by the sources page
health (a missing next year shows up in December, not as a silent mislabel).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ..core import kst

# KRX 휴장일 (정규시장): 법정 공휴일·대체공휴일·선거일·근로자의 날·연말
# NYSE holidays (full closes; early closes are trading days)
HOLIDAYS: dict[str, frozenset] = {
    "KR": frozenset(date.fromisoformat(d) for d in (
        "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02",
        "2026-05-01", "2026-05-05", "2026-05-25", "2026-06-03", "2026-08-17",
        "2026-09-24", "2026-09-25", "2026-10-05", "2026-10-09", "2026-12-25",
        "2026-12-31",
    )),
    "US": frozenset(date.fromisoformat(d) for d in (
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    )),
}
YEARS = frozenset({2026})

_TZ = {"KR": kst.KST, "US": ZoneInfo("America/New_York")}
_HOURS = {"KR": (time(9, 0), time(15, 30)), "US": (time(9, 30), time(16, 0))}


def covered(year: int) -> bool:
    return year in YEARS


def is_session(market: str, day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS.get(market, frozenset())


def last_session(market: str, day: date) -> date:
    """The newest trading day on or before `day`."""
    while not is_session(market, day):
        day -= timedelta(days=1)
    return day


def state(market: str, now: Optional[datetime] = None) -> dict:
    """phase: pre (before today's open) · open · post (after today's close) ·
    closed (not a session today). `last_session` is the newest session whose
    close has passed or that is running now — the date a P&L label names."""
    now = (now or kst.now()).astimezone(_TZ[market])
    today = now.date()
    open_t, close_t = _HOURS[market]
    if not is_session(market, today):
        phase = "closed"
    elif now.time() < open_t:
        phase = "pre"
    elif now.time() < close_t:
        phase = "open"
    else:
        phase = "post"
    last = today if phase in ("open", "post") else last_session(market, today - timedelta(days=1))
    return {"phase": phase, "today": today.isoformat(), "today_is_session": is_session(market, today),
            "last_session": last.isoformat(), "covered": covered(today.year)}
