"""Protected feed requests use the simulated ledger and current stream role."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.broker_adapters.base import Tick
from app.broker_adapters.order_venue import OrderInstruction
from app.broker_adapters.simulated_venue import SimulatedVenue
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.models.execution_ledger import Order, Position, Trade
from app.position_monitor.engine import PositionMonitor
from app.position_monitor.ports import PositionView
from app.services import broker_registry
from app.services.protected_feed_reconciliation import ProtectedFeedReconciler, read_protected_symbols
from app.services.tick_ingest import TickIngestBridge

NAME = "TEST_PROTECTED_FEED_RECONCILIATION"
TS = datetime(2026, 9, 22, 14, 1, tzinfo=timezone.utc)


@pytest.fixture
def ledger_rows():
    ids = []

    def add(symbol: str, *, position_status: str | None = None, order_status: str | None = None,
            mode: str = "simulated") -> str:
        venue = "simulated" if mode == "simulated" else "test"
        with SessionLocal.begin() as session:
            trade = Trade(execution_mode=mode, execution_venue=venue, strategy_name=NAME,
                          strategy_version="v1", direction="BUY", symbol=symbol, thesis={},
                          decision="approved", limits_snapshot={}, status="open")
            session.add(trade)
            session.flush()
            ids.append(trade.trade_id)
            if position_status is not None:
                session.add(Position(trade_id=trade.trade_id, execution_mode=mode,
                                     execution_venue=venue, symbol=symbol, side="BUY",
                                     qty=0 if position_status == "closed" else 1,
                                     avg_price=Decimal("100"), opened_at=TS, status=position_status))
            if order_status is not None:
                session.add(Order(client_order_id=f"{trade.trade_id}:entry", trade_id=trade.trade_id,
                                  execution_mode=mode, execution_venue=venue, symbol=symbol,
                                  side="BUY", position_effect="open", qty=1, status=order_status))
        return symbol

    yield add
    with SessionLocal.begin() as session:
        session.execute(delete(Order).where(Order.trade_id.in_(ids)))
        session.execute(delete(Position).where(Position.trade_id.in_(ids)))
        session.execute(delete(Trade).where(Trade.trade_id.in_(ids)))


class Provider:
    def __init__(self, *, inventory=True):
        self.connected = True
        self.symbols: set[str] = set()
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.callbacks = []
        self.inventory = inventory

    def is_connected(self):
        return self.connected

    def get_subscription_snapshot(self):
        if not self.inventory:
            raise RuntimeError("inventory unavailable")
        return tuple(self.symbols)

    async def subscribe(self, symbols):
        for symbol in symbols:
            self.calls.append(symbol)
            if symbol in self.fail:
                raise RuntimeError("subscription failed")
            self.symbols.add(symbol)

    async def disconnect(self):
        self.connected = False

    def on_tick(self, callback):
        self.callbacks.append(callback)

    def emit(self, symbol, price):
        if symbol in self.symbols:
            for callback in self.callbacks:
                callback(Tick(symbol=symbol, price=price, size=1, exchange_ts=TS))


def test_projection_restored_positions_working_orders_and_terminal_rules(ledger_rows):
    ledger_rows("ZZPFA", position_status="open", order_status="approved")
    ledger_rows("ZZPFB", position_status="closing")
    for status in ("submitted", "partially_filled", "unknown"):
        ledger_rows(f"ZZPF{status[:3].upper()}", order_status=status)
    for status in ("filled", "cancelled", "rejected", "expired"):
        ledger_rows(f"ZZPF{status[:3].upper()}X", order_status=status)
    ledger_rows("ZZPFC", position_status="closed")
    ledger_rows("ZZPFD", position_status="open", mode="paper")
    assert read_protected_symbols(SessionLocal) == {
        "ZZPFA", "ZZPFB", "ZZPFSUB", "ZZPFPAR", "ZZPFUNK",
    }


@pytest.mark.asyncio
async def test_additive_inventory_failures_takeover_and_exposure_change(monkeypatch):
    import app.services.protected_feed_reconciliation as module

    needs = {"ZZPFA", "ZZPFB"}
    monkeypatch.setattr(module, "read_protected_symbols", lambda factory: frozenset(needs))
    owner = ProtectedFeedReconciler(SessionLocal)
    first = Provider()
    first.symbols.add("MANUAL")
    first.fail.add("ZZPFA")
    await broker_registry.take_over_streaming(first)
    try:
        await owner.reconcile_once()
        assert first.calls == ["ZZPFA", "ZZPFB"]
        assert first.symbols == {"MANUAL", "ZZPFB"}
        first.fail.clear()
        await owner.reconcile_once()
        assert first.symbols == {"MANUAL", "ZZPFA", "ZZPFB"}
        needs.remove("ZZPFA")
        await owner.reconcile_once()
        assert "MANUAL" in first.symbols and "ZZPFA" in first.symbols

        replacement = Provider()
        await broker_registry.take_over_streaming(replacement)
        await owner.reconcile_once()
        assert replacement.calls == ["ZZPFB"]
        assert first.calls == ["ZZPFA", "ZZPFB", "ZZPFA"]
        needs.add("ZZPFC")
        await owner.reconcile_once()
        assert replacement.calls[-1] == "ZZPFC"
        replacement.connected = False
        needs.add("ZZPFD")
        await owner.reconcile_once()
        assert "ZZPFD" not in replacement.calls
        replacement.connected = True
        await owner.reconcile_once()
        assert "ZZPFD" in replacement.calls
    finally:
        await owner.stop()
        broker_registry.clear_all()


@pytest.mark.asyncio
async def test_failed_read_and_unavailable_inventory_remain_retryable(monkeypatch):
    import app.services.protected_feed_reconciliation as module

    reads = iter((RuntimeError("db down"), frozenset({"ZZPFA"}), frozenset({"ZZPFA"})))

    def read(_):
        value = next(reads)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(module, "read_protected_symbols", read)
    owner = ProtectedFeedReconciler(SessionLocal)
    provider = Provider(inventory=False)
    await broker_registry.take_over_streaming(provider)
    try:
        await owner.reconcile_once()
        assert provider.calls == []
        await owner.reconcile_once()
        await owner.reconcile_once()
        assert provider.calls == ["ZZPFA", "ZZPFA"]
    finally:
        await owner.stop()
        broker_registry.clear_all()


@pytest.mark.asyncio
async def test_no_overlap_and_shutdown_settles_work(monkeypatch):
    import app.services.protected_feed_reconciliation as module

    monkeypatch.setattr(module, "read_protected_symbols", lambda factory: frozenset({"ZZPFA"}))
    entered = asyncio.Event()
    blocker = asyncio.Event()

    class BlockingProvider(Provider):
        async def subscribe(self, symbols):
            entered.set()
            await blocker.wait()
            await super().subscribe(symbols)

    provider = BlockingProvider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    try:
        owner.start()
        await asyncio.wait_for(entered.wait(), 2)
        await owner.reconcile_once()
        assert provider.calls == []
        await asyncio.wait_for(owner.stop(), 2)
        blocker.set()
        await asyncio.sleep(0)
        assert provider.calls == []
    finally:
        blocker.set()
        await owner.stop()
        broker_registry.clear_all()


@pytest.mark.asyncio
async def test_periodic_owner_observes_new_exposure(monkeypatch):
    import app.services.protected_feed_reconciliation as module

    needs = {"ZZPFA"}
    monkeypatch.setattr(module, "read_protected_symbols", lambda factory: frozenset(needs))
    requested = asyncio.Event()

    class SignallingProvider(Provider):
        async def subscribe(self, symbols):
            await super().subscribe(symbols)
            requested.set()

    provider = SignallingProvider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=0.01)
    try:
        owner.start()
        await asyncio.wait_for(requested.wait(), 2)
        assert provider.calls == ["ZZPFA"]
        requested.clear()
        needs.add("ZZPFB")
        await asyncio.wait_for(requested.wait(), 2)
        assert provider.calls == ["ZZPFA", "ZZPFB"]
    finally:
        await owner.stop()
        broker_registry.clear_all()


@pytest.mark.asyncio
async def test_protected_tick_reaches_monitor_and_venue_outside_scanner(monkeypatch):
    import app.services.protected_feed_reconciliation as module

    symbol = "ZZPFX"
    scanner_universe = {"AAPL"}
    assert symbol not in scanner_universe
    monkeypatch.setattr(module, "read_protected_symbols", lambda factory: frozenset({symbol}))
    bus = EventBus()
    provider = Provider()
    bridge = None
    venue = SimulatedVenue(event_bus=bus, clock=MarketClock())
    position = PositionView(position_id=uuid4(), symbol=symbol, side="BUY", qty=1,
                            stop=95.0, target=110.0, opened_at=TS)

    class Reader:
        def get_open_positions(self):
            return (position,)

    observed = asyncio.Event()
    monitor = PositionMonitor(bus, Reader(), on_exit_intent=lambda intent: observed.set(),
                              pulse_interval_seconds=None)
    owner = ProtectedFeedReconciler(SessionLocal)
    await bus.start()
    await venue.connect()
    monitor.start()
    try:
        bridge = TickIngestBridge(provider, bus)
        await broker_registry.take_over_streaming(provider, bridge)
        await owner.reconcile_once()
        assert provider.calls == [symbol]
        # An independent venue order and held monitor position both consume
        # the same existing PriceUpdated path from the protected stream.
        monkeypatch.setattr(venue._clock, "is_regular_session", lambda ts=None: True)
        ack = await venue.place_order(OrderInstruction(client_order_id="protected:entry", symbol=symbol,
                                                       side="BUY", qty=1))
        assert ack.status == "submitted"
        provider.emit(symbol, 94.0)
        await asyncio.wait_for(observed.wait(), 2)
        async def filled():
            while (await venue.get_order("protected:entry")).status != "filled":
                await asyncio.sleep(0)
        await asyncio.wait_for(filled(), 2)
    finally:
        await owner.stop()
        await monitor.stop()
        await venue.disconnect()
        broker_registry.clear_all()
        await bus.stop()
