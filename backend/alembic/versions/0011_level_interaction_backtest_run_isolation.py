"""isolate Level Interaction persistence by backtest run

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-20

Level Interaction state is a mutable restart checkpoint, while Level
Interaction events are an append-only analytical log. Both need explicit
run ownership during replay: state so one run cannot inherit another's
checkpoint, and events so replay history can be queried and cleaned up by
its owning run.

Pre-0011 backtest rows have no recoverable run provenance. They are derived
replay data, so this migration removes only those rows and preserves all
live Level Interaction state and events.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = ("level_interaction_state", "level_interaction_events")


def _add_origin_and_run_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("is_backtest", sa.Boolean(), nullable=True))
    op.add_column(
        table_name,
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            f"UPDATE {table_name} AS li SET is_backtest = s.is_backtest "
            "FROM symbols AS s WHERE s.id = li.symbol_id"
        )
    )
    op.execute(sa.text(f"DELETE FROM {table_name} WHERE is_backtest IS TRUE"))
    op.alter_column(
        table_name,
        "is_backtest",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )


def upgrade() -> None:
    for table_name in _TABLES:
        _add_origin_and_run_columns(table_name)

    op.drop_constraint(
        "level_interaction_state_symbol_id_fkey",
        "level_interaction_state",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_level_interaction_state_symbol_namespace",
        "level_interaction_state",
        "symbols",
        ["symbol_id", "is_backtest"],
        ["id", "is_backtest"],
    )
    op.create_foreign_key(
        "fk_level_interaction_state_backtest_run",
        "level_interaction_state",
        "backtests",
        ["backtest_run_id"],
        ["run_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_level_interaction_state_origin_run_pair",
        "level_interaction_state",
        "(is_backtest IS FALSE AND backtest_run_id IS NULL) OR "
        "(is_backtest IS TRUE AND backtest_run_id IS NOT NULL)",
    )
    op.drop_constraint(
        "uq_level_state_symbol_tf_key",
        "level_interaction_state",
        type_="unique",
    )
    op.create_index(
        "uq_level_interaction_state_live_scope",
        "level_interaction_state",
        ["symbol_id", "timeframe", "level_key"],
        unique=True,
        postgresql_where=sa.text("is_backtest IS FALSE"),
    )
    op.create_index(
        "uq_level_interaction_state_backtest_run_scope",
        "level_interaction_state",
        ["symbol_id", "timeframe", "level_key", "backtest_run_id"],
        unique=True,
        postgresql_where=sa.text("is_backtest IS TRUE"),
    )
    op.create_index(
        "ix_level_interaction_state_backtest_run_id",
        "level_interaction_state",
        ["backtest_run_id"],
        postgresql_where=sa.text("backtest_run_id IS NOT NULL"),
    )

    op.drop_constraint(
        "level_interaction_events_symbol_id_fkey",
        "level_interaction_events",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_level_interaction_events_symbol_namespace",
        "level_interaction_events",
        "symbols",
        ["symbol_id", "is_backtest"],
        ["id", "is_backtest"],
    )
    op.create_foreign_key(
        "fk_level_interaction_events_backtest_run",
        "level_interaction_events",
        "backtests",
        ["backtest_run_id"],
        ["run_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_level_interaction_events_origin_run_pair",
        "level_interaction_events",
        "(is_backtest IS FALSE AND backtest_run_id IS NULL) OR "
        "(is_backtest IS TRUE AND backtest_run_id IS NOT NULL)",
    )
    op.create_index(
        "ix_level_interaction_events_backtest_run_id",
        "level_interaction_events",
        ["backtest_run_id"],
        postgresql_where=sa.text("backtest_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    # The legacy state uniqueness cannot represent two runs for one symbol,
    # and dropping event attribution would manufacture provenance-free replay
    # history. Remove replay-derived rows from both tables; preserve live rows.
    for table_name in _TABLES:
        op.execute(sa.text(f"DELETE FROM {table_name} WHERE is_backtest IS TRUE"))

    op.drop_index(
        "ix_level_interaction_events_backtest_run_id",
        table_name="level_interaction_events",
    )
    op.drop_constraint(
        "ck_level_interaction_events_origin_run_pair",
        "level_interaction_events",
        type_="check",
    )
    op.drop_constraint(
        "fk_level_interaction_events_backtest_run",
        "level_interaction_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_level_interaction_events_symbol_namespace",
        "level_interaction_events",
        type_="foreignkey",
    )
    op.drop_column("level_interaction_events", "backtest_run_id")
    op.drop_column("level_interaction_events", "is_backtest")
    op.create_foreign_key(
        "level_interaction_events_symbol_id_fkey",
        "level_interaction_events",
        "symbols",
        ["symbol_id"],
        ["id"],
    )

    op.drop_index(
        "ix_level_interaction_state_backtest_run_id",
        table_name="level_interaction_state",
    )
    op.drop_index(
        "uq_level_interaction_state_backtest_run_scope",
        table_name="level_interaction_state",
    )
    op.drop_index(
        "uq_level_interaction_state_live_scope",
        table_name="level_interaction_state",
    )
    op.drop_constraint(
        "ck_level_interaction_state_origin_run_pair",
        "level_interaction_state",
        type_="check",
    )
    op.drop_constraint(
        "fk_level_interaction_state_backtest_run",
        "level_interaction_state",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_level_interaction_state_symbol_namespace",
        "level_interaction_state",
        type_="foreignkey",
    )
    op.drop_column("level_interaction_state", "backtest_run_id")
    op.drop_column("level_interaction_state", "is_backtest")
    op.create_foreign_key(
        "level_interaction_state_symbol_id_fkey",
        "level_interaction_state",
        "symbols",
        ["symbol_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_level_state_symbol_tf_key",
        "level_interaction_state",
        ["symbol_id", "timeframe", "level_key"],
    )
