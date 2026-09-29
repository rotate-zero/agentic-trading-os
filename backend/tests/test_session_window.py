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


@pytest.mark.parametrize("day", [date(2025, 12, 24), date(2027, 1, 5)])
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
