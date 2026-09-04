"""Momentum Strategy tests — pure functions, no DB, no event loop, plus
one end-to-end evaluate() pass using this sandbox's spec-conformant
Strategy/StrategyConfig/Opportunity stand-in. Mirrors the house style
in backend/tests/test_market_state_scoring.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.strategy_engine.momentum_strategy import (
    DEFAULT_ACCELERATION_SCORE_THRESHOLD,
    DEFAULT_TREND_SCORE_THRESHOLD,
    DEFAULT_VOLUME_REGIME_THRESHOLD,
    MomentumStrategy,
    default_config,
    ma_key,
    match_direction,
    score_confidence,
)
from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState


def test_ma_key_matches_feature_engine_convention():
    assert ma_key("sma", 9) == "sma_9"
    assert ma_key("ema", 20) == "ema_20"


def test_match_direction_buy_when_all_conditions_align():
    assert (
        match_direction(
            fast_ma=101.0,
            slow_ma=100.0,
            trend_score=70.0,
            acceleration_score=60.0,
            volume_regime_score=50.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "BUY"
    )


def test_match_direction_sell_is_buys_mirror_image():
    assert (
        match_direction(
            fast_ma=99.0,
            slow_ma=100.0,
            trend_score=30.0,
            acceleration_score=40.0,
            volume_regime_score=50.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "SELL"
    )


def test_match_direction_none_on_no_crossover():
    assert (
        match_direction(
            fast_ma=100.0,
            slow_ma=100.0,
            trend_score=90.0,
            acceleration_score=90.0,
            volume_regime_score=90.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_trend_not_confirming():
    # crossover says BUY, but trend_score sits at neutral — should not fire
    assert (
        match_direction(
            fast_ma=101.0,
            slow_ma=100.0,
            trend_score=50.0,
            acceleration_score=90.0,
            volume_regime_score=90.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_volume_floor_fails_regardless_of_direction():
    assert (
        match_direction(
            fast_ma=101.0,
            slow_ma=100.0,
            trend_score=90.0,
            acceleration_score=90.0,
            volume_regime_score=0.0,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            acceleration_score_threshold=DEFAULT_ACCELERATION_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_score_confidence_neutral_inputs_give_neutral_ish_score():
    # trend/accel exactly at 50 contribute 0 to their components; volume 0;
    # regression None contributes the neutral 50 placeholder at its own weight.
    score = score_confidence(50.0, 50.0, 0.0, None)
    assert score == pytest.approx(0.15 * 50.0, abs=0.01)


def test_score_confidence_saturates_at_100():
    score = score_confidence(100.0, 100.0, 100.0, 1.0)  # 1.0 >> cap of 0.5
    assert score == 100.0


def test_score_confidence_missing_regression_is_neutral_not_zero():
    with_none = score_confidence(80.0, 80.0, 80.0, None)
    with_zero_slope = score_confidence(80.0, 80.0, 80.0, 0.0)
    # None (honest-absence, contributes neutral 50) should score HIGHER
    # than an explicit flat slope of 0.0 (contributes 0) — they are not
    # the same thing and must not collapse to the same number.
    assert with_none > with_zero_slope


def _make_market_state(**overrides) -> MarketState:
    base = dict(
        timeframe="5m",
        candle_ts=datetime(2026, 9, 4, 14, 35, tzinfo=timezone.utc),
        trend_score=70.0,
        volatility_regime_score=50.0,
        volume_regime_score=60.0,
        vwap_relationship_score=55.0,
        acceleration_score=60.0,
    )
    base.update(overrides)
    return MarketState(**base)


def _make_features(**overrides) -> FeatureSet:
    base = dict(
        timeframe="5m",
        candle_ts=datetime(2026, 9, 4, 14, 35, tzinfo=timezone.utc),
        close=101.5,
        features={
            "sma_9": 101.0,
            "sma_20": 100.0,
            "regression_9_slope_norm": 0.2,
        },
    )
    base.update(overrides)
    return FeatureSet(**base)


@pytest.mark.asyncio
async def test_evaluate_end_to_end_buy_opportunity():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(),
        features=_make_features(),
        context=ContextChanged(),
    )
    assert opp is not None
    assert opp.direction == "BUY"
    assert opp.strategy == "Momentum"
    assert opp.version == "momentum_v1"
    assert opp.structural_invalidation == 100.0  # slow_ma
    assert opp.structural_target == pytest.approx(101.5 + 2.0 * (101.5 - 100.0))
    assert opp.evidence["basis"] == "closed"
    assert opp.evidence["conditions"]["fast_ma"] == 101.0


@pytest.mark.asyncio
async def test_evaluate_returns_none_on_wrong_timeframe():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(timeframe="1m"),
        features=_make_features(timeframe="1m"),
        context=ContextChanged(),
    )
    assert opp is None


@pytest.mark.asyncio
async def test_evaluate_returns_none_when_acceleration_score_missing():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(acceleration_score=None),
        features=_make_features(),
        context=ContextChanged(),
    )
    assert opp is None


@pytest.mark.asyncio
async def test_evaluate_returns_none_when_ma_not_warmed_up():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = MomentumStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(),
        features=_make_features(features={"sma_9": 101.0}),  # sma_20 missing
        context=ContextChanged(),
    )
    assert opp is None
