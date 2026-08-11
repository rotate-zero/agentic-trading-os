"""
Tests for the AFTER_HOURS session addition and session_bounds() — the
anchor candle_aggregator.py buckets off of. No DB involved; MarketClock is
pure wall-clock logic.
"""
from __future__ import annotations

from datetime import datetime
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
