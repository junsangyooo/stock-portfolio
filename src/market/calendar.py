"""Exchange sessions: is a day a trading day, and where a market stands now.

The pages labelled a weekday holiday (한글날, Thanksgiving) as '장중' all day,
coloured every quote late and re-polled every five minutes. Nothing stored
can tell a holiday apart while it is happening — bars arrive after the close
— so the holidays are listed here, from the exchanges' published calendars,
together with the days whose hours differ (KRX opens at 10:00 on the year's
first trading day and on 수능 day, which also closes at 16:30; NYSE closes at
13:00 before some holidays).

The values cover 2025-2027 for KRX and NYSE, taken from the exchanges'
published calendars; some 2027 entries are provisional until the exchanges
confirm them. The quant engine on the same server keeps its own copy of this
table in its own repository: when a year is added here, add it there too.

The list covers whole years. `covered(year)` is checked by the sources page
health and a test (a missing next year shows up in December, not as a silent
mislabel).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ..core import kst

# KRX 휴장일 (정규시장): 법정 공휴일·대체공휴일·선거일·노동절·제헌절·연말.
# Weekend days are omitted, as KRX omits them.
_KR_HOLIDAYS = {
    "2025-01-01": "신정",
    "2025-01-27": "임시공휴일",
    "2025-01-28": "설날 연휴",
    "2025-01-29": "설날",
    "2025-01-30": "설날 연휴",
    "2025-03-03": "삼일절 대체공휴일",
    "2025-05-01": "근로자의 날",
    "2025-05-05": "어린이날·부처님오신날",
    "2025-05-06": "어린이날 대체공휴일",
    "2025-06-03": "임시공휴일(대통령선거)",
    "2025-06-06": "현충일",
    "2025-08-15": "광복절",
    "2025-10-03": "개천절",
    "2025-10-06": "추석",
    "2025-10-07": "추석 연휴",
    "2025-10-08": "추석 대체공휴일",
    "2025-10-09": "한글날",
    "2025-12-25": "성탄절",
    "2025-12-31": "연말 휴장일",
    "2026-01-01": "신정",
    "2026-02-16": "설날 연휴",
    "2026-02-17": "설날",
    "2026-02-18": "설날 연휴",
    "2026-03-02": "삼일절 대체공휴일",
    "2026-05-01": "노동절",
    "2026-05-05": "어린이날",
    "2026-05-25": "부처님오신날 대체공휴일",
    "2026-06-03": "전국동시지방선거",
    "2026-07-17": "제헌절",
    "2026-08-17": "광복절 대체공휴일",
    "2026-09-24": "추석 연휴",
    "2026-09-25": "추석",
    "2026-10-05": "개천절 대체공휴일",
    "2026-10-09": "한글날",
    "2026-12-25": "성탄절",
    "2026-12-31": "연말 휴장일",
    "2027-01-01": "신정",
    "2027-02-08": "설날 연휴",
    "2027-02-09": "설날 대체공휴일",
    "2027-03-01": "삼일절",
    "2027-05-03": "노동절 대체공휴일",
    "2027-05-05": "어린이날",
    "2027-05-13": "부처님오신날",
    "2027-07-19": "제헌절 대체공휴일",
    "2027-08-16": "광복절 대체공휴일",
    "2027-09-14": "추석 연휴",
    "2027-09-15": "추석",
    "2027-09-16": "추석 연휴",
    "2027-10-04": "개천절 대체공휴일",
    "2027-10-11": "한글날 대체공휴일",
    "2027-12-27": "성탄절 대체공휴일",
    "2027-12-31": "연말 휴장일",
}
# NYSE full closes (early closes are trading days, in _US_SPECIAL)
_US_HOLIDAYS = {
    "2025-01-01": "New Year's Day",
    "2025-01-09": "National Day of Mourning (President Carter)",
    "2025-01-20": "Martin Luther King Jr. Day",
    "2025-02-17": "Washington's Birthday",
    "2025-04-18": "Good Friday",
    "2025-05-26": "Memorial Day",
    "2025-06-19": "Juneteenth",
    "2025-07-04": "Independence Day",
    "2025-09-01": "Labor Day",
    "2025-11-27": "Thanksgiving Day",
    "2025-12-25": "Christmas Day",
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day",
    "2027-01-18": "Martin Luther King Jr. Day",
    "2027-02-15": "Washington's Birthday",
    "2027-03-26": "Good Friday",
    "2027-05-31": "Memorial Day",
    "2027-06-18": "Juneteenth (observed)",
    "2027-07-05": "Independence Day (observed)",
    "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving Day",
    "2027-12-24": "Christmas Day (observed)",
}
# Days whose hours differ from the regular ones: (open, close, why), market-local
_KR_SPECIAL = {
    "2025-01-02": (time(10, 0), time(15, 30), "새해 첫 거래일"),
    "2025-11-13": (time(10, 0), time(16, 30), "수능"),
    "2026-01-02": (time(10, 0), time(15, 30), "새해 첫 거래일"),
    "2026-11-19": (time(10, 0), time(16, 30), "수능"),
    "2027-01-04": (time(10, 0), time(15, 30), "새해 첫 거래일"),
    "2027-11-18": (time(10, 0), time(16, 30), "수능"),
}
_US_SPECIAL = {
    "2025-07-03": (time(9, 30), time(13, 0), "early close"),
    "2025-11-28": (time(9, 30), time(13, 0), "early close"),
    "2025-12-24": (time(9, 30), time(13, 0), "early close"),
    "2026-11-27": (time(9, 30), time(13, 0), "early close"),
    "2026-12-24": (time(9, 30), time(13, 0), "early close"),
    "2027-11-26": (time(9, 30), time(13, 0), "early close"),
}

HOLIDAYS: dict[str, dict[date, str]] = {
    "KR": {date.fromisoformat(d): n for d, n in _KR_HOLIDAYS.items()},
    "US": {date.fromisoformat(d): n for d, n in _US_HOLIDAYS.items()},
}
SPECIAL: dict[str, dict[date, tuple[time, time, str]]] = {
    "KR": {date.fromisoformat(d): v for d, v in _KR_SPECIAL.items()},
    "US": {date.fromisoformat(d): v for d, v in _US_SPECIAL.items()},
}
YEARS = frozenset({2025, 2026, 2027})

_TZ = {"KR": kst.KST, "US": ZoneInfo("America/New_York")}
_HOURS = {"KR": (time(9, 0), time(15, 30)), "US": (time(9, 30), time(16, 0))}


def covered(year: int) -> bool:
    return year in YEARS


def is_session(market: str, day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS.get(market, {})


def hours(market: str, day: date) -> tuple[time, time]:
    """(open, close) of a session day, market-local — the regular hours
    unless the day is listed in SPECIAL."""
    sp = SPECIAL.get(market, {}).get(day)
    return (sp[0], sp[1]) if sp else _HOURS[market]


def last_session(market: str, day: date) -> date:
    """The newest trading day on or before `day`."""
    while not is_session(market, day):
        day -= timedelta(days=1)
    return day


def state(market: str, now: Optional[datetime] = None) -> dict:
    """phase: pre (before today's open) · open · post (after today's close) ·
    closed (not a session today). `last_session` is the newest session whose
    close has passed or that is running now — the date a P&L label names.
    `open`/`close` are today's hours ('HH:MM', market-local; None when
    closed), so a late open or an early close moves the phase with it."""
    now = (now or kst.now()).astimezone(_TZ[market])
    today = now.date()
    session = is_session(market, today)
    open_t, close_t = hours(market, today)
    if not session:
        phase = "closed"
    elif now.time() < open_t:
        phase = "pre"
    elif now.time() < close_t:
        phase = "open"
    else:
        phase = "post"
    last = today if phase in ("open", "post") else last_session(market, today - timedelta(days=1))
    return {"phase": phase, "today": today.isoformat(), "today_is_session": session,
            "last_session": last.isoformat(), "covered": covered(today.year),
            "open": open_t.strftime("%H:%M") if session else None,
            "close": close_t.strftime("%H:%M") if session else None,
            # weekday holidays, so a page counts '2거래일 전' over 한글날 right
            "holidays": sorted(d.isoformat() for d in HOLIDAYS.get(market, ()))}
