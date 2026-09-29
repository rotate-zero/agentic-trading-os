"""The shared simulated EOD window has no database or live clock dependency."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.backtest_runner.fill_simulator import regular_session_close_utc
from app.core.config import Settings
from app.core.market_clock import MarketClock
from app.core.session_window import UnsupportedEodCalendarError, eod_session_window

_ET = ZoneInfo("America/New_York")
_CLOCK = MarketClock()


def _opened(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 10, 0, tzinfo=_ET)


@pytest.mark.parametrize(
    ("day", "close_hour_utc", "flatten_hour_utc", "flatten_minute_utc"),
    [
        (date(2026, 1, 6), 21, 20, 59),  # standard time
        (date(2026, 3, 9), 20, 19, 59),  # after spring DST shift
        (date(2026, 8, 11), 20, 19, 59),
        (date(2026, 11, 2), 21, 20, 59),  # after fall DST shift
        (date(2026, 11, 27), 18, 17, 59),  # 13:00 ET half-day
        (date(2026, 12, 24), 18, 17, 59),
    ],
)
def test_regular_half_day_and_dst_windows(day, close_hour_utc, flatten_hour_utc, flatten_minute_utc):
    window = eod_session_window(_CLOCK, _opened(day))
    assert window is not None
    assert window.close_at == datetime(day.year, day.month, day.day, close_hour_utc, tzinfo=timezone.utc)
    assert window.flatten_at == datetime(
        day.year, day.month, day.day, flatten_hour_utc, flatten_minute_utc, tzinfo=timezone.utc
    )


def test_exact_inclusive_exclusive_boundaries_and_aware_now():
    window = eod_session_window(_CLOCK, _opened(date(2026, 8, 11)))
    assert window is not None
    microsecond = timedelta(microseconds=1)
    assert not window.contains(window.flatten_at - microsecond)
    assert window.contains(window.flatten_at)
    assert window.contains(window.close_at - microsecond)
    assert not window.contains(window.close_at)
    assert not window.contains(window.close_at + microsecond)
    assert window.contains(window.flatten_at.astimezone(_ET))
    with pytest.raises(ValueError, match="timezone-aware"):
        window.contains(datetime(2026, 8, 11, 15, 59))


@pytest.mark.parametrize("day", [date(2026, 1, 1), date(2026, 11, 26), date(2026, 8, 15)])
def test_covered_holiday_or_weekend_has_no_window(day):
    assert eod_session_window(_CLOCK, _opened(day)) is None


@pytest.mark.parametrize(
    "day", [date(2025, 12, 24), date(2025, 12, 31), date(2029, 1, 2), date(2029, 3, 6), date(2030, 6, 4)]
)
def test_unsupported_year_fails_closed_even_when_weekday(day):
    assert not _CLOCK.has_calendar_for_year(day.year)
    with pytest.raises(UnsupportedEodCalendarError, match=str(day.year)):
        eod_session_window(_CLOCK, _opened(day))


@pytest.mark.parametrize("lead", [0, 901, -1, 1.5, True, "60"])
def test_invalid_lead_rejected(lead):
    with pytest.raises(ValueError, match="lead_seconds"):
        eod_session_window(_CLOCK, _opened(date(2026, 8, 11)), lead)


def test_extreme_valid_leads_and_naive_opening():
    opened = _opened(date(2026, 8, 11))
    for lead in (1, 900):
        window = eod_session_window(_CLOCK, opened, lead)
        assert window is not None
        assert window.close_at - window.flatten_at == timedelta(seconds=lead)
    with pytest.raises(ValueError, match="timezone-aware"):
        eod_session_window(_CLOCK, datetime(2026, 8, 11, 10, 0))


def test_opening_in_utc_uses_et_entry_day():
    # 00:30 UTC on Aug 12 is still the Aug 11 trading day in New York.
    window = eod_session_window(_CLOCK, datetime(2026, 8, 12, 0, 30, tzinfo=timezone.utc))
    assert window is not None
    assert window.close_at == datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)


def test_close_parity_with_backtest_for_every_supported_2026_trading_day():
    day = date(2026, 1, 1)
    last = date(2026, 12, 31)
    checked = 0
    while day <= last:
        window = eod_session_window(_CLOCK, _opened(day))
        if day.weekday() < 5 and not _CLOCK.is_holiday(day):
            assert window is not None
            assert window.close_at == regular_session_close_utc(_CLOCK, day)
            checked += 1
        else:
            assert window is None
        day += timedelta(days=1)
    assert checked == 251


def test_setting_default_range_and_environment_style_parsing():
    assert Settings(_env_file=None).execution_eod_flatten_lead_seconds == 60
    for lead in (1, 900, "60"):
        assert Settings(_env_file=None, execution_eod_flatten_lead_seconds=lead).execution_eod_flatten_lead_seconds == int(lead)
    for lead in (0, 901, -1, True, 1.5, "60.0"):
        with pytest.raises(ValueError, match="execution_eod_flatten_lead_seconds"):
            Settings(_env_file=None, execution_eod_flatten_lead_seconds=lead)


# --- 2027-2028 coverage (market-clock-2027-2028-coverage) ---------------------
# Expected instants below are written out by hand from the NYSE schedule and
# the US DST rules (2027: EDT 03-14..11-07; 2028: EDT 03-12..11-05), not derived
# from the code under test.


@pytest.mark.parametrize(
    ("day", "close_utc_hour"),
    [
        (date(2027, 1, 5), 21),    # EST
        (date(2027, 3, 12), 21),   # last EST day before the 03-14 spring shift
        (date(2027, 3, 15), 20),   # first weekday after it (EDT)
        (date(2027, 7, 2), 20),    # Friday before the observed holiday: NYSE lists no early close
        (date(2027, 11, 5), 20),   # last EDT weekday before the 11-07 fall shift
        (date(2027, 11, 8), 21),   # first EST weekday after it
        (date(2027, 11, 26), 18),  # early close 13:00 EST
        (date(2027, 12, 23), 21),  # day before the observed Christmas closure: regular close
        (date(2027, 12, 31), 21),  # no New Year's observance on Friday 2027-12-31
        (date(2028, 1, 3), 21),    # first 2028 trading day
        (date(2028, 3, 10), 21),   # last EST weekday before the 03-12 spring shift
        (date(2028, 3, 13), 20),   # first EDT weekday
        (date(2028, 7, 3), 17),    # early close 13:00 EDT
        (date(2028, 7, 5), 20),
        (date(2028, 11, 3), 20),   # last EDT weekday before the 11-05 fall shift
        (date(2028, 11, 6), 21),   # first EST weekday
        (date(2028, 11, 24), 18),  # early close 13:00 EST
        (date(2028, 12, 29), 21),  # last 2028 trading day
    ],
)
def test_2027_2028_regular_early_and_dst_close_instants(day, close_utc_hour):
    window = eod_session_window(_CLOCK, _opened(day))
    assert window is not None
    assert window.close_at == datetime(day.year, day.month, day.day, close_utc_hour, tzinfo=timezone.utc)
    assert window.flatten_at == window.close_at - timedelta(seconds=60)
    assert window.flatten_at.tzinfo is timezone.utc and window.close_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "day",
    [
        date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
        date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
        date(2028, 1, 17), date(2028, 2, 21), date(2028, 4, 14), date(2028, 5, 29), date(2028, 6, 19),
        date(2028, 7, 4), date(2028, 9, 4), date(2028, 11, 23), date(2028, 12, 25),
        date(2028, 1, 1), date(2027, 12, 25), date(2028, 12, 31),  # weekends
    ],
)
def test_2027_2028_covered_holidays_and_weekends_have_no_window(day):
    assert _CLOCK.has_calendar_for_year(day.year)
    assert eod_session_window(_CLOCK, _opened(day)) is None


def test_2028_has_no_new_years_holiday_and_the_boundary_days_trade():
    assert not _CLOCK.is_holiday(date(2028, 1, 1))  # Saturday, and not observed on a weekday
    assert not _CLOCK.is_holiday(date(2027, 12, 31))
    assert eod_session_window(_CLOCK, _opened(date(2028, 1, 1))) is None  # None only because Saturday
    assert eod_session_window(_CLOCK, _opened(date(2027, 12, 31))) is not None


def test_2028_july_3_early_close_only_that_day():
    early = eod_session_window(_CLOCK, _opened(date(2028, 7, 3)))
    assert early is not None
    assert early.close_at.astimezone(_ET).time().isoformat() == "13:00:00"
    assert early.contains(datetime(2028, 7, 3, 16, 59, 30, tzinfo=timezone.utc))
    assert not early.contains(datetime(2028, 7, 3, 17, 0, tzinfo=timezone.utc))
    assert not _CLOCK.is_half_day(date(2028, 7, 5))
    assert not _CLOCK.is_half_day(date(2028, 7, 2))


def test_2027_2028_exact_boundaries_on_early_close_days():
    microsecond = timedelta(microseconds=1)
    for day, close in ((date(2027, 11, 26), 18), (date(2028, 11, 24), 18), (date(2028, 7, 3), 17)):
        window = eod_session_window(_CLOCK, _opened(day))
        assert window is not None
        assert window.close_at == datetime(day.year, day.month, day.day, close, tzinfo=timezone.utc)
        assert not window.contains(window.flatten_at - microsecond)
        assert window.contains(window.flatten_at)
        assert window.contains(window.close_at - microsecond)
        assert not window.contains(window.close_at)


@pytest.mark.parametrize(
    ("opened_utc", "expected"),
    [
        # 2027-12-31 22:00 ET is already 2028-01-01 03:00Z, but the ET entry day is 2027-12-31.
        (datetime(2028, 1, 1, 3, 0, tzinfo=timezone.utc), datetime(2027, 12, 31, 21, 0, tzinfo=timezone.utc)),
        # 2026-12-31 22:00 ET -> 2027-01-01 03:00Z: entry day is 2026-12-31 (Thursday).
        (datetime(2027, 1, 1, 3, 0, tzinfo=timezone.utc), datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)),
        # 2027-01-01 15:00Z is the New Year's holiday in ET -> covered closed day.
        (datetime(2027, 1, 1, 15, 0, tzinfo=timezone.utc), None),
        # 2028-12-31 23:30 ET -> 2029-01-01 04:30Z: entry day 2028-12-31 is a covered Sunday, not unsupported.
        (datetime(2029, 1, 1, 4, 30, tzinfo=timezone.utc), None),
    ],
)
def test_year_boundaries_use_the_et_entry_day(opened_utc, expected):
    window = eod_session_window(_CLOCK, opened_utc)
    if expected is None:
        assert window is None
    else:
        assert window is not None and window.close_at == expected


def test_year_boundary_into_an_unsupported_year_fails_closed():
    # 2025-12-31 22:00 ET is 2026-01-01 03:00Z: the ET entry day (2025) decides, not the UTC year.
    with pytest.raises(UnsupportedEodCalendarError, match="2025"):
        eod_session_window(_CLOCK, datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc))
    # First weekday of 2029 is unsupported; NYSE's 2029 schedule is not verified here.
    with pytest.raises(UnsupportedEodCalendarError, match="2029"):
        eod_session_window(_CLOCK, datetime(2029, 1, 2, 15, 0, tzinfo=timezone.utc))
    assert [y for y in range(2020, 2035) if _CLOCK.has_calendar_for_year(y)] == [2026, 2027, 2028]


def test_close_parity_with_backtest_for_every_supported_trading_day_2026_to_2028():
    expected_days = {2026: 251, 2027: 251, 2028: 251}
    for year, expected in expected_days.items():
        day, last, checked = date(year, 1, 1), date(year, 12, 31), 0
        while day <= last:
            window = eod_session_window(_CLOCK, _opened(day))
            if day.weekday() < 5 and not _CLOCK.is_holiday(day):
                assert window is not None, day
                assert window.close_at == regular_session_close_utc(_CLOCK, day), day
                local_close = window.close_at.astimezone(_ET).time().isoformat()
                assert local_close == ("13:00:00" if _CLOCK.is_half_day(day) else "16:00:00"), day
                checked += 1
            else:
                assert window is None, day
            day += timedelta(days=1)
        assert checked == expected, year
