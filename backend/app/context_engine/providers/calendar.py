"""
CalendarProvider — session timing, Fed days, and holidays
(trading-intelligence-architecture.md §5, v1 provider list). Wraps
MarketClock for everything MarketClock already knows; adds Fed-day
awareness, which MarketClock doesn't have.

Fed-day set lives here, not on MarketClock (decision #92): it's Context's
own question ("what kind of day is it"), not a session-mechanics concern
any of MarketClock's other callers (candle aggregation, VWAP) have any
use for. Same "small hardcoded set, real calendar source later" scope
note MarketClock's own `_HOLIDAYS_2026` already carries — swap for a
real, multi-year FOMC calendar source before Phase 5+ trades on it, a
data problem, not an interface change, same as that note promises.
"""
from __future__ import annotations

from datetime import date, datetime

from app.context_engine.provider import ContextProvider
from app.core.market_clock import MarketClock, get_market_clock

# 2026 FOMC decision dates (the announcement day, not the two-day meeting
# start). TODO(Phase 5+): replace with a real, multi-year FOMC calendar
# source — same scope note as MarketClock's _HOLIDAYS_2026.
_FOMC_DATES_2026: set[date] = {
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
}


class CalendarProvider(ContextProvider):
    name = "calendar"

    def __init__(self, clock: MarketClock | None = None) -> None:
        self._clock = clock or get_market_clock()

    async def evaluate(self, ts: datetime | None = None) -> dict:
        """`ts` is test-only — ContextEngine always calls this with no
        arguments in production. Same optional-ts convention MarketClock
        itself uses on every method called below, threaded through here
        so this provider can be tested deterministically the same way
        test_market_clock.py already tests MarketClock directly."""
        today = self._clock.trading_day(ts)
        session = self._clock.current_session(ts)
        return {
            "session": session.value,
            "is_market_open": self._clock.is_market_open(ts),
            "is_half_day": self._clock.is_half_day(today),
            "minutes_since_open": self._clock.minutes_since_open(ts),
            "fed_day": today in _FOMC_DATES_2026,
            "trading_day": today.isoformat(),
        }
