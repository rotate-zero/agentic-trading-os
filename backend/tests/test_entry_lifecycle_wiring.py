"""
End-to-end entry-lifecycle integration tests (entry-lifecycle-wiring) —
real Postgres, a real EventBus, and the actual production classes
main.py's lifespan() now wires together: AuthorizerStub with the real
PostgresTradeLedger + the new governor.PortfolioStateAdapter,
ExecutionEngine with the real PostgresOrderLedger + the new
PostgresFillLedger, a real SimulatedVenue, and PortfolioState's own
event worker with the real PostgresPositionLedger.

Only MarketClock (session-hours gate) and ReferencePriceTracker (rule
5's sizing input) are test doubles — both are pure, DB-free, and
already covered by decision execution-authorizer-and-engine's own
tests; faking them here keeps this suite deterministic without
weakening what it actually proves (the new adapters and their wiring,
not the rule pipeline itself).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

import app.governor.engine as governor_engine_module
from app.broker_adapters.simulated_venue import SimulatedVenue
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.execution_engine.engine import ExecutionEngine
from app.execution_engine.fill_ledger import PostgresFillLedger
from app.execution_engine.postgres import PostgresOrderLedger
from app.governor.engine import AuthorizerStub
from app.governor.portfolio_state_reader import PortfolioStateAdapter
from app.governor.postgres import PostgresTradeLedger
from app.models.execution_ledger import (
    Fill,
    Order,
    PortfolioStateCursor,
    Position,
    PositionFillReceipt,
    Trade,
    TradeReservation,
)
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.postgres import PostgresPositionLedger
from app.schemas.events.envelope import EventEnvelope, EventType
from app.strategy_engine.base_strategy import Opportunity
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots

NAME = "TEST_ENTRY_LIFECYCLE_WIRING"
SYMBOL = "ZZLW"  # dedicated symbol — avoids colliding with other suites' fixture data
NOW = datetime.now(timezone.utc)


def clean():
    with SessionLocal.begin() as s:
        ids = select(Trade.trade_id).where(Trade.strategy_name == NAME)
        orders = select(Order.client_order_id).where(Order.trade_id.in_(ids))
        fills = select(Fill.ledger_seq).where(Fill.client_order_id.in_(orders))
        s.query(PositionFillReceipt).filter(PositionFillReceipt.ledger_seq.in_(fills)).delete(synchronize_session=False)
        s.query(Fill).filter(Fill.client_order_id.in_(orders)).delete(synchronize_session=False)
        s.query(Position).filter(Position.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Order).filter(Order.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(TradeReservation).filter(TradeReservation.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Trade).filter(Trade.strategy_name == NAME).delete(synchronize_session=False)
        s.query(PortfolioStateCursor).delete()


@pytest.fixture(autouse=True)
def database():
    clean()
    yield
    clean()


class _FixedSessionClock(MarketClock):
    def is_regular_session(self, ts=None) -> bool:
        return True


class _FakeReferencePriceTracker:
    """rule 5's sizing input — pure, DB-free, already covered by
    execution-authorizer-and-engine's own tests; faked here for
    determinism (see module docstring)."""

    def __init__(self, price: float | None) -> None:
        self._price = price

    def start(self, bus: EventBus) -> None:
        pass

    def stop(self) -> None:
        pass

    def get(self, symbol: str) -> float | None:
        return self._price


class _FixedVenueProvider:
    def __init__(self, venue) -> None:
        self._venue = venue

    def get_execution_venue(self):
        return self._venue


def _opportunity_payload(**overrides) -> dict:
    base = dict(
        strategy=NAME, version="test_v1", direction="BUY", confidence=0.8,
        structural_invalidation=90.0, structural_target=120.0,
        evidence={"conditions": {}, "reason": "test", "basis": "live"},
        status="actionable", setup_detected_at=NOW,
    )
    base.update(overrides)
    return Opportunity(**base).model_dump(mode="json")


@pytest.fixture
async def pipeline(monkeypatch):
    """Wires the same production classes main.py's lifespan() now
    constructs — a smaller, direct stand-in for booting the whole
    FastAPI app so each test doesn't pay that cost (the real lifespan
    sequencing itself is covered separately, in
    test_main_execution_pipeline.py)."""
    monkeypatch.setattr(
        governor_engine_module, "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots(market_state={"trend_score": 1.0}, context={"news": []}),
    )
    bus = EventBus()
    await bus.start()
    published: list[EventEnvelope] = []
    bus.subscribe_all(lambda env: published.append(env))

    venue = SimulatedVenue(clock=_FixedSessionClock())
    await venue.connect()

    position_ledger = PostgresPositionLedger(SessionLocal)
    portfolio_state = PortfolioState(execution_mode="simulated", ledger=position_ledger, bus=bus)
    await portfolio_state.start()

    order_ledger = PostgresOrderLedger(SessionLocal)
    trade_ledger = PostgresTradeLedger(SessionLocal)
    fill_ledger = PostgresFillLedger(SessionLocal)
    portfolio_reader = PortfolioStateAdapter(portfolio_state, SessionLocal)

    authorizer = AuthorizerStub(
        bus, trade_ledger, portfolio_reader,
        market_clock=_FixedSessionClock(), reference_price_tracker=_FakeReferencePriceTracker(100.0),
    )
    authorizer.start()

    # order_ledger doubles as decision_authorization — same real
    # PostgresOrderLedger implements both Protocols (see its module
    # docstring / the decision entry).
    engine = ExecutionEngine(
        bus, order_ledger, order_ledger, fill_ledger=fill_ledger, venue_provider=_FixedVenueProvider(venue)
    )
    engine.start()

    yield bus, published, venue, portfolio_state

    await engine.stop()
    await authorizer.stop()
    await portfolio_state.stop()
    await venue.disconnect()
    await bus.stop()


async def test_opportunity_to_open_position_end_to_end(pipeline):
    """The core delivery proof: OpportunityCreated in, an open,
    fill-backed Position out, entirely through real Postgres rows and
    the real production classes — no fakes anywhere in the persistence
    path."""
    bus, published, venue, portfolio_state = pipeline

    await bus.publish(
        EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=SYMBOL, payload=_opportunity_payload())
    )
    await asyncio.sleep(0.3)

    types = [e.event_type for e in published]
    assert types.count(EventType.TRADE_PLANNED) == 1
    assert types.count(EventType.GOVERNOR_DECISION) == 1
    assert types.count(EventType.ORDER_APPROVED) == 1

    client_order_id = next(e for e in published if e.event_type == EventType.ORDER_APPROVED).payload["order_id"]

    with SessionLocal() as s:
        trade = s.scalar(select(Trade).where(Trade.strategy_name == NAME))
        assert trade is not None and trade.decision == "approved"
        reservation = s.scalar(select(TradeReservation).where(TradeReservation.trade_id == trade.trade_id))
        assert reservation is not None
        order_row = s.scalar(select(Order).where(Order.client_order_id == client_order_id))
        assert order_row is not None
        assert order_row.status == "submitted"
        assert order_row.execution_venue == venue.venue_id

    # Drive the venue's own fill — a real market tick at the reference price.
    venue.ingest_tick(SYMBOL, 100.0, NOW)
    await asyncio.sleep(0.3)

    with SessionLocal() as s:
        order_row = s.scalar(select(Order).where(Order.client_order_id == client_order_id))
        assert order_row.status == "filled"
        fill_row = s.scalar(select(Fill).where(Fill.client_order_id == client_order_id))
        assert fill_row is not None
        assert fill_row.qty == order_row.qty
        assert fill_row.anomaly is None
        assert fill_row.execution_venue == venue.venue_id

    assert EventType.ORDER_FILLED in [e.event_type for e in published]

    await portfolio_state.refresh()
    snapshot = portfolio_state.get_snapshot(trading_day=date.today())
    assert snapshot is not None
    assert len(snapshot.exposures) == 1
    assert snapshot.exposures[0].symbol == SYMBOL
    assert snapshot.exposures[0].qty == order_row.qty
    assert snapshot.exposures[0].is_in_flight is False

    with SessionLocal() as s:
        trade = s.scalar(select(Trade).where(Trade.strategy_name == NAME))
        position = s.scalar(select(Position).where(Position.trade_id == trade.trade_id))
        assert position is not None
        assert position.status == "open"
        assert position.qty == order_row.qty
        assert position.symbol == SYMBOL

    # And the same governor.PortfolioStateAdapter an authorizer would
    # consult for the NEXT opportunity now correctly reports this
    # symbol as busy (rule 4) — proving the read side, not just the
    # write side, is wired end to end.
    reader = PortfolioStateAdapter(portfolio_state, SessionLocal)
    live_snapshot = reader.get_snapshot("simulated", date.today())
    assert any(e.symbol == SYMBOL for e in live_snapshot.exposures)


async def test_rejected_opportunity_never_reaches_the_execution_pipeline(pipeline):
    bus, published, venue, portfolio_state = pipeline
    # A BUY whose stop is ABOVE the 100.0 reference price is invalid
    # geometry (rule 5) — rejected before any order/ledger write.
    payload = _opportunity_payload(structural_invalidation=150.0)
    await bus.publish(EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=SYMBOL, payload=payload))
    await asyncio.sleep(0.3)

    types = [e.event_type for e in published]
    assert EventType.PLAN_REJECTED in types
    assert EventType.ORDER_APPROVED not in types

    with SessionLocal() as s:
        trade = s.scalar(select(Trade).where(Trade.strategy_name == NAME))
        assert trade is not None and trade.decision == "rejected"
        assert s.scalar(select(Order).where(Order.trade_id == trade.trade_id)) is None


async def test_second_opportunity_for_a_busy_symbol_is_rejected_by_the_read_side(pipeline):
    """Proves governor.PortfolioStateAdapter's translation feeds real
    rejections, not just that it returns SOME snapshot: after the first
    entry fills, a second OpportunityCreated for the same symbol must
    be rejected (rule 4, symbol_busy) — this can only happen if the
    adapter's exposures actually reached rules.py."""
    bus, published, venue, portfolio_state = pipeline

    await bus.publish(
        EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=SYMBOL, payload=_opportunity_payload())
    )
    await asyncio.sleep(0.3)
    venue.ingest_tick(SYMBOL, 100.0, NOW)
    await asyncio.sleep(0.3)
    await portfolio_state.refresh()
    published.clear()

    await bus.publish(
        EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=SYMBOL, payload=_opportunity_payload())
    )
    await asyncio.sleep(0.3)

    types = [e.event_type for e in published]
    assert EventType.PLAN_REJECTED in types
    assert EventType.ORDER_APPROVED not in types
    rejection = next(e for e in published if e.event_type == EventType.PLAN_REJECTED)
    assert "symbol_busy" in rejection.payload["reasons"]
