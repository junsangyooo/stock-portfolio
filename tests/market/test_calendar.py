"""Exchange sessions — a weekday holiday is not '장중'."""
from datetime import date, datetime

from src.core.kst import KST
from src.market import calendar


def test_weekday_holidays_are_closed_all_day():
    hangul = datetime(2026, 10, 9, 10, 0, tzinfo=KST)               # 한글날, a Friday
    s = calendar.state("KR", hangul)
    assert s["phase"] == "closed" and s["last_session"] == "2026-10-08"
    thanksgiving = datetime(2026, 11, 27, 0, 0, tzinfo=KST)         # 26th 10:00 New York
    assert calendar.state("US", thanksgiving)["phase"] == "closed"


def test_phases_and_the_session_a_label_names():
    assert calendar.state("KR", datetime(2026, 10, 14, 8, 10, tzinfo=KST))["phase"] == "pre"
    s = calendar.state("KR", datetime(2026, 10, 14, 8, 10, tzinfo=KST))
    assert s["last_session"] == "2026-10-13"                         # before the open: yesterday
    assert calendar.state("KR", datetime(2026, 10, 14, 15, 40, tzinfo=KST))["last_session"] == "2026-10-14"
    us = calendar.state("US", datetime(2026, 10, 14, 23, 0, tzinfo=KST))   # 10:00 New York
    assert us["phase"] == "open" and us["last_session"] == "2026-10-14"
    assert calendar.last_session("KR", date(2026, 10, 5)) == date(2026, 10, 2)   # 개천절 substitute


def test_the_list_covers_this_year():
    """Add next year's holidays before it starts (KRX and NYSE publish them)."""
    assert calendar.covered(datetime.now(KST).year)
