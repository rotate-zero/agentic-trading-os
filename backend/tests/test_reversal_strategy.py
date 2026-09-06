"""
ReversalStrategy tests, same three-tier shape as
test_first_pullback_strategy.py: pure functions, an isolated staleness
guard check, then end-to-end evaluate() against a REAL EventBus + REAL
LevelInteractionEngine + REAL Postgres (skipped as a whole if Postgres
isn't reachable).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.context import ContextChanged
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.reversal_strategy import (
    DEFAULT_TREND_SCORE_THRESHOLD,
    ReversalStrategy,
    default_config,
    match_direction,
    score_confidence,
)
from app.trading_intelligence.level_interaction_engine import LevelInteractionEngine

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


def test_match_direction_sell_when_established_uptrend_conquered():
    assert match_direction(70.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) == "SELL"


def test_match_direction_buy_when_established_downtrend_conquered():
    assert match_direction(30.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) == "BUY"


def test_match_direction_none_when_no_established_trend():
    assert match_direction(50.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None


def test_match_direction_raises_on_invalid_threshold():
    with pytest.raises(ValueError):
        match_direction(70.0, trend_score_threshold=40.0)


def test_score_confidence_rewards_more_prior_touches():
    fewer_touches = score_confidence(70.0, 60.0, touch_count_today=1)
    more_touches = score_confidence(70.0, 60.0, touch_count_today=4)
    assert more_touches > fewer_touches
    assert 0.0 <= fewer_touches <= 100.0
    assert 0.0 <= more_touches <= 100.0


def test_score_confidence_caps_touch_count_component():
    at_cap = score_confidence(70.0, 60.0, touch_count_today=4)
    beyond_cap = score_confidence(70.0, 60.0, touch_count_today=20)
    assert at_cap == beyond_cap  # saturates, doesn't keep climbing unbounded


# --- Tier 2: staleness guard, isolated (no DB) ------------------------------


@pytest.mark.asyncio
async def test_stale_snapshot_returns_none_without_touching_tracker(monkeypatch):
    stale_ts = (_DAY1 - timedelta(minutes=1)).isoformat()
    entry = {
        "zone": "below",
        "touch_count_today": 1,
        "trading_day": "2026-08-10",
        "last_applied_candle_ts": stale_ts,
    }

    class _Stub:
        def get_snapshot(self, symbol=None):
            return {symbol: {"1m": {"vwap": entry}}}

    monkeypatch.setattr(
        "app.strategy_engine.reversal_strategy.get_level_interaction_engine",
        lambda: _Stub(),
    )
    strategy = ReversalStrategy(default_config(active_from=_DAY1))
    features = FeatureSet(timeframe="1m", candle_ts=_DAY1, close=98.0, features={"vwap": 100.0})
    result = await strategy.evaluate("__REV_STALE__", _market_state(70.0), features, ContextChanged())
    assert result is None
    assert "__REV_STALE__" not in strategy._state


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
    strategy: ReversalStrategy,
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
    await asyncio.sleep(settle_seconds)
    return await strategy.evaluate(symbol, market_state, features, ContextChanged())


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


@pytest.mark.asyncio
async def test_end_to_end_established_uptrend_conquered_fires_sell():
    ticker = "__REV_SELL__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = ReversalStrategy(default_config(active_from=_DAY1))
    uptrend = _market_state(70.0)  # trend_score still reads the OLD, about-to-break direction

    try:
        r1 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, uptrend)
        assert r1 is None

        r2 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, uptrend)
        assert r2 is None  # touch starts

        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 98.0, 100.0, uptrend)
        assert r3 is not None
        assert r3.direction == "SELL"
        assert r3.structural_invalidation == 100.0  # anchor_price, real touch (not gap-through)
        assert r3.evidence["conditions"]["invalidation_source"] == "anchor_price"
        assert r3.evidence["conditions"]["touch_count_today"] == 1
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_rejected_touch_does_not_fire():
    ticker = "__REV_HOLD__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = ReversalStrategy(default_config(active_from=_DAY1))
    uptrend = _market_state(70.0)

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, uptrend)
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, uptrend)
        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 101.5, 100.0, uptrend)
        assert r3 is None  # bounced back — level held, no reversal
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_second_touch_conquest_still_fires_unlike_first_pullback():
    """Deliberately different from First Pullback — touch_count_today
    is not required to be 1 (module docstring)."""
    ticker = "__REV_SECOND__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = ReversalStrategy(default_config(active_from=_DAY1))
    uptrend = _market_state(70.0)

    try:
        t = _DAY1
        await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, uptrend)
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, uptrend)  # touch 1 starts
        t += timedelta(minutes=1)
        r1 = await _publish_and_evaluate(bus, strategy, ticker, t, 101.5, 100.0, uptrend)  # touch 1 rejected — level holds
        assert r1 is None
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, uptrend)  # touch 2 starts
        t += timedelta(minutes=1)
        r2 = await _publish_and_evaluate(bus, strategy, ticker, t, 98.0, 100.0, uptrend)  # touch 2 conquered — reversal
        assert r2 is not None
        assert r2.evidence["conditions"]["touch_count_today"] == 2
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_gap_through_conquest_falls_back_to_live_level_value():
    """No inside_aura observed at all — anchor_price unavailable,
    invalidation falls back to features.features[level_key] (module
    docstring)."""
    ticker = "__REV_GAP__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = ReversalStrategy(default_config(active_from=_DAY1))
    uptrend = _market_state(70.0)

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, uptrend)  # steady "above"
        # Jump straight to "below" — inside_aura (99.8-100.2) never printed.
        r2 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 95.0, 100.0, uptrend)
        assert r2 is not None
        assert r2.direction == "SELL"
        assert r2.structural_invalidation == 100.0  # fallback: live vwap value, not a captured anchor
        assert r2.evidence["conditions"]["invalidation_source"] == "live_level_value"
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_fires_at_most_once_per_day():
    ticker = "__REV_ONCE__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = ReversalStrategy(default_config(active_from=_DAY1))
    uptrend = _market_state(70.0)

    try:
        t = _DAY1
        await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, uptrend)
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, uptrend)
        t += timedelta(minutes=1)
        r1 = await _publish_and_evaluate(bus, strategy, ticker, t, 98.0, 100.0, uptrend)
        assert r1 is not None
        t += timedelta(minutes=1)
        # A brand-new touch conquered again the same day — should NOT re-fire.
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, uptrend)
        t += timedelta(minutes=1)
        r2 = await _publish_and_evaluate(bus, strategy, ticker, t, 103.0, 100.0, uptrend)
        assert r2 is None
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)
