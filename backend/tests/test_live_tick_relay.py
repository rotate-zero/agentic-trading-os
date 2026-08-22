import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import PriceUpdated
from app.services.live_tick_relay import LiveTickRelay

_FAST_FLUSH = 0.05  # seconds — real flush_interval_seconds is 5.0 in production;
# tests use a tiny interval so they don't actually wait 5 real seconds each.


async def _emit(bus: EventBus, symbol: str, price: float, size: int, exchange_ts: datetime) -> None:
    await bus.publish(
        make_envelope(EventType.PRICE_UPDATED, PriceUpdated(price=price, size=size, exchange_ts=exchange_ts), symbol=symbol)
    )


@pytest.mark.asyncio
async def test_ticks_for_an_active_symbol_flush_as_one_throttled_snapshot():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.PRICE_SNAPSHOT, lambda env: received.append(env))

        relay = LiveTickRelay(bus, flush_interval_seconds=_FAST_FLUSH)
        relay.set_active_symbols(["NVDA"])
        relay.start()

        base = datetime(2026, 7, 25, 10, 30, 0, tzinfo=timezone.utc)
        await _emit(bus, "NVDA", 100.0, 10, base)
        await _emit(bus, "NVDA", 101.0, 5, base + timedelta(seconds=1))
        await _emit(bus, "NVDA", 99.5, 7, base + timedelta(seconds=2))
        await asyncio.sleep(_FAST_FLUSH * 3)  # let the worker apply ticks + one flush cycle run

        # Several ticks inside one flush window collapse into exactly one
        # PriceSnapshot — the throttling this whole feature exists for.
        assert len(received) == 1
        snap = received[0].payload
        assert snap["open"] == 100.0
        assert snap["high"] == 101.0
        assert snap["low"] == 99.5
        assert snap["close"] == 99.5
        assert snap["volume"] == 22
    finally:
        await relay.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_symbol_not_in_active_set_never_produces_a_snapshot():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.PRICE_SNAPSHOT, lambda env: received.append(env))

        relay = LiveTickRelay(bus, flush_interval_seconds=_FAST_FLUSH)
        relay.set_active_symbols(["NVDA"])  # AAPL deliberately not included
        relay.start()

        await _emit(bus, "AAPL", 200.0, 1, datetime.now(timezone.utc))
        await asyncio.sleep(_FAST_FLUSH * 3)

        assert received == []
    finally:
        await relay.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_quiet_symbol_is_not_republished_on_the_next_flush():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.PRICE_SNAPSHOT, lambda env: received.append(env))

        relay = LiveTickRelay(bus, flush_interval_seconds=_FAST_FLUSH)
        relay.set_active_symbols(["NVDA"])
        relay.start()

        await _emit(bus, "NVDA", 100.0, 10, datetime.now(timezone.utc))
        await asyncio.sleep(_FAST_FLUSH * 3)  # first flush — publishes once
        await asyncio.sleep(_FAST_FLUSH * 3)  # second flush — no new ticks since

        # Honest-state rule: a symbol with nothing new since the last flush
        # is skipped, never re-sent as a stale duplicate.
        assert len(received) == 1
    finally:
        await relay.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_minute_rollover_starts_a_fresh_bar_not_a_carried_over_one():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.PRICE_SNAPSHOT, lambda env: received.append(env))

        relay = LiveTickRelay(bus, flush_interval_seconds=_FAST_FLUSH)
        relay.set_active_symbols(["NVDA"])
        relay.start()

        base = datetime(2026, 7, 25, 10, 30, 0, tzinfo=timezone.utc)
        await _emit(bus, "NVDA", 100.0, 10, base)
        await asyncio.sleep(_FAST_FLUSH * 3)
        assert received[-1].payload["open"] == 100.0

        # Next minute: a fresh bar, NOT high/low carried over from the prior one.
        await _emit(bus, "NVDA", 105.0, 3, base + timedelta(minutes=1))
        await asyncio.sleep(_FAST_FLUSH * 3)

        assert len(received) == 2
        new_bar = received[-1].payload
        assert new_bar["open"] == 105.0
        assert new_bar["high"] == 105.0
        assert new_bar["low"] == 105.0
        assert new_bar["volume"] == 3  # not 13 — the old bar's volume must not carry over
    finally:
        await relay.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_set_active_symbols_rejects_more_than_the_max():
    bus = EventBus()
    await bus.start()
    try:
        relay = LiveTickRelay(bus, max_active_symbols=8, flush_interval_seconds=_FAST_FLUSH)
        with pytest.raises(ValueError):
            relay.set_active_symbols([f"SYM{i}" for i in range(9)])
        assert relay.get_active_symbols() == []  # rejected wholesale, not truncated-and-accepted
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_removing_a_symbol_drops_its_accumulated_bar():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.PRICE_SNAPSHOT, lambda env: received.append(env))

        relay = LiveTickRelay(bus, flush_interval_seconds=_FAST_FLUSH)
        relay.set_active_symbols(["NVDA", "AAPL"])
        relay.start()

        await _emit(bus, "NVDA", 100.0, 10, datetime.now(timezone.utc))
        # NVDA is removed from the active set BEFORE the first flush fires —
        # its dirty, half-built bar must not leak out as a snapshot anyway.
        relay.set_active_symbols(["AAPL"])
        await asyncio.sleep(_FAST_FLUSH * 3)

        assert received == []
        assert relay.get_active_symbols() == ["AAPL"]
    finally:
        await relay.stop()
        await bus.stop()
