"""CalendarProvider tests — pure logic via MarketClock plus a small
hardcoded FOMC set, no DB (same posture as test_market_clock.py).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.context_engine.providers.calendar import CalendarProvider

_ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


async def test_fed_day_true_on_fomc_date():
    provider = CalendarProvider()
    result = await provider.evaluate(_et(2026, 9, 16, 10, 0))
    assert result["fed_day"] is True


async def test_fed_day_false_on_non_fomc_date():
    provider = CalendarProvider()
    # 2026-08-11 is an ordinary Tuesday, not an FOMC date.
    result = await provider.evaluate(_et(2026, 8, 11, 10, 0))
    assert result["fed_day"] is False


async def test_session_and_open_reflect_market_clock():
    provider = CalendarProvider()
    result = await provider.evaluate(_et(2026, 8, 11, 10, 0))
    assert result["session"] == "open"
    assert result["is_market_open"] is True
    assert result["is_half_day"] is False
    assert result["trading_day"] == "2026-08-11"


async def test_closed_outside_session_hours():
    provider = CalendarProvider()
    result = await provider.evaluate(_et(2026, 8, 11, 21, 0))
    assert result["session"] == "closed"
    assert result["is_market_open"] is False


async def test_half_day_flag_reflects_market_clock():
    provider = CalendarProvider()
    # Christmas Eve 2026 is a MarketClock half-day.
    result = await provider.evaluate(_et(2026, 12, 24, 10, 0))
    assert result["is_half_day"] is True


async def test_minutes_since_open_present_during_session():
    provider = CalendarProvider()
    result = await provider.evaluate(_et(2026, 8, 11, 10, 15))
    assert result["minutes_since_open"] == 45
