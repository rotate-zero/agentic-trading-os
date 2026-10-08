"""Candle-exclusion diagnostics (task late-tick-candle-diagnostics).

Controlled fake providers and buses only: no network, database or credentials.
Covers the bridge counters (both exclusion branches, reason precedence, raw
publication preserved, candle output unchanged), the immutable snapshot, the
narrow registry reader and GET /market/tick-bridge-status.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.broker_adapters.base import Tick
from app.main import app
from app.schemas.events.envelope import EventType
from app.services import broker_registry
from app.services.tick_ingest import BridgeState, TickBridgeDiagnostics, TickIngestBridge

T0 = datetime(2026, 7, 25, 10, 30, 0, tzinfo=timezone.utc)


class _Provider:
    def __init__(self) -> None:
        self.callbacks: list = []

    def on_tick(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *, minutes: int = 0, seconds: int = 0, price: float = 100.0, size: int = 1, symbol: str = "NVDA") -> None:
        tick = Tick(symbol=symbol, price=price, size=size, exchange_ts=T0 + timedelta(minutes=minutes, seconds=seconds))
        for callback in list(self.callbacks):
            callback(tick)

    async def disconnect(self) -> None:
        pass


class _Bus:
    """Records envelopes; can hold only CandleClosed publishes at a gate."""

    def __init__(self) -> None:
        self.published: list = []
        self.candle_gate: asyncio.Event | None = None
        self.candle_entered = asyncio.Event()

    async def publish(self, envelope) -> None:
        if self.candle_gate is not None and envelope.event_type == EventType.CANDLE_CLOSED:
            self.candle_entered.set()
            await self.candle_gate.wait()
        self.published.append(envelope)

    def count(self, kind) -> int:
        return sum(1 for e in self.published if e.event_type == kind)

    def candles(self) -> list:
        return [e for e in self.published if e.event_type == EventType.CANDLE_CLOSED]


@pytest.fixture(autouse=True)
def _reset():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


async def _drain() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


def _make() -> tuple[_Provider, _Bus, TickIngestBridge]:
    provider, bus = _Provider(), _Bus()
    return provider, bus, TickIngestBridge(provider, bus)


def _by_symbol(snap: TickBridgeDiagnostics) -> dict[str, tuple[int, int]]:
    return {r.symbol: (r.older_than_active_bucket, r.already_closed_minute) for r in snap.by_symbol}


# --- counters -------------------------------------------------------------------


async def test_fresh_bridge_reports_zero_counts_identity_and_read_time():
    _, _, bridge = _make()
    before = datetime.now(timezone.utc)
    snap = bridge.get_diagnostics_snapshot()
    assert (snap.older_than_active_bucket, snap.already_closed_minute, snap.total) == (0, 0, 0)
    assert snap.by_symbol == () and snap.state is BridgeState.ACTIVE
    assert snap.bridge_id and snap.created_at <= snap.read_at and snap.read_at >= before
    assert snap.read_at.tzinfo is not None
    bridge.stop()


async def test_bridge_instances_have_distinct_identities():
    _, _, a = _make()
    _, _, b = _make()
    assert a.get_diagnostics_snapshot().bridge_id != b.get_diagnostics_snapshot().bridge_id
    assert a.get_diagnostics_snapshot().bridge_id == a.get_diagnostics_snapshot().bridge_id
    a.stop()
    b.stop()


async def test_older_than_active_bucket_is_counted_once_and_excluded_from_the_candle():
    provider, bus, bridge = _make()
    provider.emit(minutes=5, price=100.0, size=10)
    await _drain()
    provider.emit(minutes=3, price=50.0, size=99)  # older than the active minute-5 bucket
    await _drain()
    snap = bridge.get_diagnostics_snapshot()
    assert (snap.older_than_active_bucket, snap.already_closed_minute, snap.total) == (1, 0, 1)
    assert _by_symbol(snap) == {"NVDA": (1, 0)}
    assert bus.count(EventType.PRICE_UPDATED) == 2  # raw publication preserved for the excluded tick
    await bridge._flush_stale_buckets(T0 + timedelta(minutes=6, seconds=1))
    (candle,) = bus.candles()
    p = candle.payload
    assert (p["open"], p["high"], p["low"], p["close"], p["volume"]) == (100.0, 100.0, 100.0, 100.0, 10)
    bridge.stop()


async def test_already_closed_minute_after_rollover_wins_precedence_over_older_than_active():
    provider, bus, bridge = _make()
    provider.emit(minutes=0)
    await _drain()
    provider.emit(minutes=1)  # rollover closes minute 0; active bucket is minute 1
    await _drain()
    provider.emit(minutes=0, seconds=30)  # satisfies BOTH conditions; existing code checks closed first
    await _drain()
    snap = bridge.get_diagnostics_snapshot()
    assert (snap.older_than_active_bucket, snap.already_closed_minute) == (0, 1)
    assert len(bus.candles()) == 1
    assert bus.count(EventType.PRICE_UPDATED) == 3
    bridge.stop()


async def test_late_tick_after_wall_clock_flush_counts_as_closed_and_does_not_reopen():
    provider, bus, bridge = _make()
    provider.emit(minutes=0)
    await _drain()
    await bridge._flush_stale_buckets(T0 + timedelta(minutes=1, seconds=1))
    assert len(bus.candles()) == 1
    provider.emit(minutes=0, seconds=45)  # no active bucket after the flush
    await _drain()
    snap = bridge.get_diagnostics_snapshot()
    assert (snap.older_than_active_bucket, snap.already_closed_minute) == (0, 1)
    await bridge._flush_stale_buckets(T0 + timedelta(minutes=3))
    assert len(bus.candles()) == 1  # nothing reopened, no duplicate candle
    assert bus.count(EventType.PRICE_UPDATED) == 2
    bridge.stop()


async def test_same_minute_source_regression_is_in_the_candle_and_not_counted():
    provider, bus, bridge = _make()
    provider.emit(minutes=0, seconds=20, price=101.0, size=5)
    provider.emit(minutes=0, seconds=10, price=99.5, size=7)  # earlier second, SAME minute
    await _drain()
    assert bridge.get_diagnostics_snapshot().total == 0
    await bridge._flush_stale_buckets(T0 + timedelta(minutes=1, seconds=1))
    p = bus.candles()[0].payload
    assert (p["low"], p["high"], p["close"], p["volume"]) == (99.5, 101.0, 99.5, 12)
    bridge.stop()


async def test_counts_are_separated_per_symbol_and_sorted():
    provider, _, bridge = _make()
    for sym in ("NVDA", "AAPL", "MSFT"):
        provider.emit(minutes=5, symbol=sym)
    await _drain()
    provider.emit(minutes=1, symbol="NVDA")
    provider.emit(minutes=2, symbol="NVDA")
    provider.emit(minutes=4, symbol="AAPL")
    await _drain()
    provider.emit(minutes=6, symbol="AAPL")  # rolls AAPL only
    await _drain()
    provider.emit(minutes=5, symbol="AAPL")  # closed for AAPL
    await _drain()
    snap = bridge.get_diagnostics_snapshot()
    assert _by_symbol(snap) == {"AAPL": (1, 1), "NVDA": (2, 0)}  # MSFT never excluded: absent
    assert [r.symbol for r in snap.by_symbol] == ["AAPL", "NVDA"]
    assert (snap.older_than_active_bucket, snap.already_closed_minute) == (3, 1)
    bridge.stop()


async def test_excluded_tick_during_paused_candle_publication_is_counted_once_after_it_settles():
    provider, bus, bridge = _make()
    provider.emit(minutes=0)
    await _drain()
    bus.candle_gate = asyncio.Event()
    provider.emit(minutes=1)  # holds the symbol lock inside the CandleClosed publish
    await asyncio.wait_for(bus.candle_entered.wait(), 1)
    provider.emit(minutes=0, seconds=30)  # raw publish proceeds; bucket step waits for the lock
    await _drain()
    assert bridge.get_diagnostics_snapshot().total == 0  # not yet decided: nothing counted early
    assert bus.count(EventType.PRICE_UPDATED) == 3
    bus.candle_gate.set()
    await _drain()
    snap = bridge.get_diagnostics_snapshot()
    assert (snap.older_than_active_bucket, snap.already_closed_minute) == (0, 1)
    assert len(bus.candles()) == 1
    bridge.stop()


# --- retirement -----------------------------------------------------------------


async def test_ticks_after_stop_are_not_counted_and_counters_stay_readable():
    provider, _, bridge = _make()
    provider.emit(minutes=5)
    await _drain()
    provider.emit(minutes=3)
    await _drain()
    bridge.stop()
    provider.emit(minutes=1)  # inert callback: no work, no count
    await _drain()
    snap = bridge.get_diagnostics_snapshot()
    assert snap.state is BridgeState.RETIRING and snap.older_than_active_bucket == 1 and snap.total == 1
    await bridge.aclose()
    assert bridge.get_diagnostics_snapshot().state is BridgeState.RETIRED
    assert bridge.get_diagnostics_snapshot().total == 1


async def test_handler_retired_while_waiting_for_the_lock_does_not_count():
    provider, bus, bridge = _make()
    provider.emit(minutes=0)
    await _drain()
    bus.candle_gate = asyncio.Event()
    provider.emit(minutes=1)
    await asyncio.wait_for(bus.candle_entered.wait(), 1)
    provider.emit(minutes=0, seconds=30)  # queued behind the paused close
    await _drain()
    bridge.stop()
    bus.candle_gate.set()
    await _drain()
    await bridge.aclose()
    assert bridge.get_diagnostics_snapshot().total == 0
    assert bus.candles() == []  # retirement semantics unchanged: no late candle


# --- snapshot properties --------------------------------------------------------


async def test_snapshot_is_immutable_and_isolated_from_later_counting():
    provider, _, bridge = _make()
    provider.emit(minutes=5)
    await _drain()
    provider.emit(minutes=3)
    await _drain()
    first = bridge.get_diagnostics_snapshot()
    provider.emit(minutes=2)
    await _drain()
    second = bridge.get_diagnostics_snapshot()
    assert first.total == 1 and second.total == 2 and first is not second
    assert isinstance(first.by_symbol, tuple) and _by_symbol(first) == {"NVDA": (1, 0)}
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.older_than_active_bucket = 99  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.by_symbol[0].already_closed_minute = 5  # type: ignore[misc]
    with pytest.raises(AttributeError):
        first.by_symbol.append(None)  # type: ignore[attr-defined]
    bridge.stop()


async def test_storage_is_bounded_by_tracked_symbols_not_by_ticks():
    provider, _, bridge = _make()
    provider.emit(minutes=5)
    await _drain()
    for i in range(200):
        provider.emit(minutes=5, seconds=i % 50)  # accepted ticks: no entries created
    await _drain()
    assert bridge._excluded_older_than_active == {} and bridge._excluded_already_closed == {}
    for _ in range(50):
        provider.emit(minutes=1)
    await _drain()
    assert bridge._excluded_older_than_active == {"NVDA": 50} and bridge._excluded_already_closed == {}
    bridge.stop()


async def test_reading_a_snapshot_publishes_nothing_and_changes_nothing():
    provider, bus, bridge = _make()
    provider.emit(minutes=5)
    await _drain()
    published, buckets = list(bus.published), dict(bridge._buckets)
    for _ in range(3):
        bridge.get_diagnostics_snapshot()
    await _drain()
    assert bus.published == published and bridge._buckets == buckets
    bridge.stop()


# --- registry reader + route ----------------------------------------------------


def _get() -> dict:
    response = TestClient(app).get("/market/tick-bridge-status")  # no lifespan: a pure registry read
    assert response.status_code == 200
    return response.json()


async def test_registry_reader_is_none_without_a_bridge():
    assert broker_registry.get_streaming_bridge_diagnostics() is None
    body = _get()
    assert body["status"] == "unavailable" and body["reason"] == "no_streaming_bridge"
    assert body["bridge"] is None and body["candle_exclusions"] is None and body["read_at"] is None


async def test_route_reports_the_registered_bridge_counts_and_identity():
    provider, _, bridge = _make()
    await broker_registry.take_over_streaming(provider, bridge)
    provider.emit(minutes=5, symbol="NVDA")
    await _drain()
    provider.emit(minutes=2, symbol="NVDA")
    await _drain()
    provider.emit(minutes=6, symbol="NVDA")
    await _drain()
    provider.emit(minutes=5, symbol="NVDA")
    await _drain()
    body = _get()
    assert body["status"] == "available" and body["reason"] is None
    snap = bridge.get_diagnostics_snapshot()
    assert body["bridge"]["id"] == snap.bridge_id and body["bridge"]["state"] == "active"
    ex = body["candle_exclusions"]
    assert ex["basis"] == "bridge_candle_construction"
    assert ex["totals"] == {"older_than_active_bucket": 1, "already_closed_minute": 1, "total": 2}
    assert ex["by_symbol"] == {"NVDA": {"older_than_active_bucket": 1, "already_closed_minute": 1, "total": 2}}
    assert datetime.fromisoformat(body["read_at"]).tzinfo is not None
    assert "upstream" in body["note"] and "one-to-one" in body["note"]
    assert _get()["bridge"]["id"] == body["bridge"]["id"]  # stable identity across reads


async def test_route_reflects_replacement_and_clear_as_new_identity_then_unavailable():
    p1, _, b1 = _make()
    await broker_registry.take_over_streaming(p1, b1)
    first = _get()["bridge"]["id"]
    p2, _, b2 = _make()
    await broker_registry.take_over_streaming(p2, b2)
    second = _get()
    assert second["bridge"]["id"] != first and second["candle_exclusions"]["totals"]["total"] == 0
    broker_registry.clear_streaming_provider()
    assert _get()["reason"] == "no_streaming_bridge"
    await broker_registry.settle_retired_bridges()


async def test_route_never_calls_a_provider_or_changes_registry_state():
    provider, bus, bridge = _make()
    await broker_registry.take_over_streaming(provider, bridge)

    def boom(*a, **k):
        raise AssertionError("route must not touch the provider")

    provider.disconnect = boom  # type: ignore[assignment]
    provider.on_tick = boom  # type: ignore[assignment]
    published = list(bus.published)
    _get()
    assert broker_registry.get_streaming_provider() is provider and bus.published == published
    assert bridge.state is BridgeState.ACTIVE


async def test_route_reports_snapshot_failure_as_unavailable_not_500(monkeypatch):
    def boom():
        raise RuntimeError("secret detail")

    monkeypatch.setattr(broker_registry, "get_streaming_bridge_diagnostics", boom)
    body = _get()
    assert body["status"] == "unavailable" and body["reason"] == "snapshot_failed"
    assert "secret" not in str(body)


def test_route_table_adds_exactly_one_get():
    methods: dict[str, set[str]] = {}
    for route in app.routes:
        if getattr(route, "path", "") == "/market/tick-bridge-status":
            methods.setdefault(route.path, set()).update(route.methods or set())
    assert methods == {"/market/tick-bridge-status": {"GET"}}


async def test_real_route_body_is_accepted_by_the_measurement_reducer_and_yields_a_delta():
    from app.measurement import streaming_coverage as sc

    provider, _, bridge = _make()
    await broker_registry.take_over_streaming(provider, bridge)
    provider.emit(minutes=5)
    await _drain()
    start = sc.reduce_bridge_status(_get())
    provider.emit(minutes=1)
    provider.emit(minutes=2)
    await _drain()
    end = sc.reduce_bridge_status(_get())
    assert start["read_ok"] and end["read_ok"]
    delta = sc.compute_bridge_delta(start, end, ("NVDA",))
    assert delta["availability"] == "available" and delta["older_than_active_bucket"] == 2 and delta["total"] == 2
    assert delta["monitored_symbols"]["total"] == 2
    unavailable = sc.reduce_bridge_status(_unavailable_body())
    assert unavailable["read_ok"] and unavailable["status"] == "unavailable"


def _unavailable_body() -> dict:
    broker_registry.clear_all()
    return _get()
