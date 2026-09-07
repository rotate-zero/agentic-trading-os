"""
VWAPStrategy tests, same three-tier shape as test_reversal_strategy.py:
pure functions, an isolated staleness guard check, then end-to-end
evaluate() against a REAL EventBus + REAL LevelInteractionEngine + REAL
Postgres (skipped as a whole if Postgres isn't reachable).
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
from app.strategy_engine.scoring_utils import ESTABLISHED_TREND_SCORE_THRESHOLD
from app.strategy_engine.vwap_strategy import (
    DEFAULT_TREND_SCORE_THRESHOLD,
    VWAPStrategy,
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


def test_default_threshold_matches_shared_established_constant():
    """decision #113 — VWAP and Reversal must default to the exact same
    number or their firing conditions stop being complements."""
    assert DEFAULT_TREND_SCORE_THRESHOLD == ESTABLISHED_TREND_SCORE_THRESHOLD


def test_match_direction_buy_when_conquered_above_and_trend_neutral():
    assert match_direction("above", 50.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) == "BUY"


def test_match_direction_sell_when_conquered_below_and_trend_neutral():
    assert match_direction("below", 50.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) == "SELL"


def test_match_direction_none_when_trend_established_bullish():
    """Reversal's territory, not VWAP's — module docstring's disjoint design."""
    assert match_direction("above", 70.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None
    assert match_direction("below", 70.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None


def test_match_direction_none_when_trend_established_bearish():
    assert match_direction("above", 30.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None
    assert match_direction("below", 30.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None


def test_match_direction_none_at_exact_neutral_band_boundary():
    """40/60 themselves belong to the established side (>= / <=), not the
    neutral band — no double-counting at the boundary (scoring_utils.py's
    own trend_established_side() contract)."""
    assert match_direction("above", 60.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None
    assert match_direction("below", 40.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None


def test_match_direction_defensive_none_for_unexpected_zone():
    """observe_resolution() should never hand back anything but "above"/
    "below" on a "conquered" resolution — defensive guard, not a real path."""
    assert match_direction("inside_aura", 50.0, trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD) is None


def test_score_confidence_clamped_between_0_and_100():
    assert 0.0 <= score_confidence(volume_regime_score=100.0, distance_pct=5.0) <= 100.0
    assert 0.0 <= score_confidence(volume_regime_score=0.0, distance_pct=0.0) <= 100.0


def test_score_confidence_none_safe_for_missing_distance_pct():
    """distance_pct can be None (e.g. latest_close/latest_level_value not
    both available yet) — treated as a non-contributing 0.0, not a crash."""
    result = score_confidence(volume_regime_score=60.0, distance_pct=None)
    assert 0.0 <= result <= 100.0


def test_score_confidence_rewards_greater_distance():
    small_distance = score_confidence(volume_regime_score=60.0, distance_pct=0.05)
    large_distance = score_confidence(volume_regime_score=60.0, distance_pct=0.4)
    assert large_distance > small_distance


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
        "app.strategy_engine.vwap_strategy.get_level_interaction_engine",
        lambda: _Stub(),
    )
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    features = FeatureSet(timeframe="1m", candle_ts=_DAY1, close=98.0, features={"vwap": 100.0})
    result = await strategy.evaluate("__VWAP_STALE__", _market_state(50.0), features, ContextChanged())
    assert result is None
    assert "__VWAP_STALE__" not in strategy._state


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
    strategy: VWAPStrategy,
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
async def test_end_to_end_conquered_above_with_neutral_trend_fires_buy():
    ticker = "__VWAP_BUY__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    neutral = _market_state(50.0)  # no established trend — VWAP's own territory

    try:
        r1 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 99.0, 100.0, neutral)  # steady "below"
        assert r1 is None

        r2 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, neutral)
        assert r2 is None  # touch starts

        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 101.0, 100.0, neutral)
        assert r3 is not None
        assert r3.direction == "BUY"  # actual resolved zone (above), not a mirror of trend_score
        assert r3.structural_invalidation == 100.0  # anchor_price, real touch (not gap-through)
        assert r3.evidence["conditions"]["invalidation_source"] == "anchor_price"
        assert r3.evidence["conditions"]["touch_count_today"] == 1
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_conquered_below_with_neutral_trend_fires_sell():
    ticker = "__VWAP_SELL__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    neutral = _market_state(50.0)

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 101.0, 100.0, neutral)  # steady "above"
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, neutral)
        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 99.0, 100.0, neutral)
        assert r3 is not None
        assert r3.direction == "SELL"
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_established_trend_blocks_firing_even_on_a_real_conquest():
    """Same candle sequence as the BUY test above — the only difference
    is trend_score. This is the disjoint-design boundary itself: an
    identical conquest that Reversal would act on must NOT also fire
    VWAP (decision #113's whole point)."""
    ticker = "__VWAP_BLOCKED__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    established = _market_state(70.0)  # Reversal's territory

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 99.0, 100.0, established)
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, established)
        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 101.0, 100.0, established)
        assert r3 is None
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_rejected_touch_does_not_fire():
    ticker = "__VWAP_HOLD__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    neutral = _market_state(50.0)

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 99.0, 100.0, neutral)
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 100.0, 100.0, neutral)
        r3 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=2), 98.5, 100.0, neutral)
        assert r3 is None  # bounced back to "below" — no control transition
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_gap_through_conquest_falls_back_to_live_level_value():
    ticker = "__VWAP_GAP__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    neutral = _market_state(50.0)

    try:
        await _publish_and_evaluate(bus, strategy, ticker, _DAY1, 99.0, 100.0, neutral)  # steady "below"
        # Jump straight to "above" — inside_aura (99.8-100.2) never printed.
        r2 = await _publish_and_evaluate(bus, strategy, ticker, _DAY1 + timedelta(minutes=1), 105.0, 100.0, neutral)
        assert r2 is not None
        assert r2.direction == "BUY"
        assert r2.structural_invalidation == 100.0  # fallback: live vwap value, not a captured anchor
        assert r2.evidence["conditions"]["invalidation_source"] == "live_level_value"
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_genuine_alternation_fires_both_directions():
    """Two consecutive real conquests, both under a neutral trend, must
    alternate zones by construction of level_touch_tracking.py's own
    classification rule — proves the dedup guard doesn't wrongly
    suppress genuine back-to-back transitions."""
    ticker = "__VWAP_ALT__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    neutral = _market_state(50.0)

    try:
        t = _DAY1
        await _publish_and_evaluate(bus, strategy, ticker, t, 99.0, 100.0, neutral)
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, neutral)
        t += timedelta(minutes=1)
        r1 = await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, neutral)
        assert r1 is not None and r1.direction == "BUY"

        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, neutral)  # new touch starts from "above"
        t += timedelta(minutes=1)
        r2 = await _publish_and_evaluate(bus, strategy, ticker, t, 99.0, 100.0, neutral)
        assert r2 is not None and r2.direction == "SELL"  # genuine flip — fires again immediately
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_same_zone_repeat_after_established_window_is_suppressed():
    """The one real repeat case (module docstring's 'Cadence' section):
    VWAP only sees conquests that land in the neutral band, so a run of
    conquests that happen entirely while trend is ESTABLISHED can leave
    the zone back where VWAP itself last fired, once trend returns to
    neutral, without VWAP ever having seen the intervening flips.
    last_fired_zone must suppress that repeat, then still fire on the
    next GENUINE new zone."""
    ticker = "__VWAP_REPEAT__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    neutral = _market_state(50.0)
    established = _market_state(70.0)

    try:
        t = _DAY1
        # 1) Neutral: below -> inside_aura -> above. Fires BUY. last_fired_zone = "above".
        await _publish_and_evaluate(bus, strategy, ticker, t, 99.0, 100.0, neutral)
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, neutral)
        t += timedelta(minutes=1)
        r1 = await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, neutral)
        assert r1 is not None and r1.direction == "BUY"

        # 2) Established: above -> inside_aura -> below. Conquered, but blocked (Reversal's territory).
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, established)
        t += timedelta(minutes=1)
        r2 = await _publish_and_evaluate(bus, strategy, ticker, t, 99.0, 100.0, established)
        assert r2 is None

        # 3) Established: below -> inside_aura -> above. Conquered again, still blocked.
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, established)
        t += timedelta(minutes=1)
        r3 = await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, established)
        assert r3 is None

        # 4) Established: above -> inside_aura -> below. Conquered again, still blocked. Zone now "below".
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, established)
        t += timedelta(minutes=1)
        r4 = await _publish_and_evaluate(bus, strategy, ticker, t, 99.0, 100.0, established)
        assert r4 is None

        # 5) Trend back to neutral: below -> inside_aura -> above. A REAL conquest, zone
        #    resolves to "above" — same zone VWAP last fired for in step 1 — suppressed.
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, neutral)
        t += timedelta(minutes=1)
        r5 = await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, neutral)
        assert r5 is None  # suppressed — nothing new since VWAP's own last fire

        # 6) Neutral: above -> inside_aura -> below. A genuinely different zone from
        #    VWAP's last fire ("above") — fires SELL.
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, neutral)
        t += timedelta(minutes=1)
        r6 = await _publish_and_evaluate(bus, strategy, ticker, t, 99.0, 100.0, neutral)
        assert r6 is not None and r6.direction == "SELL"
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_end_to_end_new_trading_day_resets_last_fired_zone():
    ticker = "__VWAP_NEWDAY__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    import app.trading_intelligence.level_interaction_engine as lie_module

    lie_module._level_interaction_engine = engine
    strategy = VWAPStrategy(default_config(active_from=_DAY1))
    neutral = _market_state(50.0)

    try:
        t = _DAY1
        await _publish_and_evaluate(bus, strategy, ticker, t, 99.0, 100.0, neutral)
        t += timedelta(minutes=1)
        await _publish_and_evaluate(bus, strategy, ticker, t, 100.0, 100.0, neutral)
        t += timedelta(minutes=1)
        r1 = await _publish_and_evaluate(bus, strategy, ticker, t, 101.0, 100.0, neutral)
        assert r1 is not None and r1.direction == "BUY"

        # Next real trading day, same symbol, same zone ("above" is still steady) —
        # a fresh touch that conquers back to "above" must fire again, not be
        # suppressed by yesterday's last_fired_zone.
        day2 = _DAY1 + timedelta(days=1)
        day2_neutral = _market_state(50.0, candle_ts=day2)
        await _publish_and_evaluate(bus, strategy, ticker, day2, 100.0, 100.0, day2_neutral)
        r2 = await _publish_and_evaluate(
            bus, strategy, ticker, day2 + timedelta(minutes=1), 99.0, 100.0, day2_neutral
        )
        assert r2 is not None and r2.direction == "SELL"
    finally:
        await engine.stop()
        await bus.stop()
        lie_module._level_interaction_engine = None
        _clean_test_symbol(ticker)
