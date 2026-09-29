"""
Tests for the AFTER_HOURS session addition and session_bounds() — the
anchor candle_aggregator.py buckets off of. No DB involved; MarketClock is
pure wall-clock logic.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.market_clock import MarketClock, Session

_ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


def test_after_hours_session_recognized():
    clock = MarketClock()
    # 2026-08-11 is a Tuesday, not a holiday/half-day.
    assert clock.current_session(_et(2026, 8, 11, 17, 0)) == Session.AFTER_HOURS


def test_after_hours_ends_at_20_00():
    clock = MarketClock()
    assert clock.current_session(_et(2026, 8, 11, 19, 59)) == Session.AFTER_HOURS
    assert clock.current_session(_et(2026, 8, 11, 20, 0)) == Session.CLOSED


def test_is_market_open_unaffected_by_after_hours():
    """is_market_open() means the REGULAR session specifically — adding
    AFTER_HOURS as a real Session value must not make this start returning
    True for it."""
    clock = MarketClock()
    assert clock.is_market_open(_et(2026, 8, 11, 17, 0)) is False


def test_session_bounds_pre_market():
    clock = MarketClock()
    start, end = clock.session_bounds(_et(2026, 8, 11, 6, 15))
    assert (start.hour, start.minute) == (4, 0)
    assert (end.hour, end.minute) == (9, 30)


def test_session_bounds_after_hours():
    clock = MarketClock()
    start, end = clock.session_bounds(_et(2026, 8, 11, 18, 0))
    assert (start.hour, start.minute) == (16, 0)
    assert (end.hour, end.minute) == (20, 0)


def test_session_bounds_regular_session_ignores_lunch_and_power_hour_sub_labels():
    """The actual point of this change: OPEN, LUNCH, and POWER_HOUR must
    all resolve to the SAME (09:30, 16:00) bounds — regular session is one
    continuous aggregation domain, not reset at 11:30/14:30."""
    clock = MarketClock()
    open_bounds = clock.session_bounds(_et(2026, 8, 11, 9, 45))  # OPEN
    lunch_bounds = clock.session_bounds(_et(2026, 8, 11, 12, 0))  # LUNCH
    power_hour_bounds = clock.session_bounds(_et(2026, 8, 11, 15, 0))  # POWER_HOUR

    assert clock.current_session(_et(2026, 8, 11, 9, 45)) == Session.OPEN
    assert clock.current_session(_et(2026, 8, 11, 12, 0)) == Session.LUNCH
    assert clock.current_session(_et(2026, 8, 11, 15, 0)) == Session.POWER_HOUR
    assert open_bounds == lunch_bounds == power_hour_bounds == (_et(2026, 8, 11, 9, 30), _et(2026, 8, 11, 16, 0))


def test_session_bounds_regular_session_respects_half_day_close():
    clock = MarketClock()
    # 2026-11-27 is a configured half-day (13:00 ET close).
    start, end = clock.session_bounds(_et(2026, 11, 27, 10, 0))
    assert (end.hour, end.minute) == (13, 0)


def test_session_bounds_none_when_closed():
    clock = MarketClock()
    assert clock.session_bounds(_et(2026, 8, 11, 2, 0)) is None  # overnight
    assert clock.session_bounds(_et(2026, 8, 15, 12, 0)) is None  # Saturday


def test_next_session_boundary_reaches_after_hours_close():
    """Previously impossible to reach: the old candidate list only ever
    contained 16:00 as a closing boundary. 20:00 must now be reachable."""
    clock = MarketClock()
    boundary = clock.next_session_boundary(_et(2026, 8, 11, 19, 0))
    assert (boundary.hour, boundary.minute) == (20, 0)


# --- verified NYSE calendar 2026-2028 (market-clock-2027-2028-coverage) --------
# Independent copy of https://www.nyse.com/trade/hours-calendars (checked
# 2026-09-30). The holiday/early-close tables in market_clock.py must equal it.

_NYSE_HOLIDAYS = {
    2026: [(1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3), (9, 7), (11, 26), (12, 25)],
    2027: [(1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18), (7, 5), (9, 6), (11, 25), (12, 24)],
    2028: [(1, 17), (2, 21), (4, 14), (5, 29), (6, 19), (7, 4), (9, 4), (11, 23), (12, 25)],
}
_NYSE_EARLY_CLOSES = {
    2026: [(11, 27), (12, 24)],
    2027: [(11, 26)],
    2028: [(7, 3), (11, 24)],
}


def _dates(table):
    return {y: {date(y, m, d) for m, d in md} for y, md in table.items()}


def test_calendar_tables_equal_the_verified_official_schedule_for_every_day():
    holidays, early = _dates(_NYSE_HOLIDAYS), _dates(_NYSE_EARLY_CLOSES)
    clock = MarketClock()
    for year in holidays:
        day = date(year, 1, 1)
        while day.year == year:
            assert clock.is_holiday(day) == (day in holidays[year]), day
            assert clock.is_half_day(day) == (day in early[year]), day
            day += timedelta(days=1)


def test_calendar_data_is_internally_consistent():
    holidays, early = _dates(_NYSE_HOLIDAYS), _dates(_NYSE_EARLY_CLOSES)
    for year in holidays:
        assert all(d.weekday() < 5 for d in holidays[year]), year  # observed dates are weekdays
        assert all(d.weekday() < 5 for d in early[year]), year
        assert not (holidays[year] & early[year]), year


def test_has_calendar_for_year_reports_exactly_the_verified_years():
    clock = MarketClock()
    assert {y for y in range(2000, 2100) if clock.has_calendar_for_year(y)} == {2026, 2027, 2028}
    assert not clock.has_calendar_for_year(2025)
    assert not clock.has_calendar_for_year(2029)


def test_unverified_years_have_no_holiday_or_early_close_data_and_do_not_raise():
    clock = MarketClock()
    assert not clock.is_holiday(date(2029, 1, 1)) and not clock.is_half_day(date(2025, 11, 28))
    assert clock.current_session(_et(2029, 1, 3, 10, 0)) == Session.OPEN  # weekday logic unchanged


def test_2027_holiday_and_early_close_session_membership():
    clock = MarketClock()
    for month, day in ((1, 1), (3, 26), (6, 18), (7, 5), (12, 24)):
        assert clock.current_session(_et(2027, month, day, 10, 0)) == Session.CLOSED
        assert not clock.is_market_open(_et(2027, month, day, 10, 0))
        assert clock.session_bounds(_et(2027, month, day, 10, 0)) is None
    # Fri 2027-07-02 and Thu 2027-12-23 are ordinary sessions.
    assert clock.current_session(_et(2027, 7, 2, 15, 30)) == Session.POWER_HOUR
    assert clock.current_session(_et(2027, 12, 23, 15, 30)) == Session.POWER_HOUR
    # Fri 2027-11-26: early close 13:00 ET.
    assert clock.is_market_open(_et(2027, 11, 26, 12, 59))
    assert clock.current_session(_et(2027, 11, 26, 12, 59)) == Session.LUNCH
    assert clock.is_regular_session(_et(2027, 11, 26, 12, 59))
    assert not clock.is_market_open(_et(2027, 11, 26, 13, 0))
    assert clock.current_session(_et(2027, 11, 26, 13, 0)) == Session.CLOSED
    assert not clock.is_regular_session(_et(2027, 11, 26, 14, 0))
    assert clock.session_bounds(_et(2027, 11, 26, 10, 0)) == (_et(2027, 11, 26, 9, 30), _et(2027, 11, 26, 13, 0))
    assert clock.session_bounds(_et(2027, 11, 26, 13, 0)) is None


def test_2028_july_3_early_close_and_no_new_years_holiday():
    clock = MarketClock()
    assert clock.is_market_open(_et(2028, 7, 3, 12, 59))
    assert not clock.is_market_open(_et(2028, 7, 3, 13, 0))
    assert clock.current_session(_et(2028, 7, 3, 13, 0)) == Session.CLOSED
    assert clock.session_bounds(_et(2028, 7, 3, 11, 0)) == (_et(2028, 7, 3, 9, 30), _et(2028, 7, 3, 13, 0))
    assert clock.current_session(_et(2028, 7, 4, 10, 0)) == Session.CLOSED  # Independence Day
    assert clock.current_session(_et(2028, 7, 5, 15, 30)) == Session.POWER_HOUR
    # Dec 31 2027 (Fri) trades; Sat 2028-01-01 is closed only as a weekend.
    assert clock.current_session(_et(2027, 12, 31, 10, 0)) == Session.OPEN
    assert clock.current_session(_et(2028, 1, 1, 10, 0)) == Session.CLOSED
    assert clock.current_session(_et(2028, 1, 3, 10, 0)) == Session.OPEN
    assert clock.current_session(_et(2028, 11, 24, 13, 0)) == Session.CLOSED
    assert clock.session_bounds(_et(2028, 11, 24, 10, 0))[1] == _et(2028, 11, 24, 13, 0)


def test_dst_session_membership_from_utc_instants():
    clock = MarketClock()
    utc = ZoneInfo("UTC")
    # 13:30Z is 08:30 EST before the 2027-03-14 shift, 09:30 EDT after it.
    assert clock.current_session(datetime(2027, 3, 12, 13, 30, tzinfo=utc)) == Session.PRE_MARKET
    assert clock.current_session(datetime(2027, 3, 15, 13, 30, tzinfo=utc)) == Session.OPEN
    # 14:30Z is 10:30 EDT before the 2028-11-05 shift, 09:30 EST after it.
    assert clock.current_session(datetime(2028, 11, 3, 14, 30, tzinfo=utc)) == Session.OPEN
    assert clock.current_session(datetime(2028, 11, 3, 13, 29, tzinfo=utc)) == Session.PRE_MARKET
    assert clock.current_session(datetime(2028, 11, 6, 14, 29, tzinfo=utc)) == Session.PRE_MARKET
    assert clock.current_session(datetime(2028, 11, 6, 14, 30, tzinfo=utc)) == Session.OPEN
    assert clock.trading_day(datetime(2028, 1, 1, 4, 0, tzinfo=utc)) == date(2027, 12, 31)


def test_next_session_boundary_skips_2027_2028_holidays_and_year_end():
    clock = MarketClock()
    # After-hours close Fri 2027-07-02 -> Sat/Sun, then Mon 07-05 (observed holiday) -> Tue 07-06 04:00.
    assert clock.next_session_boundary(_et(2027, 7, 2, 20, 0)) == _et(2027, 7, 6, 4, 0)
    # Thu 2027-12-23 close -> Fri 12-24 (observed Christmas), weekend -> Mon 12-27.
    assert clock.next_session_boundary(_et(2027, 12, 23, 20, 0)) == _et(2027, 12, 27, 4, 0)
    # Fri 2027-12-31 close -> Sat 2028-01-01 is not a holiday, just a weekend -> Mon 2028-01-03.
    assert clock.next_session_boundary(_et(2027, 12, 31, 20, 0)) == _et(2028, 1, 3, 4, 0)
    # Fri 2028-01-14 close -> Mon 01-17 (MLK) -> Tue 01-18.
    assert clock.next_session_boundary(_et(2028, 1, 14, 20, 0)) == _et(2028, 1, 18, 4, 0)
