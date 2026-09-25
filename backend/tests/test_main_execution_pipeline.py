"""
main.py's execution-pipeline startup wiring (entry-lifecycle-wiring,
scope item 5), exercised through the REAL FastAPI lifespan via
TestClient — not `reconcile_with_venue()` called directly (that
function's own correctness is already covered by
test_reconciliation.py's `test_submitted_order_venue_lost_state_
expires`). This file proves the WIRING: that main.py actually calls
rebuild -> connect -> reconcile -> resume in the right order, on the
right objects, on every real process boot.

`SimulatedVenue` is never durable across a restart (a fresh instance
every `lifespan()` call, by construction — see main.py) — so any
order left "submitted" by a process that then exits IS, by
definition, the "process died before an in-flight order got a fill"
case matching the task's own AC #9 shape; there is no separate way to
construct a "process died mid-fill" scenario against this venue
(a real, durable venue like IBKR would be a different story).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.context_engine.engine as context_engine_module
import app.context_engine.fundamentals_refresh as fundamentals_refresh_module
import app.event_bus.bus as bus_module
import app.execution_engine.engine as execution_engine_module
import app.feature_engine.engine as feature_engine_module
import app.governor.engine as governor_engine_module
import app.market_state_engine.engine as market_state_engine_module
import app.services.live_tick_relay as live_tick_relay_module
import app.strategy_engine.scheduler as strategy_scheduler_module
import app.trading_intelligence.level_interaction_engine as level_interaction_engine_module
import app.trading_intelligence.opportunity_cache as opportunity_cache_module
from app.api.websocket import channels as channels_module
from app.api.websocket import manager as manager_module
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import get_event_bus
from app.main import app as fastapi_app
from app.models.execution_ledger import (
    Fill,
    Order,
    PortfolioStateCursor,
    Position,
    PositionFillReceipt,
    Trade,
    TradeReservation,
)
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import PriceUpdated
from app.services import broker_registry
from app.strategy_engine.base_strategy import Opportunity
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots

NAME = "TEST_MAIN_EXECUTION_PIPELINE"
SYMBOL = "ZZMEP"
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


def _reset_singletons() -> None:
    """A mid-test repeat of conftest.py's own `_reset_app_singletons`
    body — needed here (and only here) because this test spans TWO
    separate `with TestClient(app)` blocks to simulate an actual process
    restart. Each `TestClient.__enter__`/`__exit__` tears down and
    rebuilds its own anyio portal/event loop; every cached singleton's
    asyncio primitives are bound to whichever loop first touched them
    (conftest.py's own docstring), so without this second, mid-test
    reset the SECOND `with` block would reuse loop-#1-bound objects on
    loop #2 and fail exactly the way conftest.py's docstring describes.
    Does not touch conftest.py itself — its own autouse reset still runs
    once at this test's start/end; this is an additional, local,
    mid-test-only repeat."""
    bus_module._event_bus = None
    manager_module._manager = None
    channels_module._gateway = None
    feature_engine_module._feature_engine = None
    level_interaction_engine_module._level_interaction_engine = None
    live_tick_relay_module._live_tick_relay = None
    context_engine_module._context_engine = None
    fundamentals_refresh_module._fundamentals_refresh_jobs = None
    market_state_engine_module._market_state_engine = None
    strategy_scheduler_module._strategy_scheduler = None
    opportunity_cache_module._opportunity_cache = None
    governor_engine_module._authorizer_stub = None
    execution_engine_module._execution_engine = None
    broker_registry.clear_all()


def _opportunity_payload(**overrides) -> dict:
    base = dict(
        strategy=NAME, version="test_v1", direction="BUY", confidence=0.8,
        structural_invalidation=90.0, structural_target=120.0,
        evidence={"conditions": {}, "reason": "test", "basis": "live"},
        status="actionable", setup_detected_at=NOW,
    )
    base.update(overrides)
    return Opportunity(**base).model_dump(mode="json")


def _price_envelope() -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.PRICE_UPDATED, symbol=SYMBOL,
        payload=PriceUpdated(price=100.0, size=100, exchange_ts=NOW).model_dump(mode="json"),
    )


def _opportunity_envelope() -> EventEnvelope:
    return EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=SYMBOL, payload=_opportunity_payload())


def test_orphaned_submitted_order_is_expired_on_restart_and_pipeline_resumes(monkeypatch):
    # Both AuthorizerStub (session-hours, rule 1) and SimulatedVenue (its
    # own independent session guard) default to the real MarketClock —
    # patching the shared base class covers both regardless of which
    # module's own get_market_clock() call produced the instance.
    monkeypatch.setattr(MarketClock, "is_regular_session", lambda self, ts=None: True)
    monkeypatch.setattr(
        governor_engine_module, "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots(market_state={"trend_score": 1.0}, context={"news": []}),
    )

    # --- "process" #1: submit an entry order, then die before any fill -----
    with TestClient(fastapi_app) as client:
        bus = get_event_bus()
        client.portal.call(bus.publish, _price_envelope())
        client.portal.call(bus.publish, _opportunity_envelope())
        client.portal.call(asyncio.sleep, 0.3)
        # No tick is ever sent — the order is still "submitted" at the
        # (about to be discarded) venue when this block exits.

    with SessionLocal() as s:
        trade = s.scalar(select(Trade).where(Trade.strategy_name == NAME))
        assert trade is not None, "OpportunityCreated never reached an approved decision — test setup is broken"
        order_row = s.scalar(select(Order).where(Order.trade_id == trade.trade_id))
        assert order_row is not None and order_row.status == "submitted"
        client_order_id = order_row.client_order_id

    _reset_singletons()

    # --- "process" #2: a fresh lifespan, fresh (empty-memory) SimulatedVenue
    with TestClient(fastapi_app) as client:
        assert client.get("/health").status_code == 200

        with SessionLocal() as s:
            order_row = s.scalar(select(Order).where(Order.client_order_id == client_order_id))
            assert order_row.status == "expired"
            assert order_row.reject_reason == "venue_lost_state_on_restart"

        # The pipeline itself must still be live after a clean (non-
        # discrepant — an expired stale order is not one, per
        # reconciliation.py's own ReconciliationReport.has_discrepancy)
        # reconciliation: a fresh Opportunity for the SAME symbol — now
        # free again, since the stale order expired without ever
        # becoming a position — is approved normally.
        bus = get_event_bus()
        published: list[EventEnvelope] = []
        bus.subscribe_all(lambda env: published.append(env))
        client.portal.call(bus.publish, _price_envelope())
        client.portal.call(bus.publish, _opportunity_envelope())
        client.portal.call(asyncio.sleep, 0.3)

        types = [e.event_type for e in published]
        assert EventType.ORDER_APPROVED in types, f"execution pipeline did not resume after restart: {types}"

    _reset_singletons()
