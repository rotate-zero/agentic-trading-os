"""isolate backtest Daily Levels by run

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-16

The live/backtest namespace added by 0009 protects live state, but every
backtest for a ticker still reconciles against the same backtest
``daily_levels_state`` rows.  This revision adds the run identity needed
to keep separate replays from mutating or archiving one another's levels.

Existing backtest Daily Levels rows cannot be assigned honestly to one
specific run, so only those replay-derived rows are removed.  Live rows
remain unchanged with ``backtest_run_id IS NULL``.  No other symbol-
dependent table is involved in this migration.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_levels_state",
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Pre-0010 replay rows carry no run identity and cannot be backfilled
    # without inventing provenance. They are derived data; live rows are
    # retained byte-for-byte.
    op.execute(sa.text("DELETE FROM daily_levels_state WHERE is_backtest IS TRUE"))

    op.create_foreign_key(
        "fk_daily_levels_state_backtest_run",
        "daily_levels_state",
        "backtests",
        ["backtest_run_id"],
        ["run_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_daily_levels_state_origin_run_pair",
        "daily_levels_state",
        "(is_backtest IS FALSE AND backtest_run_id IS NULL) OR "
        "(is_backtest IS TRUE AND backtest_run_id IS NOT NULL)",
    )

    op.drop_index("ix_daily_levels_state_symbol_status", table_name="daily_levels_state")
    op.create_index(
        "ix_daily_levels_state_reconcile_scope",
        "daily_levels_state",
        ["symbol_id", "is_backtest", "backtest_run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_levels_state_reconcile_scope", table_name="daily_levels_state")
    op.create_index(
        "ix_daily_levels_state_symbol_status",
        "daily_levels_state",
        ["symbol_id", "status"],
    )
    op.drop_constraint(
        "ck_daily_levels_state_origin_run_pair",
        "daily_levels_state",
        type_="check",
    )
    op.drop_constraint(
        "fk_daily_levels_state_backtest_run",
        "daily_levels_state",
        type_="foreignkey",
    )
    op.drop_column("daily_levels_state", "backtest_run_id")
