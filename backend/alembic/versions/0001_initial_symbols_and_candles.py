"""initial: symbols + partitioned candles

Revision ID: 0001
Revises:
Create Date: 2026-07-25

Implements confirmed decision #2: plain PostgreSQL with native monthly
declarative partitioning on `candles` (candle_ts), no TimescaleDB.
See docs/architecture/system-design.md §6.1 and §4.13, and the scope note
at the top of app/models/market_data.py re: month-only (not
month+timeframe) partitioning for now.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("exchange", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_symbols_ticker", "symbols", ["ticker"])

    op.create_table(
        "candles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("candle_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", "candle_ts"),
        sa.UniqueConstraint("symbol_id", "timeframe", "candle_ts", name="uq_candle_symbol_tf_ts"),
        postgresql_partition_by="RANGE (candle_ts)",
    )
    op.create_index("ix_candles_symbol_tf_ts", "candles", ["symbol_id", "timeframe", "candle_ts"])

    # Seed the first two monthly partitions (this migration's create date +
    # one month of runway). Adding future partitions is an ops task, not an
    # app-code task — see the TODO below. A partitioned table with no
    # partition covering "now" will reject inserts, so at least one seed
    # partition is required for the table to be usable at all, not just a
    # nice-to-have.
    #
    # Boundaries use an explicit +00 (UTC) offset, not bare dates. Postgres
    # interprets a bare date literal like '2026-07-01' using the DB
    # session's local TimeZone setting at the moment the migration runs —
    # which means the exact same migration produces a different physical
    # partition boundary depending on who/where runs it (verified: running
    # this with a session TimeZone of Asia/Dhaka produced a 2026-06-30T18:00Z
    # boundary, six hours off from the intended UTC midnight). Pinning +00
    # here makes the boundary deterministic everywhere.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS candles_y2026m07 PARTITION OF candles
            FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00')
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS candles_y2026m08 PARTITION OF candles
            FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00')
        """
    )

    # TODO(Phase 3+): automate future-partition creation (a scheduled job,
    # or pg_partman) before this becomes a real operational gap — i.e.
    # before Market Data Engine is actually writing candles in Phase 4.
    # Not needed for Phase 2 scaffolding, so not built speculatively now.


def downgrade() -> None:
    op.drop_table("candles")  # partitions drop automatically with the parent
    op.drop_index("ix_symbols_ticker", table_name="symbols")
    op.drop_table("symbols")
