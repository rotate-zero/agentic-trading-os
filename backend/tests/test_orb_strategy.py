"""ORBStrategy tests, in two tiers:

1. Pure functions (match_direction, score_confidence,
   breakout_strength_fraction) — no DB, no event loop, no MarketClock.
2. End-to-end evaluate() across a simulated trading day, using real
   MarketClock-anchored ET timestamps (same `_et()` convention as
   test_feature_engine.py / test_candle_aggregator.py) — proves the
   GATE-stage day-scoped state machine (formation → freeze → breakout →
   fire-once → day rollover), not just the pure MATCH/SCORE math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.orb_strategy import (
    DEFAULT_OR_MINUTES,
    DEFAULT_TREND_SCORE_THRESHOLD,
    DEFAULT_VOLUME_REGIME_THRESHOLD,
    ORBStrategy,
    breakout_strength_fraction,
    default_config,
    match_direction,
    score_confidence,
)


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


# --- Tier 1: pure functions -------------------------------------------------


def test_match_direction_buy_on_breakout_above_high():
    assert (
        match_direction(
            close=102.0, or_high=101.0, or_low=100.0,
            trend_score=70.0, volume_regime_score=60.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "BUY"
    )


def test_match_direction_sell_on_breakdown_below_low():
    assert (
        match_direction(
            close=98.0, or_high=101.0, or_low=100.0,
            trend_score=30.0, volume_regime_score=60.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "SELL"
    )


def test_match_direction_none_inside_the_range():
    assert (
        match_direction(
            close=100.5, or_high=101.0, or_low=100.0,
            trend_score=90.0, volume_regime_score=90.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_volume_floor_fails():
    """Participation floor is checked once, direction-agnostic — a
    breakout on thin volume matches nothing, same convention
    momentum_strategy.py's own volume floor uses."""
    assert (
        match_direction(
            close=102.0, or_high=101.0, or_low=100.0,
            trend_score=90.0, volume_regime_score=0.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_trend_disagrees_with_breakout():
    """Price breaks above the range but Market State's own trend read
    disagrees — MATCH withholds; a raw breakout isn't sufficient alone."""
    assert (
        match_direction(
            close=102.0, or_high=101.0, or_low=100.0,
            trend_score=20.0, volume_regime_score=60.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_rejects_threshold_at_or_below_50():
    """Reconciled from this morning's concurrent-session review of the
    now-discarded momentum_strategy.py/vwap_strategy.py: both had this
    exact vulnerability (SELL mirrors the threshold as `100 - threshold`,
    so threshold<=50 flips the mirror onto the wrong side of neutral).
    Applied here directly rather than rediscovered independently."""
    with pytest.raises(ValueError, match="must be > 50.0"):
        match_direction(
            close=102.0, or_high=101.0, or_low=100.0,
            trend_score=55.0, volume_regime_score=60.0,
            trend_score_threshold=40.0,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )


def test_breakout_strength_fraction_buy_scales_with_distance_beyond_range():
    narrow = breakout_strength_fraction(101.1, 101.0, 100.0, "BUY")
    wide = breakout_strength_fraction(102.0, 101.0, 100.0, "BUY")
    assert 0.0 < narrow < wide


def test_breakout_strength_fraction_sell_direction():
    assert breakout_strength_fraction(99.0, 101.0, 100.0, "SELL") == pytest.approx(1.0)


def test_breakout_strength_fraction_zero_width_range_is_honest_zero_not_error():
    assert breakout_strength_fraction(105.0, 100.0, 100.0, "BUY") == 0.0


def test_score_confidence_saturates_at_100():
    assert score_confidence(100.0, 100.0, 1.0) == 100.0  # 1.0 >> cap of 0.5


def test_score_confidence_neutral_trend_contributes_zero_trend_component():
    # trend exactly at 50 contributes 0 to its own component; only
    # volume + breakout components remain.
    score = score_confidence(50.0, 40.0, 0.0)
    assert score == pytest.approx(0.30 * 40.0, abs=0.01)


# --- Tier 2: end-to-end evaluate() across a simulated day -------------------


def _make_market_state(candle_ts: datetime, **overrides) -> MarketState:
    base = dict(
        timeframe="1m",
        candle_ts=candle_ts,
        trend_score=70.0,
        volatility_regime_score=50.0,
        volume_regime_score=60.0,
        vwap_relationship_score=55.0,
        acceleration_score=60.0,
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


async def _run_formation_window(strategy: ORBStrategy, symbol: str, session_open: datetime, or_minutes: int = DEFAULT_OR_MINUTES):
    """Feeds `or_minutes` 1m candles (a flat 100-101 range) starting at
    `session_open`, returns the list of evaluate() results (should all
    be None — still forming)."""
    results = []
    for i in range(or_minutes):
        ts = session_open + timedelta(minutes=i)
        fs = _make_features(ts, close=100.5, high=101.0, low=100.0)
        ms = _make_market_state(ts)
        results.append(await strategy.evaluate(symbol, ms, fs, ContextChanged()))
    return results


@pytest.mark.asyncio
async def test_formation_window_never_fires():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = ORBStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)  # a real Monday
    results = await _run_formation_window(strategy, "TEST", session_open)
    assert all(r is None for r in results)


@pytest.mark.asyncio
async def test_breakout_after_formation_fires_once_then_stays_silent():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = ORBStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_formation_window(strategy, "TEST", session_open)

    breakout_ts = session_open + timedelta(minutes=DEFAULT_OR_MINUTES)
    fs = _make_features(breakout_ts, close=102.0, high=102.0, low=101.0)
    ms = _make_market_state(breakout_ts)
    opp = await strategy.evaluate("TEST", ms, fs, ContextChanged())

    assert opp is not None
    assert opp.strategy == "ORB"
    assert opp.direction == "BUY"
    assert opp.structural_invalidation == 100.0  # or_low
    assert opp.structural_target == pytest.approx(102.0 + 2.0 * (102.0 - 100.0))
    assert opp.evidence["conditions"]["or_high"] == 101.0
    assert opp.evidence["conditions"]["or_low"] == 100.0

    # Next candle still above the range: MATCH would fire BUY again, but
    # the strategy's own "fires once per direction per day" rule (module
    # docstring) withholds it.
    next_ts = breakout_ts + timedelta(minutes=1)
    fs2 = _make_features(next_ts, close=103.0, high=103.2, low=102.5)
    ms2 = _make_market_state(next_ts)
    opp2 = await strategy.evaluate("TEST", ms2, fs2, ContextChanged())
    assert opp2 is None


@pytest.mark.asyncio
async def test_reversal_after_a_buy_can_still_fire_a_sell():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = ORBStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_formation_window(strategy, "TEST", session_open)

    buy_ts = session_open + timedelta(minutes=DEFAULT_OR_MINUTES)
    buy_fs = _make_features(buy_ts, close=102.0, high=102.0, low=101.0)
    buy_opp = await strategy.evaluate("TEST", _make_market_state(buy_ts), buy_fs, ContextChanged())
    assert buy_opp is not None and buy_opp.direction == "BUY"

    # Price later reverses and breaks the OPPOSITE side — a distinct
    # direction, allowed to fire once of its own even though BUY already fired.
    sell_ts = buy_ts + timedelta(minutes=5)
    sell_fs = _make_features(sell_ts, close=99.0, high=99.5, low=98.5)
    sell_ms = _make_market_state(sell_ts, trend_score=20.0)
    sell_opp = await strategy.evaluate("TEST", sell_ms, sell_fs, ContextChanged())
    assert sell_opp is not None
    assert sell_opp.direction == "SELL"
    assert sell_opp.structural_invalidation == 101.0  # or_high


@pytest.mark.asyncio
async def test_missing_high_low_returns_none_honest_absence():
    """A FeatureSet without high/low (pre-decision-#99 shape, or an
    aggregated-timeframe FeatureSet that slipped past the timeframe
    check) — ORB can't accumulate or test a range without real wicks."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = ORBStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)
    fs = FeatureSet(timeframe="1m", candle_ts=ts, close=100.0, features={})  # no high/low
    opp = await strategy.evaluate("TEST", _make_market_state(ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_missed_formation_window_never_fires_that_day():
    """Process only starts watching this symbol after the opening range
    already closed — no honest range was ever observed, so ORB stays
    silent for the rest of that day rather than fabricating one from a
    single late candle. See orb_strategy.py's module docstring."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = ORBStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    late_ts = session_open + timedelta(minutes=DEFAULT_OR_MINUTES + 5)  # already past formation
    fs = _make_features(late_ts, close=105.0, high=105.5, low=104.5)
    opp = await strategy.evaluate("TEST", _make_market_state(late_ts), fs, ContextChanged())
    assert opp is None

    # Still silent on a subsequent candle the same day, even a dramatic move.
    later_ts = late_ts + timedelta(minutes=10)
    fs2 = _make_features(later_ts, close=120.0, high=121.0, low=119.0)
    opp2 = await strategy.evaluate("TEST", _make_market_state(later_ts), fs2, ContextChanged())
    assert opp2 is None


@pytest.mark.asyncio
async def test_new_trading_day_resets_state_and_can_fire_again():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = ORBStrategy(config)

    day1_open = _et(2026, 8, 17, 9, 30)  # Monday
    await _run_formation_window(strategy, "TEST", day1_open)
    day1_breakout_ts = day1_open + timedelta(minutes=DEFAULT_OR_MINUTES)
    day1_opp = await strategy.evaluate(
        "TEST", _make_market_state(day1_breakout_ts),
        _make_features(day1_breakout_ts, close=102.0, high=102.0, low=101.0),
        ContextChanged(),
    )
    assert day1_opp is not None

    day2_open = _et(2026, 8, 18, 9, 30)  # Tuesday — new trading_day
    day2_results = await _run_formation_window(strategy, "TEST", day2_open)
    assert all(r is None for r in day2_results)  # forming fresh, not carrying over day1's range

    day2_breakout_ts = day2_open + timedelta(minutes=DEFAULT_OR_MINUTES)
    day2_opp = await strategy.evaluate(
        "TEST", _make_market_state(day2_breakout_ts),
        _make_features(day2_breakout_ts, close=102.0, high=102.0, low=101.0),
        ContextChanged(),
    )
    assert day2_opp is not None  # fires again — day1's fired_directions didn't leak across days


@pytest.mark.asyncio
async def test_different_symbols_track_independent_state():
    """One ORBStrategy instance serves the whole symbol universe (same
    singleton-with-internal-keying shape as FeatureEngine/
    MarketStateEngine) — a breakout on one symbol must not affect
    another's independently-forming range. See base_strategy.py's
    module docstring, assumption #4."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = ORBStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)

    await _run_formation_window(strategy, "AAA", session_open)
    # BBB has only seen a couple of candles — still forming.
    for i in range(2):
        ts = session_open + timedelta(minutes=i)
        await strategy.evaluate(
            "BBB", _make_market_state(ts),
            _make_features(ts, close=50.0, high=50.2, low=49.8),
            ContextChanged(),
        )

    breakout_ts = session_open + timedelta(minutes=DEFAULT_OR_MINUTES)
    aaa_opp = await strategy.evaluate(
        "AAA", _make_market_state(breakout_ts),
        _make_features(breakout_ts, close=102.0, high=102.0, low=101.0),
        ContextChanged(),
    )
    assert aaa_opp is not None  # AAA's range fully formed — breaks out normally

    bbb_opp = await strategy.evaluate(
        "BBB", _make_market_state(breakout_ts),
        _make_features(breakout_ts, close=52.0, high=52.0, low=51.5),
        ContextChanged(),
    )
    assert bbb_opp is None  # BBB never finished forming (only 2 of 15 candles seen) — honest absence
