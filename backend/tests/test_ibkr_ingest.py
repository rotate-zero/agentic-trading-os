import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.broker_adapters.base import Tick
from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventType
from app.services.ibkr_ingest import IBKRIngestBridge


class _FakeAdapter:
    """Minimal stand-in — IBKRIngestBridge only ever calls on_tick() on
    whatever adapter it's given, so a fake adapter that just remembers the
    callback is enough; no real IBKR connection needed for these tests."""

    def __init__(self) -> None:
        self._callback = None

    def on_tick(self, callback) -> None:
        self._callback = callback

    def emit(self, tick: Tick) -> None:
        assert self._callback is not None, "IBKRIngestBridge did not register a callback"
        self._callback(tick)


@pytest.mark.asyncio
async def test_ticks_within_same_minute_aggregate_into_one_bucket():
    bus = EventBus()
    await bus.start()
    try:
        received_candles: list = []
        bus.subscribe(EventType.CANDLE_CLOSED, lambda env: received_candles.append(env))

        adapter = _FakeAdapter()
        IBKRIngestBridge(adapter, bus)

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
        await bus.stop()


@pytest.mark.asyncio
async def test_every_tick_also_publishes_price_updated():
    bus = EventBus()
    await bus.start()
    try:
        received_ticks: list = []
        bus.subscribe(EventType.PRICE_UPDATED, lambda env: received_ticks.append(env))

        adapter = _FakeAdapter()
        IBKRIngestBridge(adapter, bus)

        adapter.emit(Tick(symbol="AAPL", price=200.0, size=1, exchange_ts=datetime.now(timezone.utc)))
        await asyncio.sleep(0.05)

        assert len(received_ticks) == 1
        assert received_ticks[0].symbol == "AAPL"
        assert received_ticks[0].payload["price"] == 200.0
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_multiple_symbols_bucket_independently():
    bus = EventBus()
    await bus.start()
    try:
        received_candles: list = []
        bus.subscribe(EventType.CANDLE_CLOSED, lambda env: received_candles.append(env))

        adapter = _FakeAdapter()
        IBKRIngestBridge(adapter, bus)

        base = datetime(2026, 7, 25, 10, 30, 0, tzinfo=timezone.utc)
        adapter.emit(Tick(symbol="NVDA", price=100.0, size=1, exchange_ts=base))
        adapter.emit(Tick(symbol="AAPL", price=200.0, size=1, exchange_ts=base))
        # Roll NVDA into the next minute; AAPL should stay open.
        adapter.emit(Tick(symbol="NVDA", price=101.0, size=1, exchange_ts=base + timedelta(minutes=1)))
        await asyncio.sleep(0.05)

        assert len(received_candles) == 1
        assert received_candles[0].symbol == "NVDA"
    finally:
        await bus.stop()
