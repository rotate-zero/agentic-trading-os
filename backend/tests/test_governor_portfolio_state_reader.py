"""
Real PostgreSQL tests for governor/portfolio_state_reader.py's
PortfolioStateAdapter — the third and last concrete adapter
entry-lifecycle-wiring's scope item 3 required (OrderLedgerPort/
DecisionAuthorizationPort and TradeLedgerPort were already on `main`,
undocumented, before this task began — see the decision entry).
"""
from __future__ import annotations

from datetime import date

import pytest

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.governor.portfolio_state_reader import PortfolioStateAdapter, PortfolioStateUnavailable
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.postgres import PostgresPositionLedger
from tests.test_position_ledger_postgres import database, order, source  # noqa: F401


@pytest.fixture
async def started_portfolio_state():
    bus = EventBus()
    await bus.start()
    state = PortfolioState(execution_mode="simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
    await state.start()
    yield state
    await state.stop()
    await bus.stop()


async def test_mode_mismatch_is_refused_not_silently_answered(started_portfolio_state):
    adapter = PortfolioStateAdapter(started_portfolio_state, SessionLocal)
    with pytest.raises(PortfolioStateUnavailable, match="no Portfolio State wired"):
        adapter.get_snapshot("paper", date(2026, 1, 5))


async def test_not_ready_raises_rather_than_returning_a_default():
    # A never-started instance has _ready=False, _state=None — get_snapshot()
    # returns None per its own docstring ("None = not restored") — no bus
    # or worker needed to observe that from the read side.
    fresh = PortfolioState(execution_mode="simulated", ledger=PostgresPositionLedger(SessionLocal))
    adapter = PortfolioStateAdapter(fresh, SessionLocal)
    with pytest.raises(PortfolioStateUnavailable, match="not ready"):
        adapter.get_snapshot("simulated", date(2026, 1, 5))


async def test_unresolved_anomaly_halts_new_entries_per_i14(started_portfolio_state):
    order_id, _ = order(mode="simulated", qty=10)
    source(order_id, qty=10)
    source(order_id, qty=5, anomaly="overfill")  # a second, over-quantity fill
    await started_portfolio_state.refresh()

    adapter = PortfolioStateAdapter(started_portfolio_state, SessionLocal)
    with pytest.raises(PortfolioStateUnavailable, match="unresolved anomalous fill"):
        adapter.get_snapshot("simulated", date(2026, 1, 5))


async def test_open_position_translates_into_governors_own_snapshot_shape(started_portfolio_state):
    order_id, _ = order(mode="simulated", qty=4)
    source(order_id, qty=4)  # fills the order in full — no in-flight remainder to also show up as exposure
    await started_portfolio_state.refresh()

    adapter = PortfolioStateAdapter(started_portfolio_state, SessionLocal)
    snapshot = adapter.get_snapshot("simulated", date(2026, 1, 5))

    assert snapshot.execution_mode == "simulated"
    assert snapshot.realized_pnl_today == 0.0
    assert len(snapshot.exposures) == 1
    exposure = snapshot.exposures[0]
    assert exposure.symbol == "AAPL"
    assert exposure.direction == "BUY"
    assert exposure.qty == 4
    assert exposure.avg_entry_price == 100.0
    assert exposure.stop == 90.0
    assert exposure.is_in_flight is False
    assert isinstance(exposure.avg_entry_price, float)  # translated from Decimal, not passed through
