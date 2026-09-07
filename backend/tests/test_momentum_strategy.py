"""MomentumStrategy tests, in two tiers, same shape as
test_volume_spike_strategy.py (this strategy has no LevelInteractionEngine/
Postgres dependency either — pure GATE/MATCH/SCORE math plus end-to-end
evaluate() against real MarketClock-anchored ET timestamps):

1. Pure functions (match_direction, score_confidence) — no DB, no event
   loop, no MarketClock.
2. End-to-end evaluate() across a simulated trading day — proves the
   GATE-stage swing-lookback warm-up, cooldown, day-rollover state
   machine, and the acceleration/trend/volume hierarchy, not just the
   pure MATCH/SCORE math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.momentum_strategy import (
    DEFAULT_ACCELERATION_SCORE_THRESHOLD,
    DEFAULT_LOOKBACK_BARS,
    DEFAULT_TREND_CONTEXT_THRESHOLD,
    DEFAULT_VOLUME_REGIME_THRESHOLD,
    MomentumStrategy,
    default_config,
    match_direction,
    score_confidence,
)


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


# --- Tier 1: pure functions -------------------------------------------------


def test_match_direction_buy_on_accelerating_uptrend_with_volume():
    assert (
        match_direction(
            trend_score=60.0,
            acceleration_score=80.0,
            volume_regime_score=60.0,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            trend_context_threshold=DEFAULT_TREND_CONTEXT_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "BUY"
    )


def test_match_direction_sell_on_accelerating_downtrend_with_volume():
    assert (
        match_direction(
            trend_score=40.0,
            acceleration_score=20.0,
            volume_regime_score=60.0,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            trend_context_threshold=DEFAULT_TREND_CONTEXT_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "SELL"
    )


def test_match_direction_none_when_not_accelerating():
    assert (
        match_direction(
            trend_score=60.0,
            acceleration_score=50.0,  # neutral — no acceleration either way
            volume_regime_score=60.0,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            trend_context_threshold=DEFAULT_TREND_CONTEXT_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_accelerating_but_trend_disagrees():
    """Accelerating up, but the broader trend doesn't even lean bullish
    yet — module docstring's own example of why this shouldn't match."""
    assert (
        match_direction(
            trend_score=52.0,  # below DEFAULT_TREND_CONTEXT_THRESHOLD (55.0)
            acceleration_score=90.0,
            volume_regime_score=60.0,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            trend_context_threshold=DEFAULT_TREND_CONTEXT_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_volume_floor_fails():
    assert (
        match_direction(
            trend_score=60.0,
            acceleration_score=90.0,
            volume_regime_score=10.0,  # below DEFAULT_VOLUME_REGIME_THRESHOLD (45.0)
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            trend_context_threshold=DEFAULT_TREND_CONTEXT_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_rejects_invalid_acceleration_threshold():
    with pytest.raises(ValueError, match="acceleration_score_threshold"):
        match_direction(
            trend_score=60.0,
            acceleration_score=90.0,
            volume_regime_score=60.0,
            acceleration_score_threshold=40.0,
            trend_context_threshold=DEFAULT_TREND_CONTEXT_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )


def test_match_direction_rejects_invalid_trend_context_threshold():
    with pytest.raises(ValueError, match="trend_context_threshold"):
        match_direction(
            trend_score=60.0,
            acceleration_score=90.0,
            volume_regime_score=60.0,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            trend_context_threshold=40.0,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )


def test_score_confidence_clamped_between_0_and_100():
    assert 0.0 <= score_confidence(trend_score=100.0, acceleration_score=100.0, volume_regime_score=100.0) <= 100.0
    assert 0.0 <= score_confidence(trend_score=0.0, acceleration_score=0.0, volume_regime_score=0.0) <= 100.0


def test_score_confidence_weights_acceleration_highest():
    """Module docstring's hierarchy (acceleration primary) encoded
    numerically: an extreme acceleration_score with everything else
    neutral should score higher than an extreme trend_score with
    everything else neutral."""
    accel_led = score_confidence(trend_score=50.0, acceleration_score=100.0, volume_regime_score=50.0)
    trend_led = score_confidence(trend_score=100.0, acceleration_score=50.0, volume_regime_score=50.0)
    assert accel_led > trend_led


# --- Tier 2: end-to-end evaluate() -------------------------------------------


def _make_market_state(candle_ts: datetime, **overrides) -> MarketState:
    base = dict(
        timeframe="1m",
        candle_ts=candle_ts,
        trend_score=65.0,
        volatility_regime_score=50.0,
        volume_regime_score=60.0,
        vwap_relationship_score=55.0,
        acceleration_score=80.0,
    )
    base.update(overrides)
    return MarketState(**base)


def _make_features(candle_ts: datetime, close: float, high: float, low: float, **overrides) -> FeatureSet:
    base = dict(
        timeframe="1m",
        candle_ts=candle_ts,
        close=close,
        open=close,
        high=high,
        low=low,
        volume=1000,
        features={},
    )
    base.update(overrides)
    return FeatureSet(**base)


async def _run_warmup(
    strategy: MomentumStrategy,
    symbol: str,
    session_open,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    flat_high: float = 100.5,
    flat_low: float = 99.8,
):
    """Feeds `lookback_bars` flat 1m candles starting at `session_open`,
    returns the list of evaluate() results (should all be None — still
    building an honest swing reference)."""
    results = []
    for i in range(lookback_bars):
        ts = session_open + timedelta(minutes=i)
        fs = _make_features(ts, close=100.1, high=flat_high, low=flat_low)
        ms = _make_market_state(ts, acceleration_score=50.0)  # neutral during warm-up — shouldn't matter, GATE returns first
        results.append(await strategy.evaluate(symbol, ms, fs, ContextChanged()))
    return results


@pytest.mark.asyncio
async def test_warmup_never_fires():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)  # a real Monday
    results = await _run_warmup(strategy, "TEST", session_open)
    assert all(r is None for r in results)


@pytest.mark.asyncio
async def test_acceleration_after_warmup_fires_buy_then_respects_cooldown():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", session_open, flat_high=100.5, flat_low=99.8)

    accel_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(accel_ts, close=102.0, high=102.1, low=100.9)
    opp = await strategy.evaluate("TEST", _make_market_state(accel_ts), fs, ContextChanged())

    assert opp is not None
    assert opp.strategy == "Momentum"
    assert opp.direction == "BUY"
    assert opp.structural_invalidation == pytest.approx(99.8)  # prior 10-bar swing low, not this candle's own low
    assert opp.structural_target == pytest.approx(102.0 + 2.0 * (102.0 - 99.8))
    assert opp.expected_horizon_minutes == 20
    assert opp.evidence["conditions"]["swing_low"] == pytest.approx(99.8)

    # Next candle, still accelerating, same direction: cooldown withholds it.
    next_ts = accel_ts + timedelta(minutes=1)
    fs2 = _make_features(next_ts, close=103.0, high=103.2, low=101.8)
    opp2 = await strategy.evaluate("TEST", _make_market_state(next_ts), fs2, ContextChanged())
    assert opp2 is None


@pytest.mark.asyncio
async def test_second_independent_acceleration_fires_again_after_cooldown_elapses():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", session_open)

    accel_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(accel_ts, close=102.0, high=102.1, low=100.9)
    first_opp = await strategy.evaluate("TEST", _make_market_state(accel_ts), fs, ContextChanged())
    assert first_opp is not None

    later_ts = accel_ts + timedelta(minutes=10)  # past the 5-minute cooldown
    fs2 = _make_features(later_ts, close=104.0, high=104.2, low=103.0)
    second_opp = await strategy.evaluate("TEST", _make_market_state(later_ts), fs2, ContextChanged())
    assert second_opp is not None


@pytest.mark.asyncio
async def test_accelerating_but_trend_disagreeing_never_fires():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", session_open)

    accel_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(accel_ts, close=102.0, high=102.1, low=100.9)
    opp = await strategy.evaluate(
        "TEST", _make_market_state(accel_ts, trend_score=52.0, acceleration_score=90.0), fs, ContextChanged()
    )
    assert opp is None


@pytest.mark.asyncio
async def test_outside_regular_session_never_fires():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    premarket = _et(2026, 8, 17, 8, 0)
    fs = _make_features(premarket, close=100.0, high=100.5, low=99.8)
    opp = await strategy.evaluate("TEST", _make_market_state(premarket), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_missing_high_low_returns_none_honest_absence():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)
    fs = FeatureSet(timeframe="1m", candle_ts=ts, close=100.0, features={})  # no high/low
    opp = await strategy.evaluate("TEST", _make_market_state(ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_null_acceleration_score_returns_none_honest_absence():
    """Symbol's first-ever Market State recompute (decision #93) —
    module docstring's GATE section."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)
    fs = _make_features(ts, close=100.0, high=100.5, low=99.8)
    opp = await strategy.evaluate("TEST", _make_market_state(ts, acceleration_score=None), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_new_trading_day_resets_swing_window_and_cooldown():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    day1_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", day1_open)
    accel_ts = day1_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(accel_ts, close=102.0, high=102.1, low=100.9)
    opp1 = await strategy.evaluate("TEST", _make_market_state(accel_ts), fs, ContextChanged())
    assert opp1 is not None

    day2_open = _et(2026, 8, 18, 9, 30)  # next real trading day
    # Fresh day: must re-warm up before it can fire again, even immediately
    # after a fire the prior day (cooldown from decision doesn't carry across days).
    results = await _run_warmup(strategy, "TEST", day2_open)
    assert all(r is None for r in results)


@pytest.mark.asyncio
async def test_different_symbols_track_independent_state():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "AAA", session_open)
    # BBB has seen nothing yet — still warming up regardless of AAA's own state.
    ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(ts, close=102.0, high=102.1, low=100.9)
    opp_bbb = await strategy.evaluate("BBB", _make_market_state(ts), fs, ContextChanged())
    assert opp_bbb is None
