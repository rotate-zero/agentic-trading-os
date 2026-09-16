"""namespace symbols and daily levels by live/backtest origin

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-16

D18: a replay must be able to use the same ticker as live trading without
sharing the symbol identity or Daily Levels checkpoint.  Existing rows are
live rows, so both new columns are added NOT NULL with a false server default.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "symbols",
        sa.Column("is_backtest", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.drop_constraint("symbols_ticker_key", "symbols", type_="unique")
    op.create_unique_constraint(
        "uq_symbols_ticker_is_backtest",
        "symbols",
        ["ticker", "is_backtest"],
    )
    # Required as the target of daily_levels_state's composite namespace FK.
    op.create_unique_constraint(
        "uq_symbols_id_is_backtest",
        "symbols",
        ["id", "is_backtest"],
    )

    op.add_column(
        "daily_levels_state",
        sa.Column("is_backtest", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.drop_constraint(
        "daily_levels_state_symbol_id_fkey",
        "daily_levels_state",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_daily_level_state_symbol_level_id",
        "daily_levels_state",
        type_="unique",
    )
    op.create_foreign_key(
        "fk_daily_levels_state_symbol_namespace",
        "daily_levels_state",
        "symbols",
        ["symbol_id", "is_backtest"],
        ["id", "is_backtest"],
    )
    op.create_unique_constraint(
        "uq_daily_level_state_symbol_namespace_level_id",
        "daily_levels_state",
        ["symbol_id", "is_backtest", "level_id"],
    )


def downgrade() -> None:
    # Backtest namespace rows have no representation in the old schema. They
    # are replay-derived data, so remove their dependent rows before restoring
    # the one-row-per-ticker live schema. Live rows are preserved unchanged.
    connection = op.get_bind()
    for table in (
        "level_interaction_events",
        "level_interaction_state",
        "daily_levels_state",
        "market_state_history",
        "symbol_fundamentals",
        "scanner_universe_symbols",
        "candles",
    ):
        connection.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE symbol_id IN (SELECT id FROM symbols WHERE is_backtest IS TRUE)"
            )
        )
    connection.execute(sa.text("DELETE FROM symbols WHERE is_backtest IS TRUE"))

    op.drop_constraint(
        "uq_daily_level_state_symbol_namespace_level_id",
        "daily_levels_state",
        type_="unique",
    )
    op.drop_constraint(
        "fk_daily_levels_state_symbol_namespace",
        "daily_levels_state",
        type_="foreignkey",
    )
    op.drop_column("daily_levels_state", "is_backtest")
    op.create_foreign_key(
        "daily_levels_state_symbol_id_fkey",
        "daily_levels_state",
        "symbols",
        ["symbol_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_daily_level_state_symbol_level_id",
        "daily_levels_state",
        ["symbol_id", "level_id"],
    )

    op.drop_constraint("uq_symbols_id_is_backtest", "symbols", type_="unique")
    op.drop_constraint("uq_symbols_ticker_is_backtest", "symbols", type_="unique")
    op.drop_column("symbols", "is_backtest")
    op.create_unique_constraint("symbols_ticker_key", "symbols", ["ticker"])
