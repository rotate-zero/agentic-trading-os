"""strategy_outcomes + backtests (Performance Intelligence persistence layer, decision #89's shape, this build #120)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-10

`backtests` is created first even though `strategy_outcomes` is listed
first in §5/§7 — `strategy_outcomes.backtest_run_id` is a real,
enforced FK to `backtests.run_id`, so the referenced table has to exist
first. See app/models/trading_intelligence.py's module docstring for
the two conventions this migration establishes for the first time in
this codebase (confirmed absent by grep beforehand): PostgreSQL UUID
primary/foreign keys, and JSONB for dict-shaped fields. `gen_random_uuid()`
is a PostgreSQL 16 builtin (moved into core in PG13) — no `pgcrypto`
extension needed.

`feature_snapshot_id` and `opportunity_id` on `strategy_outcomes` are
plain UUID columns with no FK constraint: `feature_snapshots` and any
`opportunities` table do not exist anywhere in this codebase yet
(confirmed by grep), and building either as a side effect of this
migration is explicitly out of scope — §5's own "referenced not
duplicated" text anticipates exactly this.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtests",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sweep_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        # Homogeneous list of tickers, not dict-shaped — ARRAY, not JSONB
        # (app/models/trading_intelligence.py's module docstring explains the split).
        sa.Column("symbol_universe", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("date_range_start", sa.Date(), nullable=False),
        sa.Column("date_range_end", sa.Date(), nullable=False),
        sa.Column("data_version", sa.String(32), nullable=False),
        sa.Column("feature_version", sa.String(32), nullable=False),
        sa.Column("walk_forward_fold", sa.Integer(), nullable=True),
        sa.Column("is_holdout", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )

    op.create_table(
        "strategy_outcomes",
        # --- A. Identity & Versioning ---
        sa.Column(
            "outcome_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(8), nullable=False),  # auto | manual — Pydantic-enforced Literal, not a DB CHECK
        sa.Column("is_backtest", sa.Boolean(), nullable=False),
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("backtests.run_id"), nullable=True),
        # --- B. Timing ---
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("setup_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("holding_seconds", sa.Integer(), nullable=False),
        # --- C. Ledger --- entry_qty == exit_qty enforced by record_strategy_outcome() at write time, not here
        sa.Column("direction", sa.String(4), nullable=False),  # BUY | SELL
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("entry_qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("exit_qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("commission_total", sa.Numeric(18, 6), nullable=True),
        sa.Column("slippage_entry", sa.Numeric(18, 6), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=False),  # net of commission_total
        sa.Column("realized_r", sa.Numeric(10, 4), nullable=False),
        sa.Column("exit_reason", sa.String(16), nullable=False),  # target | stop | time | eod_flatten | manual | reversal
        # --- D. Thesis ---
        sa.Column("structural_invalidation", sa.Numeric(18, 6), nullable=False),
        sa.Column("structural_target", sa.Numeric(18, 6), nullable=False),
        sa.Column("final_stop", sa.Numeric(18, 6), nullable=False),
        sa.Column("final_target", sa.Numeric(18, 6), nullable=False),
        sa.Column("confidence_at_signal", sa.Numeric(10, 4), nullable=False),
        # --- E. Evidence --- JSONB (see module docstring for why JSONB, not JSON)
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("market_state_at_entry", postgresql.JSONB(), nullable=False),
        sa.Column("context_at_entry", postgresql.JSONB(), nullable=False),
        sa.Column("market_state_at_exit", postgresql.JSONB(), nullable=False),
        sa.Column("context_at_exit", postgresql.JSONB(), nullable=False),
        # No FK: feature_snapshots doesn't exist yet anywhere in this codebase (confirmed by grep).
        sa.Column("feature_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("outcome_id"),
    )
    op.create_index(
        "ix_strategy_outcomes_strategy_symbol_day",
        "strategy_outcomes",
        ["strategy_name", "symbol", "trading_day"],
    )
    op.create_index(
        "ix_strategy_outcomes_backtest_run_id",
        "strategy_outcomes",
        ["backtest_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_outcomes_backtest_run_id", table_name="strategy_outcomes")
    op.drop_index("ix_strategy_outcomes_strategy_symbol_day", table_name="strategy_outcomes")
    op.drop_table("strategy_outcomes")
    op.drop_table("backtests")
