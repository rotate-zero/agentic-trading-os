"""
Performance Intelligence's write path — decision #120,
strategy-engine-design.md §5/§11. Pairs with
app/schemas/performance.py (the `StrategyOutcome`/`BacktestRun`
Pydantic contracts) and app/models/trading_intelligence.py (the
`StrategyOutcomeRecord`/`BacktestRunRecord` ORM tables).

Backtest Runner retains the original own-session wrapper. The simulated
OutcomeRecorder calls the same-session variant so its outcome insert and
trades link commit together. Both paths stage the complete StrategyOutcome
shape, including execution mode, venue and missing-snapshot reasons.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.schemas.performance import StrategyOutcome


def record_strategy_outcome(outcome: StrategyOutcome) -> None:
    """Persist one outcome in an owned transaction, preserving Backtest Runner's API.

    Quantity validation precedes session creation. Database errors roll back
    and propagate; neither invariant nor write failures are swallowed.
    """
    _validate_quantities(outcome)
    session = SessionLocal()
    try:
        record_strategy_outcome_in_session(session, outcome)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def record_strategy_outcome_in_session(session: Session, outcome: StrategyOutcome) -> None:
    """Stage an outcome in the caller's transaction; never commit or close it."""
    _validate_quantities(outcome)
    row = StrategyOutcomeRecord(**outcome.model_dump())
    session.add(row)


def _validate_quantities(outcome: StrategyOutcome) -> None:
    if outcome.entry_qty != outcome.exit_qty:
        raise ValueError(
            f"StrategyOutcome invariant violated: entry_qty ({outcome.entry_qty}) != "
            f"exit_qty ({outcome.exit_qty}) for outcome_id={outcome.outcome_id} — refusing to "
            "persist. A closed trade's StrategyOutcome must record equal entry and exit "
            "quantity (§5/§11)."
        )
