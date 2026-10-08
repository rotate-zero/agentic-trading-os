"""Retired TickIngestBridge isolation (task retired-tick-bridge-isolation).

All providers, sockets and buses here are controlled fakes: no network, no
real credentials, no database. Tests tagged [baseline-failing] exercise only
API that already existed before this task (stop(), take_over_streaming(),
clear_streaming_provider(), emit) so they can be replayed against the
pre-change source and shown to FAIL there for behavioural reasons; the rest
cover the new lifecycle surface (state, aclose(), settle_retired_bridges()).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
import websockets

from app.broker_adapters.base import Tick
from app.broker_adapters.finnhub_provider import FinnhubAdapter
from app.schemas.events.envelope import EventType
from app.services import broker_registry
from app.services.tick_ingest import BridgeState, TickIngestBridge

T0 = datetime(2026, 7, 25, 10, 30, 0, tzinfo=timezone.utc)


class _Provider:
    """List-based callbacks, like every real adapter (registrations append)."""

    def __init__(self, name: str, *, removable: bool = False) -> None:
        self.name = name
        self.callbacks: list = []
        self.disconnected = False
        self.fail_disconnect = False
        self.removed: list = []
        if removable:
            self.remove_tick_callback = self._remove

    def on_tick(self, callback) -> None:
        self.callbacks.append(callback)

    def _remove(self, callback) -> None:
        self.removed.append(callback)
        self.callbacks.remove(callback)

    def emit(self, price: float = 100.0, minutes: int = 0, symbol: str = "NVDA") -> None:
        tick = Tick(symbol=symbol, price=price, size=1, exchange_ts=T0 + timedelta(minutes=minutes))
        for callback in list(self.callbacks):
            callback(tick)

    async def disconnect(self) -> None:
        if self.fail_disconnect:
            raise RuntimeError("disconnect failed")
        self.disconnected = True


class _Bus:
    """Records envelopes; can be gated so a publish suspends mid-handler."""

    def __init__(self) -> None:
        self.published: list = []
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def publish(self, envelope) -> None:
        if self.gate is not None:
            self.entered.set()
            await self.gate.wait()
        self.published.append(envelope)

    def kinds(self) -> list:
        return [e.event_type for e in self.published]


@pytest.fixture(autouse=True)
def _reset():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


def _owned_tasks() -> list:
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("tick-ingest") and not t.done()]


async def _drain() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# --- admission ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_late_callback_after_stop_creates_no_work():  # [baseline-failing]
    bus, provider = _Bus(), _Provider("p")
    bridge = TickIngestBridge(provider, bus)
    bridge.stop()
    before = len(asyncio.all_tasks())
    provider.emit()
    assert len(asyncio.all_tasks()) == before  # no task was created
    await _drain()
    assert bus.published == []
    await bridge.aclose()


@pytest.mark.asyncio
async def test_stop_cancels_handlers_queued_but_not_yet_started():  # [baseline-failing]
    bus, provider = _Bus(), _Provider("p")
    bridge = TickIngestBridge(provider, bus)
    for i in range(3):
        provider.emit(price=100.0 + i)  # three handler tasks created, none has run yet
    bridge.stop()  # synchronous: before the loop ever schedules them
    await _drain()
    assert bus.published == []
    await bridge.aclose()
    assert _owned_tasks() == []


@pytest.mark.asyncio
async def test_paused_inflight_handler_cannot_publish_after_stop():  # [baseline-failing]
    bus, provider = _Bus(), _Provider("p")
    bridge = TickIngestBridge(provider, bus)
    provider.emit(minutes=0)
    await _drain()
    assert bus.kinds() == [EventType.PRICE_UPDATED]  # bucket for minute 0 exists
    bus.gate = asyncio.Event()
    provider.emit(minutes=1)  # rollover tick: handler pauses inside publish
    await asyncio.wait_for(bus.entered.wait(), 1)
    bridge.stop()
    bus.gate.set()  # releasing the old handler must not let it continue
    await _drain()
    await asyncio.sleep(0.02)
    assert bus.kinds() == [EventType.PRICE_UPDATED]  # no 2nd price, no rollover candle


@pytest.mark.asyncio
async def test_aclose_settles_paused_handler_without_waiting_for_a_stuck_bus():
    bus, provider = _Bus(), _Provider("p")
    bus.gate = asyncio.Event()  # never released
    bridge = TickIngestBridge(provider, bus)
    provider.emit()
    await asyncio.wait_for(bus.entered.wait(), 1)
    await asyncio.wait_for(bridge.aclose(), 1)
    assert bridge.state is BridgeState.RETIRED
    assert _owned_tasks() == []
    assert bus.published == []


@pytest.mark.asyncio
async def test_pending_flush_never_publishes_after_stop():
    class FastFlush(TickIngestBridge):
        def _seconds_until_next_flush(self) -> float:
            return 0.05

    bus, provider = _Bus(), _Provider("p")
    bridge = FastFlush(provider, bus)
    provider.emit(minutes=-5)  # stale bucket the flush loop would close
    await _drain()
    bridge.stop()  # before the flush loop wakes
    await asyncio.sleep(0.2)
    assert bus.kinds() == [EventType.PRICE_UPDATED]  # no CANDLE_CLOSED
    await bridge.aclose()


@pytest.mark.asyncio
async def test_flush_paused_mid_publish_is_cancelled_by_retirement():
    class FastFlush(TickIngestBridge):
        def _seconds_until_next_flush(self) -> float:
            return 0.05

    bus, provider = _Bus(), _Provider("p")
    bridge = FastFlush(provider, bus)
    provider.emit(minutes=-5)
    await _drain()
    bus.gate = asyncio.Event()
    await asyncio.wait_for(bus.entered.wait(), 1)  # flush is now suspended publishing
    await asyncio.wait_for(bridge.aclose(), 1)
    bus.gate.set()
    await asyncio.sleep(0.02)
    assert bus.kinds() == [EventType.PRICE_UPDATED]
    assert _owned_tasks() == []


@pytest.mark.asyncio
async def test_retired_bridge_discards_partial_bucket_and_never_flushes_it():
    bus, provider = _Bus(), _Provider("p")
    bridge = TickIngestBridge(provider, bus)
    provider.emit(minutes=-5)
    await _drain()
    bridge.stop()
    await bridge._flush_stale_buckets(datetime.now(timezone.utc))  # direct call is inert too
    assert bus.kinds() == [EventType.PRICE_UPDATED]
    assert bridge._buckets == {}
    await bridge.aclose()


# --- registry takeover / clear -------------------------------------------------


@pytest.mark.asyncio
async def test_retained_historical_provider_cannot_publish_after_takeover():  # [baseline-failing]
    bus = _Bus()
    polygon, finnhub = _Provider("polygon"), _Provider("finnhub")
    old_bridge = TickIngestBridge(polygon, bus)
    broker_registry.set_historical_provider(polygon)
    await broker_registry.take_over_streaming(polygon, old_bridge)

    new_bridge = TickIngestBridge(finnhub, bus)
    await broker_registry.take_over_streaming(finnhub, new_bridge)

    # historical role preserved: still registered, still connected
    assert polygon.disconnected is False
    assert broker_registry.get_historical_provider() is polygon
    assert broker_registry.get_streaming_provider() is finnhub

    polygon.emit(price=1.0)
    await _drain()
    assert bus.published == []  # the retained provider's old bridge is inert
    finnhub.emit(price=2.0)
    await _drain()
    assert [e.payload["price"] for e in bus.published] == [2.0]
    await new_bridge.aclose()


@pytest.mark.asyncio
async def test_queued_old_handlers_are_dropped_at_takeover():  # [baseline-failing]
    bus = _Bus()
    polygon, finnhub = _Provider("polygon"), _Provider("finnhub")
    old_bridge = TickIngestBridge(polygon, bus)
    broker_registry.set_historical_provider(polygon)
    await broker_registry.take_over_streaming(polygon, old_bridge)
    polygon.emit(price=1.0)
    polygon.emit(price=1.1)  # two handlers queued, not started
    new_bridge = TickIngestBridge(finnhub, bus)
    await broker_registry.take_over_streaming(finnhub, new_bridge)
    await _drain()
    assert bus.published == []
    await new_bridge.aclose()


@pytest.mark.asyncio
async def test_old_bridge_is_retired_before_new_provider_becomes_visible():
    bus = _Bus()
    polygon, finnhub = _Provider("polygon"), _Provider("finnhub")
    old_bridge = TickIngestBridge(polygon, bus)
    broker_registry.set_historical_provider(polygon)
    await broker_registry.take_over_streaming(polygon, old_bridge)
    seen = []

    def wake() -> None:
        seen.append((broker_registry.get_streaming_provider(), old_bridge.state))
        polygon.emit(price=9.0)  # a tick landing exactly when the new path goes live

    broker_registry.register_protected_feed_wake(wake)
    new_bridge = TickIngestBridge(finnhub, bus)
    try:
        await broker_registry.take_over_streaming(finnhub, new_bridge)
        await _drain()
    finally:
        broker_registry.unregister_protected_feed_wake(wake)
    assert seen == [(finnhub, BridgeState.RETIRED)]
    assert bus.published == []
    await new_bridge.aclose()


@pytest.mark.asyncio
async def test_immediate_clear_stops_admission_and_cleanup_is_awaited_later():  # [baseline-failing]
    bus, provider = _Bus(), _Provider("p")
    bridge = TickIngestBridge(provider, bus)
    await broker_registry.take_over_streaming(provider, bridge)
    provider.emit()
    provider.emit()  # queued, not started
    broker_registry.clear_streaming_provider()  # synchronous compatibility path
    provider.emit()  # late callback after the clear
    await _drain()
    assert bus.published == []
    assert bridge.state is BridgeState.RETIRING and not bridge.is_admitting
    await broker_registry.settle_retired_bridges()  # the lifecycle boundary
    assert bridge.state is BridgeState.RETIRED
    assert _owned_tasks() == []
    assert broker_registry.get_streaming_provider() is None


@pytest.mark.asyncio
async def test_same_provider_replacement_delivers_each_tick_once():  # [baseline-failing]
    bus, provider = _Bus(), _Provider("p")
    first = TickIngestBridge(provider, bus)
    await broker_registry.take_over_streaming(provider, first)
    second = TickIngestBridge(provider, bus)  # same provider, fresh bridge
    await broker_registry.take_over_streaming(provider, second)
    assert provider.disconnected is False
    assert len(provider.callbacks) == 2  # no public removal: old one stays, inert
    provider.emit()
    await _drain()
    assert bus.kinds() == [EventType.PRICE_UPDATED]  # exactly once
    assert first.state is BridgeState.RETIRED and second.state is BridgeState.ACTIVE
    await second.aclose()


@pytest.mark.asyncio
async def test_new_provider_candles_are_correct_and_old_partial_bucket_never_leaks():  # [baseline-failing]
    bus = _Bus()
    polygon, finnhub = _Provider("polygon"), _Provider("finnhub")
    old_bridge = TickIngestBridge(polygon, bus)
    broker_registry.set_historical_provider(polygon)
    await broker_registry.take_over_streaming(polygon, old_bridge)
    polygon.emit(price=50.0, minutes=0)  # old partial bucket for minute 0
    await _drain()
    new_bridge = TickIngestBridge(finnhub, bus)
    await broker_registry.take_over_streaming(finnhub, new_bridge)
    bus.published.clear()

    polygon.emit(price=51.0, minutes=1)  # would have rolled the old bucket over
    finnhub.emit(price=10.0, minutes=1)
    finnhub.emit(price=12.0, minutes=1)
    finnhub.emit(price=11.0, minutes=2)  # rollover on the NEW bridge only
    await _drain()
    candles = [e for e in bus.published if e.event_type == EventType.CANDLE_CLOSED]
    assert len(candles) == 1
    assert (candles[0].payload["open"], candles[0].payload["high"], candles[0].payload["close"]) == (10.0, 12.0, 12.0)
    assert candles[0].payload["volume"] == 2
    prices = [e.payload["price"] for e in bus.published if e.event_type == EventType.PRICE_UPDATED]
    assert prices == [10.0, 12.0, 11.0]
    await new_bridge.aclose()


@pytest.mark.asyncio
async def test_failed_old_disconnect_leaves_old_bridge_live_and_registry_unchanged():
    bus = _Bus()
    old, new = _Provider("old"), _Provider("new")
    old_bridge = TickIngestBridge(old, bus)
    await broker_registry.take_over_streaming(old, old_bridge)
    old.fail_disconnect = True
    with pytest.raises(RuntimeError):
        await broker_registry.take_over_streaming(new, TickIngestBridge(new, bus))
    assert broker_registry.get_streaming_provider() is old
    assert old_bridge.state is BridgeState.ACTIVE
    old.emit()
    await _drain()
    assert len(bus.published) == 1


@pytest.mark.asyncio
async def test_shutdown_retires_registered_bridge_without_changing_roles():
    bus, provider = _Bus(), _Provider("p")
    bridge = TickIngestBridge(provider, bus)
    broker_registry.set_historical_provider(provider)
    await broker_registry.take_over_streaming(provider, bridge)
    provider.emit()
    await broker_registry.retire_streaming_bridge()
    assert bridge.state is BridgeState.RETIRED and _owned_tasks() == []
    assert broker_registry.get_streaming_provider() is provider
    assert broker_registry.get_historical_provider() is provider
    provider.emit()
    await _drain()
    assert bus.published == []


# --- idempotence / capability -----------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_lifecycle_operations_are_idempotent():
    bus, provider = _Bus(), _Provider("p", removable=True)
    bridge = TickIngestBridge(provider, bus)
    await broker_registry.take_over_streaming(provider, bridge)
    await broker_registry.take_over_streaming(provider, bridge)  # same bridge: not retired
    assert bridge.state is BridgeState.ACTIVE
    bridge.stop()
    bridge.stop()
    await asyncio.gather(bridge.aclose(), bridge.aclose(), bridge.aclose())
    await bridge.aclose()
    broker_registry.clear_streaming_provider()
    broker_registry.clear_streaming_provider()
    await broker_registry.settle_retired_bridges()
    await broker_registry.settle_retired_bridges()
    await broker_registry.take_over_streaming(_Provider("q"))
    assert bridge.state is BridgeState.RETIRED
    assert provider.removed == [bridge._on_tick]  # removal requested exactly once


@pytest.mark.asyncio
async def test_without_removal_capability_callback_stays_registered_but_inert():
    bus, provider = _Bus(), _Provider("p")
    bridge = TickIngestBridge(provider, bus)
    await bridge.aclose()
    assert len(provider.callbacks) == 1
    provider.emit()
    await _drain()
    assert bus.published == []


@pytest.mark.asyncio
async def test_failing_removal_capability_never_breaks_retirement():
    bus, provider = _Bus(), _Provider("p", removable=True)

    def boom(callback) -> None:
        raise RuntimeError("nope")

    provider.remove_tick_callback = boom
    bridge = TickIngestBridge(provider, bus)
    await bridge.aclose()
    assert bridge.state is BridgeState.RETIRED


@pytest.mark.asyncio
async def test_constructor_failure_leaves_no_flush_task():
    class Bad:
        def on_tick(self, callback) -> None:
            raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        TickIngestBridge(Bad(), _Bus())
    await _drain()
    assert _owned_tasks() == []


# --- Finnhub: same-instance reconnect --------------------------------------------


class _Socket:
    def __init__(self) -> None:
        self.messages: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send(self, raw) -> None:
        json.loads(raw)

    async def close(self) -> None:
        self.closed = True

    def drop(self) -> None:
        self.messages.put_nowait(websockets.ConnectionClosed(None, None))

    def trade(self, symbol: str = "AAPL") -> None:
        self.messages.put_nowait(json.dumps({"type": "trade", "data": [
            {"s": symbol, "p": 1.0, "v": 1, "t": 1735689600000}]}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.messages.get()
        if isinstance(message, Exception):
            raise message
        return message


class _Wait:
    def __init__(self) -> None:
        self.delays: list = []
        self.queue: asyncio.Queue = asyncio.Queue()

    async def __call__(self, delay) -> None:
        self.delays.append(delay)
        await self.queue.get()


async def _until(predicate) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


@pytest.mark.asyncio
async def test_finnhub_same_instance_reconnect_keeps_active_bridge_without_duplicates():
    first, second = _Socket(), _Socket()
    sockets = [first, second]
    wait = _Wait()

    async def connect_ws(url):
        return sockets.pop(0)

    adapter = FinnhubAdapter(api_key="fake", connect_ws=connect_ws, retry_wait=wait,
                             retry_initial_seconds=1, retry_max_seconds=2)
    bus = _Bus()
    await adapter.connect()
    bridge = TickIngestBridge(adapter, bus)
    await broker_registry.take_over_streaming(adapter, bridge)
    await adapter.subscribe(["AAPL"])

    first.drop()
    await _until(lambda: len(wait.delays) == 1)
    wait.queue.put_nowait(None)
    await _until(adapter.is_connected)
    assert len(adapter._tick_callbacks) == 1  # reconnect does not re-register
    assert bridge.state is BridgeState.ACTIVE

    second.trade()
    await _until(lambda: len(bus.published) == 1)
    await _drain()
    assert bus.kinds() == [EventType.PRICE_UPDATED]  # once, not duplicated
    await broker_registry.take_over_streaming(_Provider("replacement"))
    await _drain()
    assert bridge.state is BridgeState.RETIRED


@pytest.mark.asyncio
async def test_finnhub_retained_after_takeover_cannot_publish_through_retired_bridge():
    sock = _Socket()

    async def connect_ws(url):
        return sock

    adapter = FinnhubAdapter(api_key="fake", connect_ws=connect_ws, retry_wait=_Wait(),
                             retry_initial_seconds=1)
    bus = _Bus()
    await adapter.connect()
    old_bridge = TickIngestBridge(adapter, bus)
    broker_registry.set_historical_provider(adapter)  # retained for another role
    await broker_registry.take_over_streaming(adapter, old_bridge)
    await adapter.subscribe(["AAPL"])
    other = _Provider("other")
    await broker_registry.take_over_streaming(other, TickIngestBridge(other, bus))
    assert adapter.is_connected()  # historical role keeps it connected
    sock.trade()
    await _drain()
    await asyncio.sleep(0.02)
    assert bus.published == []
    await adapter.disconnect()
    await broker_registry.retire_streaming_bridge()
