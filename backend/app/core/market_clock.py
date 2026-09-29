"""
Market Clock — the single source of truth for anything time/session-related.
See docs/architecture/system-design.md §4.3. Every other module asks the
Market Clock rather than computing session/holiday/DST logic itself.

Scope note (honesty, not silently glossed over): the holiday and early-close
calendar below is a hardcoded set, verified against the official NYSE
"Holidays & Trading Hours" page (https://www.nyse.com/trade/hours-calendars)
for 2026, 2027 and 2028 ONLY. `has_calendar_for_year()` reports exactly those
years; outside them `is_holiday()`/`is_half_day()` simply answer False (they
do not raise), so anything that must fail closed on an unverified year (the
EOD window, `core.session_window`) has to ask `has_calendar_for_year()`
itself. Session boundaries (pre_market/open/lunch/power_hour) are a
reasonable first approximation, not exchange-verified constants. Extending
coverage to another year is a data change (a new holiday set, a new early-close
set and one entry in `_VERIFIED_CALENDAR_YEARS`), not an interface change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class Session(StrEnum):
    PRE_MARKET = "pre_market"
    OPEN = "open"
    LUNCH = "lunch"
    POWER_HOUR = "power_hour"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


@dataclass(frozen=True)
class SessionWindow:
    session: Session
    start: time
    end: time  # exclusive


# Regular-session boundaries, Eastern time. See scope note above re: accuracy.
# After-hours end (20:00) is the common convention across providers, but —
# same scope note as the rest of this list — hasn't been verified against
# what Finnhub/IBKR actually deliver on this account; the recorder simply
# won't have rows past whatever the live feed actually stops sending,
# regardless of where this boundary is drawn.
_SESSION_WINDOWS: list[SessionWindow] = [
    SessionWindow(Session.PRE_MARKET, time(4, 0), time(9, 30)),
    SessionWindow(Session.OPEN, time(9, 30), time(11, 30)),
    SessionWindow(Session.LUNCH, time(11, 30), time(14, 30)),
    SessionWindow(Session.POWER_HOUR, time(14, 30), time(16, 0)),
    SessionWindow(Session.AFTER_HOURS, time(16, 0), time(20, 0)),
]

# OPEN/LUNCH/POWER_HOUR are one continuous session for anything that cares
# about session BOUNDARIES (candle aggregation, session-change detection) —
# they only exist as separate Session values for callers that care about
# the sub-label itself (e.g. a future "power hour" UI badge). See
# session_bounds() below, and confirmed-decisions.md re: this split.
_REGULAR_SESSION_LABELS = {Session.OPEN, Session.LUNCH, Session.POWER_HOUR}

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)

# NYSE full-day equity closures and 13:00 ET equity early closes, verified
# against https://www.nyse.com/trade/hours-calendars (checked 2026-09-30, which
# lists 2026, 2027 and 2028). Early closes are the page's 1:00 p.m. ET
# equity closes; the options 1:15 p.m. close and the 5:00 p.m. late-session
# closes of other venues are not modelled.
# TODO: extend with each further year NYSE publishes, and add it to
# _VERIFIED_CALENDAR_YEARS in the same change.
_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}

# Half-days (market closes at 13:00 ET).
_HALF_DAYS_2026: set[date] = {
    date(2026, 11, 27),  # day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
}

_HOLIDAYS_2027: set[date] = {
    date(2027, 1, 1),   # New Year's Day (Friday)
    date(2027, 1, 18),  # MLK Day
    date(2027, 2, 15),  # Washington's Birthday
    date(2027, 3, 26),  # Good Friday
    date(2027, 5, 31),  # Memorial Day
    date(2027, 6, 18),  # Juneteenth (observed; the 19th is a Saturday)
    date(2027, 7, 5),   # Independence Day (observed; the 4th is a Sunday)
    date(2027, 9, 6),   # Labor Day
    date(2027, 11, 25), # Thanksgiving
    date(2027, 12, 24), # Christmas Day (observed; the 25th is a Saturday)
}

_HALF_DAYS_2027: set[date] = {
    date(2027, 11, 26),  # day after Thanksgiving
    # NYSE lists no early close on Fri 2027-07-02 or Thu 2027-12-23.
}

_HOLIDAYS_2028: set[date] = {
    # No New Year's Day: Saturday 2028-01-01 is not observed on Friday 2027-12-31.
    date(2028, 1, 17),  # MLK Day
    date(2028, 2, 21),  # Washington's Birthday
    date(2028, 4, 14),  # Good Friday
    date(2028, 5, 29),  # Memorial Day
    date(2028, 6, 19),  # Juneteenth (Monday)
    date(2028, 7, 4),   # Independence Day (Tuesday)
    date(2028, 9, 4),   # Labor Day
    date(2028, 11, 23), # Thanksgiving
    date(2028, 12, 25), # Christmas Day (Monday)
}

_HALF_DAYS_2028: set[date] = {
    date(2028, 7, 3),    # day before Independence Day
    date(2028, 11, 24),  # day after Thanksgiving
}

# The years whose holiday AND early-close data above were verified. The
# single source for has_calendar_for_year(); a year is listed here only
# together with its two sets.
_VERIFIED_CALENDAR_YEARS: frozenset[int] = frozenset({2026, 2027, 2028})

_HOLIDAYS: frozenset[date] = frozenset(_HOLIDAYS_2026 | _HOLIDAYS_2027 | _HOLIDAYS_2028)
_HALF_DAYS: frozenset[date] = frozenset(_HALF_DAYS_2026 | _HALF_DAYS_2027 | _HALF_DAYS_2028)


class MarketClock:
    """See docs/architecture/system-design.md §4.3 for the full interface contract."""

    def __init__(self, tz_name: str = "America/New_York"):
        self._tz = ZoneInfo(tz_name)

    def _now(self, ts: datetime | None = None) -> datetime:
        if ts is None:
            ts = datetime.now(self._tz)
        elif ts.tzinfo is None:
            raise ValueError("MarketClock requires timezone-aware datetimes")
        else:
            ts = ts.astimezone(self._tz)
        return ts

    def is_holiday(self, d: date) -> bool:
        return d in _HOLIDAYS

    def has_calendar_for_year(self, year: int) -> bool:
        """Whether verified holiday AND early-close data cover `year`.

        True for exactly the years in `_VERIFIED_CALENDAR_YEARS` (2026-2028).
        This is informational: the session methods keep their behavior
        outside the covered years (an unverified year has no holidays or
        early closes, it does not raise), so a caller that must not guess
        an unverified session asks this first.
        """
        return year in _VERIFIED_CALENDAR_YEARS

    def is_half_day(self, d: date) -> bool:
        return d in _HALF_DAYS

    def is_market_open(self, ts: datetime | None = None) -> bool:
        now = self._now(ts)
        if now.weekday() >= 5:  # Sat/Sun
            return False
        if self.is_holiday(now.date()):
            return False
        close = time(13, 0) if self.is_half_day(now.date()) else _MARKET_CLOSE
        return _MARKET_OPEN <= now.time() < close

    def is_regular_session(self, ts: datetime | None = None) -> bool:
        """
        True only for OPEN/LUNCH/POWER_HOUR — the continuous regular-hours
        window session_bounds() already treats as one domain. False for
        PRE_MARKET, AFTER_HOURS, and CLOSED. Added for Feature Engine's
        VWAP (confirmed decision #53), which — matching
        frontend/src/indicators/vwap.ts's own convention — only
        accumulates during regular hours; pre-market/after-hours volume
        never contributes to it, unlike 5m/15m/1h aggregation (decision
        #51), which happily buckets pre-market and after-hours candles
        too, just in their own separate buckets.
        """
        return self.current_session(ts) in _REGULAR_SESSION_LABELS

    def current_session(self, ts: datetime | None = None) -> Session:
        now = self._now(ts)
        if now.weekday() >= 5 or self.is_holiday(now.date()):
            return Session.CLOSED

        if self.is_half_day(now.date()) and now.time() >= time(13, 0):
            return Session.CLOSED

        for window in _SESSION_WINDOWS:
            if window.start <= now.time() < window.end:
                return window.session
        return Session.CLOSED

    def session_bounds(self, ts: datetime | None = None) -> tuple[datetime, datetime] | None:
        """
        (start, end) of the session containing `ts`, or None if `ts` falls
        in CLOSED (overnight, weekend, holiday, or past an early half-day
        close). This is the anchor candle aggregation buckets off of —
        see candle_aggregator.py — so that a bucket never straddles a
        session boundary (e.g. a bar spanning 15:45-16:15 would silently
        blend regular-session trading with after-hours trading into one
        misleading bar).

        OPEN/LUNCH/POWER_HOUR all return the SAME bounds (market open to
        market close) rather than each other's sub-window — regular
        session is treated as one continuous aggregation domain, not reset
        at the lunch/power-hour boundaries. Only PRE_MARKET and
        AFTER_HOURS get their own distinct bounds.
        """
        now = self._now(ts)
        session = self.current_session(now)
        if session == Session.CLOSED:
            return None

        if session in _REGULAR_SESSION_LABELS:
            close_time = time(13, 0) if self.is_half_day(now.date()) else _MARKET_CLOSE
            start = now.replace(hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute, second=0, microsecond=0)
            end = now.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
            return start, end

        window = next(w for w in _SESSION_WINDOWS if w.session == session)
        start = now.replace(hour=window.start.hour, minute=window.start.minute, second=0, microsecond=0)
        end = now.replace(hour=window.end.hour, minute=window.end.minute, second=0, microsecond=0)
        return start, end

    def minutes_since_open(self, ts: datetime | None = None) -> int:
        now = self._now(ts)
        if not self.is_market_open(now):
            return 0
        open_dt = now.replace(hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute, second=0, microsecond=0)
        return max(0, int((now - open_dt).total_seconds() // 60))

    def trading_day(self, ts: datetime | None = None) -> date:
        """
        The ET calendar date `ts` falls in — the single definition of "day"
        for anything that resets daily (currently: Level Interaction Engine's
        touch counters — trading-intelligence-architecture.md, confirmed
        decision #46). Deliberately just `_now(ts).date()`: the same
        ET-conversion `session_bounds()` already does internally, pulled out
        so a consumer that only needs "which day is this" doesn't need to
        reimplement the timezone conversion itself.
        """
        return self._now(ts).date()

    def next_session_boundary(self, ts: datetime | None = None) -> datetime:
        now = self._now(ts)
        # Every window's start AND end, deduped — subsumes the old
        # "just append market close" approach, which predated AFTER_HOURS
        # existing at all and so had no way to represent 20:00 as a
        # boundary (only 16:00, via _MARKET_CLOSE, was ever a candidate).
        boundary_times = sorted({w.start for w in _SESSION_WINDOWS} | {w.end for w in _SESSION_WINDOWS})
        candidates = [
            now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0) for t in boundary_times
        ]

        for boundary in sorted(candidates):
            if boundary > now:
                return boundary

        # Nothing left today — walk forward to the next non-holiday weekday's pre-market open.
        next_day = now.date() + timedelta(days=1)
        while next_day.weekday() >= 5 or self.is_holiday(next_day):
            next_day += timedelta(days=1)
        first_window = _SESSION_WINDOWS[0]
        return datetime.combine(next_day, first_window.start, tzinfo=self._tz)


_market_clock: MarketClock | None = None


def get_market_clock() -> MarketClock:
    global _market_clock
    if _market_clock is None:
        from app.core.config import get_settings

        _market_clock = MarketClock(get_settings().market_timezone)
    return _market_clock
