"""market_state_history (Market State Engine, confirmed decision #91, this build #93)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-02

One plain (unpartitioned) append-only table — see
app/models/market_state.py's module docstring for why: it grows at
debounced-recompute rate, not tick/candle rate, and is structurally an
analytical log like `level_interaction_events`, not a restart checkpoint
like `level_interaction_state`.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_state_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("candle_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trend_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("volatility_regime_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("volume_regime_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("vwap_relationship_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("acceleration_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_market_state_history_symbol_tf_ts",
        "market_state_history",
        ["symbol_id", "timeframe", "candle_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_state_history_symbol_tf_ts", table_name="market_state_history")
    op.drop_table("market_state_history")
