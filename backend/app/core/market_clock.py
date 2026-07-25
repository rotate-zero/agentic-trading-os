"""
Market Clock — the single source of truth for anything time/session-related.
See docs/architecture/system-design.md §4.3. Every other module asks the
Market Clock rather than computing session/holiday/DST logic itself.

Scope note (Phase 2 honesty, not silently glossed over): the holiday
calendar below is a small hardcoded set for 2026 only, and session
boundaries (pre_market/open/lunch/power_hour) are a reasonable first
approximation, not exchange-verified constants. Both are fine for
scaffolding the interface everything else depends on. Before this touches
real trading decisions (Phase 5+), swap `_HOLIDAYS_2026` for a real,
multi-year exchange calendar source — that's a data problem, not an
interface change, so nothing downstream needs to change when it happens.
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
    CLOSED = "closed"


@dataclass(frozen=True)
class SessionWindow:
    session: Session
    start: time
    end: time  # exclusive


# Regular-session boundaries, Eastern time. See scope note above re: accuracy.
_SESSION_WINDOWS: list[SessionWindow] = [
    SessionWindow(Session.PRE_MARKET, time(4, 0), time(9, 30)),
    SessionWindow(Session.OPEN, time(9, 30), time(11, 30)),
    SessionWindow(Session.LUNCH, time(11, 30), time(14, 30)),
    SessionWindow(Session.POWER_HOUR, time(14, 30), time(16, 0)),
]

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)

# 2026 NYSE holidays (full-day closures only; half-days handled separately).
# TODO(Phase 3+): replace with a real, multi-year exchange calendar.
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
        return d in _HOLIDAYS_2026

    def is_half_day(self, d: date) -> bool:
        return d in _HALF_DAYS_2026

    def is_market_open(self, ts: datetime | None = None) -> bool:
        now = self._now(ts)
        if now.weekday() >= 5:  # Sat/Sun
            return False
        if self.is_holiday(now.date()):
            return False
        close = time(13, 0) if self.is_half_day(now.date()) else _MARKET_CLOSE
        return _MARKET_OPEN <= now.time() < close

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

    def minutes_since_open(self, ts: datetime | None = None) -> int:
        now = self._now(ts)
        if not self.is_market_open(now):
            return 0
        open_dt = now.replace(hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute, second=0, microsecond=0)
        return max(0, int((now - open_dt).total_seconds() // 60))

    def next_session_boundary(self, ts: datetime | None = None) -> datetime:
        now = self._now(ts)
        candidates = [
            now.replace(hour=w.start.hour, minute=w.start.minute, second=0, microsecond=0)
            for w in _SESSION_WINDOWS
        ] + [now.replace(hour=_MARKET_CLOSE.hour, minute=_MARKET_CLOSE.minute, second=0, microsecond=0)]

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
