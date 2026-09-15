"""
build_daily_history_candles — a synthetic, honestly-labeled sequence of
prior 1-DAY candles for the historical-provider seam
(`historical_provider_guard.py`), closing the gap `scenarios.py`'s own
module docstring names: `FeatureEngine`'s Daily Levels/ATR/RVOL refresh
(`_maybe_refresh_daily_levels`, `feature_engine/engine.py`) needs a real
`MarketDataProvider.get_historical(symbol, "1d", ...)` answer, and
nothing in the replay stack has ever supplied one.

**Why one shared, symbol-agnostic dataset, not per-scenario history.**
Mirrors `FixtureBacktestContextProvider`'s own precedent
(`context_provider.py`) — "same synthetic facts regardless of which
symbol" — extended here to "same synthetic facts regardless of which
symbol OR which named scenario," since nothing about Daily
Levels/ATR/RVOL is scenario-specific: every named scenario in
`scenarios.py` only needs a plausible, non-degenerate PRIOR trading
history to exist, not one shaped to any particular strategy's MATCH
condition (that shaping, if ever done, belongs to the scenario's own 1m
candles — explicitly out of scope for this module, see
`scenarios.py`'s own docstring for the four volume-gated strategies'
still-open guaranteed-fire question). Anchored to a `before` DATE
(the first replayed candle's trading day) rather than to a symbol,
so the same generator produces a correctly-dated trailing history no
matter what arbitrary `symbol` label a caller passes to `POST
/backtest/run` (decision #135; see that route's own docstring — `symbol`
is an arbitrary per-run label, never a real ticker lookup).

**How many days, and why.** `atr()` (`feature_engine/indicators/atr.py`)
needs exactly `period + 1` prior candles (`feature_engine_atr_period`
settings default: 14, so 15) — Wilder ATR needs the prior bar's close
for every True Range. `rvol()`'s `avg_daily_volume`
(`feature_engine/engine.py::_update_rvol`) needs
`feature_engine_rvol_lookback_days` (settings default: 5) COMPLETE
prior daily volumes. 15 is the binding constraint; `_NUM_DAYS = 20`
below is a modest buffer above it — the same "minimum genuinely needed
plus a modest buffer, not padded" aesthetic `scenarios.py`'s own named
CSVs already use for candle counts, applied here to day counts instead.

**Real NYSE trading days, not naive calendar days.** Walks backward from
`before` using the SAME `MarketClock.is_holiday()`/weekday logic every
other trading-day calculation in this codebase uses (confirmed by
reading `market_clock.py` directly — no "previous N trading days" helper
already exists there to call instead, so this module writes its own
small walker rather than duplicating `MarketClock`'s internals). Candle
timestamps land at NYSE close (16:00 ET) for each date, converted through
a real `ZoneInfo("America/New_York")` conversion (not a hardcoded UTC
offset) specifically so this stays correct across a DST boundary if this
generator is ever anchored to a `before` date on the other side of one —
`scenarios.py`'s four named scenarios all happen to sit in EST
(2026-02-02), but this module doesn't assume that.

**Construction, not vendor semantics — same posture `fixture_provider.py`
already states for its own candles.** No randomness, no noise model: a
small, fixed, gently-trending OHLCV sequence, chosen only to be
plausible and non-degenerate (real day-to-day price movement for ATR's
True Range to measure, real day-to-day volume for RVOL's average to
divide by) — not shaped to make any particular indicator read any
particular way. Honestly synthetic, same as every other fixture in this
package.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.broker_adapters.base import Candle
from app.core.market_clock import get_market_clock

__all__ = ["build_daily_history_candles"]

# See module docstring's "how many days, and why" — 15 genuinely needed
# (ATR's period + 1), 20 is that plus a modest buffer.
_NUM_DAYS = 20

# NYSE close, in real ET — matches core/market_clock.py's own
# `_MARKET_CLOSE = time(16, 0)` (a private constant there, not imported
# across the module boundary — duplicated as a literal here the same
# call this project already made for e.g. `context_provider.py`'s own
# duplicated `_FOMC_DATES_2026`, see that module's docstring).
_NYSE_CLOSE_ET = time(16, 0)
_ET = ZoneInfo("America/New_York")

# Deliberately plain, deterministic numbers — see module docstring's
# "construction, not vendor semantics." Gentle upward drift day over
# day, volume drifting modestly too, both just enough to give ATR a real
# (non-zero, non-degenerate) True Range and RVOL a real, non-zero
# average to divide by.
_START_PRICE = 95.0
_DAILY_DRIFT = 1.002  # ~0.2% per day
_DAILY_RANGE_PCT = 0.01  # 1% high/low band around each day's open/close
_START_VOLUME = 500_000
_DAILY_VOLUME_STEP = 1_000


def _prior_trading_days(before: date, num_days: int) -> list[date]:
    """The `num_days` real NYSE trading days strictly before `before`,
    oldest first — weekends and `MarketClock`'s own holiday set both
    excluded, the same two exclusions `_maybe_refresh_daily_levels`'s
    own "strictly-prior days only" filtering already applies on the
    consuming side."""
    clock = get_market_clock()
    days: list[date] = []
    cursor = before - timedelta(days=1)
    while len(days) < num_days:
        if cursor.weekday() < 5 and not clock.is_holiday(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def build_daily_history_candles(before: date, num_days: int = _NUM_DAYS) -> list[Candle]:
    """`num_days` synthetic 1-DAY candles, dated on the real NYSE trading
    days immediately preceding `before` (never including `before`
    itself — this seam only ever needs to satisfy
    `_maybe_refresh_daily_levels`'s "strictly-prior days only" contract,
    never today's still-forming bar). `before` is normally the first
    replayed candle's `MarketClock.trading_day(...)`, i.e. the scenario's
    own start date.

    No `symbol` parameter, deliberately — see this module's own
    docstring for why ("same synthetic facts regardless of which symbol
    OR which named scenario," `FixtureBacktestContextProvider`'s own
    precedent extended here). The caller keys these candles under
    whichever symbol it's actually running against when building the
    `(symbol, "1d") -> candles` entry a `FixtureCandleProvider` needs —
    this function has no opinion on that key.
    """
    days = _prior_trading_days(before, num_days)
    candles: list[Candle] = []
    price = _START_PRICE
    volume = _START_VOLUME
    for day in days:
        open_ = price
        close = round(open_ * _DAILY_DRIFT, 6)
        high = round(max(open_, close) * (1 + _DAILY_RANGE_PCT), 6)
        low = round(min(open_, close) * (1 - _DAILY_RANGE_PCT), 6)
        candle_ts = datetime.combine(day, _NYSE_CLOSE_ET, tzinfo=_ET).astimezone(timezone.utc)
        candles.append(
            Candle(
                timeframe="1d",
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                candle_ts=candle_ts,
            )
        )
        price = close
        volume += _DAILY_VOLUME_STEP
    return candles
