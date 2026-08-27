"""scanner universe (docs/architecture/scanner-design.md §3 — replaces the TEST_UNIVERSE placeholder)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-27

Seeded with the same 6 symbols app/scanner/universe.py's TEST_UNIVERSE
already used as a placeholder, so there's no dead-empty state on first
deploy — still just a placeholder, now editable/removable via
GET/POST/DELETE /scanner/universe instead of requiring a code change.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "SPY"]


def upgrade() -> None:
    op.create_table(
        "scanner_universe_symbols",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", name="uq_scanner_universe_symbol_id"),
    )

    conn = op.get_bind()
    symbols_table = sa.table("symbols", sa.column("id", sa.Integer), sa.column("ticker", sa.String))
    universe_table = sa.table("scanner_universe_symbols", sa.column("symbol_id", sa.Integer))

    for ticker in _SEED_SYMBOLS:
        existing = conn.execute(sa.select(symbols_table.c.id).where(symbols_table.c.ticker == ticker)).scalar_one_or_none()
        if existing is None:
            result = conn.execute(sa.insert(symbols_table).values(ticker=ticker).returning(symbols_table.c.id))
            existing = result.scalar_one()
        conn.execute(sa.insert(universe_table).values(symbol_id=existing))


def downgrade() -> None:
    op.drop_table("scanner_universe_symbols")
