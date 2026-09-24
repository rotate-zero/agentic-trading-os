"""Real PostgreSQL authorization/order adapters and reservation handoff."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.execution_engine.engine import ExecutionEngine
from app.execution_engine.ports import OrderLedgerError, OrderRecord
from app.execution_engine.postgres import PostgresOrderLedger
from app.models.execution_ledger import Order, Trade, TradeReservation
from app.portfolio_state.postgres import PostgresPositionLedger
from app.schemas.events.envelope import EventType
from tests.test_execution_engine import _FakeVenue, _FakeVenueProvider
from tests.test_position_ledger_postgres import NAME, TS, application, database, source  # noqa: F401


def reserve():
    """Seed committed approval facts independently of the writer under test."""
    trade_id = uuid4()
    with SessionLocal.begin() as s:
        s.add(Trade(trade_id=trade_id, execution_mode="simulated", execution_venue="simulated",
            strategy_name=NAME, strategy_version="1", symbol="AAPL", direction="BUY",
            decision="approved", thesis={"final_stop": 90, "final_target": 110}, status="open"))
        s.flush()
        s.add(TradeReservation(trade_id=trade_id, client_order_id=f"{trade_id}:entry", qty=10, reference_price="100.123456789"))
    return OrderRecord(f"{trade_id}:entry", str(trade_id), "AAPL", "BUY", "open", 10,
                       "market", None, "simulated", "approved", TS)


def test_reservation_restores_before_order_and_handoff_counts_once():
    record = reserve()
    portfolio = PostgresPositionLedger(SessionLocal)
    state = portfolio.load_state("simulated")
    assert len(state.orders) == 1
    assert state.orders[0].remaining_qty == 10
    assert str(state.orders[0].reference_price) == "100.123456789"
    assert portfolio.get_order(record.client_order_id) == state.orders[0]
    orders = PostgresOrderLedger(SessionLocal)
    inserted = orders.insert_order(record)
    assert inserted.inserted
    assert inserted.order.execution_venue == "simulated"
    assert portfolio.load_state("simulated").orders == state.orders
    source(record.client_order_id, qty=4)
    state = portfolio.commit_fill(application(portfolio)).state
    assert state.positions[0].qty == 4
    assert len(state.orders) == 1
    assert state.orders[0].remaining_qty == 6
    assert str(state.orders[0].reference_price) == "100.123456789"
    with SessionLocal.begin() as s:
        s.scalar(select(Order)).status = "cancelled"
    assert portfolio.load_state("simulated").orders == ()
    assert portfolio.get_order(record.client_order_id).status == "cancelled"


@pytest.mark.parametrize("changes", [
    {"symbol": "MSFT"}, {"qty": 11}, {"side": "SELL"}, {"execution_mode": "paper"},
    {"execution_mode": None}, {"position_effect": "close"}, {"order_type": "limit", "limit_price": 100},
    {"execution_venue": "ibkr"}, {"client_order_id": "tampered:entry"}, {"status": "submitted"},
])
def test_order_must_match_approved_terms(changes):
    record = reserve()
    with pytest.raises(OrderLedgerError, match="terms differ"):
        PostgresOrderLedger(SessionLocal).insert_order(replace(record, **changes))
    with SessionLocal() as s:
        assert s.scalar(select(Order)) is None
    assert PostgresPositionLedger(SessionLocal).load_state("simulated").orders[0].qty == 10


def test_concurrent_duplicate_orders_and_conflicting_retry():
    record = reserve()
    barrier = Barrier(2)
    def insert():
        barrier.wait(timeout=5)
        return PostgresOrderLedger(SessionLocal).insert_order(record)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(insert) for _ in range(2)]
        results = [f.result(timeout=5) for f in futures]
    assert sorted(r.inserted for r in results) == [False, True]
    assert results[0].order == results[1].order
    with pytest.raises(OrderLedgerError):
        PostgresOrderLedger(SessionLocal).insert_order(replace(record, qty=1))


@pytest.mark.parametrize("terminal", ["filled", "cancelled", "expired", "rejected", "partially_filled", "unknown"])
def test_stale_status_cannot_regress_progress_or_terminal_orders(terminal):
    record = reserve()
    orders = PostgresOrderLedger(SessionLocal)
    orders.insert_order(record)
    with SessionLocal.begin() as s:
        s.scalar(select(Order)).status = terminal
    orders.update_order_status(record.client_order_id, "submitted", execution_venue="simulated")
    orders.update_order_status(record.client_order_id, "rejected", reason="stale")
    assert orders.insert_order(record).order.status == terminal


def test_rejected_candidate_venue_does_not_relabel_approved_capital():
    record = reserve()
    orders = PostgresOrderLedger(SessionLocal)
    orders.insert_order(record)
    orders.update_order_status(record.client_order_id, "rejected", reason="mode_not_supported", execution_venue="ibkr")
    stored = orders.insert_order(record).order
    assert stored.status == "rejected"
    assert stored.execution_venue == "simulated"
    assert PostgresPositionLedger(SessionLocal).load_state("simulated").orders == ()


@pytest.mark.asyncio
async def test_execution_durable_insert_before_venue_and_no_duplicate_submission():
    record = reserve()
    orders = PostgresOrderLedger(SessionLocal)
    portfolio = PostgresPositionLedger(SessionLocal)
    class ObservedVenue(_FakeVenue):
        async def place_order(self, instruction):
            with SessionLocal() as s:
                stored = s.scalar(select(Order))
                assert stored.status == "approved"
                assert stored.client_order_id == record.client_order_id
            assert len(portfolio.load_state("simulated").orders) == 1
            return await super().place_order(instruction)
    venue = ObservedVenue(venue_id="simulated")
    bus = EventBus()
    engine = ExecutionEngine(bus, orders, orders,
        venue_provider=_FakeVenueProvider(venue), execution_mode_provider=lambda: "simulated")
    payload = dict(order_id=record.client_order_id, symbol="AAPL", side="BUY", qty=10,
                   position_effect="open", order_type="market")
    await engine._process_one(payload)
    await engine._process_one(payload)
    assert len(venue.place_order_calls) == 1
    assert orders.insert_order(record).order.status == "submitted"


@pytest.mark.asyncio
async def test_rejection_commit_failure_publishes_nothing_and_retains_reservation():
    record = reserve()
    class FaultSession(Session):
        pass
    factory = sessionmaker(bind=SessionLocal.kw["bind"], class_=FaultSession, autoflush=False)
    def fail_rejection(session):
        if any(isinstance(row, Order) and row.status == "rejected" for row in session.dirty):
            raise OrderLedgerError("injected rejection commit failure")
    event.listen(FaultSession, "before_commit", fail_rejection)
    bus = EventBus()
    published = []
    bus.subscribe(EventType.ORDER_STATUS_CHANGED, published.append)
    await bus.start()
    orders = PostgresOrderLedger(factory)
    engine = ExecutionEngine(bus, orders, orders,
        venue_provider=_FakeVenueProvider(None), execution_mode_provider=lambda: "simulated")
    try:
        await engine._process_one(dict(order_id=record.client_order_id, symbol="AAPL", side="BUY", qty=10,
                                      position_effect="open", order_type="market"))
        await bus._critical_queue.join()
        assert published == []
        assert PostgresPositionLedger(SessionLocal).get_order(record.client_order_id).status == "approved"
    finally:
        await bus.stop()


def decision(**changes):
    from app.governor.ports import TradeDecisionRecord
    trade_id = str(uuid4())
    record = TradeDecisionRecord("AAPL", NAME, "1", "BUY", "approved", [],
        {"max_concurrent_positions": 1, "fixed_notional_usd": 1000.0, "daily_loss_cap_usd": 100.0},
        "simulated", True, True, 98.0, 110.0, 0.8, TS, TS,
        trade_id, f"{trade_id}:entry", 10, 100.0)
    return replace(record, **changes)


def test_authorizer_commit_persists_decision_and_reservation_atomically():
    from app.governor.postgres import PostgresTradeLedger
    record = decision()
    result = PostgresTradeLedger(SessionLocal).commit_decision(record)
    assert result.opportunity_id == record.opportunity_id
    order_ledger = PostgresOrderLedger(SessionLocal)
    assert order_ledger.has_committed_decision(record.opportunity_id)
    assert not order_ledger.has_committed_decision(str(uuid4()))
    assert not order_ledger.has_committed_decision("not-a-uuid")
    with SessionLocal() as s:
        row = s.scalar(select(Trade))
        assert row.thesis["final_stop"] == 98
        assert row.limits_snapshot == record.limits_snapshot
        assert row.decision_record["market_state_snapshot_present"] is True
        assert row.entry_market_state is None  # no invented fill-time snapshot
        assert s.scalar(select(Order)) is None
    restored = PostgresPositionLedger(SessionLocal).load_state("simulated")
    assert restored.orders[0].client_order_id == record.client_order_id
    assert restored.orders[0].reference_price == 100


@pytest.mark.parametrize("mode", ["simulated", "paper", "live", "backtest", None, "unknown", "unknown-mode-longer-than-sixteen-characters"])
def test_rejected_decision_is_audit_only(mode):
    from app.governor.postgres import PostgresTradeLedger
    record = decision(decision="rejected", execution_mode=mode, reasons=["outside_regular_session"],
                      opportunity_id=None, client_order_id=None, qty=None, reference_price=None)
    result = PostgresTradeLedger(SessionLocal).commit_decision(record)
    assert result.opportunity_id is None
    with SessionLocal() as s:
        row = s.scalar(select(Trade))
        assert row.decision == "rejected"
        assert row.execution_mode == mode
        assert row.execution_venue is None
        assert row.decision_record["execution_mode"] == mode
        assert row.status is None
        assert row.reasons == record.reasons
        assert s.scalar(select(TradeReservation)) is None
        audit_id = str(row.trade_id)
    assert not PostgresOrderLedger(SessionLocal).has_committed_decision(audit_id)
    assert PostgresPositionLedger(SessionLocal).load_state("simulated").orders == ()


def test_duplicate_decision_and_conflicting_approval():
    from app.governor.postgres import PostgresTradeLedger
    from app.governor.ports import LedgerCommitError
    record = decision()
    barrier = Barrier(2)
    def commit():
        barrier.wait(timeout=5)
        return PostgresTradeLedger(SessionLocal).commit_decision(record)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit) for _ in range(2)]
        assert [f.result(timeout=5).opportunity_id for f in futures] == [record.opportunity_id] * 2
    with SessionLocal() as s:
        assert len(s.scalars(select(Trade)).all()) == 1
        assert len(s.scalars(select(TradeReservation)).all()) == 1
    with pytest.raises(LedgerCommitError, match="conflicting"):
        PostgresTradeLedger(SessionLocal).commit_decision(replace(record, qty=20))


def test_trade_commit_rollback_and_lost_acknowledgement():
    from app.governor.postgres import PostgresTradeLedger
    from app.governor.ports import LedgerCommitError
    record = decision()
    class FaultSession(Session):
        pass
    factory = sessionmaker(bind=SessionLocal.kw["bind"], class_=FaultSession, autoflush=False)
    def fail_before(session):
        session.flush()
        assert session.scalar(select(TradeReservation)) is not None
        raise LedgerCommitError("injected before commit")
    event.listen(FaultSession, "before_commit", fail_before)
    with pytest.raises(LedgerCommitError):
        PostgresTradeLedger(factory).commit_decision(record)
    with SessionLocal() as s:
        assert s.scalar(select(Trade)) is None
        assert s.scalar(select(TradeReservation)) is None
    event.remove(FaultSession, "before_commit", fail_before)
    def lose_ack(session):
        raise LedgerCommitError("lost acknowledgement")
    event.listen(FaultSession, "after_commit", lose_ack)
    with pytest.raises(LedgerCommitError):
        PostgresTradeLedger(factory).commit_decision(record)
    assert PostgresOrderLedger(SessionLocal).has_committed_decision(record.opportunity_id)
    PostgresTradeLedger(SessionLocal).commit_decision(record)
    assert len(PostgresPositionLedger(SessionLocal).load_state("simulated").orders) == 1


@pytest.mark.asyncio
async def test_real_authorizer_publication_observes_durable_reservation(monkeypatch):
    from app.governor import engine as module
    from app.governor.engine import AuthorizerStub
    from app.governor.postgres import PostgresTradeLedger
    from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots
    from tests.test_governor_engine import _FakeMarketClock, _FakeReferencePriceTracker, _FakePortfolioState, _empty_portfolio, _opportunity_payload
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots(market_state={}, context={}))
    bus = EventBus()
    portfolio = PostgresPositionLedger(SessionLocal)
    seen = []
    async def published(envelope):
        state = await asyncio.to_thread(portfolio.load_state, "simulated")
        assert len(state.orders) == 1
        assert state.orders[0].client_order_id == envelope.payload["order_id"]
        seen.append(envelope)
    bus.subscribe(EventType.ORDER_APPROVED, published)
    await bus.start()
    authorizer = AuthorizerStub(bus, PostgresTradeLedger(SessionLocal), _FakePortfolioState(_empty_portfolio()),
        execution_mode_provider=lambda: "simulated", market_clock=_FakeMarketClock(), reference_price_tracker=_FakeReferencePriceTracker())
    payload = _opportunity_payload(strategy=NAME)
    try:
        await authorizer._process_one({"symbol": "AAPL", "payload": payload})
        await bus._critical_queue.join()
        assert len(seen) == 1
        orders = PostgresOrderLedger(SessionLocal)
        venue = _FakeVenue(venue_id="simulated")
        execution = ExecutionEngine(bus, orders, orders, venue_provider=_FakeVenueProvider(venue), execution_mode_provider=lambda: "simulated")
        await execution._process_one(seen[0].payload)
        assert len(venue.place_order_calls) == 1
        assert portfolio.get_order(seen[0].payload["order_id"]).status == "submitted"
    finally:
        await bus.stop()


@pytest.mark.parametrize("changes", [
    {"opportunity_id": None}, {"client_order_id": "wrong:entry"}, {"qty": 0},
    {"qty": True}, {"reference_price": float("nan")}, {"reference_price": "invalid"},
    {"execution_mode": None}, {"execution_mode": "unknown"}, {"execution_mode": "paper"},
    {"execution_mode": "live"}, {"execution_mode": "backtest"},
])
def test_invalid_approval_rolls_back_without_reservation(changes):
    from app.governor.postgres import PostgresTradeLedger
    from app.governor.ports import LedgerCommitError
    with pytest.raises(LedgerCommitError):
        PostgresTradeLedger(SessionLocal).commit_decision(decision(**changes))
    with SessionLocal() as s:
        assert s.scalar(select(Trade)) is None
        assert s.scalar(select(TradeReservation)) is None


def test_reservation_migration_refuses_destructive_downgrade():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    record = reserve()
    config = Config()
    config.set_main_option("script_location", "alembic")
    with pytest.raises(RuntimeError, match="durable authorization"):
        command.downgrade(config, "0013")
    assert PostgresPositionLedger(SessionLocal).get_order(record.client_order_id).qty == 10
    with SessionLocal() as s:
        assert s.scalar(text("SELECT version_num FROM alembic_version")) == "0014"


@pytest.mark.parametrize("mode,venue", [(None, None), ("simulated", None), (None, "simulated"), ("unknown", "ibkr"), ("paper", "simulated")])
def test_database_keeps_approved_execution_labels_strict(mode, venue):
    from sqlalchemy.exc import IntegrityError
    with SessionLocal() as s:
        s.add(Trade(execution_mode=mode, execution_venue=venue, strategy_name=NAME, strategy_version="1",
                    symbol="AAPL", direction="BUY", decision="approved", thesis={}))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


@pytest.mark.parametrize("mode", [None, "unknown", "paper", "live", "backtest"])
@pytest.mark.asyncio
async def test_authorizer_mode_rejection_is_committed_before_publication(monkeypatch, mode):
    from app.governor import engine as module
    from app.governor.engine import AuthorizerStub
    from app.governor.postgres import PostgresTradeLedger
    from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots
    from tests.test_governor_engine import _FakeMarketClock, _FakeReferencePriceTracker, _FakePortfolioState, _empty_portfolio, _opportunity_payload
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots(market_state={}, context={}))
    bus = EventBus()
    seen = []
    bus.subscribe_all(seen.append)
    await bus.start()
    authorizer = AuthorizerStub(bus, PostgresTradeLedger(SessionLocal), _FakePortfolioState(_empty_portfolio()),
        execution_mode_provider=lambda: mode, market_clock=_FakeMarketClock(), reference_price_tracker=_FakeReferencePriceTracker())
    try:
        await authorizer._process_one({"symbol": "AAPL", "payload": _opportunity_payload(strategy=NAME)})
        await bus._critical_queue.join()
        await bus._normal_queue.join()
        assert [e.event_type for e in seen] == [EventType.PLAN_REJECTED]
        assert seen[0].payload["reasons"] == ["execution_mode_not_permitted"]
        with SessionLocal() as s:
            row = s.scalar(select(Trade))
            assert row.decision == "rejected"
            assert row.execution_mode == mode
            assert row.execution_venue is None
            assert s.scalar(select(TradeReservation)) is None
    finally:
        await bus.stop()


@pytest.mark.parametrize("failure_point", ["before_commit", "after_commit"])
@pytest.mark.asyncio
async def test_uncertain_order_insert_never_submits_and_restart_preserves_exposure(failure_point):
    record = reserve()

    class FaultSession(Session):
        pass

    factory = sessionmaker(bind=SessionLocal.kw["bind"], class_=FaultSession, autoflush=False)

    def fail(session):
        if failure_point == "before_commit":
            session.flush()
            assert session.scalar(select(Order)) is not None
        raise OrderLedgerError("injected order commit failure")

    event.listen(FaultSession, failure_point, fail)
    healthy = PostgresOrderLedger(SessionLocal)
    venue = _FakeVenue(venue_id="simulated")
    bus = EventBus()
    published = []
    bus.subscribe_all(published.append)
    await bus.start()
    execution = ExecutionEngine(bus, PostgresOrderLedger(factory), healthy,
        venue_provider=_FakeVenueProvider(venue), execution_mode_provider=lambda: "simulated")
    try:
        await execution._process_one(dict(order_id=record.client_order_id, symbol="AAPL", side="BUY",
            qty=10, position_effect="open", order_type="market"))
        await bus._critical_queue.join()
        assert venue.place_order_calls == []
        assert published == []
        with SessionLocal() as session:
            assert (session.scalar(select(Order)) is not None) == (failure_point == "after_commit")
        restored = PostgresPositionLedger(SessionLocal).load_state("simulated")
        assert len(restored.orders) == 1
        assert restored.orders[0].remaining_qty == 10
        assert healthy.insert_order(record).inserted == (failure_point == "before_commit")
    finally:
        await bus.stop()
        event.remove(FaultSession, failure_point, fail)


@pytest.mark.asyncio
async def test_venue_claiming_simulated_support_cannot_replace_authorized_venue():
    record = reserve()
    orders = PostgresOrderLedger(SessionLocal)
    venue = _FakeVenue(venue_id="different-venue", supported_modes=frozenset({"simulated"}))
    bus = EventBus()
    published = []
    bus.subscribe(EventType.ORDER_STATUS_CHANGED, published.append)
    await bus.start()
    execution = ExecutionEngine(bus, orders, orders,
        venue_provider=_FakeVenueProvider(venue), execution_mode_provider=lambda: "simulated")
    try:
        await execution._process_one(dict(order_id=record.client_order_id, symbol="AAPL", side="BUY",
            qty=10, position_effect="open", order_type="market"))
        await bus._critical_queue.join()
        assert venue.place_order_calls == []
        assert len(published) == 1
        assert published[0].payload["reason"] == "execution_venue_mismatch"
        stored = orders.insert_order(record).order
        assert stored.status == "rejected"
        assert stored.execution_venue == "simulated"
        assert PostgresPositionLedger(SessionLocal).load_state("simulated").orders == ()
    finally:
        await bus.stop()


def test_migration_preserves_rejection_audit_without_reservations():
    from alembic import command
    from alembic.config import Config
    from app.governor.postgres import PostgresTradeLedger

    PostgresTradeLedger(SessionLocal).commit_decision(decision(
        decision="rejected", execution_mode=None, opportunity_id=None,
        client_order_id=None, qty=None, reference_price=None))
    config = Config()
    config.set_main_option("script_location", "alembic")
    with pytest.raises(RuntimeError, match="durable authorization"):
        command.downgrade(config, "0013")
    with SessionLocal() as session:
        assert session.scalar(select(Trade)).decision_record["decision"] == "rejected"
        assert session.scalar(select(TradeReservation)) is None
