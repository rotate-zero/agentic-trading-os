"""
Tests for the AFTER_HOURS session addition and session_bounds() — the
anchor candle_aggregator.py buckets off of. No DB involved; MarketClock is
pure wall-clock logic.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
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


# --- next_session_boundary on verified calendar dates (market-clock-next-session-boundary) ---
# Contract: the next instant strictly after `ts` at which current_session() changes.

_MICRO = timedelta(microseconds=1)


def _assert_real_boundary(clock: MarketClock, ts: datetime, expected: datetime) -> datetime:
    """Returns `expected` exactly, aware, strictly after `ts`, and is a real session-state change:
    the session is constant from `ts` up to the instant before the boundary (no skipped transition)
    and differs at the boundary itself."""
    boundary = clock.next_session_boundary(ts)
    assert boundary == expected
    assert boundary.tzinfo is not None and boundary.utcoffset() is not None
    assert boundary > ts
    before, at = clock.current_session(boundary - _MICRO), clock.current_session(boundary)
    assert before != at
    assert clock.current_session(ts) == before
    return boundary


def test_boundary_on_covered_holiday_skips_to_next_trading_day_pre_market():
    clock = MarketClock()
    # Thu 2026-11-26 Thanksgiving: CLOSED all day; before the fix every call returned a same-day time.
    for hh, mm in [(0, 0), (3, 59), (4, 0), (9, 30), (12, 0), (16, 0), (19, 59), (23, 59)]:
        ts = _et(2026, 11, 26, hh, mm)
        assert clock.current_session(ts) == Session.CLOSED
        # Fri 2026-11-27 is a half-day whose pre-market still opens at 04:00.
        _assert_real_boundary(clock, ts, _et(2026, 11, 27, 4, 0))
    # Holiday followed by a weekend: Fri 2026-07-03 (observed) -> Mon 07-06.
    _assert_real_boundary(clock, _et(2026, 7, 3, 10, 0), _et(2026, 7, 6, 4, 0))
    # From the prior trading day's after-hours close the holiday is skipped too.
    _assert_real_boundary(clock, _et(2026, 11, 25, 20, 0), _et(2026, 11, 27, 4, 0))


def test_boundary_on_weekend_skips_to_monday_pre_market():
    clock = MarketClock()
    for ts in [_et(2026, 8, 15, 0, 0), _et(2026, 8, 15, 12, 0), _et(2026, 8, 16, 23, 59)]:  # Sat, Sat, Sun
        assert clock.current_session(ts) == Session.CLOSED
        _assert_real_boundary(clock, ts, _et(2026, 8, 17, 4, 0))
    # Friday after-hours close -> Monday, not Saturday.
    _assert_real_boundary(clock, _et(2026, 8, 14, 20, 0), _et(2026, 8, 17, 4, 0))


def test_boundary_normal_session_day_sequence_is_unchanged():
    clock = MarketClock()
    # Tue 2026-08-11: every state change, in order, then the next trading day's pre-market open.
    sequence = [(4, 0), (9, 30), (11, 30), (14, 30), (16, 0), (20, 0)]
    ts = _et(2026, 8, 11, 0, 0)
    for hh, mm in sequence:
        ts = _assert_real_boundary(clock, ts, _et(2026, 8, 11, hh, mm))
    _assert_real_boundary(clock, ts, _et(2026, 8, 12, 4, 0))
    # Strictly after: exactly at a boundary returns the following one; sub-minute instants round up.
    assert clock.next_session_boundary(_et(2026, 8, 11, 9, 30)) == _et(2026, 8, 11, 11, 30)
    assert clock.next_session_boundary(datetime(2026, 8, 11, 9, 29, 59, 999999, tzinfo=_ET)) == _et(2026, 8, 11, 9, 30)


def test_boundary_half_day_close_2026_2027_2028():
    clock = MarketClock()
    # (half-day, next trading day's pre-market open)
    cases = [
        (date(2026, 11, 27), date(2026, 11, 30)),  # Fri -> Mon
        (date(2026, 12, 24), date(2026, 12, 28)),  # Thu; Fri 12-25 Christmas, weekend -> Mon
        (date(2027, 11, 26), date(2027, 11, 29)),  # Fri -> Mon
        (date(2028, 7, 3), date(2028, 7, 5)),      # Mon; Tue 07-04 holiday -> Wed
        (date(2028, 11, 24), date(2028, 11, 27)),  # Fri -> Mon
    ]
    for half, nxt in cases:
        assert clock.is_half_day(half)
        y, m, d = half.year, half.month, half.day
        # From 11:30 the next change is the 13:00 close, not 14:30/16:00/20:00.
        _assert_real_boundary(clock, _et(y, m, d, 4, 0), _et(y, m, d, 9, 30))
        _assert_real_boundary(clock, _et(y, m, d, 9, 30), _et(y, m, d, 11, 30))
        _assert_real_boundary(clock, _et(y, m, d, 10, 0), _et(y, m, d, 11, 30))
        close = _assert_real_boundary(clock, _et(y, m, d, 11, 30), _et(y, m, d, 13, 0))
        assert clock.current_session(close - _MICRO) == Session.LUNCH
        assert clock.current_session(close) == Session.CLOSED
        assert not clock.is_market_open(close) and clock.is_market_open(close - _MICRO)
        # From the close (and anywhere after it that day) -> next trading day 04:00.
        for hh, mm in [(13, 0), (14, 30), (16, 0), (20, 0)]:
            _assert_real_boundary(clock, _et(y, m, d, hh, mm), datetime.combine(nxt, time(4, 0), tzinfo=_ET))


def test_boundary_covered_year_crossing_2027_to_2028():
    clock = MarketClock()
    # Thu 2027-12-30 closes at 20:00 -> Fri 12-31 04:00 (a trading day).
    _assert_real_boundary(clock, _et(2027, 12, 30, 20, 0), _et(2027, 12, 31, 4, 0))
    # Fri 2027-12-31 runs its normal day, then Sat/Sun and Mon 2028-01-03 (2028-01-01 is not a holiday, just a Saturday).
    ts = _et(2027, 12, 31, 14, 30)
    ts = _assert_real_boundary(clock, ts, _et(2027, 12, 31, 16, 0))
    ts = _assert_real_boundary(clock, ts, _et(2027, 12, 31, 20, 0))
    _assert_real_boundary(clock, ts, _et(2028, 1, 3, 4, 0))
    # Weekend days inside the crossing never produce a boundary of their own.
    for ts in [_et(2028, 1, 1, 4, 0), _et(2028, 1, 1, 12, 0), _et(2028, 1, 2, 9, 30)]:
        _assert_real_boundary(clock, ts, _et(2028, 1, 3, 4, 0))
    # 2026 -> 2027: Thu 2026-12-31 close -> Fri 2027-01-01 is a holiday -> Mon 2027-01-04.
    _assert_real_boundary(clock, _et(2026, 12, 31, 20, 0), _et(2027, 1, 4, 4, 0))


def test_boundary_results_are_timezone_aware_in_clock_zone_for_any_input_zone():
    clock = MarketClock()
    utc = ZoneInfo("UTC")
    # 2026-11-26 15:00Z (Thanksgiving, 10:00 ET) -> Fri 11-27 04:00 ET == 09:00Z.
    b = clock.next_session_boundary(datetime(2026, 11, 26, 15, 0, tzinfo=utc))
    assert b == datetime(2026, 11, 27, 9, 0, tzinfo=utc)
    assert b.tzinfo == _ET and b.utcoffset() == timedelta(hours=-5)
    # DST: Sat 2027-03-13 -> Mon 2027-03-15 04:00 EDT (08:00Z) after the 03-14 shift.
    b = clock.next_session_boundary(_et(2027, 3, 13, 12, 0))
    assert b == _et(2027, 3, 15, 4, 0) and b.utcoffset() == timedelta(hours=-4)
    assert b.astimezone(utc) == datetime(2027, 3, 15, 8, 0, tzinfo=utc)
    # Naive input is still rejected.
    try:
        clock.next_session_boundary(datetime(2026, 8, 11, 12, 0))
    except ValueError:
        pass
    else:
        raise AssertionError("naive datetime must raise ValueError")


def test_boundary_matches_minute_by_minute_session_changes_across_covered_windows():
    """Oracle: walk each window one minute at a time, record every instant current_session() changes,
    and require next_session_boundary() from probes in the window to return the first change after it."""
    clock = MarketClock()
    windows = [
        (_et(2027, 12, 20, 0, 0), _et(2028, 1, 6, 0, 0)),   # covered year crossing, Christmas observed
        (_et(2026, 11, 23, 0, 0), _et(2026, 12, 1, 0, 0)),  # Thanksgiving + 2026 half-day
        (_et(2026, 12, 21, 0, 0), _et(2026, 12, 30, 0, 0)), # 2026-12-24 half-day, Christmas
        (_et(2027, 11, 22, 0, 0), _et(2027, 11, 30, 0, 0)), # 2027 half-day
        (_et(2028, 6, 30, 0, 0), _et(2028, 7, 7, 0, 0)),    # 2028-07-03 half-day, 07-04 holiday
        (_et(2028, 11, 20, 0, 0), _et(2028, 11, 28, 0, 0)), # 2028 half-day
    ]
    for start, end in windows:
        minutes, ts = [], start
        while ts <= end + timedelta(days=5):
            minutes.append(ts)
            ts += timedelta(minutes=1)
        changes = [m for prev, m in zip(minutes, minutes[1:]) if clock.current_session(prev) != clock.current_session(m)]
        idx = 0
        for probe in minutes:
            if probe > end:
                break
            if probe.minute % 15 or probe.second:  # probe every quarter hour, includes every boundary minute
                continue
            while changes[idx] <= probe:
                idx += 1
            assert clock.next_session_boundary(probe) == changes[idx], probe


def test_unverified_year_2029_keeps_existing_behavior_and_is_not_claimed_covered():
    """2029 is NOT verified calendar data. `has_calendar_for_year(2029)` stays False, and the session
    methods keep treating an unverified year as having no holidays/early closes (never raising), so
    next_session_boundary() there skips weekends only. Holiday correctness is a verified-year property."""
    clock = MarketClock()
    assert clock.has_calendar_for_year(2028) is True
    assert clock.has_calendar_for_year(2029) is False
    assert clock.is_holiday(date(2029, 1, 1)) is False and clock.is_half_day(date(2029, 11, 23)) is False
    # Weekend skip still works (Fri 2029-03-02 close -> Mon 03-05).
    assert clock.next_session_boundary(_et(2029, 3, 2, 20, 0)) == _et(2029, 3, 5, 4, 0)
    assert clock.next_session_boundary(_et(2029, 3, 3, 12, 0)) == _et(2029, 3, 5, 4, 0)
    # An ordinary 2029 weekday keeps the normal sequence and never raises.
    assert clock.next_session_boundary(_et(2029, 3, 6, 9, 30)) == _et(2029, 3, 6, 11, 30)
