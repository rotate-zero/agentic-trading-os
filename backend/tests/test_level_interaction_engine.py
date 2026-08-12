"""
LevelInteractionEngine tests.

Every scenario that produces a zone TRANSITION needs real Postgres (the
engine persists on every transition — see engine module docstring), so
unlike test_feature_engine.py this file is mostly DB-backed. Skipped as a
whole, not failed, if Postgres isn't reachable — same posture as
test_candle_recorder.py. Only `classify_zone()` itself (pure, no I/O) runs
regardless.

Tests talk to LevelInteractionEngine directly via a real EventBus,
publishing synthetic FeaturesUpdated events — no FeatureEngine/CandleRecorder
involved, since FeaturesUpdated is self-contained (carries `close` — see
schemas/events/features.py) and this engine only ever subscribes to that
one event type.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet
from app.trading_intelligence.level_interaction_engine import LevelInteractionEngine, classify_zone

# A fixed, DST-unambiguous UTC instant: 2026-08-10 14:00 UTC = 10:00 ET (EDT, UTC-4).
_DAY1 = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
_DAY2 = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)  # next ET calendar day


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
            text(
                "DELETE FROM level_interaction_events WHERE symbol_id IN "
                "(SELECT id FROM symbols WHERE ticker = :t)"
            ),
            {"t": ticker},
        )
        session.execute(
            text(
                "DELETE FROM level_interaction_state WHERE symbol_id IN "
                "(SELECT id FROM symbols WHERE ticker = :t)"
            ),
            {"t": ticker},
        )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


async def _publish(bus: EventBus, symbol: str, candle_ts: datetime, close: float, features: dict[str, float]) -> None:
    payload = FeatureSet(timeframe="1m", candle_ts=candle_ts, close=close, features=features)
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=symbol))


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


# --- pure math (no DB, no event loop) ---------------------------------------


def test_classify_zone_boundaries():
    aura = 0.002  # 0.2%
    assert classify_zone(100.0, 100.0, aura) == "inside_aura"
    assert classify_zone(100.2, 100.0, aura) == "inside_aura"   # exactly at the edge
    assert classify_zone(100.3, 100.0, aura) == "above"
    assert classify_zone(99.8, 100.0, aura) == "inside_aura"
    assert classify_zone(99.7, 100.0, aura) == "below"


# --- state machine, against real Postgres -----------------------------------


@pytest.mark.asyncio
async def test_touch_then_rejected():
    ticker = "__LIE_REJ__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0})                      # below — first observation, no event
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0})  # touch — holding
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 99.0, {"sma_9": 100.0})   # exits back below — rejected
        await asyncio.sleep(0.2)

        assert len(received) == 2
        holding, rejected = received[0].payload, received[1].payload
        assert holding["status"] == "holding" and holding["touch_count_today"] == 1
        assert rejected["status"] == "rejected"
        assert rejected["observed_via"] == "dwell"
        assert rejected["seconds_in_zone"] == 60
        assert rejected["distance_pct"] == -1.0  # (99-100)/100 * 100
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_touch_then_conquered():
    ticker = "__LIE_CONQ__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0})
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0})   # touch, entered from below
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 101.0, {"sma_9": 100.0})   # exits ABOVE — opposite side — conquered
        await asyncio.sleep(0.2)

        assert len(received) == 2
        conquered = received[1].payload
        assert conquered["status"] == "conquered"
        assert conquered["observed_via"] == "dwell"
        assert conquered["distance_pct"] == 1.0
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_gap_through_is_always_conquered():
    ticker = "__LIE_GAP__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0})                        # below
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 101.0, {"sma_9": 100.0})  # straight to above — never touched the aura
        await asyncio.sleep(0.2)

        assert len(received) == 1  # no "holding" event — the aura was never actually observed
        event = received[0].payload
        assert event["status"] == "conquered"
        assert event["observed_via"] == "gap"
        assert event["seconds_in_zone"] == 0
        assert event["touch_count_today"] == 1
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_cold_start_unknown_origin_left_unclassified():
    ticker = "__LIE_COLDSTRT__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        # First-ever observation for this key is ALREADY inside the aura — no known entry side.
        await _publish(bus, ticker, _DAY1, 100.0, {"sma_9": 100.0})
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 99.0, {"sma_9": 100.0})  # exits below
        await asyncio.sleep(0.2)

        assert len(received) == 1  # nothing emitted for the cold-start observation itself
        event = received[0].payload
        assert event["status"] == "unclassified"
        assert event["observed_via"] == "cold_start_unknown_origin"
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_touch_count_increments_across_multiple_touches_same_day():
    ticker = "__LIE_COUNT__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        t = _DAY1
        for close in [99.0, 100.0, 99.0, 100.0, 99.0]:  # below, touch(1), rejected, touch(2), rejected
            await _publish(bus, ticker, t, close, {"sma_9": 100.0})
            t += timedelta(minutes=1)
        await asyncio.sleep(0.2)

        touch_counts = [e.payload["touch_count_today"] for e in received]
        assert touch_counts == [1, 1, 2, 2]  # holding(1), rejected(1), holding(2), rejected(2)
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_touch_count_resets_on_trading_day_rollover():
    ticker = "__LIE_ROLLOVER__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        # Day 1: one full touch/rejection cycle.
        await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0})
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0})
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 99.0, {"sma_9": 100.0})
        # Day 2: a fresh touch should read touch_count_today == 1, not 3.
        await _publish(bus, ticker, _DAY2, 100.0, {"sma_9": 100.0})
        await asyncio.sleep(0.2)

        assert len(received) == 3
        assert received[0].payload["touch_count_today"] == 1  # day 1 holding
        assert received[1].payload["touch_count_today"] == 1  # day 1 rejected
        assert received[2].payload["touch_count_today"] == 1  # day 2 holding — reset, not 2
        assert str(received[2].payload["trading_day"]) == "2026-08-11"
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_restart_survival_mid_touch():
    """
    Simulates a real restart WHILE a touch is actively holding: an earlier
    'process' persists the touch-start via a real LevelInteractionEngine,
    then a BRAND NEW instance — no in-memory state at all — sees the
    resolving candle and must classify it correctly against the persisted
    touch, not treat it as a fresh cold-start.
    """
    ticker = "__LIE_RESTART__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()

    try:
        engine_a = LevelInteractionEngine(bus, aura_pct=0.002)
        engine_a.start()
        try:
            await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0})
            await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0})  # touch starts
            await asyncio.sleep(0.2)
        finally:
            engine_a.stop()

        # Fresh instance — no in-memory state for this symbol at all.
        engine_b = LevelInteractionEngine(bus, aura_pct=0.002)
        engine_b.start()
        received: list = []
        bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))
        try:
            await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 99.0, {"sma_9": 100.0})  # resolves — rejected
            await asyncio.sleep(0.2)

            assert len(received) == 1
            assert received[0].payload["status"] == "rejected"  # correctly resolved against the PERSISTED touch
            assert received[0].payload["seconds_in_zone"] == 60
        finally:
            engine_b.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_multiple_level_keys_tracked_independently():
    """Reusability check: sma_9 and sma_20 in the SAME event must be
    tracked as fully independent state machines, with zero SMA-specific
    code involved — this is the generic "any key in `features`" design."""
    ticker = "__LIE_MULTI__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        # sma_9=100: close moves 80 -> 100, below -> touch (transition).
        # sma_20=200: close 80 and 100 are BOTH deep below 200's aura the whole
        # time (-60% then -50%) — genuinely no transition, the actual control
        # for "independent tracking" rather than an accidental second gap-through.
        await _publish(bus, ticker, _DAY1, 80.0, {"sma_9": 100.0, "sma_20": 200.0})
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0, "sma_20": 200.0})
        await asyncio.sleep(0.2)

        assert len(received) == 1  # only sma_9 transitioned — sma_20 stayed "below" throughout
        assert received[0].payload["level_key"] == "sma_9"
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_duplicate_features_updated_dropped():
    ticker = "__LIE_DUP__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    try:
        await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0})
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0})  # touch
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0})  # exact duplicate — must be dropped
        await asyncio.sleep(0.2)

        assert len(received) == 1  # not 2 — the duplicate never reached the state machine
    finally:
        engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)
