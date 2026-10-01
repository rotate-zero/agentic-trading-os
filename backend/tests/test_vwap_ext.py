"""
Extended VWAP tests — docs/architecture/premarket-accumulator-design.md.
Same fixtures/helpers as tests/test_feature_engine.py's own Tier 5 (VWAP,
confirmed decision #53), imported from there rather than duplicated —
this file is meant to sit alongside that one, not replace or fold into
it (vwap_ext is additive; the existing vwap tests are untouched and
still the authority on `vwap`'s own behavior).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope  # noqa: F401 — re-exported for parity with test_feature_engine.py's imports, not directly used here
from app.feature_engine.engine import FeatureEngine
from app.schemas.events.envelope import EventType
from app.services.candle_recorder import CandleRecorder
from tests.test_feature_engine import (
    _clean_test_symbol,
    _db_available,
    _et,
    _publish_candle,
    _wait_until,
    _wait_until_candles_persisted,
)


async def _wait_for_features_updated(received: list, expected_count: int, *, what: str, timeout: float = 5.0) -> None:
    """Bounded wait until `received` holds at least `expected_count` FeaturesUpdated events.

    Replaces the fixed `asyncio.sleep(0.1-0.3)` these tests used to guess how long
    EventBus -> FeatureEngine's serial worker (and its asyncio.to_thread compute) takes.
    Wraps the shared `_wait_until` so a timeout reports what was actually received
    (symbol / timeframe / candle_ts) instead of a bare 'condition not met'. This only
    waits for the events to ARRIVE; each test still asserts the exact count and every
    VWAP value afterwards, so a wait can never mask an incomplete or wrong result.
    """
    try:
        await _wait_until(lambda: len(received) >= expected_count, timeout=timeout, description=what)
    except AssertionError as exc:
        seen = [(e.symbol, e.payload["timeframe"], str(e.payload["candle_ts"])) for e in received]
        raise AssertionError(f"{exc} — expected >= {expected_count} FeaturesUpdated, received {len(seen)}: {seen}") from exc


@pytest.mark.asyncio
async def test_vwap_ext_present_during_premarket_unlike_vwap():
    """The one behavioral difference vwap_ext exists for: `vwap` excludes
    pre-market entirely (test_vwap_absent_pre_market_present_once_regular_session_opens),
    `vwap_ext` does not."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, "__TEST_FE_VWAPEXT_PM__", _et(2026, 8, 11, 8, 0), 50.0)  # pre-market
        await _wait_for_features_updated(received, 1, what="the pre-market candle's FeaturesUpdated event")

        assert len(received) == 1
        features = received[0].payload["features"]
        assert "vwap" not in features  # unchanged existing behavior
        assert features["vwap_ext"] == 50.0  # present where `vwap` is absent
        assert features["session_volume_ext"] == 10.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_vwap_ext_continues_across_the_930_boundary_without_resetting():
    """The core claim of the whole design: unlike `vwap`, which starts
    fresh at 9:30, `vwap_ext` carries pre-market's contribution INTO the
    regular session — this is what actually closes the platform-VWAP-
    discrepancy gap."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        ticker = "__TEST_FE_VWAPEXT_CONT__"
        await _publish_candle(bus, ticker, _et(2026, 8, 11, 8, 0), 50.0)  # pre-market
        await _publish_candle(bus, ticker, _et(2026, 8, 11, 9, 30), 100.0)  # regular open
        await _wait_for_features_updated(received, 2, what="two FeaturesUpdated events (pre-market bar, then the 9:30 bar)")

        assert len(received) == 2
        # `vwap` (regular-session-only) at the open == just its own bar.
        assert received[1].payload["features"]["vwap"] == 100.0
        # `vwap_ext` at the SAME candle == mean(50, 100) — pre-market's
        # bar is still in the running total. This divergence, at the
        # exact moment `vwap` resets to a single bar, is precisely the
        # discrepancy against other platforms this was built to close.
        assert received[1].payload["features"]["vwap_ext"] == pytest.approx(75.0)
        assert received[1].payload["features"]["session_volume_ext"] == 20.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_vwap_ext_resets_at_next_trading_day_not_at_930_boundary():
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        ticker = "__TEST_FE_VWAPEXT_RST__"
        await _publish_candle(bus, ticker, _et(2026, 8, 11, 8, 0), 50.0)  # day 1 pre-market
        await _publish_candle(bus, ticker, _et(2026, 8, 11, 9, 30), 100.0)  # day 1 regular open
        await _publish_candle(bus, ticker, _et(2026, 8, 12, 8, 0), 300.0)  # day 2 pre-market — must NOT blend with day 1
        await _wait_for_features_updated(received, 3, what="three FeaturesUpdated events (day 1 pre-market, day 1 open, day 2 pre-market)")

        assert len(received) == 3
        assert received[2].payload["features"]["vwap_ext"] == 300.0  # not mean(50, 100, 300)
        assert received[2].payload["features"]["session_volume_ext"] == 10.0  # not 30.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_vwap_ext_absent_after_hours_same_as_vwap_not_frozen_like_premarket_hl():
    """Deliberately does NOT freeze the way pmh/pml do — vwap_ext is
    meant to be read as a live line during trading hours, same posture
    `vwap` already has, not a frozen end-of-window reference."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        ticker = "__TEST_FE_VWAPEXT_AH__"
        await _publish_candle(bus, ticker, _et(2026, 8, 11, 9, 30), 100.0)  # regular session
        await _publish_candle(bus, ticker, _et(2026, 8, 11, 16, 30), 999.0)  # after-hours
        await _wait_for_features_updated(received, 2, what="two FeaturesUpdated events (regular-session bar, then the after-hours bar)")

        assert len(received) == 2
        assert "vwap_ext" in received[0].payload["features"]
        assert "vwap_ext" not in received[1].payload["features"]  # after-hours: absent, not frozen at 100.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_vwap_ext_is_identical_across_1m_and_5m_featuresets_on_the_same_close():
    """Same architectural invariant as `vwap` itself — computed once
    from 1m bars, attached identically to every timeframe's FeatureSet."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base = _et(2026, 8, 11, 9, 30)
        for i in range(5):
            await _publish_candle(bus, "__TEST_FE_VWAPEXT_5M__", base + timedelta(minutes=i), 100.0 + i)
        # Bounded wait for the exact FeatureSets this test asserts on (five 1m + the one
        # 5m bucket-close set), not a fixed guess at how long the serial worker takes.
        await _wait_until(
            lambda: sum(e.payload["timeframe"] == "1m" for e in received) >= 5
            and any(e.payload["timeframe"] == "5m" for e in received),
            description="five 1m FeaturesUpdated events plus the 5m bucket-close event",
        )

        by_timeframe = {e.payload["timeframe"]: e.payload["features"]["vwap_ext"] for e in received}
        assert set(by_timeframe) == {"1m", "5m"}
        assert by_timeframe["1m"] == by_timeframe["5m"] == pytest.approx(sum(100.0 + i for i in range(5)) / 5)
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_vwap_ext_backfills_pre_market_history_on_cold_start():
    """Same 'simulate a real restart' shape as test_vwap_backfills_from_persisted_history_on_cold_start
    — a pre-market candle persisted via a real CandleRecorder, then a
    BRAND NEW FeatureEngine's vwap_ext must include it on the very first
    regular-session bar it computes, not just that bar alone."""
    ticker = "__FEVWAPXCOLD__"  # 15 chars — symbols.ticker is varchar(16)
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()

    try:
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            await _publish_candle(bus, ticker, _et(2026, 8, 11, 8, 0), 50.0)  # pre-market, persisted
            # Wait for the actual persisted row before the recorder is stopped and the
            # fresh engine starts — a missing row would make the backfill assertion below
            # fail for the wrong reason (or pass-by-accident on a no-backfill engine).
            await _wait_until_candles_persisted(ticker, expected_count=1)
        finally:
            await recorder.stop()

        engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
        engine.start()
        received: list = []
        bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

        try:
            await _publish_candle(bus, ticker, _et(2026, 8, 11, 9, 30), 150.0)  # this engine's first observed bar
            # Wait for the fresh engine's cold-start backfill + compute to publish its
            # FeaturesUpdated, then assert on that exact event below.
            await _wait_for_features_updated(received, 1, what=f"the fresh engine's FeaturesUpdated event for {ticker}")

            assert len(received) == 1
            # mean(50, 150) — NOT just 150.0, which is what no backfill would wrongly produce.
            assert received[0].payload["features"]["vwap_ext"] == pytest.approx(100.0)
        finally:
            await engine.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)
