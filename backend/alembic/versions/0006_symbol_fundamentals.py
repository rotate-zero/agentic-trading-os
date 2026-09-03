"""symbol_fundamentals (decision #90's shape, built per decision #96)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-03

One plain (unpartitioned) table, one row per symbol, upserted in place by
the refresh jobs in app/context_engine/fundamentals_refresh.py — this is
a live profile, not an append-only log, so no growth-rate justification
needed the way market_state_history's docstring needed one.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "symbol_fundamentals",
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("profile_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_cap", sa.Numeric(20, 2), nullable=True),
        sa.Column("market_cap_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revenue_ttm", sa.Numeric(20, 2), nullable=True),
        sa.Column("net_income_ttm", sa.Numeric(20, 2), nullable=True),
        sa.Column("operating_cash_flow_ttm", sa.Numeric(20, 2), nullable=True),
        sa.Column("financials_period", sa.String(10), nullable=True),
        sa.Column("financials_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_earnings_date", sa.Date(), nullable=True),
        sa.Column("earnings_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_source", sa.String(20), nullable=False, server_default="finnhub"),
        sa.PrimaryKeyConstraint("symbol_id"),
    )


def downgrade() -> None:
    op.drop_table("symbol_fundamentals")
