"""
FirstPullbackStrategy tests, in three tiers:

1. Pure functions (match_direction, score_confidence,
   rejection_strength_fraction) — no DB, no event loop.
2. The staleness guard, isolated via a stub LevelInteractionEngine — no
   DB needed for this either, since it never reaches a real transition.
3. End-to-end evaluate() against a REAL EventBus + REAL
   LevelInteractionEngine + REAL Postgres (same posture as
   test_level_interaction_engine.py — skipped as a whole, not failed, if
   Postgres isn't reachable), proving the actual GATE/MATCH state
   machine against genuine engine-computed zone transitions, not a
   hand-rolled substitute.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.context import ContextChanged
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.first_pullback_strategy import (
    DEFAULT_TREND_SCORE_THRESHOLD,
    FirstPullbackStrategy,
    default_config,
    match_direction,
    rejection_strength_fraction,
    score_confidence,
)
from app.trading_intelligence.level_interaction_engine import LevelInteractionEngine, get_level_interaction_engine

_DAY1 = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)  # 10:00 ET


def _market_state(trend_score: float, volume_regime_score: float = 60.0, candle_ts: datetime = _DAY1) -> MarketState:
    return MarketState(
        timeframe="1m",
        candle_ts=candle_ts,
        trend_score=trend_score,
        volatility_regime_score=50.0,
        volume_regime_score=volume_regime_score,
        vwap_relationship_score=50.0,
        acceleration_score=None,
    )


# --- Tier 1: pure functions -------------------------------------------------


def test_match_direction_buy_on_rejection_from_above_in_uptrend():
    assert match_direction("above", 70.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) == "BUY"


def test_match_direction_sell_on_rejection_from_below_in_downtrend():
    assert match_direction("below", 30.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) == "SELL"


def test_match_direction_none_when_entered_from_and_trend_disagree():
    # Pulled back from above (uptrend shape) but trend_score reads bearish — no forced direction.
    assert match_direction("above", 30.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None
    assert match_direction("below", 70.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None


def test_match_direction_none_when_trend_not_established():
    assert match_direction("above", 55.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None


def test_match_direction_raises_on_invalid_threshold():
    with pytest.raises(ValueError):
        match_direction("above", 70.0, trend_score_threshold=40.0)


def test_rejection_strength_fraction_buy_positive_distance():
    assert rejection_strength_fraction(0.5, "BUY") == 0.5


def test_rejection_strength_fraction_buy_clamps_negative_to_zero():
    assert rejection_strength_fraction(-0.3, "BUY") == 0.0


def test_rejection_strength_fraction_sell_mirrors():
    assert rejection_strength_fraction(-0.5, "SELL") == 0.5
    assert rejection_strength_fraction(0.3, "SELL") == 0.0


def test_score_confidence_bounds_and_monotonic_in_trend_strength():
    low = score_confidence(51.0, 0.0, 0.0)
    high = score_confidence(99.0, 100.0, 1.0)
    assert 0.0 <= low <= 100.0
    assert 0.0 <= high <= 100.0
    assert high > low


# --- Tier 2: staleness guard, isolated (no DB) ------------------------------


class _StubEngine:
    """Minimal stand-in for LevelInteractionEngine — only get_snapshot()
    is ever called by the strategy."""

    def __init__(self, entry: dict | None):
        self._entry = entry

    def get_snapshot(self, symbol: str | None = None):
        if self._entry is None:
            return {}
        return {symbol: {"1m": {"vwap": self._entry}}}


@pytest.mark.asyncio
async def test_stale_snapshot_returns_none_without_touching_tracker(monkeypatch):
    """last_applied_candle_ts older than features.candle_ts must GATE out
    before ever calling observe_resolution() — see level_touch_tracking.py's
    own module docstring for why order matters."""
    stale_ts = (_DAY1 - timedelta(minutes=1)).isoformat()
    entry = {
        "zone": "above",
        "touch_count_today": 1,
        "trading_day": "2026-08-10",
        "last_applied_candle_ts": stale_ts,
    }
    monkeypatch.setattr(
        "app.strategy_engine.first_pullback_strategy.get_level_interaction_engine",
        lambda: _StubEngine(entry),
    )
    strategy = FirstPullbackStrategy(default_config(active_from=_DAY1))
    features = FeatureSet(timeframe="1m", candle_ts=_DAY1, close=101.0, features={"vwap": 100.0})
    result = await strategy.evaluate("__FP_STALE__", _market_state(70.0), features, ContextChanged())
    assert result is None
    # the tracker was never advanced — confirmed indirectly: a symbol
    # that was never actually watched has no state entry at all yet.
    assert "__FP_STALE__" not in strategy._state


@pytest.mark.asyncio
async def test_missing_last_applied_candle_ts_returns_none():
    """Engine has literally never finished processing this (symbol,
    timeframe) — treated the same as stale, not as an error."""
    entry = {"zone": "above", "touch_count_today": 1, "trading_day": "2026-08-10", "last_applied_candle_ts": None}

    class _Stub:
        def get_snapshot(self, symbol=None):
            return {symbol: {"1m": {"vwap": entry}}}

    strategy = FirstPullbackStrategy(default_config(active_from=_DAY1))
    import app.strategy_engine.first_pullback_strategy as mod

    original = mod.get_level_interaction_engine
    mod.get_level_interaction_engine = lambda: _Stub()
    try:
        features = FeatureSet(timeframe="1m", candle_ts=_DAY1, close=101.0, features={"vwap": 100.0})
        result = await strategy.evaluate("__FP_NOTS__", _market_state(70.0), features, ContextChanged())
        assert result is None
    finally:
        mod.get_level_interaction_engine = original


# --- Tier 3: end-to-end, real engine, real Postgres -------------------------


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


def _clean_test_symbol(ticker: str) -> None:
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM level_interaction_events WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
            {"t": ticker},
        )
        session.execute(
            text("DELETE FROM level_interaction_state WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
            {"t": ticker},
        )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


async def _publish_and_evaluate(
    bus: EventBus,
    strategy: FirstPullbackStrategy,
    symbol: str,
    candle_ts: datetime,
    close: float,
    vwap: float,
    market_state: MarketState,
    settle_seconds: float = 0.2,
):
    import asyncio

    features = FeatureSet(timeframe="1m", candle_ts=candle_ts, close=close, features={"vwap": vwap})
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, features, symbol=symbol))
    await asyncio.sleep(settle_seconds)  # let LevelInteractionEngine's async worker catch up
    return await strategy.evaluate(symbol, market_state, features, ContextChanged())


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


@pytest.mark.asyncio
async def test_end_to_end_first_touch_rejected_in_uptrend_fires_buy():
    ticker = "__FP_BUY__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = FirstPullbackStrategy(default_config(active_from=_DAY1))
    trend = _market_state(70.0, volume_regime_score=60.0)

    try:
        r1 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, trend)
        assert r1 is None  # first observation, steady "above" — nothing to resolve

        r2 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, trend)
        assert r2 is None  # touch starts — inside_aura, resolution comes later

        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 101.5, 100.0, trend)
        assert r3 is not None
        assert r3.direction == "BUY"
        assert r3.structural_invalidation == 100.0  # anchor_price captured at touch start
        assert r3.evidence["conditions"]["entered_from"] == "above"
        assert r3.evidence["conditions"]["level_key"] == "vwap"
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_conquered_touch_does_not_fire():
    ticker = "__FP_CONQ__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = FirstPullbackStrategy(default_config(active_from=_DAY1))
    trend = _market_state(70.0)

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, trend)
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, trend)
        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 98.0, 100.0, trend)
        assert r3 is None  # broke through to "below" — conquered, pullback failed, not an error
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_second_touch_rejected_does_not_fire():
    """touch_count_today == 2 by the time this one resolves — MATCH
    requires literally the first touch, per module docstring."""
    ticker = "__FP_SECOND__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = FirstPullbackStrategy(default_config(active_from=_DAY1))
    trend = _market_state(70.0)

    try:
        t = _DAY1
        await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, trend)
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, trend)  # touch 1 starts
        t += timedelta(minutes=1)
        r = await _publish_and_evaluate(bus, strategy, ticker, t, 101.5, 100.0, trend)  # touch 1 rejected — fires
        assert r is not None
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, trend)  # touch 2 starts
        t += timedelta(minutes=1)
        r2 = await _publish_and_evaluate(bus, strategy, ticker, t, 101.5, 100.0, trend)  # touch 2 rejected
        assert r2 is None  # not the first touch anymore
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_no_established_trend_does_not_fire():
    ticker = "__FP_NOTREND__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = FirstPullbackStrategy(default_config(active_from=_DAY1))
    flat = _market_state(50.0)  # no established trend

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, flat)
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, flat)
        r = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 101.5, 100.0, flat)
        assert r is None  # rejection happened, but no trend to confirm a direction
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_day_rollover_allows_a_fresh_first_touch():
    ticker = "__FP_ROLLOVER__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = FirstPullbackStrategy(default_config(active_from=_DAY1))
    trend = _market_state(70.0)
    day2 = _DAY1 + timedelta(days=1)

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, trend)
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, trend)
        r1 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 101.5, 100.0, trend)
        assert r1 is not None

        # Next trading day — a fresh first touch should be able to fire again.
        await _publish_and_evaluate(bus, strategy, ticker, day2, 101.0, 100.0, _market_state(70.0, candle_ts=day2))
        await _publish_and_evaluate(
            bus, strategy, ticker, day2 + timedelta(minutes=1), 100.0, 100.0, _market_state(70.0, candle_ts=day2)
        )
        r2 = await _publish_and_evaluate(
            bus, strategy, ticker, day2 + timedelta(minutes=2), 101.5, 100.0, _market_state(70.0, candle_ts=day2)
        )
        assert r2 is not None
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)
