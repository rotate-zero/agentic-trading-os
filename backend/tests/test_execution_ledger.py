"""
Execution ledger DB-level constraints — decision execution-ledger-and-
venue, migration 0012. Real Postgres only (like test_performance_
intelligence.py); this file tests what the DATABASE itself enforces,
independent of any application-level caution — AC #7's "ledger half"
(client_order_id dedup), AC #8 (venue_fill_id dedup), and AC #18
(execution_mode/execution_venue population CHECKs, including on
strategy_outcomes).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.execution_ledger import Fill, Order, Trade
from app.schemas.performance import StrategyOutcome

_STRATEGY_NAME = "TEST_EXECUTION_LEDGER"


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")


def _clean_test_rows() -> None:
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.execute(text(
            "DELETE FROM fills WHERE client_order_id IN "
            "(SELECT client_order_id FROM orders WHERE trade_id IN "
            "(SELECT trade_id FROM trades WHERE strategy_name = :n))"
        ), {"n": _STRATEGY_NAME})
        session.execute(text(
            "DELETE FROM positions WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"
        ), {"n": _STRATEGY_NAME})
        session.execute(text(
            "DELETE FROM orders WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"
        ), {"n": _STRATEGY_NAME})
        session.execute(text("DELETE FROM trades WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_test_rows()
    yield
    _clean_test_rows()


def _make_trade(session, **overrides) -> Trade:
    defaults = dict(
        execution_mode="simulated",
        execution_venue="simulated",
        strategy_name=_STRATEGY_NAME,
        strategy_version="v1",
        direction="BUY",
        symbol="AAPL",
        thesis={},
        decision="approved",
        limits_snapshot={},
        status="open",
    )
    defaults.update(overrides)
    trade = Trade(**defaults)
    session.add(trade)
    session.flush()
    return trade


def test_orders_client_order_id_is_unique_at_the_db_level() -> None:
    """AC #7, ledger half: the same client_order_id can never produce a
    second orders row, enforced by the database, not by caller discipline."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        client_order_id = f"{trade.trade_id}:entry"
        session.add(Order(
            client_order_id=client_order_id, trade_id=trade.trade_id, execution_mode="simulated",
            execution_venue="simulated", symbol="AAPL", side="BUY", position_effect="open", qty=10,
        ))
        session.commit()

        session.add(Order(
            client_order_id=client_order_id, trade_id=trade.trade_id, execution_mode="simulated",
            execution_venue="simulated", symbol="AAPL", side="BUY", position_effect="open", qty=10,
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_fills_venue_fill_id_is_unique_per_venue_at_the_db_level() -> None:
    """AC #8: a replayed venue_fill_id can never insert a second fills row."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        client_order_id = f"{trade.trade_id}:entry"
        session.add(Order(
            client_order_id=client_order_id, trade_id=trade.trade_id, execution_mode="simulated",
            execution_venue="simulated", symbol="AAPL", side="BUY", position_effect="open", qty=10,
            status="filled",
        ))
        session.commit()

        session.add(Fill(
            client_order_id=client_order_id, execution_venue="simulated", venue_fill_id=f"{client_order_id}:f1",
            qty=10, price=190.0, venue_ts=datetime.now(timezone.utc),
        ))
        session.commit()

        session.add(Fill(
            client_order_id=client_order_id, execution_venue="simulated", venue_fill_id=f"{client_order_id}:f1",
            qty=10, price=190.0, venue_ts=datetime.now(timezone.utc),
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_orders_mode_venue_pairing_check() -> None:
    """AC #18: a simulated-venue row cannot be inserted as paper/live and vice versa."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        session.add(Order(
            client_order_id=f"{trade.trade_id}:entry", trade_id=trade.trade_id,
            execution_mode="paper", execution_venue="simulated",  # inconsistent pairing
            symbol="AAPL", side="BUY", position_effect="open", qty=10,
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_trades_mode_venue_pairing_check() -> None:
    session = SessionLocal()
    try:
        with pytest.raises(IntegrityError):
            _make_trade(session, execution_mode="live", execution_venue="simulated")
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_strategy_outcomes_is_backtest_matches_execution_mode_check() -> None:
    """AC #18: is_backtest always equals (execution_mode = 'backtest') — DB-enforced."""
    from app.models.trading_intelligence import StrategyOutcomeRecord

    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        session.add(StrategyOutcomeRecord(
            opportunity_id=uuid.uuid4(), schema_version=1, strategy_name=_STRATEGY_NAME,
            strategy_version="v1", symbol="AAPL", origin="auto",
            is_backtest=True, execution_mode="simulated", execution_venue="simulated",  # mismatch
            trading_day=now.date(), setup_detected_at=now, entry_filled_at=now, exit_filled_at=now,
            holding_seconds=60, direction="BUY", entry_price=100, entry_qty=10, exit_price=101,
            exit_qty=10, realized_pnl=10, realized_r=0.5, exit_reason="target",
            structural_invalidation=99, structural_target=102, final_stop=99, final_target=102,
            confidence_at_signal=0.8, evidence={}, market_state_at_entry={}, context_at_entry={},
            market_state_at_exit={}, context_at_exit={},
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_strategy_outcomes_null_snapshot_requires_reason() -> None:
    """A live-shaped row with a NULL snapshot and no entry in
    snapshot_missing_reasons is rejected at the DB level."""
    from app.models.trading_intelligence import StrategyOutcomeRecord

    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        session.add(StrategyOutcomeRecord(
            opportunity_id=uuid.uuid4(), schema_version=2, strategy_name=_STRATEGY_NAME,
            strategy_version="v1", symbol="AAPL", origin="auto",
            is_backtest=False, execution_mode="simulated", execution_venue="simulated",
            trading_day=now.date(), setup_detected_at=now, entry_filled_at=now, exit_filled_at=now,
            holding_seconds=60, direction="BUY", entry_price=100, entry_qty=10, exit_price=101,
            exit_qty=10, realized_pnl=10, realized_r=0.5, exit_reason="target",
            structural_invalidation=99, structural_target=102, final_stop=99, final_target=102,
            confidence_at_signal=0.8, evidence={},
            market_state_at_entry=None,  # missing, and...
            context_at_entry={}, market_state_at_exit={}, context_at_exit={},
            snapshot_missing_reasons=None,  # ...no reason recorded for it
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_pydantic_schema_rejects_the_same_mismatches_before_the_db_does() -> None:
    """The Pydantic StrategyOutcome validators (schemas/performance.py) mirror the DB
    CHECKs — a caller gets a friendly ValidationError before ever reaching Postgres."""
    from pydantic import ValidationError

    now = datetime.now(timezone.utc)
    base_kwargs = dict(
        outcome_id=uuid.uuid4(), opportunity_id=uuid.uuid4(), schema_version=1,
        strategy_name=_STRATEGY_NAME, strategy_version="v1", symbol="AAPL", origin="auto",
        trading_day=now.date(), setup_detected_at=now, entry_filled_at=now, exit_filled_at=now,
        holding_seconds=60, direction="BUY", entry_price=100.0, entry_qty=10.0, exit_price=101.0,
        exit_qty=10.0, realized_pnl=10.0, realized_r=0.5, exit_reason="target",
        structural_invalidation=99.0, structural_target=102.0, final_stop=99.0, final_target=102.0,
        confidence_at_signal=0.8, evidence={},
        market_state_at_entry={}, context_at_entry={}, market_state_at_exit={}, context_at_exit={},
    )
    with pytest.raises(ValidationError):
        StrategyOutcome(is_backtest=True, execution_mode="simulated", **base_kwargs)

    with pytest.raises(ValidationError):
        StrategyOutcome(
            is_backtest=False, execution_mode="backtest",
            **{**base_kwargs, "market_state_at_entry": None},
        )
