"""Pure, covered-calendar placement window for simulated EOD flatten.

No polling, order placement, venue calls, or clock reads happen here. A caller
supplies the position opening instant and the current instant explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.market_clock import MarketClock

_ET = ZoneInfo("America/New_York")
_REGULAR_CLOSE = time(16, 0)
_HALF_DAY_CLOSE = time(13, 0)


class UnsupportedEodCalendarError(ValueError):
    """The entry trading day's calendar year has no EOD coverage."""


@dataclass(frozen=True)
class EodSessionWindow:
    """UTC placement interval [flatten_at, close_at), never a fill deadline."""

    flatten_at: datetime
    close_at: datetime

    def contains(self, now: datetime) -> bool:
        """True from flatten_at inclusive until close_at exclusive."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return self.flatten_at <= now < self.close_at


def eod_session_window(
    clock: MarketClock, opened_at: datetime, lead_seconds: int = 60,
) -> EodSessionWindow | None:
    """Return the entry day's UTC EOD window, or None on a covered closed day.

    `opened_at` must be timezone-aware. Its ET trading day is obtained from
    `clock.trading_day`. An unsupported calendar year raises
    `UnsupportedEodCalendarError`, unlike a covered holiday/weekend (None).
    `lead_seconds` is an integer from 1 through 900, inclusive. There is no
    implicit current time: pass an aware `now` to `window.contains(now)`.
    """
    if opened_at.tzinfo is None or opened_at.utcoffset() is None:
        raise ValueError("opened_at must be timezone-aware")
    if isinstance(lead_seconds, bool) or not isinstance(lead_seconds, int) or not 1 <= lead_seconds <= 900:
        raise ValueError("lead_seconds must be an integer from 1 through 900")

    entry_day = clock.trading_day(opened_at)
    if not clock.has_calendar_for_year(entry_day.year):
        raise UnsupportedEodCalendarError(f"no EOD calendar coverage for {entry_day.year}")
    if entry_day.weekday() >= 5 or clock.is_holiday(entry_day):
        return None

    close_time = _HALF_DAY_CLOSE if clock.is_half_day(entry_day) else _REGULAR_CLOSE
    close_at = datetime.combine(entry_day, close_time, tzinfo=_ET).astimezone(timezone.utc)
    return EodSessionWindow(close_at - timedelta(seconds=lead_seconds), close_at)
