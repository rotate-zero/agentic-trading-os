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
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet
from app.trading_intelligence.level_interaction_engine import (
    LevelInteractionEngine,
    _is_sma_ema_slope_key,
    classify_zone,
)

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


async def _publish(
    bus: EventBus, symbol: str, candle_ts: datetime, close: float, features: dict[str, float], daily_levels: list[dict] | None = None
) -> None:
    payload = FeatureSet(timeframe="1m", candle_ts=candle_ts, close=close, features=features, daily_levels=daily_levels or [])
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


def test_is_sma_ema_slope_key_matches_exactly_the_four_published_suffixes():
    # sma_slope()/ema_slope() (indicators/sma.py, indicators/ema.py,
    # confirmed decision #83) publish exactly these four suffixes per
    # period — pinning all four, not just the three ("slope",
    # "slope_angle", "r2") the original bug report named. "slope_pct"
    # has the identical problem and was missing from the report.
    for key in (
        "sma_9_slope", "sma_9_r2", "sma_9_slope_pct", "sma_9_slope_angle",
        "ema_20_slope", "ema_20_r2", "ema_20_slope_pct", "ema_20_slope_angle",
    ):
        assert _is_sma_ema_slope_key(key), key


def test_is_sma_ema_slope_key_false_for_the_base_levels_and_other_families():
    # sma_9/ema_20 themselves are real levels — still tracked, never
    # excluded. Regression/KAMA share the identical "_slope"/"_r2"
    # suffix shape but are deliberately out of scope (decision #85).
    for key in ("sma_9", "ema_20", "vwap", "pdh", "cam_r1", "regression_9_slope", "regression_9_r2", "kama_9_slope"):
        assert not _is_sma_ema_slope_key(key), key


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
        await engine.stop()
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
        await engine.stop()
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
        await engine.stop()
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
        await engine.stop()
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
        await engine.stop()
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
        await engine.stop()
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
            await engine_a.stop()
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
            await engine_b.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_sma_ema_slope_family_keys_excluded_from_level_tracking():
    """Confirmed decision #85 — before this fix, _process_one walked
    every features.items() key unconditionally, so sma_9_slope (a $/bar
    rate), sma_9_r2 (a 0-1 fit-quality score), sma_9_slope_pct (%/bar),
    sma_9_slope_angle (degrees), and their ema_ equivalents were getting
    real zone/touch classifications against `close` as if they were
    price levels.

    Deliberately sets every slope-family value EQUAL to close — exactly
    the condition that would register a cold-start "inside_aura" touch
    (and an event on any FUTURE transition) if these were still being
    tracked — so this proves the exclusion is real, not just untested by
    accident because the values happened to already be far from close.
    """
    ticker = "__LIE_SLOPEX__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    close = 100.0
    features = {
        "sma_9": 150.0,  # a real level, deliberately far from close — the control
        "sma_9_slope": close,
        "sma_9_r2": close,
        "sma_9_slope_pct": close,
        "sma_9_slope_angle": close,
        "ema_20_slope": close,
        "ema_20_r2": close,
        "ema_20_slope_pct": close,
        "ema_20_slope_angle": close,
    }

    try:
        await _publish(bus, ticker, _DAY1, close, features)
        await asyncio.sleep(0.2)

        # No event for any slope-family key — not even a cold-start
        # "unclassified" one — despite every one of them numerically
        # equal to close.
        assert received == []

        # Only "sma_9" (the real, still-tracked level) ever entered this
        # engine's in-memory state — the eight slope-family keys never
        # got so much as a first-ever cold-start observation recorded.
        snapshot = engine.get_snapshot(ticker)
        keys_seen = set(snapshot.get(ticker, {}).get("1m", {}).keys())
        assert keys_seen == {"sma_9"}

        # A second candle that WOULD flip every slope-family key's
        # classify_zone() result if it were still being evaluated
        # (close moves from equal-to-level to way outside any aura) —
        # still produces nothing for any of them. The control DOES fire
        # here (close=1000 is now far from sma_9=150 — a legitimate,
        # expected gap-through for the one real level), which is exactly
        # why this asserts per-level-key rather than an empty list from
        # here on: proves the slope-family keys stayed silent even while
        # real tracking was actively happening in the same event.
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), close * 10, features)
        await asyncio.sleep(0.2)
        assert {e.payload["level_key"] for e in received} == {"sma_9"}
    finally:
        await engine.stop()
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
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_get_snapshot_shows_live_holding_state():
    """
    Checks the two things get_snapshot() computes fresh at call time
    rather than reading from persisted state — seconds_in_zone and
    distance_pct while a touch is still open — by publishing a SECOND
    candle while still holding, with price drifting slightly closer to
    the anchor, and confirming distance_pct actually moves to reflect
    that (not the stale value from touch start).
    """
    ticker = "__LIE_SNAP__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()

    try:
        await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0})  # below — not holding yet
        await asyncio.sleep(0.1)
        snap = engine.get_snapshot(ticker)
        below = snap[ticker]["1m"]["sma_9"]
        assert below["zone"] == "below"
        assert "holding" not in below
        assert below["distance_pct"] == -1.0  # confirmed decision #49 — present even outside an active touch

        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0})  # touch — anchor=100.0
        await asyncio.sleep(0.1)
        snap = engine.get_snapshot(ticker)
        entry = snap[ticker]["1m"]["sma_9"]
        assert entry["distance_pct"] == 0.0  # top-level now, not nested under "holding"
        holding = entry["holding"]
        assert holding["anchor_price"] == 100.0
        assert holding["entered_from"] == "below"
        assert "distance_pct" not in holding  # moved up a level — no longer duplicated here

        await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 100.15, {"sma_9": 100.0})  # still inside aura, drifted up
        await asyncio.sleep(0.1)
        snap = engine.get_snapshot(ticker)
        entry = snap[ticker]["1m"]["sma_9"]
        assert entry["distance_pct"] == 0.15  # live — reflects the LATEST close, not the stale touch-start value
        assert entry["holding"]["anchor_price"] == 100.0  # anchor itself never moves during a hold

        assert engine.get_snapshot(symbol="__LIE_NEVER_SEEN__") == {}
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_get_snapshot_steady_state_distance_uses_live_level_not_a_frozen_one():
    """
    Confirmed decision #49: outside an active touch, distance_pct tracks
    the CURRENT live level value, not whatever it was when this zone was
    entered — deliberately different from the holding case (which stays
    anchored). Proven by drifting the level value itself between two
    candles that both stay "above," and confirming distance_pct reflects
    the NEW level, not the one from zone entry.
    """
    ticker = "__LIE_STEADY__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()

    try:
        await _publish(bus, ticker, _DAY1, 110.0, {"sma_9": 100.0})  # first observation — above, cold start
        await asyncio.sleep(0.1)
        snap = engine.get_snapshot(ticker)
        entry = snap[ticker]["1m"]["sma_9"]
        assert entry["zone"] == "above"
        assert entry["distance_pct"] == 10.0  # (110-100)/100*100
        assert isinstance(entry["seconds_in_zone"], int) and entry["seconds_in_zone"] >= 0

        # Level itself drifts up; price stays above the whole time — no zone transition.
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 112.0, {"sma_9": 105.0})
        await asyncio.sleep(0.1)
        snap = engine.get_snapshot(ticker)
        entry = snap[ticker]["1m"]["sma_9"]
        assert entry["zone"] == "above"  # confirmed no transition — this is the steady-state path, not a new touch
        assert round(entry["distance_pct"], 3) == round((112.0 - 105.0) / 105.0 * 100, 3)  # tracks the NEW level
    finally:
        await engine.stop()
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
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


# --- Daily Levels (Stage 3, confirmed decision #64) --------------------------
#
# The design doc's own prescribed proof (§9's Stage 3 checkbox, written
# before any of this existed): "a test that publishes daily_levels and
# confirms touch/reject/conquer tracking works against a level_id the
# engine was never told about by name." These tests deliberately use a
# level_id in Stage 2's real format ("TICKER-DL-<n>") specifically so
# nothing here could be mistaken for the engine special-casing a "DL"
# pattern — it's tracked purely because _process_one now walks the
# daily_levels list at all, identically to every other level_key.


@pytest.mark.asyncio
async def test_daily_level_gets_touch_then_rejected_tracking():
    """The core Stage 3 proof — mirrors test_touch_then_rejected() above
    exactly, but through daily_levels instead of features, confirming
    the SAME state machine, SAME event shape, SAME everything applies —
    only the source of the level_key differs."""
    ticker = "__LIE_DLREJ__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    daily_level = {"level_id": f"{ticker}-DL-42", "price": 100.0, "strength": 3, "distinct_candle_count": 2}

    try:
        await _publish(bus, ticker, _DAY1, 99.0, {}, daily_levels=[daily_level])
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {}, daily_levels=[daily_level])  # touch
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 99.0, {}, daily_levels=[daily_level])   # rejected
        await asyncio.sleep(0.2)

        assert len(received) == 2
        holding, rejected = received[0].payload, received[1].payload
        assert holding["level_key"] == f"{ticker}-DL-42"
        assert holding["status"] == "holding" and holding["touch_count_today"] == 1
        assert rejected["level_key"] == f"{ticker}-DL-42"
        assert rejected["status"] == "rejected"
        assert rejected["observed_via"] == "dwell"
        assert rejected["distance_pct"] == -1.0
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_daily_level_conquered():
    """Same shape as test_touch_then_conquered() above, through
    daily_levels — confirms BOTH resolution directions work, not just
    rejected."""
    ticker = "__LIE_DLCNQ__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    daily_level = {"level_id": f"{ticker}-DL-7", "price": 100.0, "strength": 5, "distinct_candle_count": 3}

    try:
        await _publish(bus, ticker, _DAY1, 99.0, {}, daily_levels=[daily_level])
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {}, daily_levels=[daily_level])   # touch, from below
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 101.0, {}, daily_levels=[daily_level])   # exits ABOVE — conquered
        await asyncio.sleep(0.2)

        assert len(received) == 2
        assert received[1].payload["status"] == "conquered"
        assert received[1].payload["level_key"] == f"{ticker}-DL-7"
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_daily_levels_and_scalar_features_tracked_independently_in_the_same_event():
    """A single FeaturesUpdated carrying BOTH a scalar feature (sma_9)
    AND a daily_levels entry — proves the two loops in _process_one
    don't interfere with each other, and per-level isolation (module
    docstring) holds across the two different iteration sources, not
    just within one of them."""
    ticker = "__LIE_DLMIX__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()
    received: list = []
    bus.subscribe(EventType.LEVEL_INTERACTION_CHANGED, lambda e: received.append(e))

    daily_level = {"level_id": f"{ticker}-DL-3", "price": 200.0, "strength": 2, "distinct_candle_count": 2}

    try:
        # Both levels start "below" their respective values (close 99 <
        # sma 100; close 99 is nowhere near daily-level 200, also "below").
        await _publish(bus, ticker, _DAY1, 99.0, {"sma_9": 100.0}, daily_levels=[daily_level])
        # Touch the SMA only — the daily level (at 200) stays "below", untouched.
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=1), 100.0, {"sma_9": 100.0}, daily_levels=[daily_level])
        await asyncio.sleep(0.2)

        assert len(received) == 1
        assert received[0].payload["level_key"] == "sma_9"  # the daily level produced NO event — correctly untouched
        assert received[0].payload["status"] == "holding"

        # Now touch the daily level too. Moving close all the way to 200
        # legitimately drags sma_9 (aura around 100) into "conquered" as
        # a real, separate side effect — not a test bug to work around,
        # just why this asserts per-level-key rather than an exact total
        # count from here on: both loops in _process_one fired, on their
        # own genuinely different transitions, independently of each other.
        await _publish(bus, ticker, _DAY1 + timedelta(minutes=2), 200.0, {"sma_9": 100.0}, daily_levels=[daily_level])
        await asyncio.sleep(0.2)

        by_key = {e.payload["level_key"]: e.payload for e in received}
        assert by_key[f"{ticker}-DL-3"]["status"] == "holding"
        assert by_key["sma_9"]["status"] == "conquered"
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_get_snapshot_includes_daily_levels_with_zero_special_casing():
    """get_snapshot() (module docstring: {symbol: {timeframe: {level_key:
    {...}}}}) needed NO code changes for Stage 3 — this test exists to
    prove that claim directly rather than leave it asserted only in a
    comment. A daily level's level_id shows up as just another key,
    identical in shape to sma_9's entry."""
    ticker = "__LIE_DLSNAP__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)
    engine.start()

    daily_level = {"level_id": f"{ticker}-DL-9", "price": 50.0, "strength": 4, "distinct_candle_count": 2}

    try:
        await _publish(bus, ticker, _DAY1, 50.0, {}, daily_levels=[daily_level])  # cold-start-unknown-origin, inside_aura
        await asyncio.sleep(0.2)

        snapshot = engine.get_snapshot(ticker)
        entry = snapshot[ticker]["1m"][f"{ticker}-DL-9"]
        assert entry["zone"] == "inside_aura"
        assert entry["touch_count_today"] == 1
        assert "holding" in entry
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


# --- stop() shutdown-race regression (confirmed decision #84) ---------------


@pytest.mark.asyncio
async def test_stop_waits_for_an_in_flight_persist_before_returning():
    """
    Reproduces the real ForeignKeyViolation-on-teardown race behind
    decision #84, deterministically rather than by luck of scheduling:
    `_persist_state` is wrapped with an artificial delay so a write is
    GUARANTEED to still be running in the executor thread at the exact
    moment `stop()` is called (no `asyncio.sleep` between publish and
    stop — that gap is precisely what earlier runs of this suite got
    away with often enough to look correct).

    Two things prove the fix, not just the absence of a crash:
    - `stop()` itself must take at least as long as the artificial delay
      — if it returned quickly, that would mean it went back to
      cancelling rather than actually waiting for the in-flight thread.
    - Deleting the symbol row immediately after `stop()` returns (same
      teardown shape `_clean_test_symbol` uses in this file and in
      test_intelligence_routes.py) must not raise — proving the write
      that referenced `symbol_id` had genuinely finished, not just that
      timing happened to work out this run.

    Feeds `engine._queue` directly rather than publishing through the Bus
    — found necessary while writing this test, not assumed: `bus.publish`
    is fire-and-forget onto the BUS's own queue, dispatched to
    `_on_features_updated` (which is what actually reaches this engine's
    OWN queue) by the bus's separate `_consume` task on a later loop
    iteration. Publishing and immediately calling `stop()` raced the
    sentinel into this engine's queue AHEAD of the real item often enough
    to make the very first version of this test flaky against the FIXED
    code — the sentinel got processed first and the worker exited before
    the real item was ever pulled off the queue at all. Queuing directly
    removes that unrelated race, isolating this test to the one thing
    it's meant to prove: cancellation-vs-drain behavior once an item is
    genuinely in this engine's own queue.
    """
    ticker = "__LIE_STOPRACE__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = LevelInteractionEngine(bus, aura_pct=0.002)

    delay_seconds = 0.3
    original_persist_state = engine._persist_state

    def _slow_persist_state(*args, **kwargs):
        time.sleep(delay_seconds)  # runs inside the executor thread — a real blocking delay, not a mock
        return original_persist_state(*args, **kwargs)

    engine._persist_state = _slow_persist_state  # type: ignore[method-assign]
    engine.start()

    try:
        # Fed straight onto the engine's own queue (see docstring above) —
        # same dict shape _on_features_updated itself would have produced.
        # First-ever observation for this symbol, so the cold-start branch
        # in _process_level calls _persist_state unconditionally before
        # returning (see its own "brand new" comment) — one item is
        # enough to guarantee a write gets scheduled.
        engine._queue.put_nowait(
            {"symbol": ticker, "timeframe": "1m", "close": 100.0, "candle_ts": _DAY1, "features": {"sma_9": 100.0}}
        )
        # Give the worker task a real chance to dequeue the item and get
        # asyncio.to_thread's executor submission actually running (into
        # the artificial 0.3s sleep) before stop() is called — a bare
        # `asyncio.sleep(0)` risks yielding only once, before the thread
        # pool has genuinely started the callable.
        await asyncio.sleep(0.05)

        t0 = time.monotonic()
        await engine.stop()  # nothing queued after this — the one item is still mid-persist
        elapsed = time.monotonic() - t0

        # A generous floor, not delay_seconds itself: ~0.05s of the
        # artificial delay was already spent during the sleep above,
        # before stop() was even called. Anything comfortably above
        # "basically instant" (the old buggy behavior measured ~0.0001s
        # in this same scenario) is a clean pass/fail signal without
        # being sensitive to exact scheduling overhead.
        assert elapsed >= 0.15, (
            f"stop() returned after {elapsed:.3f}s — expected it to block for close to the "
            f"remaining {delay_seconds}s artificial persist delay; it isn't actually waiting "
            "for in-flight work anymore"
        )

        # The real proof: this must not raise a ForeignKeyViolation. If
        # stop() let the write land after this DELETE instead of before
        # it, this call reproduces decision #84's original bug exactly.
        _clean_test_symbol(ticker)
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)
