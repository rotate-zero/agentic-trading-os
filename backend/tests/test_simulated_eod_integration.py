"""Real PostgreSQL, EventBus, monitor, Execution worker and SimulatedVenue EOD path."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.broker_adapters.simulated_venue import SimulatedVenue
from app.broker_adapters.order_venue import OrderInstruction
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.execution_engine.engine import ExecutionEngine
from app.execution_engine.exit_ledger import PostgresExitLedger
from app.execution_engine.exit_ledger import ExitLedgerError
from app.execution_engine.fill_ledger import PostgresFillLedger
from app.execution_engine.postgres import PostgresOrderLedger
from app.models.execution_ledger import ExitRequest, Fill, Order, PortfolioStateCursor, Position, PositionFillReceipt, Trade
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.postgres import PostgresPositionLedger
from app.position_monitor.engine import PositionMonitor
from app.position_monitor.engine import ExitIntent
from app.position_monitor.handoff import ObservationState
from app.position_monitor.portfolio_state_reader import PortfolioStatePositionReader
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated

NAME = "TEST_SIMULATED_EOD_INTEGRATION"
UTC = timezone.utc
OPENED = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
FLATTEN = datetime(2026, 9, 16, 19, 59, tzinfo=UTC)
CLOSE = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now=FLATTEN + timedelta(seconds=5)):
        self.now = now

    def __call__(self):
        return self.now


class VenueClock(MarketClock):
    def __init__(self, wall):
        super().__init__()
        self.wall = wall

    def is_regular_session(self, ts=None):
        return super().is_regular_session(ts or self.wall())


class Provider:
    def __init__(self, venue):
        self.venue = venue

    def get_execution_venue(self):
        return self.venue


def cleanup():
    with SessionLocal.begin() as s:
        ids = select(Trade.trade_id).where(Trade.strategy_name == NAME)
        orders = select(Order.client_order_id).where(Order.trade_id.in_(ids))
        positions = select(Position.position_id).where(Position.trade_id.in_(ids))
        s.query(PositionFillReceipt).filter(PositionFillReceipt.position_id.in_(positions)).delete(synchronize_session=False)
        s.query(ExitRequest).filter(ExitRequest.position_id.in_(positions)).delete(synchronize_session=False)
        s.query(Fill).filter(Fill.client_order_id.in_(orders)).delete(synchronize_session=False)
        s.query(Order).filter(Order.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Position).filter(Position.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Trade).filter(Trade.strategy_name == NAME).delete(synchronize_session=False)
        s.query(PortfolioStateCursor).filter(PortfolioStateCursor.execution_mode == "simulated").delete()


@pytest.fixture(autouse=True)
def clean_rows():
    cleanup()
    yield
    cleanup()


def seed(symbol="ZZEODINT", qty=5):
    with SessionLocal.begin() as s:
        trade = Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=NAME,
                      strategy_version="v1", direction="BUY", symbol=symbol, decision="approved", status="open")
        s.add(trade)
        s.flush()
        entry_id = f"{trade.trade_id}:entry"
        s.add(Order(client_order_id=entry_id, trade_id=trade.trade_id,
                    execution_mode="simulated", execution_venue="simulated", symbol=symbol,
                    side="BUY", position_effect="open", qty=qty, order_type="market", status="filled"))
        s.flush()
        s.add(Fill(client_order_id=entry_id, execution_venue="simulated", venue_fill_id=f"{entry_id}:f1",
                   qty=qty, price=100, venue_ts=OPENED))
        return trade.trade_id, symbol


async def position_after_restore(portfolio, trade_id):
    await portfolio.refresh()
    with SessionLocal.begin() as s:
        position = s.scalar(select(Position).where(Position.trade_id == trade_id))
        assert position is not None
        position.stop, position.target = 90, 120
        pid = position.position_id
    await portfolio.refresh()
    return pid


async def settle(bus, monitor, engine, portfolio):
    await bus._normal_queue.join()
    await monitor._queue.join()
    await engine._queue.join()
    await bus._critical_queue.join()
    await portfolio._queue.join()


def rows(pid):
    with SessionLocal() as s:
        request = s.get(ExitRequest, pid)
        orders = s.scalars(select(Order).where(Order.position_id == pid).order_by(Order.id)).all()
        fills = s.scalars(select(Fill).where(Fill.client_order_id.in_([o.client_order_id for o in orders]))).all()
        return request, orders, fills


@pytest.mark.asyncio
async def test_eod_attempt_then_real_late_fill_closes_once():
    trade_id, symbol = seed()
    wall = Clock()
    bus = EventBus()
    venue = SimulatedVenue(event_bus=bus, clock=VenueClock(wall))
    await bus.start()
    await venue.connect()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)
    ledger = PostgresExitLedger(SessionLocal, clock=wall)
    order_ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                             exit_ledger=ledger, portfolio_state=portfolio, venue_provider=Provider(venue))
    monitor = PositionMonitor(bus, PortfolioStatePositionReader(portfolio), wall_clock=wall,
                              pulse_interval_seconds=None, on_observation=engine.on_observation)
    engine.bind_position_monitor(monitor)
    engine.start()
    monitor.start()
    try:
        await bus.publish(make_envelope(EventType.PRICE_UPDATED,
            PriceUpdated(price=101, size=1, exchange_ts=OPENED + timedelta(hours=5)), symbol=symbol))
        await settle(bus, monitor, engine, portfolio)
        assert monitor.enqueue_pulse()
        await settle(bus, monitor, engine, portfolio)
        request, orders, fills = rows(pid)
        assert request.exit_reason == "eod_flatten"
        assert (request.eod_flatten_at, request.eod_close_at) == (FLATTEN, CLOSE)
        assert len(orders) == 1 and orders[0].status == "submitted"
        assert orders[0].exit_dispatch_started_at is not None and fills == []
        assert monitor.get_observations()[0].state is ObservationState.ACKNOWLEDGED
        assert monitor.enqueue_pulse()
        await settle(bus, monitor, engine, portfolio)
        assert len(rows(pid)[1]) == 1
        await bus.publish(make_envelope(EventType.CANDLE_CLOSED,
            CandleClosed(timeframe="1m", open=101, high=102, low=89, close=100, volume=1,
                         candle_ts=OPENED + timedelta(hours=5, minutes=1)), symbol=symbol))
        await settle(bus, monitor, engine, portfolio)
        request, orders, fills = rows(pid)
        assert request.fallback_reason == "stop"
        assert len(orders) == 1 and fills == []  # active EOD close remains exclusive
        wall.now = CLOSE + timedelta(minutes=1)
        # Venue fills an accepted order on a delivered after-hours tick.
        await bus.publish(make_envelope(EventType.PRICE_UPDATED,
            PriceUpdated(price=99, size=1, exchange_ts=wall.now), symbol=symbol))
        await settle(bus, monitor, engine, portfolio)
        await engine._service_exits()  # records expiry from persisted bounds
        request, orders, fills = rows(pid)
        with SessionLocal() as s:
            position = s.get(Position, pid)
            assert position.status == "closed" and position.qty == 0
        assert len(orders) == len(fills) == 1
        assert orders[0].status == "filled" and fills[0].venue_ts == wall.now
        assert await venue.get_order(orders[0].client_order_id) is not None
    finally:
        await monitor.stop()
        await engine.stop()
        await portfolio.stop()
        await venue.disconnect()
        await bus.stop()


@pytest.mark.asyncio
async def test_no_tick_no_order_and_missed_window():
    trade_id, _ = seed(symbol="ZZEODNONE")
    wall = Clock()
    bus = EventBus()
    await bus.start()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)
    monitor = PositionMonitor(bus, PortfolioStatePositionReader(portfolio), wall_clock=wall,
                              pulse_interval_seconds=None)
    monitor.start()
    try:
        monitor.enqueue_pulse()
        await monitor._queue.join()
        wall.now = CLOSE
        monitor.enqueue_pulse()
        await monitor._queue.join()
        assert rows(pid) == (None, [], [])
        assert monitor.pending_observations() == ()
    finally:
        await monitor.stop()
        await portfolio.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_protective_stop_before_pulse_prevents_eod_order():
    trade_id, symbol = seed(symbol="ZZEODSTOP")
    wall = Clock()
    bus = EventBus()
    await bus.start()
    venue = SimulatedVenue(event_bus=bus, clock=VenueClock(wall))
    await venue.connect()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)
    ledger = PostgresExitLedger(SessionLocal, clock=wall)
    order_ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                             exit_ledger=ledger, portfolio_state=portfolio, venue_provider=Provider(venue))
    monitor = PositionMonitor(bus, PortfolioStatePositionReader(portfolio), wall_clock=wall,
                              pulse_interval_seconds=None, on_observation=engine.on_observation)
    engine.bind_position_monitor(monitor)
    engine.start()
    monitor.start()
    try:
        await bus.publish(make_envelope(EventType.PRICE_UPDATED,
            PriceUpdated(price=89, size=1, exchange_ts=OPENED + timedelta(hours=5)), symbol=symbol))
        await settle(bus, monitor, engine, portfolio)
        monitor.enqueue_pulse()
        await settle(bus, monitor, engine, portfolio)
        request, orders, fills = rows(pid)
        assert request.exit_reason == "stop" and request.eod_flatten_at is None
        assert len(orders) == 1 and orders[0].exit_reason == "stop" and fills == []
        assert len(venue._orders) == 1
        assert [o.kind for o in monitor.get_observations()] == ["protective"]
    finally:
        await monitor.stop()
        await engine.stop()
        await portfolio.stop()
        await venue.disconnect()
        await bus.stop()


@pytest.mark.asyncio
async def test_venue_rejection_keeps_actual_reason_and_retries_once_before_close():
    trade_id, symbol = seed(symbol="ZZEODREJECT")
    wall = Clock()
    venue_clock = VenueClock(wall)
    venue_clock.allow = False
    original_is_open = venue_clock.is_regular_session
    venue_clock.is_regular_session = lambda ts=None: venue_clock.allow and original_is_open(ts)
    bus = EventBus()
    await bus.start()
    venue = SimulatedVenue(clock=venue_clock)
    await venue.connect()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)
    ledger = PostgresExitLedger(SessionLocal, clock=wall)
    order_ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                             exit_ledger=ledger, portfolio_state=portfolio, venue_provider=Provider(venue))
    engine.start()
    try:
        intent = ExitIntent(pid, symbol, "BUY", 5, "eod_flatten", 101,
                            OPENED + timedelta(hours=5), FLATTEN, CLOSE)
        assert ledger.observe_exit(intent).acknowledged
        await engine._service_exits()
        _, orders, fills = rows(pid)
        assert len(orders) == 1 and orders[0].status == "rejected"
        assert orders[0].reject_reason == "outside_regular_session" and fills == []
        await engine._service_exits()
        assert len(rows(pid)[1]) == 1  # retry delay
        venue_clock.allow = True
        wall.now += timedelta(seconds=6)
        await engine._service_exits()
        _, orders, fills = rows(pid)
        assert [o.status for o in orders] == ["rejected", "submitted"]
        assert [o.exit_reason for o in orders] == ["eod_flatten", "eod_flatten"]
        assert len(venue._orders) == 2 and fills == []
        await engine._service_exits()
        assert len(rows(pid)[1]) == 2
    finally:
        await engine.stop()
        await portfolio.stop()
        await venue.disconnect()
        await bus.stop()


@pytest.mark.asyncio
async def test_working_entry_is_cancelled_and_its_existing_fill_is_deduped_before_eod_close():
    trade_id, symbol = seed(symbol="ZZEODENTRY")
    wall = Clock()
    bus = EventBus()
    await bus.start()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)
    entry_id = f"{trade_id}:entry"
    with SessionLocal.begin() as s:
        entry = s.scalar(select(Order).where(Order.client_order_id == entry_id))
        entry.qty, entry.status = 10, "partially_filled"
    await portfolio.refresh()
    venue = SimulatedVenue(clock=VenueClock(wall), partial_fill_planner=lambda instruction:
                           [5, 5] if instruction.position_effect == "open" else [instruction.qty])
    await venue.connect()
    await venue.place_order(OrderInstruction(client_order_id=entry_id, symbol=symbol, side="BUY",
                                             qty=10, order_type="market", limit_price=None,
                                             position_effect="open"))
    venue.ingest_tick(symbol, 100, OPENED)  # venue reports the same already-committed entry fill
    ledger = PostgresExitLedger(SessionLocal, clock=wall)
    order_ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                             exit_ledger=ledger, portfolio_state=portfolio, venue_provider=Provider(venue))
    engine.start()
    try:
        assert ledger.observe_exit(ExitIntent(pid, symbol, "BUY", 5, "eod_flatten", 101,
                                        OPENED + timedelta(hours=5), FLATTEN, CLOSE)).acknowledged
        await engine._service_exits()
        await engine._queue.join()
        assert (await venue.get_order(entry_id)).status == "cancelled"
        with SessionLocal() as s:
            assert s.scalar(select(Order).where(Order.client_order_id == entry_id)).status == "cancelled"
            assert s.scalar(select(Fill).where(Fill.client_order_id == entry_id)) is not None
            assert len(s.scalars(select(Fill).where(Fill.client_order_id == entry_id)).all()) == 1
        await engine._service_exits()
        _, closes, fills = rows(pid)
        assert len(closes) == 1 and closes[0].qty == 5 and closes[0].status == "submitted"
        assert fills == [] and len(venue._orders) == 2
    finally:
        await engine.stop()
        await portfolio.stop()
        await venue.disconnect()
        await bus.stop()


@pytest.mark.parametrize("fault", ["venue_exception", "lost_status_commit"])
@pytest.mark.asyncio
async def test_claimed_eod_call_is_never_blindly_resent_after_uncertainty(monkeypatch, fault):
    trade_id, symbol = seed(symbol="ZZEODUNCERTAIN")
    wall = Clock()
    bus = EventBus()
    await bus.start()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)

    class CountingVenue(SimulatedVenue):
        close_calls = 0

        async def place_order(self, instruction):
            if instruction.position_effect == "close":
                self.close_calls += 1
                if fault == "venue_exception":
                    raise RuntimeError("simulated lost venue acknowledgement")
            return await super().place_order(instruction)

    venue = CountingVenue(clock=VenueClock(wall))
    await venue.connect()
    ledger = PostgresExitLedger(SessionLocal, clock=wall)
    order_ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                             exit_ledger=ledger, portfolio_state=portfolio, venue_provider=Provider(venue))
    if fault == "lost_status_commit":
        def fail_status(*args, **kwargs):
            raise ExitLedgerError("simulated lost status commit")
        monkeypatch.setattr(ledger, "set_status", fail_status)
    try:
        assert ledger.observe_exit(ExitIntent(pid, symbol, "BUY", 5, "eod_flatten", 101,
                                        OPENED + timedelta(hours=5), FLATTEN, CLOSE)).acknowledged
        await engine._service_exits()
        await engine._service_exits()
        request, orders, fills = rows(pid)
        assert request.exit_reason == "eod_flatten"
        assert len(orders) == 1 and orders[0].status == "approved"
        assert orders[0].exit_dispatch_started_at is not None and fills == []
        assert venue.close_calls == 1
        if fault == "venue_exception":
            assert await venue.get_order(orders[0].client_order_id) is None
        else:
            assert (await venue.get_order(orders[0].client_order_id)).status == "submitted"
    finally:
        await portfolio.stop()
        await venue.disconnect()
        await bus.stop()


@pytest.mark.asyncio
async def test_partial_venue_fills_keep_one_close_until_real_remaining_fill():
    trade_id, symbol = seed(symbol="ZZEODPART")
    wall = Clock()
    bus = EventBus()
    await bus.start()
    venue = SimulatedVenue(clock=VenueClock(wall), partial_fill_planner=lambda instruction: [3, 2])
    await venue.connect()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)
    ledger = PostgresExitLedger(SessionLocal, clock=wall)
    order_ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                             exit_ledger=ledger, portfolio_state=portfolio, venue_provider=Provider(venue))
    engine.start()
    try:
        assert ledger.observe_exit(ExitIntent(pid, symbol, "BUY", 5, "eod_flatten", 101,
                                        OPENED + timedelta(hours=5), FLATTEN, CLOSE)).acknowledged
        await engine._service_exits()
        order_id = rows(pid)[1][0].client_order_id
        venue.ingest_tick(symbol, 99, wall.now)
        await engine._queue.join()
        await bus._critical_queue.join()
        await portfolio._queue.join()
        with SessionLocal() as s:
            assert s.get(Position, pid).qty == 2
        _, orders, fills = rows(pid)
        assert len(orders) == 1 and orders[0].status == "partially_filled"
        assert len(fills) == 1 and fills[0].qty == 3
        wall.now = CLOSE + timedelta(minutes=1)
        await engine._service_exits()
        assert len(rows(pid)[1]) == 1 and rows(pid)[0].eod_expired_at is not None
        venue.ingest_tick(symbol, 98, wall.now)
        await engine._queue.join()
        await bus._critical_queue.join()
        await portfolio._queue.join()
        with SessionLocal() as s:
            assert s.get(Position, pid).status == "closed"
        _, orders, fills = rows(pid)
        assert len(orders) == 1 and orders[0].client_order_id == order_id
        assert [f.qty for f in fills] == [3, 2]
    finally:
        await engine.stop()
        await portfolio.stop()
        await venue.disconnect()
        await bus.stop()


@pytest.mark.asyncio
async def test_failed_eod_commit_retains_both_ordered_slots_until_replay(monkeypatch):
    trade_id, symbol = seed(symbol="ZZEODORDER")
    wall = Clock()
    bus = EventBus()
    await bus.start()
    portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await portfolio.start()
    pid = await position_after_restore(portfolio, trade_id)
    ledger = PostgresExitLedger(SessionLocal, clock=wall)
    order_ledger = PostgresOrderLedger(SessionLocal)
    engine = ExecutionEngine(bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                             exit_ledger=ledger, portfolio_state=portfolio, venue_provider=Provider(None))
    monitor = PositionMonitor(bus, PortfolioStatePositionReader(portfolio), wall_clock=wall,
                              pulse_interval_seconds=None)
    engine.bind_position_monitor(monitor)
    monitor.start()
    try:
        await bus.publish(make_envelope(EventType.PRICE_UPDATED,
            PriceUpdated(price=101, size=1, exchange_ts=OPENED + timedelta(hours=5)), symbol=symbol))
        await bus._normal_queue.join()
        await monitor._queue.join()
        monitor.enqueue_pulse()
        await monitor._queue.join()
        await bus.publish(make_envelope(EventType.CANDLE_CLOSED,
            CandleClosed(timeframe="1m", open=101, high=102, low=89, close=100, volume=1,
                         candle_ts=OPENED + timedelta(hours=5, minutes=1)), symbol=symbol))
        await bus._normal_queue.join()
        await monitor._queue.join()
        assert [o.kind for o in monitor.pending_observations()] == ["eod", "protective"]
        original = ledger.observe_exit
        seen = []

        def fail_first(intent):
            seen.append(intent.exit_reason)
            if len(seen) == 1:
                raise RuntimeError("injected transaction failure")
            return original(intent)

        monkeypatch.setattr(ledger, "observe_exit", fail_first)
        await engine._service_exits()
        assert seen == ["eod_flatten"]  # later same-position work cannot leapfrog a failed commit
        assert rows(pid)[0] is None
        assert len(monitor.pending_observations()) == 2
        await engine._service_exits()
        assert seen == ["eod_flatten", "eod_flatten", "stop"]
        assert monitor.pending_observations() == ()
        assert [o.state for o in monitor.get_observations()] == [
            ObservationState.ACKNOWLEDGED, ObservationState.ACKNOWLEDGED]
        request, orders, fills = rows(pid)
        assert request.exit_reason == "eod_flatten" and request.fallback_reason == "stop"
        assert len(orders) == 1 and orders[0].status == "approved" and fills == []
    finally:
        await monitor.stop()
        await portfolio.stop()
        await bus.stop()


def test_real_lifespan_eod_and_fresh_venue_restart_block(monkeypatch):
    import app.broker_adapters.simulated_venue as venue_module
    import app.execution_engine.exit_ledger as exit_module
    import app.position_monitor.engine as monitor_module
    from app.main import app as fastapi_app
    from app.event_bus.bus import get_event_bus
    from tests.test_main_execution_pipeline import _reset_singletons

    trade_id, symbol = seed(symbol="ZZEODLIFE")
    wall = Clock()

    async def restore_entry():
        bus = EventBus()
        await bus.start()
        portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
        try:
            await portfolio.start()
            return await position_after_restore(portfolio, trade_id)
        finally:
            await portfolio.stop()
            await bus.stop()

    pid = asyncio.run(restore_entry())
    original_ledger = exit_module.PostgresExitLedger
    original_monitor = monitor_module.PositionMonitor
    original_venue = venue_module.SimulatedVenue
    monkeypatch.setattr(exit_module, "PostgresExitLedger",
                        lambda sessions: original_ledger(sessions, clock=wall))

    class TimedMonitor(original_monitor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, wall_clock=wall, pulse_interval_seconds=None, **kwargs)

    monkeypatch.setattr(monitor_module, "PositionMonitor", TimedMonitor)
    created = []

    class RetainedVenue(original_venue):
        async def connect(self):
            await super().connect()
            if not self._orders:
                entry_id = f"{trade_id}:entry"
                await self.place_order(OrderInstruction(client_order_id=entry_id, symbol=symbol, side="BUY",
                                                        qty=5, order_type="market", limit_price=None,
                                                        position_effect="open"))
                self.ingest_tick(symbol, 100, OPENED)

    def make_venue(*, event_bus):
        if len(created) == 1:
            venue = created[0]
            venue._event_bus = event_bus  # retained book, new process bus
        else:
            venue = (RetainedVenue if not created else original_venue)(
                event_bus=event_bus, clock=VenueClock(wall))
        created.append(venue)
        return venue

    monkeypatch.setattr(venue_module, "SimulatedVenue", make_venue)
    with TestClient(fastapi_app) as client:
        assert client.get("/health/execution-startup").json()["status"] == "ready"
        bus = get_event_bus()
        client.portal.call(bus.publish, make_envelope(EventType.PRICE_UPDATED,
            PriceUpdated(price=101, size=1, exchange_ts=OPENED + timedelta(hours=5)), symbol=symbol))
        client.portal.call(asyncio.sleep, 0.05)
        monitor = fastapi_app.state.position_monitor
        assert monitor.enqueue_pulse()
        client.portal.call(asyncio.sleep, 0.1)
        request, orders, fills = rows(pid)
        assert request.exit_reason == "eod_flatten"
        assert len(orders) == 1 and orders[0].status == "submitted" and fills == []
        client.portal.call(bus.publish, make_envelope(EventType.CANDLE_CLOSED,
            CandleClosed(timeframe="1m", open=101, high=102, low=89, close=100, volume=1,
                         candle_ts=OPENED + timedelta(hours=5, minutes=1)), symbol=symbol))
        client.portal.call(asyncio.sleep, 0.1)
        assert rows(pid)[0].fallback_reason == "stop"
        assert len(rows(pid)[1]) == 1
        assert len(created[0]._orders) == 2  # entry and exactly one EOD close
    assert not created[0]._subscribed and created[0]._callbacks == []
    wall.now = CLOSE + timedelta(seconds=1)
    _reset_singletons()
    with TestClient(fastapi_app) as client:
        assert client.get("/health/execution-startup").json()["status"] == "ready"
        monitor = fastapi_app.state.position_monitor
        assert len(monitor.get_observations()) == 2
        assert monitor.get_observations()[0].state is ObservationState.EXPIRED
        assert monitor.get_observations()[1].state is ObservationState.ACKNOWLEDGED
        assert rows(pid)[0].eod_expired_at is not None
        monitor.enqueue_pulse()
        client.portal.call(asyncio.sleep, 0.1)
        assert len(rows(pid)[1]) == 1 and len(created[1]._orders) == 2
    _reset_singletons()
    with TestClient(fastapi_app) as client:
        assert client.get("/health/execution-startup").json()["status"] == "reconciliation_blocked"
        assert fastapi_app.state.position_monitor is None
        assert rows(pid)[1][0].status == "expired"  # fresh venue lost the submitted order
        assert len(created[2]._orders) == 0  # no orphaned close was placed


@pytest.mark.parametrize("after_close", [False, True])
def test_lifespan_revalidates_proven_unsent_eod_reservation(monkeypatch, after_close):
    import app.broker_adapters.simulated_venue as venue_module
    import app.execution_engine.exit_ledger as exit_module
    import app.position_monitor.engine as monitor_module
    from app.main import app as fastapi_app

    trade_id, symbol = seed(symbol="ZZEODUNSENT")
    wall = Clock()

    async def restore_entry():
        bus = EventBus()
        await bus.start()
        portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
        try:
            await portfolio.start()
            return await position_after_restore(portfolio, trade_id)
        finally:
            await portfolio.stop()
            await bus.stop()

    pid = asyncio.run(restore_entry())
    original_ledger = exit_module.PostgresExitLedger
    ledger = original_ledger(SessionLocal, clock=wall)
    assert ledger.observe_exit(ExitIntent(pid, symbol, "BUY", 5, "eod_flatten", 101,
                                   OPENED + timedelta(hours=5), FLATTEN, CLOSE)).acknowledged
    reservation = ledger.prepare_exit(pid).action.client_order_id
    wall.now = CLOSE + timedelta(seconds=1) if after_close else FLATTEN + timedelta(seconds=10)
    original_monitor = monitor_module.PositionMonitor
    original_venue = venue_module.SimulatedVenue
    monkeypatch.setattr(exit_module, "PostgresExitLedger", lambda sessions: original_ledger(sessions, clock=wall))

    class TimedMonitor(original_monitor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, wall_clock=wall, pulse_interval_seconds=None, **kwargs)

    class RetainedEntryVenue(original_venue):
        async def connect(self):
            await super().connect()
            saved_clock = self._clock
            self._clock = VenueClock(Clock())  # entry was accepted earlier in the session
            entry_id = f"{trade_id}:entry"
            await self.place_order(OrderInstruction(client_order_id=entry_id, symbol=symbol, side="BUY",
                                                    qty=5, order_type="market", limit_price=None,
                                                    position_effect="open"))
            self._clock = saved_clock
            self.ingest_tick(symbol, 100, OPENED)

    created = []

    def make_venue(*, event_bus):
        venue = RetainedEntryVenue(event_bus=event_bus, clock=VenueClock(wall))
        created.append(venue)
        return venue

    monkeypatch.setattr(monitor_module, "PositionMonitor", TimedMonitor)
    monkeypatch.setattr(venue_module, "SimulatedVenue", make_venue)
    with TestClient(fastapi_app) as client:
        assert client.get("/health/execution-startup").json()["status"] == "ready"
        client.portal.call(asyncio.sleep, 0.6)
        request, orders, fills = rows(pid)
        assert len(orders) == 1 and orders[0].client_order_id == reservation and fills == []
        if after_close:
            assert request.eod_expired_at is not None
            assert orders[0].status == "cancelled" and orders[0].reject_reason == "eod_window_closed"
            assert orders[0].exit_dispatch_started_at is None
            assert len(created[0]._orders) == 1  # entry only; no orphaned close
            assert fastapi_app.state.position_monitor.get_observations()[0].state is ObservationState.EXPIRED
        else:
            assert orders[0].status == "submitted" and orders[0].exit_dispatch_started_at is not None
            assert len(created[0]._orders) == 2
            assert fastapi_app.state.position_monitor.get_observations()[0].state is ObservationState.ACKNOWLEDGED
