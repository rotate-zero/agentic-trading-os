"""
Orchestration tests for app/execution_engine/engine.py's ExecutionEngine —
a real EventBus, fake OrderLedgerPort/DecisionAuthorizationPort/OrderVenue
(fork 1: this task doesn't own the real ledger, and the real SimulatedVenue
is the sibling `execution-ledger-and-venue` task's own build — see
execution_engine/ports.py's own docstring).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.execution_engine.engine import ExecutionEngine
from app.execution_engine.ports import (
    OrderInsertResult,
    OrderLedgerError,
    OrderRecord,
    VenueAck,
    VenueOrderInstruction,
)
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import OrderApproved, PlanRejected

_NOW = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)


@dataclass
class _FakeOrderLedger:
    fail_insert: bool = False
    fail_update: bool = False
    rows: dict[str, OrderRecord] = field(default_factory=dict)
    status_updates: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)

    def insert_order(self, record: OrderRecord) -> OrderInsertResult:
        if self.fail_insert:
            raise OrderLedgerError("simulated insert failure")
        existing = self.rows.get(record.client_order_id)
        if existing is not None:
            return OrderInsertResult(order=existing, inserted=False)
        self.rows[record.client_order_id] = record
        return OrderInsertResult(order=record, inserted=True)

    def update_order_status(self, client_order_id, status, *, reason=None, execution_venue=None) -> None:
        if self.fail_update:
            raise OrderLedgerError("simulated update failure")
        self.status_updates.append((client_order_id, status, reason, execution_venue))


@dataclass
class _FakeDecisionAuthorization:
    known_trade_ids: set[str]

    def has_committed_decision(self, opportunity_id: str) -> bool:
        return opportunity_id in self.known_trade_ids


class _FakeVenue:
    def __init__(
        self,
        *,
        venue_id: str = "fake-venue",
        supported_modes: frozenset[str] = frozenset({"simulated"}),
        ack: VenueAck = VenueAck(status="submitted"),
        release: asyncio.Event | None = None,
    ) -> None:
        self._venue_id = venue_id
        self._supported_modes = supported_modes
        self._ack = ack
        # `entered` is set the moment place_order() is reached. When a `release`
        # event is supplied, place_order() then blocks until the test sets it,
        # so a test controls exactly how long placement stays in flight.
        self._release = release
        self.entered = asyncio.Event()
        self.place_order_calls: list[VenueOrderInstruction] = []

    @property
    def venue_id(self) -> str:
        return self._venue_id

    @property
    def supported_modes(self) -> frozenset[str]:
        return self._supported_modes

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    async def place_order(self, instruction: VenueOrderInstruction) -> VenueAck:
        self.place_order_calls.append(instruction)
        self.entered.set()
        if self._release is not None:
            await self._release.wait()
        return self._ack

    async def cancel_order(self, client_order_id: str) -> None:
        pass

    async def get_order(self, client_order_id: str):
        return None

    async def list_open_orders(self):
        return []

    async def get_fills(self, client_order_id: str):
        return []

    async def get_positions(self):
        return []

    def on_order_update(self, callback) -> None:
        pass


class _FakeVenueProvider:
    def __init__(self, venue: _FakeVenue | None) -> None:
        self._venue = venue

    def get_execution_venue(self):
        return self._venue


def _order_approved_payload(**overrides) -> dict:
    base = dict(order_id="TRADE1:entry", symbol="AAPL", side="BUY", qty=10, order_type="market", position_effect="open")
    base.update(overrides)
    return OrderApproved(**base).model_dump(mode="json")


async def _publish_order_approved(bus: EventBus, **overrides) -> None:
    payload = _order_approved_payload(**overrides)
    envelope = EventEnvelope(event_type=EventType.ORDER_APPROVED, symbol=payload["symbol"], payload=payload)
    await bus.publish(envelope)


_WAIT_TIMEOUT = 5.0  # generous upper bound; a healthy wait returns within milliseconds


async def _wait_until(predicate, description: str, *, timeout: float = _WAIT_TIMEOUT, describe=None) -> None:
    """Poll for a positive completion signal, failing with what was observed
    if it never arrives within `timeout` (never a fixed guess-the-delay sleep)."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            observed = f"; observed: {describe()!r}" if describe is not None else ""
            pytest.fail(f"timed out after {timeout}s waiting for {description}{observed}")
        await asyncio.sleep(0.005)


def _track_processing(engine: ExecutionEngine) -> list[str]:
    """Record the order_id of every queued OrderApproved the engine's worker has
    FINISHED handling (returned or raised). It wraps the engine instance's
    `_process_one` only — no production code is touched — and gives the
    "nothing happened" tests a positive proof that the input was processed
    before they assert absence."""
    processed: list[str] = []
    original = engine._process_one  # noqa: SLF001 — test introspection only

    async def tracked(payload):
        try:
            await original(payload)
        finally:
            processed.append(payload.get("order_id"))

    engine._process_one = tracked  # type: ignore[method-assign]  # noqa: SLF001
    return processed


async def _wait_order_processed(bus: EventBus, processed: list[str], order_id: str, *, count: int = 1) -> None:
    """Barrier: `count` OrderApproved(s) for `order_id` were fully handled by the
    engine AND every event the engine published for them has been dispatched.

    Order matters: the engine's worker finishing proves the ledger/venue work is
    done; then both bus lanes are joined, because asyncio.Queue only counts an
    envelope as finished after every subscriber has returned — so any
    OrderStatusChanged the engine published has reached its subscribers too."""
    await _wait_until(
        lambda: processed.count(order_id) >= count,
        f"engine to finish processing {order_id} (x{count})",
        describe=lambda: list(processed),
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(bus._critical_queue.join(), bus._normal_queue.join()),  # noqa: SLF001
            timeout=_WAIT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        pytest.fail(f"timed out after {_WAIT_TIMEOUT}s waiting for the event bus to dispatch; depths={bus.queue_depths()}")


def _make_engine(
    bus: EventBus,
    *,
    ledger: _FakeOrderLedger | None = None,
    known_trade_ids: set[str] | None = None,
    venue: _FakeVenue | None = "DEFAULT",  # type: ignore[assignment]
    execution_mode: str | None = "simulated",
) -> tuple[ExecutionEngine, _FakeOrderLedger]:
    ledger = ledger or _FakeOrderLedger()
    decision_auth = _FakeDecisionAuthorization(known_trade_ids if known_trade_ids is not None else {"TRADE1"})
    if venue == "DEFAULT":
        venue = _FakeVenue()
    engine = ExecutionEngine(
        bus,
        ledger,
        decision_auth,
        venue_provider=_FakeVenueProvider(venue),
        execution_mode_provider=lambda: execution_mode,
    )
    return engine, ledger


@pytest.mark.asyncio
async def test_happy_path_inserts_ledger_row_and_places_order() -> None:
    bus = EventBus()
    await bus.start()
    published: list[EventEnvelope] = []
    bus.subscribe_all(lambda env: published.append(env))
    engine, ledger = _make_engine(bus)
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus)
        await _wait_until(
            lambda: ledger.status_updates, "ledger 'submitted' update for TRADE1:entry",
            describe=lambda: ledger.status_updates,
        )
        # Absence of any OrderStatusChanged is only meaningful once the order is
        # fully processed and the bus has dispatched whatever it published.
        await _wait_order_processed(bus, processed, "TRADE1:entry")

        assert "TRADE1:entry" in ledger.rows
        assert ledger.status_updates == [("TRADE1:entry", "submitted", None, "fake-venue")]
        real_events = [e for e in published if e.event_type != EventType.ORDER_APPROVED]
        assert real_events == []  # no OrderStatusChanged on a successful placement
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_duplicate_order_approved_sends_no_second_venue_call() -> None:
    """AC #7 (client-order-id-mint half): the same OrderApproved delivered
    twice creates one ledger row and one venue submission."""
    bus = EventBus()
    await bus.start()
    engine, ledger = _make_engine(bus)
    venue = engine._venue_provider.get_execution_venue()  # noqa: SLF001 — test introspection only
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus)
        await _wait_order_processed(bus, processed, "TRADE1:entry", count=1)
        await _publish_order_approved(bus)  # identical order_id — a retry/re-delivery
        # The duplicate must be *processed* (and ignored) before "no second
        # venue call" is asserted — a delay alone would not prove it.
        await _wait_order_processed(bus, processed, "TRADE1:entry", count=2)

        assert len(venue.place_order_calls) == 1
        assert len(ledger.rows) == 1
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_authorization_gate_rejects_order_with_no_committed_decision() -> None:
    """I2 / AC #19 entry-gate half: an entry OrderApproved with no
    committed authorizer decision is refused and logged — no ledger row
    is even written."""
    bus = EventBus()
    await bus.start()
    published: list[EventEnvelope] = []
    bus.subscribe(EventType.ORDER_STATUS_CHANGED, lambda env: published.append(env))
    engine, ledger = _make_engine(bus, known_trade_ids=set())  # TRADE1 not known
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus)
        await _wait_until(lambda: published, "OrderStatusChanged(rejected) published", describe=lambda: published)
        # Fully processed + bus flushed before asserting there is exactly one
        # event and no ledger row.
        await _wait_order_processed(bus, processed, "TRADE1:entry")

        assert ledger.rows == {}
        assert len(published) == 1
        assert published[0].payload["status"] == "rejected"
        assert published[0].payload["reason"] == "no_committed_decision"
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_venue_refusal_no_venue_configured() -> None:
    """AC #5 venue-refusal half."""
    bus = EventBus()
    await bus.start()
    published: list[EventEnvelope] = []
    bus.subscribe(EventType.ORDER_STATUS_CHANGED, lambda env: published.append(env))
    engine, ledger = _make_engine(bus, venue=None)
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus)
        await _wait_until(lambda: published, "OrderStatusChanged(rejected) published", describe=lambda: published)
        await _wait_order_processed(bus, processed, "TRADE1:entry")

        assert ledger.status_updates == [("TRADE1:entry", "rejected", "no_execution_venue_configured", None)]
        assert published[0].payload["reason"] == "no_execution_venue_configured"
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_venue_refusal_mode_not_supported() -> None:
    """AC #5 venue-refusal half: the venue exists but does not support
    this order's execution mode."""
    bus = EventBus()
    await bus.start()
    published: list[EventEnvelope] = []
    bus.subscribe(EventType.ORDER_STATUS_CHANGED, lambda env: published.append(env))
    venue = _FakeVenue(supported_modes=frozenset({"paper"}))  # does NOT support "simulated"
    engine, ledger = _make_engine(bus, venue=venue)
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus)
        await _wait_until(lambda: published, "OrderStatusChanged(rejected) published", describe=lambda: published)
        # place_order_calls == [] below is an absence claim: require the order
        # to be fully processed first.
        await _wait_order_processed(bus, processed, "TRADE1:entry")

        assert ledger.status_updates == [("TRADE1:entry", "rejected", "mode_not_supported", "fake-venue")]
        assert venue.place_order_calls == []  # never routed
        assert published[0].payload["reason"] == "mode_not_supported"
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_venue_rejection_ack_is_recorded_and_published() -> None:
    bus = EventBus()
    await bus.start()
    published: list[EventEnvelope] = []
    bus.subscribe(EventType.ORDER_STATUS_CHANGED, lambda env: published.append(env))
    venue = _FakeVenue(ack=VenueAck(status="rejected", reason="insufficient_liquidity"))
    engine, ledger = _make_engine(bus, venue=venue)
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus)
        await _wait_until(lambda: published, "OrderStatusChanged(rejected) published", describe=lambda: published)
        await _wait_order_processed(bus, processed, "TRADE1:entry")

        assert ledger.status_updates == [("TRADE1:entry", "rejected", "insufficient_liquidity", "fake-venue")]
        assert published[0].payload["reason"] == "insufficient_liquidity"
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_close_position_effect_is_dropped_not_processed() -> None:
    bus = EventBus()
    await bus.start()
    engine, ledger = _make_engine(bus)
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus, order_id="TRADE1:exit:1", position_effect="close")
        # Absence test: first prove the engine finished handling this exact
        # input (it was dropped, not merely still queued).
        await _wait_order_processed(bus, processed, "TRADE1:exit:1")

        assert ledger.rows == {}
        assert ledger.status_updates == []
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_malformed_client_order_id_is_dropped() -> None:
    bus = EventBus()
    await bus.start()
    engine, ledger = _make_engine(bus)
    processed = _track_processing(engine)
    engine.start()
    try:
        await _publish_order_approved(bus, order_id="not-a-real-id")
        await _wait_order_processed(bus, processed, "not-a-real-id")

        assert ledger.rows == {}
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_critical_lane_not_blocked_by_slow_venue_call() -> None:
    """AC #20 (I7): a venue whose place_order() blocks does not delay an
    unrelated critical event.

    The venue signals when placement is entered and then blocks until this
    test releases it, so the test proves PlanRejected is delivered *while*
    placement is still in flight — not merely "within some time"."""
    bus = EventBus()
    await bus.start()
    plan_rejected_received: list[float] = []
    bus.subscribe(EventType.PLAN_REJECTED, lambda env: plan_rejected_received.append(time.monotonic()))

    release = asyncio.Event()
    slow_venue = _FakeVenue(release=release)
    engine, ledger = _make_engine(bus, venue=slow_venue)
    engine.start()
    try:
        await _publish_order_approved(bus)
        try:
            await asyncio.wait_for(slow_venue.entered.wait(), timeout=_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            pytest.fail(f"venue place_order() was never entered within {_WAIT_TIMEOUT}s")
        assert not release.is_set()
        assert ledger.status_updates == []  # placement is still blocked inside the venue

        t_publish = time.monotonic()
        await bus.publish(
            make_envelope(EventType.PLAN_REJECTED, PlanRejected(symbol="MSFT", reasons=["unrelated"]), symbol="MSFT")
        )
        await _wait_until(
            lambda: plan_rejected_received,
            "PlanRejected delivery while the venue call is blocked",
            describe=lambda: plan_rejected_received,
        )

        # Delivered while the venue was provably still blocked, and promptly.
        assert not release.is_set()
        assert ledger.status_updates == []
        assert plan_rejected_received, "PlanRejected should have been delivered promptly"
        assert plan_rejected_received[0] - t_publish < 0.5

        # Let the blocked placement finish, and wait for its positive completion
        # signal (the ledger update) instead of sleeping for it.
        release.set()
        await _wait_until(
            lambda: ledger.status_updates == [("TRADE1:entry", "submitted", None, "fake-venue")],
            "blocked placement to complete after release",
            describe=lambda: ledger.status_updates,
        )
    finally:
        release.set()  # teardown must never hang on a still-blocked venue call
        await engine.stop()
        await bus.stop()


def test_execution_engine_package_imports_no_concrete_broker_module_at_module_scope() -> None:
    """No sibling `execution-ledger-and-venue` module is imported at
    module (import) time — only defensively, inside
    default_execution_venue_provider(), so this task's code runs fine
    whether or not that module has merged yet."""
    import ast
    import pathlib

    pkg_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "execution_engine"
    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in tree.body:  # module-level statements only — not inside functions
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.services.broker_registry"), (
                    f"{py_file.name} imports broker_registry at module scope"
                )
