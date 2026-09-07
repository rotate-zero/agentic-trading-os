"""GapStrategy tests, in two tiers:

1. Pure functions (match_direction, score_confidence,
   gap_strength_fraction) — no DB, no event loop, no MarketClock.
2. End-to-end evaluate() across simulated trading days, using real
   MarketClock-anchored ET timestamps (same `_et()` convention as
   test_orb_strategy.py) — proves the GATE-stage day-scoped state
   machine (session gate, gap-availability gate, fire-once, day
   rollover, max-age window), not just the pure MATCH/SCORE math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.gap_strategy import (
    DEFAULT_MAX_MINUTES_SINCE_OPEN,
    DEFAULT_MIN_GAP_PCT,
    DEFAULT_TREND_SCORE_THRESHOLD,
    DEFAULT_VOLUME_REGIME_THRESHOLD,
    GapStrategy,
    default_config,
    gap_strength_fraction,
    match_direction,
    score_confidence,
)


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


# --- Tier 1: pure functions -------------------------------------------------


def test_match_direction_buy_on_gap_up_holding_above_open():
    assert (
        match_direction(
            close=101.5, regular_open=100.0, gap_pct=3.0,
            trend_score=70.0, volume_regime_score=60.0,
            minutes_since_open=5,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )
        == "BUY"
    )


def test_match_direction_sell_on_gap_down_holding_below_open():
    assert (
        match_direction(
            close=98.5, regular_open=100.0, gap_pct=-3.0,
            trend_score=30.0, volume_regime_score=60.0,
            minutes_since_open=5,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )
        == "SELL"
    )


def test_match_direction_none_when_gap_up_already_given_back():
    """Gapped up, but close has already fallen back through the open
    print — the "holding" thesis is already false."""
    assert (
        match_direction(
            close=99.5, regular_open=100.0, gap_pct=3.0,
            trend_score=90.0, volume_regime_score=90.0,
            minutes_since_open=5,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )
        is None
    )


def test_match_direction_none_when_gap_too_small():
    assert (
        match_direction(
            close=100.5, regular_open=100.0, gap_pct=0.5,  # below DEFAULT_MIN_GAP_PCT (2.0)
            trend_score=90.0, volume_regime_score=90.0,
            minutes_since_open=5,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )
        is None
    )


def test_match_direction_none_when_volume_floor_fails():
    """Participation floor is checked once, direction-agnostic — same
    convention orb_strategy.py's own volume floor uses."""
    assert (
        match_direction(
            close=101.5, regular_open=100.0, gap_pct=3.0,
            trend_score=90.0, volume_regime_score=0.0,
            minutes_since_open=5,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )
        is None
    )


def test_match_direction_none_when_past_max_window():
    """Decision #111 — a gap that would otherwise clearly match is
    refused once minutes_since_open exceeds the configured window,
    regardless of how confirming everything else is."""
    assert (
        match_direction(
            close=101.5, regular_open=100.0, gap_pct=3.0,
            trend_score=90.0, volume_regime_score=90.0,
            minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN + 1,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )
        is None
    )


def test_match_direction_fires_at_exactly_the_window_boundary():
    """minutes_since_open == max_minutes_since_open is still inside the
    window (the check is strictly-greater-than) — a boundary worth
    pinning explicitly rather than leaving to chance."""
    assert (
        match_direction(
            close=101.5, regular_open=100.0, gap_pct=3.0,
            trend_score=90.0, volume_regime_score=90.0,
            minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )
        == "BUY"
    )


def test_match_direction_rejects_threshold_at_or_below_50():
    """Same guard as orb_strategy.py's match_direction() — decision #99's
    fix, now via the shared scoring_utils.validate_mirror_threshold()."""
    with pytest.raises(ValueError):
        match_direction(
            close=101.5, regular_open=100.0, gap_pct=3.0,
            trend_score=55.0, volume_regime_score=90.0,
            minutes_since_open=5,
            min_gap_pct=DEFAULT_MIN_GAP_PCT,
            trend_score_threshold=40.0,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            max_minutes_since_open=DEFAULT_MAX_MINUTES_SINCE_OPEN,
        )


def test_gap_strength_fraction_is_absolute_value():
    assert gap_strength_fraction(3.5) == 3.5
    assert gap_strength_fraction(-4.2) == 4.2


def test_score_confidence_clamped_between_0_and_100():
    assert 0.0 <= score_confidence(trend_score=100.0, volume_regime_score=100.0, gap_strength=50.0) <= 100.0
    assert 0.0 <= score_confidence(trend_score=0.0, volume_regime_score=0.0, gap_strength=0.0) <= 100.0


# --- Tier 2: end-to-end evaluate() -------------------------------------------


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


def _make_features(candle_ts: datetime, close: float, **overrides) -> FeatureSet:
    features = overrides.pop("features", {})
    base = dict(
        timeframe="1m",
        candle_ts=candle_ts,
        close=close,
        open=close,
        high=close,
        low=close,
        volume=1000,
        features=features,
    )
    base.update(overrides)
    return FeatureSet(**base)


def _gap_features(candle_ts: datetime, close: float, *, pdc: float, gap_dollars: float, gap_pct: float, **overrides) -> FeatureSet:
    # regular_open included directly (decision #111) — Feature Engine
    # publishes it as its own key now, this test helper mirrors that
    # rather than making evaluate() reconstruct it.
    return _make_features(
        candle_ts, close,
        features={"pdc": pdc, "gap_dollars": gap_dollars, "gap_pct": gap_pct, "regular_open": pdc + gap_dollars},
        **overrides,
    )


@pytest.mark.asyncio
async def test_no_signal_before_gap_is_established():
    """Pre-market, or a fresh symbol/deployment with no prior trading
    day — gap_pct/gap_dollars/pdc/regular_open aren't in
    `features.features` yet."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)
    fs = _make_features(ts, close=100.0)  # no gap keys at all
    opp = await strategy.evaluate("TEST", _make_market_state(ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_gap_up_continuation_fires_buy():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)  # a real Monday, first regular candle
    # pdc=100, regular_open=103 -> gap_pct=3.0%, gap_dollars=3.0
    fs = _gap_features(ts, close=104.0, pdc=100.0, gap_dollars=3.0, gap_pct=3.0)
    opp = await strategy.evaluate("TEST", _make_market_state(ts), fs, ContextChanged())

    assert opp is not None
    assert opp.strategy == "Gap"
    assert opp.direction == "BUY"
    assert opp.structural_invalidation == pytest.approx(103.0)  # regular_open, read directly
    assert opp.structural_target == pytest.approx(104.0 + 2.0 * (104.0 - 103.0))
    assert opp.expected_horizon_minutes == 60  # decision #111 default
    assert opp.evidence["conditions"]["gap_pct"] == 3.0
    assert opp.evidence["conditions"]["regular_open"] == pytest.approx(103.0)

    # Same day, gap still holding: the strategy already answered this
    # question for today — module docstring's "one fire per symbol per day."
    next_ts = ts + timedelta(minutes=1)
    fs2 = _gap_features(next_ts, close=105.0, pdc=100.0, gap_dollars=3.0, gap_pct=3.0)
    opp2 = await strategy.evaluate("TEST", _make_market_state(next_ts), fs2, ContextChanged())
    assert opp2 is None


@pytest.mark.asyncio
async def test_gap_down_continuation_fires_sell():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)
    # pdc=100, regular_open=97 -> gap_pct=-3.0%, gap_dollars=-3.0
    fs = _gap_features(ts, close=96.0, pdc=100.0, gap_dollars=-3.0, gap_pct=-3.0)
    opp = await strategy.evaluate("TEST", _make_market_state(ts, trend_score=25.0), fs, ContextChanged())

    assert opp is not None
    assert opp.direction == "SELL"
    assert opp.structural_invalidation == pytest.approx(97.0)
    assert opp.structural_target == pytest.approx(96.0 - 2.0 * (97.0 - 96.0))


@pytest.mark.asyncio
async def test_gap_already_filled_before_first_evaluate_never_fires():
    """Gap up, but by the time this process observes it, close has
    already fallen back through the open print — honest absence, not a
    fabricated signal."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)
    ts = _et(2026, 8, 17, 9, 35)
    fs = _gap_features(ts, close=99.0, pdc=100.0, gap_dollars=3.0, gap_pct=3.0)  # close below regular_open=103
    opp = await strategy.evaluate("TEST", _make_market_state(ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_outside_regular_session_never_fires():
    """Pre-market/after-hours — gap continuation is a regular-session
    concept, even if gap_pct happens to already be in the features dict
    (e.g. after-hours, same day, gap frozen earlier that morning)."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)
    after_hours_ts = _et(2026, 8, 17, 17, 0)
    fs = _gap_features(after_hours_ts, close=104.0, pdc=100.0, gap_dollars=3.0, gap_pct=3.0)
    opp = await strategy.evaluate("TEST", _make_market_state(after_hours_ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_past_max_window_never_fires_even_though_gap_still_holds():
    """Decision #111, design review §2. Same clean gap-up setup as
    test_gap_up_continuation_fires_buy, but evaluated well past the
    configured max_minutes_since_open — refused even though the gap is
    still holding by every other measure, since the thesis is now
    considered stale rather than a fresh continuation."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)
    stale_ts = _et(2026, 8, 17, 9, 30) + timedelta(minutes=DEFAULT_MAX_MINUTES_SINCE_OPEN + 5)
    fs = _gap_features(stale_ts, close=104.0, pdc=100.0, gap_dollars=3.0, gap_pct=3.0)
    opp = await strategy.evaluate("TEST", _make_market_state(stale_ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_new_trading_day_resets_state_and_can_fire_again():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)

    day1_ts = _et(2026, 8, 17, 9, 30)  # Monday
    day1_fs = _gap_features(day1_ts, close=104.0, pdc=100.0, gap_dollars=3.0, gap_pct=3.0)
    day1_opp = await strategy.evaluate("TEST", _make_market_state(day1_ts), day1_fs, ContextChanged())
    assert day1_opp is not None

    day2_ts = _et(2026, 8, 18, 9, 30)  # Tuesday — new trading_day, new gap
    day2_fs = _gap_features(day2_ts, close=97.0, pdc=104.0, gap_dollars=-4.0, gap_pct=-3.846154)
    day2_opp = await strategy.evaluate("TEST", _make_market_state(day2_ts, trend_score=20.0), day2_fs, ContextChanged())
    assert day2_opp is not None  # fires again — day1's `fired` flag didn't leak across days
    assert day2_opp.direction == "SELL"


@pytest.mark.asyncio
async def test_different_symbols_track_independent_state():
    """One GapStrategy instance serves the whole symbol universe (same
    singleton-with-internal-keying shape as FeatureEngine/
    MarketStateEngine/orb_strategy.py) — a fired gap on one symbol must
    not affect another's independent state."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = GapStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)

    aaa_fs = _gap_features(ts, close=104.0, pdc=100.0, gap_dollars=3.0, gap_pct=3.0)
    aaa_opp = await strategy.evaluate("AAA", _make_market_state(ts), aaa_fs, ContextChanged())
    assert aaa_opp is not None

    bbb_fs = _gap_features(ts, close=53.0, pdc=50.0, gap_dollars=2.0, gap_pct=4.0)
    bbb_opp = await strategy.evaluate("BBB", _make_market_state(ts), bbb_fs, ContextChanged())
    assert bbb_opp is not None  # BBB's own first evaluate() today — AAA firing didn't set BBB.fired
