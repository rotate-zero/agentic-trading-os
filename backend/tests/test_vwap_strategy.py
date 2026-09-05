"""VWAP Strategy tests — pure functions, no DB, no event loop, plus one
end-to-end evaluate() pass. Mirrors test_momentum_strategy.py's style.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.strategy_engine.vwap_strategy import (
    DEFAULT_TREND_SCORE_THRESHOLD,
    DEFAULT_VOLUME_REGIME_THRESHOLD,
    DEFAULT_VWAP_SCORE_HIGH,
    DEFAULT_VWAP_SCORE_LOW,
    VWAPStrategy,
    _band_quality,
    default_config,
    match_direction,
    score_confidence,
)
from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState


def test_match_direction_buy_in_band_with_trend_confirming():
    assert (
        match_direction(
            vwap_relationship_score=58.0,
            trend_score=70.0,
            volume_regime_score=50.0,
            vwap_score_low=DEFAULT_VWAP_SCORE_LOW,
            vwap_score_high=DEFAULT_VWAP_SCORE_HIGH,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "BUY"
    )


def test_match_direction_sell_is_buys_mirror_band():
    assert (
        match_direction(
            vwap_relationship_score=42.0,  # mirror of 58.0 around 50
            trend_score=30.0,
            volume_regime_score=50.0,
            vwap_score_low=DEFAULT_VWAP_SCORE_LOW,
            vwap_score_high=DEFAULT_VWAP_SCORE_HIGH,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        == "SELL"
    )


def test_match_direction_none_right_at_vwap():
    # score == 50 (sitting exactly at VWAP) falls in neither band — noise, not a signal
    assert (
        match_direction(
            vwap_relationship_score=50.0,
            trend_score=90.0,
            volume_regime_score=90.0,
            vwap_score_low=DEFAULT_VWAP_SCORE_LOW,
            vwap_score_high=DEFAULT_VWAP_SCORE_HIGH,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_extended_beyond_band():
    # score of 90 is well past the "not yet extended" ceiling
    assert (
        match_direction(
            vwap_relationship_score=90.0,
            trend_score=90.0,
            volume_regime_score=90.0,
            vwap_score_low=DEFAULT_VWAP_SCORE_LOW,
            vwap_score_high=DEFAULT_VWAP_SCORE_HIGH,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_none_when_volume_floor_fails():
    assert (
        match_direction(
            vwap_relationship_score=58.0,
            trend_score=90.0,
            volume_regime_score=0.0,
            vwap_score_low=DEFAULT_VWAP_SCORE_LOW,
            vwap_score_high=DEFAULT_VWAP_SCORE_HIGH,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )
        is None
    )


def test_match_direction_rejects_trend_threshold_at_or_below_50():
    with pytest.raises(ValueError):
        match_direction(
            vwap_relationship_score=58.0,
            trend_score=70.0,
            volume_regime_score=50.0,
            vwap_score_low=DEFAULT_VWAP_SCORE_LOW,
            vwap_score_high=DEFAULT_VWAP_SCORE_HIGH,
            trend_score_threshold=50.0,  # not > 50
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )


@pytest.mark.parametrize(
    "low,high",
    [
        (50.0, DEFAULT_VWAP_SCORE_HIGH),  # low not > 50
        (48.0, DEFAULT_VWAP_SCORE_HIGH),  # low dips below neutral entirely
        (60.0, 55.0),  # inverted band
        (60.0, 60.0),  # zero-width band
        (DEFAULT_VWAP_SCORE_LOW, 101.0),  # high beyond 100
    ],
)
def test_match_direction_rejects_invalid_vwap_band(low, high):
    with pytest.raises(ValueError):
        match_direction(
            vwap_relationship_score=58.0,
            trend_score=70.0,
            volume_regime_score=50.0,
            vwap_score_low=low,
            vwap_score_high=high,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
        )


def test_band_quality_peaks_at_midpoint():
    mid = (DEFAULT_VWAP_SCORE_LOW + DEFAULT_VWAP_SCORE_HIGH) / 2.0
    assert _band_quality(mid, DEFAULT_VWAP_SCORE_LOW, DEFAULT_VWAP_SCORE_HIGH) == 100.0


def test_band_quality_tapers_to_zero_at_edges():
    assert _band_quality(DEFAULT_VWAP_SCORE_LOW, DEFAULT_VWAP_SCORE_LOW, DEFAULT_VWAP_SCORE_HIGH) == pytest.approx(0.0, abs=0.01)
    assert _band_quality(DEFAULT_VWAP_SCORE_HIGH, DEFAULT_VWAP_SCORE_LOW, DEFAULT_VWAP_SCORE_HIGH) == pytest.approx(0.0, abs=0.01)


def test_score_confidence_saturates_at_100():
    mid = (DEFAULT_VWAP_SCORE_LOW + DEFAULT_VWAP_SCORE_HIGH) / 2.0
    score = score_confidence(
        mid, 100.0, 100.0,
        vwap_score_low=DEFAULT_VWAP_SCORE_LOW,
        vwap_score_high=DEFAULT_VWAP_SCORE_HIGH,
        direction="BUY",
    )
    assert score == 100.0


def _make_market_state(**overrides) -> MarketState:
    base = dict(
        timeframe="1m",
        candle_ts=datetime(2026, 9, 4, 14, 35, tzinfo=timezone.utc),
        trend_score=70.0,
        volatility_regime_score=50.0,
        volume_regime_score=60.0,
        vwap_relationship_score=58.0,
        acceleration_score=60.0,
    )
    base.update(overrides)
    return MarketState(**base)


def _make_features(**overrides) -> FeatureSet:
    base = dict(
        timeframe="1m",
        candle_ts=datetime(2026, 9, 4, 14, 35, tzinfo=timezone.utc),
        close=101.5,
        features={"vwap": 100.0, "atr_14": 1.2},
    )
    base.update(overrides)
    return FeatureSet(**base)


@pytest.mark.asyncio
async def test_evaluate_end_to_end_buy_opportunity():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VWAPStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(),
        features=_make_features(),
        context=ContextChanged(),
    )
    assert opp is not None
    assert opp.direction == "BUY"
    assert opp.strategy == "VWAP"
    assert opp.version == "vwap_v1"
    assert opp.structural_invalidation == 100.0  # vwap itself
    assert opp.structural_target == pytest.approx(101.5 + 2.0 * 1.2)
    assert opp.evidence["basis"] == "closed"
    assert opp.evidence["conditions"]["market_state_timeframe"] == "1m"


@pytest.mark.asyncio
async def test_evaluate_returns_none_when_close_already_falsifies_thesis():
    # market_state says BUY (vwap_relationship_score in the BUY band), but
    # the LOCAL close/vwap pair used for PROPOSE's own math has close
    # actually below vwap — the "holding VWAP as a level" thesis is
    # already false for the data this Opportunity would be built from.
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VWAPStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(),  # vwap_relationship_score=58.0 -> BUY
        features=_make_features(close=99.5, features={"vwap": 100.0, "atr_14": 1.2}),
        context=ContextChanged(),
    )
    assert opp is None


@pytest.mark.asyncio
async def test_evaluate_returns_none_when_vwap_missing():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VWAPStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(),
        features=_make_features(features={"atr_14": 1.2}),  # no vwap key
        context=ContextChanged(),
    )
    assert opp is None


@pytest.mark.asyncio
async def test_evaluate_returns_none_on_wrong_timeframe():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VWAPStrategy(config)
    opp = await strategy.evaluate(
        market_state=_make_market_state(timeframe="5m"),
        features=_make_features(timeframe="5m"),
        context=ContextChanged(),
    )
    assert opp is None
