import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.broker_adapters.base import Tick
from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventType
from app.services.tick_ingest import TickIngestBridge


class _FakeAdapter:
    """Minimal stand-in — TickIngestBridge only ever calls on_tick() on
    whatever provider it's given, so a fake provider that just remembers
    the callback is enough; no real connection to any provider needed."""

    def __init__(self) -> None:
        self._callback = None

    def on_tick(self, callback) -> None:
        self._callback = callback

    def emit(self, tick: Tick) -> None:
        assert self._callback is not None, "TickIngestBridge did not register a callback"
        self._callback(tick)


@pytest.mark.asyncio
async def test_ticks_within_same_minute_aggregate_into_one_bucket():
    bus = EventBus()
    await bus.start()
    try:
        received_candles: list = []
        bus.subscribe(EventType.CANDLE_CLOSED, lambda env: received_candles.append(env))

        adapter = _FakeAdapter()
        bridge = TickIngestBridge(adapter, bus)

        base = datetime(2026, 7, 25, 10, 30, 0, tzinfo=timezone.utc)
        adapter.emit(Tick(symbol="NVDA", price=100.0, size=10, exchange_ts=base))
        adapter.emit(Tick(symbol="NVDA", price=101.0, size=5, exchange_ts=base + timedelta(seconds=20)))
        adapter.emit(Tick(symbol="NVDA", price=99.5, size=7, exchange_ts=base + timedelta(seconds=40)))
        await asyncio.sleep(0.05)

        assert received_candles == []  # no minute rollover yet -> nothing closed

        # Next minute arrives -> the previous bucket finalizes and publishes.
        adapter.emit(Tick(symbol="NVDA", price=100.5, size=3, exchange_ts=base + timedelta(minutes=1)))
        await asyncio.sleep(0.05)

        assert len(received_candles) == 1
        candle = received_candles[0].payload
        assert candle["open"] == 100.0
        assert candle["high"] == 101.0
        assert candle["low"] == 99.5
        assert candle["close"] == 99.5  # last tick before rollover
        assert candle["volume"] == 22  # 10 + 5 + 7
    finally:
        bridge.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_every_tick_also_publishes_price_updated():
    bus = EventBus()
    await bus.start()
    try:
        received_ticks: list = []
        bus.subscribe(EventType.PRICE_UPDATED, lambda env: received_ticks.append(env))

        adapter = _FakeAdapter()
        bridge = TickIngestBridge(adapter, bus)

        adapter.emit(Tick(symbol="AAPL", price=200.0, size=1, exchange_ts=datetime.now(timezone.utc)))
        await asyncio.sleep(0.05)

        assert len(received_ticks) == 1
        assert received_ticks[0].symbol == "AAPL"
        assert received_ticks[0].payload["price"] == 200.0
    finally:
        bridge.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_multiple_symbols_bucket_independently():
    bus = EventBus()
    await bus.start()
    try:
        received_candles: list = []
        bus.subscribe(EventType.CANDLE_CLOSED, lambda env: received_candles.append(env))

        adapter = _FakeAdapter()
        bridge = TickIngestBridge(adapter, bus)

        base = datetime(2026, 7, 25, 10, 30, 0, tzinfo=timezone.utc)
        adapter.emit(Tick(symbol="NVDA", price=100.0, size=1, exchange_ts=base))
        adapter.emit(Tick(symbol="AAPL", price=200.0, size=1, exchange_ts=base))
        # Roll NVDA into the next minute; AAPL should stay open.
        adapter.emit(Tick(symbol="NVDA", price=101.0, size=1, exchange_ts=base + timedelta(minutes=1)))
        await asyncio.sleep(0.05)

        assert len(received_candles) == 1
        assert received_candles[0].symbol == "NVDA"
    finally:
        bridge.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_stale_bucket_closes_on_wall_clock_even_without_a_new_tick():
    """Regression test for the reported bug: a candle used to only close
    once a tick for the NEXT minute happened to arrive — on a quiet moment
    that could be tens of seconds late. This drives _flush_stale_buckets
    directly with a controlled 'now' (instead of sleeping for a real wall-
    clock minute) to prove the close no longer depends on a new tick ever
    showing up."""
    bus = EventBus()
    await bus.start()
    try:
        received_candles: list = []
        bus.subscribe(EventType.CANDLE_CLOSED, lambda env: received_candles.append(env))

        adapter = _FakeAdapter()
        bridge = TickIngestBridge(adapter, bus)

        base = datetime(2026, 7, 25, 9, 34, 0, tzinfo=timezone.utc)
        adapter.emit(Tick(symbol="NVDA", price=100.0, size=10, exchange_ts=base))
        await asyncio.sleep(0.05)

        assert received_candles == []  # nothing closed yet — no new tick arrived

        # Simulate the wall clock reaching 09:35:42 (the exact reported
        # symptom: 42 seconds into the next minute) with still no new tick.
        await bridge._flush_stale_buckets(base + timedelta(minutes=1, seconds=42))
        await asyncio.sleep(0.05)  # EventBus dispatches subscribers off a queue, not inline with publish()

        assert len(received_candles) == 1
        candle = received_candles[0].payload
        assert candle["open"] == 100.0
        assert candle["close"] == 100.0
        assert candle["volume"] == 10

        # A late tick for the now-closed minute starts a fresh bucket
        # rather than raising — the old entry is gone, not stale-referenced.
        adapter.emit(Tick(symbol="NVDA", price=102.0, size=2, exchange_ts=base + timedelta(minutes=1, seconds=50)))
        await asyncio.sleep(0.05)
        assert len(received_candles) == 1  # still just the one close
    finally:
        bridge.stop()
        await bus.stop()
