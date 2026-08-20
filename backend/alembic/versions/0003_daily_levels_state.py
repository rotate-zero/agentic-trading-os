"""daily levels state (Daily Levels Stage 2, confirmed decision #63)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19

One table — see app/models/daily_levels.py's module docstring for why
this doesn't need a companion events table the way
level_interaction_state/level_interaction_events does (yet — that's
Stage 3's concern, and would reuse the EXISTING level_interaction_events
table, not add a new one).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_levels_state",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("level_id", sa.String(64), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("strength", sa.Integer(), nullable=False),
        sa.Column("distinct_candle_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("first_seen_day", sa.Date(), nullable=False),
        sa.Column("last_confirmed_day", sa.Date(), nullable=False),
        sa.Column("archived_day", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "level_id", name="uq_daily_level_state_symbol_level_id"),
    )
    # The reconciliation query's own access pattern (design doc §4 /
    # decision #63): "give me this symbol's currently-active levels,"
    # every time a new day's clustering pass runs.
    op.create_index(
        "ix_daily_levels_state_symbol_status",
        "daily_levels_state",
        ["symbol_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_levels_state_symbol_status", table_name="daily_levels_state")
    op.drop_table("daily_levels_state")
