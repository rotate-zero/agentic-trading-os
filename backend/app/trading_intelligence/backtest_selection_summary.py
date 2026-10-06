"""Complete-population performance summary for one recorded run or sweep."""
from __future__ import annotations

import uuid

from sqlalchemy import and_, func, select

from app.db.session import SessionLocal
from app.models.trading_intelligence import BacktestRunRecord, StrategyOutcomeRecord


def read_backtest_selection_summary(*, run_id: uuid.UUID | None, sweep_id: uuid.UUID | None) -> dict:
    """Aggregate in a worker-owned, read-only PostgreSQL snapshot.

    The run table drives the LEFT JOIN so a run with no eligible outcomes
    still contributes its recorded strategy/configuration/provenance group.
    No outcome-list limit is applied.
    """
    run = BacktestRunRecord
    outcome = StrategyOutcomeRecord
    group_columns = (
        run.strategy_name, run.strategy_version, run.config_hash,
        run.data_version, run.feature_version,
    )
    statement = (
        select(
            *group_columns,
            func.count(func.distinct(run.run_id)).label("run_count"),
            func.count(outcome.outcome_id).label("total_outcomes"),
            func.count(outcome.outcome_id).filter(outcome.realized_r > 0).label("wins"),
            func.count(outcome.outcome_id).filter(outcome.realized_r < 0).label("losses"),
            func.count(outcome.outcome_id).filter(outcome.realized_r == 0).label("breakevens"),
            func.avg(outcome.realized_r).label("mean_realized_r"),
        )
        .select_from(run)
        .outerjoin(
            outcome,
            and_(
                outcome.backtest_run_id == run.run_id,
                outcome.is_backtest.is_(True),
                outcome.execution_mode == "backtest",
            ),
        )
        .where(run.run_id == run_id if run_id is not None else run.sweep_id == sweep_id)
        .group_by(*group_columns)
        .order_by(*group_columns)
    )

    session = SessionLocal()
    try:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
        rows = session.execute(statement).all()
    finally:
        session.close()

    groups = [
        {
            "strategy_name": row.strategy_name,
            "strategy_version": row.strategy_version,
            "config_hash": row.config_hash,
            "data_version": row.data_version,
            "feature_version": row.feature_version,
            "run_count": row.run_count,
            "total_outcomes": row.total_outcomes,
            "wins": row.wins,
            "losses": row.losses,
            "breakevens": row.breakevens,
            "win_rate": row.wins / row.total_outcomes if row.total_outcomes else None,
            "mean_realized_r": float(row.mean_realized_r) if row.mean_realized_r is not None else None,
        }
        for row in rows
    ]
    return {"selection_found": bool(groups), "groups": groups}
