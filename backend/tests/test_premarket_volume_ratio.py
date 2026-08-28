"""
Pre-market volume ratio tests — docs/architecture/premarket-accumulator-design.md.
Two tiers, deliberately separate: direct-poke sync tests against
`_update_premarket_volume_ratio` (mirrors tests/test_feature_engine.py's
own `_update_rvol` direct-poke style — cheap, thorough for the actual
math/gating logic) and a fake-provider test for
`_maybe_refresh_premarket_baseline`'s async fetch/filter/group-by-day
logic, which has no existing precedent in this codebase to mirror since
`_maybe_refresh_daily_levels`'s own fetch mechanics aren't unit-tested
at that level either — written fresh here because the day-grouping
logic is new and worth covering directly, not just via its consumer.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.event_bus.bus import EventBus
from app.feature_engine.engine import FeatureEngine
from app.schemas.events.market_data import CandleClosed
from app.services import broker_registry
from tests.test_feature_engine import _et


def _premarket_bar(volume: int, ts: datetime) -> CandleClosed:
    return CandleClosed(timeframe="1m", open=1.0, high=1.0, low=1.0, close=1.0, volume=volume, candle_ts=ts)


class _FakePremarketProvider:
    """Minimal stand-in for MarketDataProvider — only get_historical is
    ever called by _maybe_refresh_premarket_baseline."""

    def __init__(self, bars: list[CandleClosed]) -> None:
        self._bars = bars

    async def get_historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[CandleClosed]:
        return self._bars


# --- Tier: direct-poke sync tests (_update_premarket_volume_ratio) ----------


def test_absent_outside_premarket_even_with_good_data():
    """The one behavioral rule that makes this different from vwap_ext:
    no continuation story — simply doesn't exist outside its one
    window, unlike vwap_ext which keeps running into regular session."""
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    ticker = "__TEST_FE_PMVR_REG__"
    engine._premarket_baseline_cache[ticker] = {"for_day": _et(2026, 8, 11, 0, 0).date(), "avg_premarket_volume": 1000.0}
    result = engine._update_premarket_volume_ratio(ticker, _et(2026, 8, 11, 9, 45), session_volume_ext=500.0)  # regular session
    assert result == {}


def test_absent_when_session_volume_ext_is_none():
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    ticker = "__TEST_FE_PMVR_NOSV__"
    engine._premarket_baseline_cache[ticker] = {"for_day": _et(2026, 8, 11, 0, 0).date(), "avg_premarket_volume": 1000.0}
    assert engine._update_premarket_volume_ratio(ticker, _et(2026, 8, 11, 8, 0), None) == {}


def test_absent_when_baseline_not_yet_cached():
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    ticker = "__TEST_FE_PMVR_NOBASE__"
    assert engine._update_premarket_volume_ratio(ticker, _et(2026, 8, 11, 8, 0), session_volume_ext=500.0) == {}


def test_absent_when_baseline_cached_but_none_not_enough_history():
    """Mirrors avg_premarket_volume being None after
    _maybe_refresh_premarket_baseline ran but found fewer than
    feature_engine_premarket_lookback_days complete prior sessions."""
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    ticker = "__TEST_FE_PMVR_SHORT__"
    engine._premarket_baseline_cache[ticker] = {"for_day": _et(2026, 8, 11, 0, 0).date(), "avg_premarket_volume": None}
    assert engine._update_premarket_volume_ratio(ticker, _et(2026, 8, 11, 8, 0), session_volume_ext=500.0) == {}


def test_uses_premarket_specific_baseline_and_window_not_regular_session_ones():
    """Direct poke against the real MarketClock, proving the
    premarket-window elapsed-fraction machinery specifically (4:00-9:30
    = 330 minutes total, NOT 390) — reuses rvol()'s math but the wrong
    total_session_minutes here would silently produce a regular-session
    answer instead of a pre-market one."""
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    ticker = "__TEST_FE_PMVR_MATH__"
    engine._premarket_baseline_cache[ticker] = {"for_day": _et(2026, 8, 11, 0, 0).date(), "avg_premarket_volume": 3300.0}

    # 4:00 + 165 minutes = 6:45am -> exactly half of the 330-minute
    # pre-market window has elapsed. Expected-by-now = 3300 * 0.5 = 1650.
    result = engine._update_premarket_volume_ratio(ticker, _et(2026, 8, 11, 6, 45), session_volume_ext=3300.0)
    assert result == {"premarket_volume_ratio": pytest.approx(3300.0 / 1650.0)}
    assert "rvol" not in result  # renamed, not aliased — the two keys must never collide


def test_floors_elapsed_minutes_at_one_for_the_very_first_premarket_candle():
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    ticker = "__TEST_FE_PMVR_FIRST__"
    engine._premarket_baseline_cache[ticker] = {"for_day": _et(2026, 8, 11, 0, 0).date(), "avg_premarket_volume": 3300.0}
    result = engine._update_premarket_volume_ratio(ticker, _et(2026, 8, 11, 4, 0), session_volume_ext=5.0)
    assert "premarket_volume_ratio" in result
    assert result["premarket_volume_ratio"] > 0


# --- Tier: async fetch/filter/group-by-day (_maybe_refresh_premarket_baseline) ---


@pytest.mark.asyncio
async def test_refresh_filters_to_premarket_rows_and_averages_per_day():
    """The core new logic this whole feature depends on: mixed
    pre-market + regular-session + today's-not-yet-elapsed bars go in,
    only strictly-prior pre-market volume, summed per day and averaged,
    comes out."""
    ticker = "__TEST_FE_PMVR_FETCH__"
    bars = [
        # Day 1 (2026-08-10): pre-market volume totals 1000, plus a
        # regular-session bar that must NOT be counted.
        _premarket_bar(600, _et(2026, 8, 10, 8, 0)),
        _premarket_bar(400, _et(2026, 8, 10, 9, 0)),
        _premarket_bar(99999, _et(2026, 8, 10, 10, 0)),  # regular session — must be excluded
        # Day 2 (2026-08-11): pre-market volume totals 2000.
        _premarket_bar(2000, _et(2026, 8, 11, 8, 0)),
        # Today (2026-08-12, the candle_ts's own day): must be excluded
        # even though it's a pre-market bar — not yet a COMPLETE prior day.
        _premarket_bar(99999, _et(2026, 8, 12, 8, 0)),
    ]

    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    engine._premarket_lookback_days = 2
    broker_registry.set_historical_provider(_FakePremarketProvider(bars))
    try:
        await engine._maybe_refresh_premarket_baseline({"symbol": ticker, "candle_ts": _et(2026, 8, 12, 9, 0)})
    finally:
        broker_registry.clear_historical_provider()

    cached = engine._premarket_baseline_cache[ticker]
    assert cached["avg_premarket_volume"] == pytest.approx((1000 + 2000) / 2)


@pytest.mark.asyncio
async def test_refresh_leaves_baseline_none_when_fewer_complete_days_than_lookback():
    ticker = "__TEST_FE_PMVR_FETCHSHORT__"
    bars = [_premarket_bar(1000, _et(2026, 8, 11, 8, 0))]  # only 1 complete prior day, lookback wants 2

    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    engine._premarket_lookback_days = 2
    broker_registry.set_historical_provider(_FakePremarketProvider(bars))
    try:
        await engine._maybe_refresh_premarket_baseline({"symbol": ticker, "candle_ts": _et(2026, 8, 12, 9, 0)})
    finally:
        broker_registry.clear_historical_provider()

    assert engine._premarket_baseline_cache[ticker]["avg_premarket_volume"] is None


@pytest.mark.asyncio
async def test_refresh_is_a_noop_on_the_same_day_once_already_cached():
    """Same once-per-(symbol, ET day) gate _maybe_refresh_daily_levels
    already uses — a second call the same day must not re-fetch."""
    ticker = "__TEST_FE_PMVR_ONCE__"
    call_count = 0

    class _CountingProvider:
        async def get_historical(self, symbol, timeframe, start, end):
            nonlocal call_count
            call_count += 1
            return [_premarket_bar(1000, _et(2026, 8, 11, 8, 0))]

    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    engine._premarket_lookback_days = 1
    broker_registry.set_historical_provider(_CountingProvider())
    try:
        await engine._maybe_refresh_premarket_baseline({"symbol": ticker, "candle_ts": _et(2026, 8, 12, 9, 0)})
        await engine._maybe_refresh_premarket_baseline({"symbol": ticker, "candle_ts": _et(2026, 8, 12, 9, 5)})
    finally:
        broker_registry.clear_historical_provider()

    assert call_count == 1


@pytest.mark.asyncio
async def test_refresh_when_no_historical_provider_connected_leaves_baseline_none():
    ticker = "__TEST_FE_PMVR_NOPROV__"
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    broker_registry.clear_historical_provider()  # ensure a clean slate regardless of test order
    await engine._maybe_refresh_premarket_baseline({"symbol": ticker, "candle_ts": _et(2026, 8, 12, 9, 0)})
    assert engine._premarket_baseline_cache[ticker]["avg_premarket_volume"] is None
