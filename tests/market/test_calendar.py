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


def test_late_open_and_early_close_move_the_phase():
    exam = datetime(2026, 11, 19, 9, 30, tzinfo=KST)                # 수능: KRX opens 10:00
    s = calendar.state("KR", exam)
    assert s["phase"] == "pre" and (s["open"], s["close"]) == ("10:00", "16:30")
    assert s["last_session"] == "2026-11-18"
    assert calendar.state("KR", datetime(2026, 11, 19, 16, 0, tzinfo=KST))["phase"] == "open"
    assert calendar.state("KR", datetime(2026, 1, 2, 9, 30, tzinfo=KST))["phase"] == "pre"   # 새해 첫 거래일
    after_thanksgiving = datetime(2026, 11, 28, 3, 30, tzinfo=KST)  # 27th 13:30 New York
    s = calendar.state("US", after_thanksgiving)
    assert s["phase"] == "post" and s["close"] == "13:00"
    assert calendar.state("KR", datetime(2026, 10, 9, 10, 0, tzinfo=KST))["open"] is None


def test_constitution_day_and_the_neighbour_years_are_listed():
    assert not calendar.is_session("KR", date(2026, 7, 17))         # 제헌절, a holiday again from 2026
    assert not calendar.is_session("KR", date(2027, 7, 19))
    assert not calendar.is_session("US", date(2025, 1, 9))          # National Day of Mourning
    assert calendar.covered(2025) and calendar.covered(2027)


def test_the_table_matches_the_new_quant_calendar():
    """src/ may not import autotrade/ (D14), so the values are copied; this
    is the one place that reads both and says when they drift apart."""
    from autotrade.market import calendar as at

    assert calendar.HOLIDAYS["KR"] == at.KRX_HOLIDAYS
    assert calendar.HOLIDAYS["US"] == at.NYSE_HOLIDAYS
    for mine, theirs in ((calendar.SPECIAL["KR"], at.KRX_SPECIAL),
                         (calendar.SPECIAL["US"], at.NYSE_SPECIAL)):
        assert {d: v[:2] for d, v in mine.items()} == {d: (h.open, h.close) for d, h in theirs.items()}
    assert (calendar.hours("KR", date(2026, 10, 14)), calendar.hours("US", date(2026, 10, 14))) == (
        (at.KRX.regular_open, at.KRX.regular_close), (at.NYSE.regular_open, at.NYSE.regular_close))
    assert calendar.YEARS == set(range(at.COVERAGE_START.year, at.COVERAGE_END.year + 1))
