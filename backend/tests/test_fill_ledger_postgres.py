"""
Real PostgreSQL tests for execution_engine/fill_ledger.py's
PostgresFillLedger — the new fill-ingestion adapter entry-lifecycle-
wiring adds. Isolated database, no mocks (project convention).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.execution_engine.fill_ledger import FillLedgerError, FillRecord, PostgresFillLedger
from app.models.execution_ledger import Fill, Order, Trade

TS = datetime(2026, 1, 5, 15, tzinfo=timezone.utc)
NAME = "TEST_FILL_LEDGER_ADAPTER"


def clean():
    with SessionLocal.begin() as s:
        ids = select(Trade.trade_id).where(Trade.strategy_name == NAME)
        orders = select(Order.client_order_id).where(Order.trade_id.in_(ids))
        s.query(Fill).filter(Fill.client_order_id.in_(orders)).delete(synchronize_session=False)
        s.query(Order).filter(Order.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Trade).filter(Trade.strategy_name == NAME).delete(synchronize_session=False)


@pytest.fixture(autouse=True)
def database():
    clean()
    yield
    clean()


def seed_order(*, qty=10, status="submitted", side="BUY"):
    with SessionLocal.begin() as s:
        trade = Trade(
            execution_mode="simulated", execution_venue="simulated", strategy_name=NAME, strategy_version="1",
            direction=side, symbol="AAPL", thesis={"final_stop": 90, "final_target": 120},
            decision="approved", status="open",
        )
        s.add(trade)
        s.flush()
        order = Order(
            client_order_id=f"{trade.trade_id}:entry", trade_id=trade.trade_id, execution_mode="simulated",
            execution_venue="simulated", symbol="AAPL", side=side, qty=qty, position_effect="open", status=status,
        )
        s.add(order)
        return order.client_order_id


def test_fill_persists_and_advances_order_status_to_partially_filled():
    order_id = seed_order(qty=10)
    ledger = PostgresFillLedger(SessionLocal)
    result = ledger.record_fill(FillRecord(order_id, "f1", qty=4, price=100.0, venue_ts=TS, status="partially_filled"))
    assert result.inserted
    assert result.symbol == "AAPL"
    assert result.side == "BUY"
    assert result.order_status == "partially_filled"
    assert result.anomaly is None
    with SessionLocal() as s:
        row = s.scalar(select(Order).where(Order.client_order_id == order_id))
        assert row.status == "partially_filled"
        fill_row = s.scalar(select(Fill).where(Fill.venue_fill_id == "f1"))
        assert fill_row.qty == 4 and fill_row.anomaly is None


def test_fill_to_full_quantity_advances_order_status_to_filled():
    order_id = seed_order(qty=10)
    ledger = PostgresFillLedger(SessionLocal)
    ledger.record_fill(FillRecord(order_id, "f1", qty=10, price=100.0, venue_ts=TS, status="filled"))
    with SessionLocal() as s:
        assert s.scalar(select(Order).where(Order.client_order_id == order_id)).status == "filled"


def test_duplicate_fill_is_a_noop_not_an_error():
    order_id = seed_order(qty=10)
    ledger = PostgresFillLedger(SessionLocal)
    first = ledger.record_fill(FillRecord(order_id, "f1", qty=10, price=100.0, venue_ts=TS, status="filled"))
    second = ledger.record_fill(FillRecord(order_id, "f1", qty=10, price=100.0, venue_ts=TS, status="filled"))
    assert first.inserted is True
    assert second.inserted is False
    assert second.order_status == "filled"
    with SessionLocal() as s:
        assert s.scalar(select(Fill.ledger_seq).where(Fill.venue_fill_id == "f1")) is not None
        # exactly one row — a second insert would have violated the UNIQUE constraint
        count = len(s.scalars(select(Fill).where(Fill.venue_fill_id == "f1")).all())
        assert count == 1


def test_overfill_is_persisted_and_flagged_not_rejected():
    order_id = seed_order(qty=10)
    ledger = PostgresFillLedger(SessionLocal)
    ledger.record_fill(FillRecord(order_id, "f1", qty=10, price=100.0, venue_ts=TS, status="filled"))
    result = ledger.record_fill(FillRecord(order_id, "f2", qty=5, price=101.0, venue_ts=TS, status="filled"))
    assert result.inserted is True
    assert result.anomaly == "overfill"
    with SessionLocal() as s:
        assert s.scalar(select(Fill).where(Fill.venue_fill_id == "f2")).anomaly == "overfill"


def test_fill_for_unknown_order_raises_rather_than_violating_the_fk():
    ledger = PostgresFillLedger(SessionLocal)
    with pytest.raises(FillLedgerError, match="unknown order"):
        ledger.record_fill(FillRecord(f"{uuid4()}:entry", "f1", qty=10, price=100.0, venue_ts=TS, status="filled"))
    with SessionLocal() as s:
        assert s.scalar(select(Fill).where(Fill.venue_fill_id == "f1")) is None


def test_stale_out_of_order_status_does_not_regress_but_fill_is_still_persisted():
    order_id = seed_order(qty=10)
    ledger = PostgresFillLedger(SessionLocal)
    ledger.record_fill(FillRecord(order_id, "f1", qty=10, price=100.0, venue_ts=TS, status="filled"))
    # A second, later-arriving update claiming only partially_filled is not
    # forward of "filled" — I11: status only ever moves forward.
    result = ledger.record_fill(FillRecord(order_id, "f2", qty=1, price=99.0, venue_ts=TS, status="partially_filled"))
    assert result.inserted is True
    assert result.order_status == "filled"
    with SessionLocal() as s:
        assert s.scalar(select(Order).where(Order.client_order_id == order_id)).status == "filled"
        assert s.scalar(select(Fill).where(Fill.venue_fill_id == "f2")) is not None


def test_execution_venue_is_read_from_the_order_row_not_the_caller():
    order_id = seed_order(qty=10)
    ledger = PostgresFillLedger(SessionLocal)
    ledger.record_fill(FillRecord(order_id, "f1", qty=10, price=100.0, venue_ts=TS, status="filled"))
    with SessionLocal() as s:
        row = s.scalar(select(Fill).where(Fill.venue_fill_id == "f1"))
        assert row.execution_venue == "simulated"
