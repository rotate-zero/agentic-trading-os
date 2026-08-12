"""level interaction state + events (Level Interaction Engine, confirmed decision #46)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-12

Two plain (unpartitioned) tables — see app/models/trading_intelligence.py's
module docstring for why these don't need `candles`-style range
partitioning at this volume.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "level_interaction_state",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("level_key", sa.String(32), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("touch_count_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("zone", sa.String(16), nullable=False),
        sa.Column("zone_entered_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("touch_anchor_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("touch_entered_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("touch_entered_from", sa.String(8), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "timeframe", "level_key", name="uq_level_state_symbol_tf_key"),
    )

    op.create_table(
        "level_interaction_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("level_key", sa.String(32), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("entered_from", sa.String(8), nullable=True),
        sa.Column("exited_to", sa.String(8), nullable=False),
        sa.Column("entered_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exited_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds_in_zone", sa.Integer(), nullable=False),
        sa.Column("anchor_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("distance_pct", sa.Numeric(10, 4), nullable=False),
        sa.Column("observed_via", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_level_events_symbol_tf_key_day",
        "level_interaction_events",
        ["symbol_id", "timeframe", "level_key", "trading_day"],
    )


def downgrade() -> None:
    op.drop_index("ix_level_events_symbol_tf_key_day", table_name="level_interaction_events")
    op.drop_table("level_interaction_events")
    op.drop_table("level_interaction_state")
