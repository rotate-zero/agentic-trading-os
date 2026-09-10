"""
Performance Intelligence persistence-layer tests — decision #120,
strategy-engine-design.md §5/§7/§11. `record_strategy_outcome()` writes
directly via a real `SessionLocal()`, so like test_market_state_engine.py
this file needs real Postgres — skipped as a whole, not failed, if
unreachable, same posture and same `_db_available()` check.

No live caller exists for `record_strategy_outcome` yet (Execution
Engine/Position Monitor don't exist) — every `StrategyOutcome`
constructed here is synthetic and directly constructed, per decision
#120's own scope (same "prove the contract, don't fabricate the
caller" precedent as state_snapshot.py's own tests, decision #98).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.trading_intelligence import BacktestRunRecord, StrategyOutcomeRecord
from app.schemas.performance import BacktestRun, StrategyOutcome
from app.trading_intelligence.performance import record_strategy_outcome

_STRATEGY_NAME = "TEST_PERF_INTEL_STRATEGY"


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
    """strategy_outcomes first, then backtests — backtest_run_id is a
    real FK, so a test-created backtests row can't be deleted while a
    test-created strategy_outcomes row still references it."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.execute(text("DELETE FROM backtests WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_test_rows()
    yield
    _clean_test_rows()


def _sample_market_state() -> dict:
    # Realistic shape per schemas/events/market_state.py's MarketState.
    return {
        "trend_score": 62.5,
        "volatility_regime_score": 41.0,
        "volume_regime_score": 58.0,
        "vwap_relationship_score": 70.0,
        "acceleration_score": 12.0,
    }


def _sample_context() -> dict:
    return {"gap_day": False, "session_type": "regular", "vix_regime": "normal"}


def _make_outcome(**overrides) -> StrategyOutcome:
    now = datetime(2026, 9, 10, 14, 35, tzinfo=timezone.utc)
    fields = dict(
        outcome_id=uuid.uuid4(),
        opportunity_id=uuid.uuid4(),
        schema_version=1,
        strategy_name=_STRATEGY_NAME,
        strategy_version="orb_v1",
        symbol="AAPL",
        origin="auto",
        is_backtest=False,
        backtest_run_id=None,
        trading_day=date(2026, 9, 10),
        setup_detected_at=now,
        signal_confirmed_at=now,
        decided_at=now,
        entry_filled_at=now,
        exit_filled_at=now.replace(hour=15, minute=10),
        holding_seconds=2100,
        direction="BUY",
        entry_price=228.50,
        entry_qty=100,
        exit_price=230.10,
        exit_qty=100,
        commission_total=1.00,
        slippage_entry=0.02,
        realized_pnl=159.00,
        realized_r=1.6,
        exit_reason="target",
        structural_invalidation=227.80,
        structural_target=230.50,
        final_stop=227.80,
        final_target=230.50,
        confidence_at_signal=0.72,
        evidence={"conditions": {"or_break": True}, "reason": "opening range breakout", "basis": "live"},
        market_state_at_entry=_sample_market_state(),
        context_at_entry=_sample_context(),
        market_state_at_exit=_sample_market_state(),
        context_at_exit=_sample_context(),
        feature_snapshot_id=None,
    )
    fields.update(overrides)
    return StrategyOutcome(**fields)


# --- Pydantic-level tests ----------------------------------------------------

def test_valid_strategy_outcome_construction_succeeds():
    outcome = _make_outcome()
    assert outcome.strategy_name == _STRATEGY_NAME
    assert outcome.entry_qty == outcome.exit_qty == 100


def test_missing_required_field_fails_validation():
    fields = _make_outcome().model_dump()
    del fields["realized_pnl"]  # required, §5 group C (Ledger)
    with pytest.raises(ValidationError):
        StrategyOutcome(**fields)


def test_valid_backtest_run_construction_succeeds():
    run = BacktestRun(
        run_id=uuid.uuid4(),
        sweep_id=uuid.uuid4(),
        strategy_name=_STRATEGY_NAME,
        strategy_version="orb_v1",
        config_hash="cfg_abc123",
        symbol_universe=["AAPL", "MSFT"],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 6, 30),
        data_version="polygon_2026_08",
        feature_version="feature_engine_v1",
        walk_forward_fold=1,
        is_holdout=False,
        created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    assert run.symbol_universe == ["AAPL", "MSFT"]


# --- record_strategy_outcome() write-time invariant --------------------------

def test_entry_qty_not_equal_exit_qty_is_rejected_and_never_written():
    outcome = _make_outcome(entry_qty=100, exit_qty=60)
    with pytest.raises(ValueError, match="entry_qty"):
        record_strategy_outcome(outcome)

    # Confirm the raise happened BEFORE any write — no row exists at all,
    # not just "a row exists but is wrong" (decision #120 test requirement #4).
    session = SessionLocal()
    try:
        count = session.execute(
            text("SELECT COUNT(*) FROM strategy_outcomes WHERE outcome_id = :oid"),
            {"oid": str(outcome.outcome_id)},
        ).scalar_one()
    finally:
        session.close()
    assert count == 0


# --- Full round trip -----------------------------------------------------------

def test_strategy_outcome_round_trip_persists_and_reads_back_full_shape():
    outcome = _make_outcome(
        is_backtest=False,
        backtest_run_id=None,  # genuinely nullable field, exercised as None
        feature_snapshot_id=None,  # genuinely nullable field, exercised as None
    )
    record_strategy_outcome(outcome)

    session = SessionLocal()
    try:
        row = session.get(StrategyOutcomeRecord, outcome.outcome_id)
    finally:
        session.close()

    assert row is not None
    # A. Identity & Versioning
    assert row.outcome_id == outcome.outcome_id
    assert row.opportunity_id == outcome.opportunity_id
    assert row.schema_version == outcome.schema_version
    assert row.strategy_name == outcome.strategy_name
    assert row.strategy_version == outcome.strategy_version
    assert row.symbol == outcome.symbol
    assert row.origin == outcome.origin
    assert row.is_backtest is False
    assert row.backtest_run_id is None
    # B. Timing
    assert row.trading_day == outcome.trading_day
    assert row.setup_detected_at == outcome.setup_detected_at
    assert row.entry_filled_at == outcome.entry_filled_at
    assert row.exit_filled_at == outcome.exit_filled_at
    assert row.holding_seconds == outcome.holding_seconds
    # C. Ledger
    assert row.direction == outcome.direction
    assert float(row.entry_price) == outcome.entry_price
    assert float(row.entry_qty) == outcome.entry_qty
    assert float(row.exit_price) == outcome.exit_price
    assert float(row.exit_qty) == outcome.exit_qty
    assert float(row.commission_total) == outcome.commission_total
    assert float(row.slippage_entry) == outcome.slippage_entry
    assert float(row.realized_pnl) == outcome.realized_pnl
    assert float(row.realized_r) == outcome.realized_r
    assert row.exit_reason == outcome.exit_reason
    # D. Thesis
    assert float(row.structural_invalidation) == outcome.structural_invalidation
    assert float(row.structural_target) == outcome.structural_target
    assert float(row.final_stop) == outcome.final_stop
    assert float(row.final_target) == outcome.final_target
    assert float(row.confidence_at_signal) == outcome.confidence_at_signal
    # E. Evidence — JSONB dict fields survive the round trip intact
    assert row.evidence == outcome.evidence
    assert row.market_state_at_entry == outcome.market_state_at_entry
    assert row.context_at_entry == outcome.context_at_entry
    assert row.market_state_at_exit == outcome.market_state_at_exit
    assert row.context_at_exit == outcome.context_at_exit
    assert row.feature_snapshot_id is None


def test_backtest_run_id_none_persists_fine_for_live_outcome():
    outcome = _make_outcome(is_backtest=False, backtest_run_id=None)
    record_strategy_outcome(outcome)  # must not raise

    session = SessionLocal()
    try:
        row = session.get(StrategyOutcomeRecord, outcome.outcome_id)
    finally:
        session.close()
    assert row is not None
    assert row.backtest_run_id is None


def test_backtest_run_id_requires_existing_backtests_row():
    nonexistent_run_id = uuid.uuid4()
    outcome = _make_outcome(is_backtest=True, backtest_run_id=nonexistent_run_id)

    with pytest.raises(IntegrityError):
        record_strategy_outcome(outcome)

    # Confirm the rollback actually happened — no orphaned row left behind.
    session = SessionLocal()
    try:
        count = session.execute(
            text("SELECT COUNT(*) FROM strategy_outcomes WHERE outcome_id = :oid"),
            {"oid": str(outcome.outcome_id)},
        ).scalar_one()
    finally:
        session.close()
    assert count == 0


def test_backtest_run_id_persists_when_backtests_row_exists_first():
    session = SessionLocal()
    try:
        backtest_row = BacktestRunRecord(
            sweep_id=uuid.uuid4(),
            strategy_name=_STRATEGY_NAME,
            strategy_version="orb_v1",
            config_hash="cfg_abc123",
            symbol_universe=["AAPL"],
            date_range_start=date(2026, 1, 1),
            date_range_end=date(2026, 6, 30),
            data_version="polygon_2026_08",
            feature_version="feature_engine_v1",
            walk_forward_fold=1,
            is_holdout=False,
        )
        session.add(backtest_row)
        session.commit()
        session.refresh(backtest_row)
        run_id = backtest_row.run_id
    finally:
        session.close()

    outcome = _make_outcome(is_backtest=True, backtest_run_id=run_id)
    record_strategy_outcome(outcome)  # must not raise now that the backtests row exists

    session = SessionLocal()
    try:
        row = session.get(StrategyOutcomeRecord, outcome.outcome_id)
    finally:
        session.close()
    assert row is not None
    assert row.backtest_run_id == run_id
