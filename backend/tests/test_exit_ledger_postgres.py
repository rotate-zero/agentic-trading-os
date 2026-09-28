"""Position-bound simulated exit reservations against the local test database."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.execution_engine.exit_ledger import PostgresExitLedger
from app.models.execution_ledger import ExitRequest, Fill, Order, Position, Trade
from app.position_monitor.engine import ExitIntent

NAME = "TEST_SIMULATED_EXIT_LEDGER"
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def clean():
    def remove():
        with SessionLocal.begin() as session:
            ids = select(Trade.trade_id).where(Trade.strategy_name == NAME)
            orders = select(Order.client_order_id).where(Order.trade_id.in_(ids))
            positions = select(Position.position_id).where(Position.trade_id.in_(ids))
            session.query(ExitRequest).filter(ExitRequest.position_id.in_(positions)).delete(synchronize_session=False)
            session.query(Fill).filter(Fill.client_order_id.in_(orders)).delete(synchronize_session=False)
            session.query(Order).filter(Order.trade_id.in_(ids)).delete(synchronize_session=False)
            session.query(Position).filter(Position.trade_id.in_(ids)).delete(synchronize_session=False)
            session.query(Trade).filter(Trade.strategy_name == NAME).delete(synchronize_session=False)

    remove()
    yield
    remove()


def seeded(*, entry_status="filled"):
    with SessionLocal.begin() as session:
        trade = Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=NAME,
                      strategy_version="v1", direction="BUY", symbol="ZZEXIT", decision="approved", status="open")
        session.add(trade)
        session.flush()
        position = Position(trade_id=trade.trade_id, execution_mode="simulated", execution_venue="simulated",
                            symbol="ZZEXIT", side="BUY", qty=5, avg_price=100, stop=90, target=120,
                            opened_at=NOW - timedelta(minutes=1), status="open")
        session.add(position)
        session.flush()
        entry = Order(client_order_id=f"{trade.trade_id}:entry", trade_id=trade.trade_id,
                      execution_mode="simulated", execution_venue="simulated", symbol="ZZEXIT",
                      side="BUY", position_effect="open", qty=10, order_type="market", status=entry_status)
        session.add(entry)
        return trade.trade_id, position.position_id, entry.client_order_id


def intent(position_id):
    return ExitIntent(position_id=position_id, symbol="ZZEXIT", side="BUY", qty=999,
                      exit_reason="stop", trigger_price=89, trigger_ts=NOW)


def test_cancel_entry_then_reserve_one_close_and_retry_after_rejection():
    _, position_id, entry_id = seeded(entry_status="partially_filled")
    ledger = PostgresExitLedger(SessionLocal)
    assert ledger.observe(intent(position_id))
    assert ledger.observe(intent(position_id))
    assert ledger.prepare(position_id).client_order_id == entry_id
    assert ledger.set_status(entry_id, "cancelled")

    first = ledger.prepare(position_id)
    assert (first.kind, first.qty, first.side) == ("submit", 5, "SELL")
    assert ledger.prepare(position_id) == first
    assert ledger.confirm_recovery_exit(first.client_order_id)
    assert ledger.set_status(first.client_order_id, "rejected", reason="outside_regular_session")
    assert ledger.prepare(position_id) is None  # bounded retry, no busy loop

    with SessionLocal.begin() as session:
        request = session.get(ExitRequest, position_id)
        request.retry_after = NOW - timedelta(seconds=1)
    second = ledger.prepare(position_id)
    assert second.client_order_id.endswith(":exit:2")
    assert second.qty == 5
    with SessionLocal() as session:
        assert session.get(Position, position_id).exit_attempt == 2
        assert len(session.scalars(select(Order).where(Order.position_id == position_id)).all()) == 2


def test_pre_submit_guard_rejects_new_entry_activity_and_unapplied_fill():
    trade_id, position_id, entry_id = seeded()
    ledger = PostgresExitLedger(SessionLocal)
    assert ledger.observe(intent(position_id))
    close = ledger.prepare(position_id)
    assert close is not None

    with SessionLocal.begin() as session:
        session.scalar(select(Order).where(Order.client_order_id == entry_id)).status = "submitted"
    assert not ledger.confirm_recovery_exit(close.client_order_id)

    with SessionLocal.begin() as session:
        session.scalar(select(Order).where(Order.client_order_id == entry_id)).status = "filled"
        session.add(Fill(client_order_id=entry_id, execution_venue="simulated",
                         venue_fill_id=f"{entry_id}:late", qty=1, price=100, venue_ts=NOW))
    assert not ledger.confirm_recovery_exit(close.client_order_id)
    assert ledger.prepare(position_id) is None
    with SessionLocal() as session:
        assert session.get(Trade, trade_id).status == "open"


def test_closed_position_does_not_create_or_retry_exit():
    _, position_id, _ = seeded()
    ledger = PostgresExitLedger(SessionLocal)
    assert ledger.observe(intent(position_id))
    with SessionLocal.begin() as session:
        position = session.get(Position, position_id)
        position.qty = 0
        position.status = "closed"
    assert ledger.prepare(position_id) is None
    assert ledger.pending_position_ids() == ()


def test_database_rejects_second_active_close_for_same_position():
    trade_id, position_id, _ = seeded()
    ledger = PostgresExitLedger(SessionLocal)
    assert ledger.observe(intent(position_id))
    first = ledger.prepare(position_id)
    assert first is not None
    with pytest.raises(IntegrityError):
        with SessionLocal.begin() as session:
            session.add(Order(client_order_id=f"{trade_id}:exit:other", trade_id=trade_id,
                              position_id=position_id, execution_mode="simulated",
                              execution_venue="simulated", symbol="ZZEXIT", side="SELL",
                              position_effect="close", qty=5, order_type="market", status="approved"))
    assert ledger.prepare(position_id) == first
