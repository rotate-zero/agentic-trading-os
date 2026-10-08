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
        # A source-time regression inside the same minute remains part of its candle.
        adapter.emit(Tick(symbol="NVDA", price=99.5, size=7, exchange_ts=base + timedelta(seconds=10)))
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


@pytest.mark.asyncio
async def test_older_minute_tick_does_not_close_newer_bucket_or_repeat_candle():
    bus = EventBus()
    await bus.start()
    try:
        candles: list = []
        prices: list = []
        bus.subscribe(EventType.CANDLE_CLOSED, candles.append)
        bus.subscribe(EventType.PRICE_UPDATED, prices.append)
        adapter = _FakeAdapter()
        bridge = TickIngestBridge(adapter, bus)
        base = datetime(2026, 7, 25, 10, 30, tzinfo=timezone.utc)

        async def send(price: float, size: int, seconds: int) -> None:
            adapter.emit(Tick(symbol="NVDA", price=price, size=size,
                              exchange_ts=base + timedelta(seconds=seconds)))
            await asyncio.gather(*tuple(bridge._tick_tasks))
            await bus._normal_queue.join()

        await send(100.0, 10, 0)
        await send(110.0, 3, 60)  # closes 10:30 and opens 10:31
        await send(90.0, 7, 45)  # late 10:30 trade must not roll 10:31 backward
        await send(111.0, 4, 80)
        await bridge._flush_stale_buckets(base + timedelta(minutes=2))
        await bus._normal_queue.join()

        assert len(prices) == 4  # raw PriceUpdated delivery remains intact
        assert [(datetime.fromisoformat(item.payload["candle_ts"]), item.payload["volume"])
                for item in candles] == [(base, 10), (base + timedelta(minutes=1), 7)]
        assert candles[1].payload["open"] == 110.0
        assert candles[1].payload["close"] == 111.0
    finally:
        await bridge.aclose()
        await bus.stop()


@pytest.mark.asyncio
async def test_older_minute_tick_after_wall_flush_does_not_reopen_closed_bucket():
    bus = EventBus()
    await bus.start()
    try:
        candles: list = []
        bus.subscribe(EventType.CANDLE_CLOSED, candles.append)
        adapter = _FakeAdapter()
        bridge = TickIngestBridge(adapter, bus)
        base = datetime(2026, 7, 25, 10, 30, tzinfo=timezone.utc)

        adapter.emit(Tick(symbol="NVDA", price=100.0, size=10, exchange_ts=base))
        await asyncio.gather(*tuple(bridge._tick_tasks))
        await bridge._flush_stale_buckets(base + timedelta(minutes=1))
        adapter.emit(Tick(symbol="NVDA", price=90.0, size=7,
                          exchange_ts=base + timedelta(seconds=45)))
        await asyncio.gather(*tuple(bridge._tick_tasks))
        await bridge._flush_stale_buckets(base + timedelta(minutes=2))
        await bus._normal_queue.join()

        assert [(datetime.fromisoformat(item.payload["candle_ts"]), item.payload["volume"])
                for item in candles] == [(base, 10)]
    finally:
        await bridge.aclose()
        await bus.stop()


@pytest.mark.asyncio
async def test_older_tick_during_paused_candle_publish_keeps_new_bucket_intact():
    class PausedCandleBus:
        def __init__(self) -> None:
            self.events: list = []
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def publish(self, envelope) -> None:
            if envelope.event_type is EventType.CANDLE_CLOSED and not self.release.is_set():
                self.entered.set()
                await self.release.wait()
            self.events.append(envelope)

    bus = PausedCandleBus()
    adapter = _FakeAdapter()
    bridge = TickIngestBridge(adapter, bus)
    base = datetime(2026, 7, 25, 10, 30, tzinfo=timezone.utc)
    try:
        adapter.emit(Tick(symbol="NVDA", price=100.0, size=10, exchange_ts=base))
        await asyncio.gather(*tuple(bridge._tick_tasks))

        adapter.emit(Tick(symbol="NVDA", price=110.0, size=3,
                          exchange_ts=base + timedelta(minutes=1)))
        await asyncio.wait_for(bus.entered.wait(), 1)
        adapter.emit(Tick(symbol="NVDA", price=90.0, size=7,
                          exchange_ts=base + timedelta(seconds=45)))
        adapter.emit(Tick(symbol="NVDA", price=111.0, size=4,
                          exchange_ts=base + timedelta(minutes=1, seconds=20)))
        bus.release.set()
        await asyncio.gather(*tuple(bridge._tick_tasks))
        await bridge._flush_stale_buckets(base + timedelta(minutes=2))

        candles = [item.payload for item in bus.events
                   if item.event_type is EventType.CANDLE_CLOSED]
        assert [(datetime.fromisoformat(item["candle_ts"]), item["volume"])
                for item in candles] == [(base, 10), (base + timedelta(minutes=1), 7)]
        assert candles[1]["open"] == 110.0
        assert candles[1]["close"] == 111.0
        assert sum(item.event_type is EventType.PRICE_UPDATED for item in bus.events) == 4
    finally:
        await bridge.aclose()


@pytest.mark.asyncio
async def test_future_minute_waits_for_paused_prior_candle_close():
    class FirstCloseGateBus:
        def __init__(self) -> None:
            self.events: list = []
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.pause_next = True

        async def publish(self, envelope) -> None:
            if envelope.event_type is EventType.CANDLE_CLOSED and self.pause_next:
                self.pause_next = False
                self.entered.set()
                await self.release.wait()
            self.events.append(envelope)

    bus = FirstCloseGateBus()
    adapter = _FakeAdapter()
    bridge = TickIngestBridge(adapter, bus)
    base = datetime(2026, 7, 25, 10, 30, tzinfo=timezone.utc)
    try:
        adapter.emit(Tick(symbol="NVDA", price=100.0, size=1, exchange_ts=base))
        await asyncio.gather(*tuple(bridge._tick_tasks))
        adapter.emit(Tick(symbol="NVDA", price=110.0, size=1,
                          exchange_ts=base + timedelta(minutes=1)))
        await asyncio.wait_for(bus.entered.wait(), 1)
        adapter.emit(Tick(symbol="NVDA", price=120.0, size=1,
                          exchange_ts=base + timedelta(minutes=2)))
        await asyncio.sleep(0)
        assert not any(item.event_type is EventType.CANDLE_CLOSED for item in bus.events)

        bus.release.set()
        await asyncio.gather(*tuple(bridge._tick_tasks))
        await bridge._flush_stale_buckets(base + timedelta(minutes=3))

        assert [datetime.fromisoformat(item.payload["candle_ts"])
                for item in bus.events if item.event_type is EventType.CANDLE_CLOSED] == [
                    base, base + timedelta(minutes=1), base + timedelta(minutes=2)]
    finally:
        await bridge.aclose()
