"""Protected-feed wake/coalescing and post-commit notification regressions."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

import app.services.protected_feed_reconciliation as reconciliation
from app.broker_adapters.finnhub_provider import FinnhubAdapter
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.execution_engine.engine import ExecutionEngine
from app.execution_engine.exit_ledger import ExitAction, PrepareDisposition, PrepareResult
from app.execution_engine.postgres import PostgresOrderLedger
from app.models.execution_ledger import Order, Position, Trade, TradeReservation
from app.portfolio_state.accounting import LedgerFill
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.ports import CommitResult, InFlightOrder, LedgerState
from app.schemas.events.envelope import EventType
from app.schemas.events.execution import OrderApproved, OrderFilled
from app.services import broker_registry
from app.services.protected_feed_reconciliation import ProtectedFeedReconciler


async def until(predicate, timeout=2):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(poll(), timeout)


class Provider:
    provider_id = "wake-test"

    def __init__(self):
        self.connected = True
        self.symbols = set()
        self.calls = []
        self.entered = asyncio.Event()
        self.release = None

    def is_connected(self):
        return self.connected

    def get_subscription_snapshot(self):
        return tuple(self.symbols)

    async def subscribe(self, symbols):
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        self.calls.extend(symbols)
        self.symbols.update(symbols)

    async def disconnect(self):
        self.connected = False


@pytest.fixture(autouse=True)
def clean_registry():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


@pytest.fixture
def durable_trade():
    """Isolate and remove the PostgreSQL rows owned by one wake test."""
    trade_id = uuid4()
    yield trade_id
    with SessionLocal.begin() as session:
        session.execute(delete(Position).where(Position.trade_id == trade_id))
        session.execute(delete(Order).where(Order.trade_id == trade_id))
        session.execute(delete(TradeReservation).where(TradeReservation.trade_id == trade_id))
        session.execute(delete(Trade).where(Trade.trade_id == trade_id))


@pytest.mark.asyncio
async def test_exposure_wake_is_immediate_and_burst_is_coalesced(monkeypatch):
    needs = set()
    monkeypatch.setattr(reconciliation, "read_protected_symbols", lambda _: frozenset(needs))
    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    try:
        owner.start()
        owner.start()
        await until(lambda: owner.get_snapshot().attempts_completed == 1)
        needs.add("ZZWAKE")
        for _ in range(100):
            owner.request_reconcile()
        await until(lambda: "ZZWAKE" in provider.symbols)
        await until(lambda: owner.get_snapshot().attempts_completed == 2)
        await asyncio.sleep(0)
        assert owner.get_snapshot().attempts_started == 2
        assert provider.calls == ["ZZWAKE"]
    finally:
        await owner.stop()


@pytest.mark.asyncio
async def test_wake_during_blocked_cycle_survives_for_one_followup(monkeypatch):
    needs = {"ZZFIRST"}
    monkeypatch.setattr(reconciliation, "read_protected_symbols", lambda _: frozenset(needs))
    provider = Provider()
    provider.release = asyncio.Event()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    try:
        owner.start()
        await provider.entered.wait()
        needs.add("ZZSECOND")
        for _ in range(30):
            owner.request_reconcile()
        assert owner.get_snapshot().cycle_in_progress
        provider.release.set()
        await until(lambda: owner.get_snapshot().attempts_completed == 2)
        assert provider.symbols == {"ZZFIRST", "ZZSECOND"}
        await asyncio.sleep(0)
        assert owner.get_snapshot().attempts_started == 2
    finally:
        provider.release.set()
        await owner.stop()


@pytest.mark.asyncio
async def test_failure_wakes_are_cooled_and_periodic_fallback_survives(monkeypatch):
    reads = 0
    fail = True

    def read(_):
        nonlocal reads
        reads += 1
        if fail:
            raise RuntimeError("database unavailable")
        return frozenset({"ZZRECOVER"})

    monkeypatch.setattr(reconciliation, "read_protected_symbols", read)
    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    try:
        owner.start()
        await until(lambda: owner.get_snapshot().attempts_completed == 1)
        for _ in range(100):
            owner.request_reconcile()
        await asyncio.sleep(0.05)
        assert reads == 1  # no tight retry loop while DB is failing
        fail = False
        await until(lambda: "ZZRECOVER" in provider.symbols, timeout=2)
        assert reads == 2
    finally:
        await owner.stop()

    # Omit the notification entirely: the existing periodic path still runs.
    needs = set()
    monkeypatch.setattr(reconciliation, "read_protected_symbols", lambda _: frozenset(needs))
    second = ProtectedFeedReconciler(SessionLocal, interval_seconds=0.02)
    try:
        second.start()
        await until(lambda: second.get_snapshot().attempts_completed == 1)
        needs.add("ZZPERIOD")
        await until(lambda: "ZZPERIOD" in provider.symbols)
    finally:
        await second.stop()


@pytest.mark.asyncio
async def test_takeover_targets_current_owner_and_stop_unregisters(monkeypatch):
    needs = {"ZZOWNER"}
    monkeypatch.setattr(reconciliation, "read_protected_symbols", lambda _: frozenset(needs))
    first, second = Provider(), Provider()
    await broker_registry.take_over_streaming(first)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    owner.start()
    try:
        await until(lambda: "ZZOWNER" in first.symbols)
        await broker_registry.take_over_streaming(second)
        await until(lambda: "ZZOWNER" in second.symbols)
        assert first.calls == ["ZZOWNER"]
        before = owner.get_snapshot().attempts_completed
        await owner.stop()
        owner.request_reconcile()
        await broker_registry.take_over_streaming(Provider())
        await asyncio.sleep(0)
        assert owner.get_snapshot().attempts_completed == before
        owner.start()  # one new task and one new registry callback
        await until(lambda: owner.get_snapshot().attempts_completed == before + 1)
    finally:
        await owner.stop()


@pytest.mark.asyncio
async def test_critical_bus_dispatch_stays_free_while_subscription_blocks(monkeypatch):
    monkeypatch.setattr(reconciliation, "read_protected_symbols", lambda _: frozenset({"ZZBLOCK"}))
    provider = Provider()
    provider.release = asyncio.Event()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    bus = EventBus()
    observed = asyncio.Event()
    bus.subscribe(EventType.ORDER_FILLED, lambda _: (owner.request_reconcile(), observed.set()))
    await bus.start()
    try:
        owner.start()
        await provider.entered.wait()
        await bus.publish(make_envelope(EventType.ORDER_FILLED, OrderFilled(
            order_id="test", side="BUY", qty=1, fill_price=1.0,
            fill_ts=datetime.now(timezone.utc),
        ), symbol="ZZBLOCK"))
        await asyncio.wait_for(observed.wait(), 1)
        assert owner.get_snapshot().cycle_in_progress
    finally:
        provider.release.set()
        await owner.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_entry_order_commit_wakes_owner_and_query_sees_row(durable_trade):
    """The real order ledger commits before ExecutionEngine signals the owner."""
    trade_id = durable_trade
    symbol = "ZW" + trade_id.hex[:8].upper()
    with SessionLocal.begin() as session:
        session.add(Trade(trade_id=trade_id, execution_mode="simulated", execution_venue="simulated",
                          strategy_name="TEST_PROTECTED_FEED_EVENT_WAKE", strategy_version="v1",
                          direction="BUY", symbol=symbol, thesis={}, decision="approved",
                          limits_snapshot={}, status="open"))
        session.add(TradeReservation(trade_id=trade_id, client_order_id=f"{trade_id}:entry",
                                     qty=1, reference_price=Decimal("100")))

    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    placed = asyncio.Event()
    release = asyncio.Event()

    class Venue:
        supported_modes = {"simulated"}
        venue_id = "simulated"

        async def place_order(self, instruction):
            placed.set()
            await release.wait()
            return SimpleNamespace(status="submitted", venue_order_id="local", reason=None)

    venue = Venue()
    ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(EventBus(), ledger, ledger,
                             venue_provider=SimpleNamespace(get_execution_venue=lambda: venue),
                             execution_mode_provider=lambda: "simulated")
    try:
        owner.start()
        await until(lambda: owner.get_snapshot().attempts_completed == 1)
        payload = OrderApproved(order_id=f"{trade_id}:entry", symbol=symbol, side="BUY",
                                qty=1, position_effect="open").model_dump()
        work = asyncio.create_task(engine._process_one(payload))
        await placed.wait()  # order INSERT returned; venue is deliberately blocked
        with SessionLocal() as session:
            row = session.scalar(select(Order).where(Order.trade_id == trade_id))
            assert row is not None and row.status == "approved"
        await until(lambda: symbol in provider.symbols)
        assert symbol in owner.get_snapshot().last_set_read.symbols
        release.set()
        await work
    finally:
        release.set()
        await owner.stop()


@pytest.mark.asyncio
async def test_exit_order_wake_only_for_new_committed_reservation(monkeypatch):
    """The execution hook signals after prepare_exit returns its commit result."""
    wakes = []
    monkeypatch.setattr(broker_registry, "request_protected_feed_reconcile", lambda: wakes.append(True))
    position_id = uuid4()

    class ExitLedger:
        def __init__(self):
            self.created = True

        def prepare_exit(self, _position_id):
            return PrepareResult(PrepareDisposition.SUBMIT, position_id,
                                 ExitAction("submit", "exit-test", "ZZEXIT", "SELL", 1, "stop"),
                                 created_order=self.created)

    ledger = ExitLedger()
    engine = ExecutionEngine(EventBus(), None, None, exit_ledger=ledger)
    await engine._service_exit_position(position_id, None)
    assert wakes == [True]
    ledger.created = False
    await engine._service_exit_position(position_id, None)
    assert wakes == [True]


@pytest.mark.asyncio
async def test_position_commit_wakes_owner_after_row_is_visible(durable_trade):
    """A controlled ledger commits a real PostgreSQL position before returning."""
    trade_id = durable_trade
    symbol = "ZP" + trade_id.hex[:8].upper()
    order_id = f"{trade_id}:entry"
    ts = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)
    with SessionLocal.begin() as session:
        session.add(Trade(trade_id=trade_id, execution_mode="simulated", execution_venue="simulated",
                          strategy_name="TEST_PROTECTED_FEED_EVENT_WAKE", strategy_version="v1",
                          direction="BUY", symbol=symbol, thesis={}, decision="approved",
                          limits_snapshot={}, status="open"))
        session.flush()
        session.add(Order(client_order_id=order_id, trade_id=trade_id, execution_mode="simulated",
                          execution_venue="simulated", symbol=symbol, side="BUY", position_effect="open",
                          qty=1, status="filled"))

    fill = LedgerFill(ledger_seq=1, venue_fill_id=f"wake:{trade_id}", client_order_id=order_id,
                      trade_id=trade_id, execution_mode="simulated", execution_venue="simulated",
                      symbol=symbol, side="BUY", position_effect="open", qty=1,
                      price=Decimal("100"), venue_ts=ts, stop=Decimal("90"))

    class CommittingLedger:
        def __init__(self):
            self.state = LedgerState("simulated", 0, ts)

        def load_state(self, mode):
            return self.state

        def pending_fills(self, mode, cursor):
            return (fill,) if cursor == 0 else ()

        def get_order(self, client_order_id):
            return InFlightOrder(order_id, symbol, "BUY", 1, "open", "simulated", "filled", filled_qty=1)

        def commit_fill(self, application):
            p = application.position
            with SessionLocal.begin() as session:
                session.add(Position(position_id=p.position_id, trade_id=trade_id,
                                     execution_mode="simulated", execution_venue="simulated",
                                     symbol=symbol, side="BUY", qty=p.qty, avg_price=p.avg_price,
                                     opened_at=p.opened_at, status=p.status))
            self.state = LedgerState("simulated", 1, ts, positions=(p,))
            return CommitResult(True, self.state)

    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    bus = EventBus()
    await bus.start()
    portfolio = PortfolioState("simulated", ledger=CommittingLedger(), bus=bus)
    try:
        owner.start()
        await until(lambda: owner.get_snapshot().attempts_completed == 1)
        assert symbol not in provider.symbols
        await portfolio.start()
        await until(lambda: symbol in provider.symbols)
        with SessionLocal() as session:
            assert session.scalar(select(Position).where(Position.trade_id == trade_id)) is not None
        assert symbol in owner.get_snapshot().last_set_read.symbols
    finally:
        await portfolio.stop()
        await owner.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_same_instance_finnhub_reconnect_wakes_for_new_exposure(monkeypatch):
    class Socket:
        def __init__(self):
            self.messages = asyncio.Queue()
            self.sent = []
            self.closed = False

        async def send(self, raw):
            import json
            self.sent.append(json.loads(raw))

        async def close(self):
            self.closed = True

        def drop(self):
            self.messages.put_nowait(None)

        def __aiter__(self):
            return self

        async def __anext__(self):
            item = await self.messages.get()
            if item is None:
                raise StopAsyncIteration
            return item

    first, second = Socket(), Socket()
    sockets = iter((first, second))
    gate = asyncio.Event()

    async def connect(_url):
        return next(sockets)

    async def wait(_delay):
        await gate.wait()

    needs = {"ZZFHONE"}
    monkeypatch.setattr(reconciliation, "read_protected_symbols", lambda _: frozenset(needs))
    adapter = FinnhubAdapter(api_key="fake", connect_ws=connect, retry_wait=wait,
                             retry_initial_seconds=1)
    await adapter.connect()
    await broker_registry.take_over_streaming(adapter)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=3600)
    try:
        owner.start()
        await until(lambda: "ZZFHONE" in adapter.get_subscription_snapshot())
        first.drop()
        await until(lambda: not adapter.is_connected())
        needs.add("ZZFHTWO")
        gate.set()
        await until(lambda: "ZZFHTWO" in adapter.get_subscription_snapshot())
        assert [m["symbol"] for m in second.sent] == ["ZZFHONE", "ZZFHTWO"]
        assert owner.get_snapshot().attempts_started >= 2
    finally:
        await owner.stop()
        await adapter.disconnect()
