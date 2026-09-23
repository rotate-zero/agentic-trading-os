"""execution ledger (trades/orders/fills/positions/portfolio_state_cursor) + strategy_outcomes execution_mode/execution_venue (decision #172, execution-ledger-and-venue)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-22

Two things land in one migration because the design doc's persistence
sketch (execution-engine-design.md §6.8) explicitly scopes them
together: the new ledger tables the Execution Engine/Portfolio State
will write to, and the EX-2/EX-7 changes `strategy_outcomes` needs so a
live (non-backtest) row can exist at all.

See app/models/execution_ledger.py's module docstring for the column
conventions (native UUID for identity needed before insert, JSONB for
dict-shaped fields, the shared execution_mode/execution_venue pairing
CHECK) — this migration's `trades`/`orders`/`fills`/`positions`/
`portfolio_state_cursor` tables mirror that file's ORM classes exactly;
keep the two in sync by hand if either changes.

`strategy_outcomes` (EX-2, EX-7):
  - `execution_mode` (backtest|simulated|paper|live) and
    `execution_venue` (simulated|ibkr|...) are added NOT NULL, backfilled
    from `is_backtest` for existing rows (`is_backtest = true` becomes
    `execution_mode='backtest', execution_venue='simulated'`).
  - **This migration ABORTS if any `is_backtest = false` row already
    exists** — no legitimate writer of one exists yet (design doc §6.8),
    so labelling it `live` would be a guess, not a fact. It reports the
    offending `outcome_id`s for manual review instead of silently
    choosing a label.
  - The four snapshot columns become nullable, with a new
    `snapshot_missing_reasons` JSONB column and a CHECK that (a) a
    `backtest` row still requires all four (preserving decision #128),
    and (b) any NULL snapshot on ANY row has a reason recorded for it.
  - `is_backtest` is kept (compatibility) but is now a CHECKed function
    of `execution_mode`, not an independently-writable classifier.

`schema_version` itself is not touched here — it is an application-level
contract number (`schemas/performance.py:StrategyOutcome.schema_version`),
not a column whose historical values this migration should rewrite. Rows
already written under schema 1 stay 1 (accurate for what they were);
`record_strategy_outcome()`'s callers write 2 going forward once the
mirrored Pydantic/ORM changes (this same delivery) are deployed.

Downgrade note: reverting the four snapshot columns to NOT NULL will
fail if any row written after this migration actually has a NULL
snapshot — an inherent, accepted limitation of reversing a
nullability relaxation once new data depends on it (same class of
caveat 0011's downgrade already documents for its own irreversible
paths).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MODE_VENUE_PAIRING = "(execution_venue = 'simulated') = (execution_mode IN ('backtest', 'simulated'))"

_ORDER_STATUSES = (
    "approved",
    "submitted",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected",
    "unknown",
    "expired",
)

_SNAPSHOT_COLUMNS = (
    "market_state_at_entry",
    "context_at_entry",
    "market_state_at_exit",
    "context_at_exit",
)


def _create_ledger_tables() -> None:
    op.create_table(
        "trades",
        sa.Column(
            "trade_id", postgresql.UUID(as_uuid=True), nullable=False
        ),  # app-supplied (not server-generated) — see execution_ledger.py docstring
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("execution_venue", sa.String(32), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("thesis", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=True),
        sa.Column("limits_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("entry_market_state", postgresql.JSONB(), nullable=True),
        sa.Column("entry_context", postgresql.JSONB(), nullable=True),
        sa.Column("entry_snapshot_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_snapshot_missing_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("strategy_outcomes.outcome_id"), nullable=True),
        sa.Column("outcome_status", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("trade_id"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_trades_decision"),
        sa.CheckConstraint("direction IN ('BUY', 'SELL')", name="ck_trades_direction"),
        sa.CheckConstraint("status IS NULL OR status IN ('open', 'closing', 'closed')", name="ck_trades_status"),
        sa.CheckConstraint(_MODE_VENUE_PAIRING, name="ck_trades_mode_venue_pairing"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("client_order_id", sa.String(128), nullable=False),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trades.trade_id"), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("execution_venue", sa.String(32), nullable=False),
        sa.Column("venue_order_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("position_effect", sa.String(8), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False, server_default="market"),
        sa.Column("limit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="approved"),
        sa.Column("exit_reason", sa.String(16), nullable=True),
        sa.Column("reject_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_orders_side"),
        sa.CheckConstraint("position_effect IN ('open', 'close')", name="ck_orders_position_effect"),
        sa.CheckConstraint("order_type IN ('market', 'limit')", name="ck_orders_order_type"),
        sa.CheckConstraint(f"status IN {_ORDER_STATUSES!r}", name="ck_orders_status"),
        sa.CheckConstraint(_MODE_VENUE_PAIRING, name="ck_orders_mode_venue_pairing"),
    )
    op.create_index("ix_orders_trade_id", "orders", ["trade_id"])

    op.create_table(
        "fills",
        sa.Column("ledger_seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("client_order_id", sa.String(128), sa.ForeignKey("orders.client_order_id"), nullable=False),
        sa.Column("execution_venue", sa.String(32), nullable=False),
        sa.Column("venue_fill_id", sa.String(128), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("venue_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commission", sa.Numeric(18, 6), nullable=True),
        sa.Column("anomaly", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("ledger_seq"),
        sa.UniqueConstraint("execution_venue", "venue_fill_id", name="uq_fills_venue_fill_id"),
        sa.CheckConstraint("anomaly IS NULL OR anomaly IN ('overfill', 'unmatched_order')", name="ck_fills_anomaly"),
    )
    op.create_index("ix_fills_client_order_id", "fills", ["client_order_id"])

    op.create_table(
        "positions",
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trades.trade_id"), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("execution_venue", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("stop", sa.Numeric(18, 6), nullable=True),
        sa.Column("target", sa.Numeric(18, 6), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=True),
        sa.Column("exit_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("position_id"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_positions_side"),
        sa.CheckConstraint("status IN ('open', 'closing', 'closed')", name="ck_positions_status"),
        sa.CheckConstraint(_MODE_VENUE_PAIRING, name="ck_positions_mode_venue_pairing"),
    )
    op.create_index("ix_positions_trade_id", "positions", ["trade_id"])
    op.create_index("ix_positions_symbol_status", "positions", ["symbol", "status"])

    op.create_table(
        "portfolio_state_cursor",
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("last_applied_ledger_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("execution_mode"),
    )


def _drop_ledger_tables() -> None:
    op.drop_table("portfolio_state_cursor")
    op.drop_index("ix_positions_symbol_status", table_name="positions")
    op.drop_index("ix_positions_trade_id", table_name="positions")
    op.drop_table("positions")
    op.drop_index("ix_fills_client_order_id", table_name="fills")
    op.drop_table("fills")
    op.drop_index("ix_orders_trade_id", table_name="orders")
    op.drop_table("orders")
    op.drop_table("trades")


def _migrate_strategy_outcomes_up() -> None:
    bind = op.get_bind()
    live_rows = bind.execute(
        sa.text("SELECT outcome_id FROM strategy_outcomes WHERE is_backtest = false")
    ).fetchall()
    if live_rows:
        ids = ", ".join(str(row[0]) for row in live_rows)
        raise RuntimeError(
            "Migration 0012 aborted: found strategy_outcomes row(s) with "
            f"is_backtest = false (outcome_id(s): {ids}). No legitimate writer of "
            "is_backtest = false rows exists yet (design doc §6.8) -- labelling "
            "them execution_mode='live' here would be a guess, not a fact. "
            "Investigate these rows manually, then re-run this migration."
        )

    op.add_column("strategy_outcomes", sa.Column("execution_mode", sa.String(16), nullable=True))
    op.add_column("strategy_outcomes", sa.Column("execution_venue", sa.String(32), nullable=True))
    op.execute(
        sa.text(
            "UPDATE strategy_outcomes SET execution_mode = 'backtest', execution_venue = 'simulated' "
            "WHERE is_backtest = true"
        )
    )
    op.alter_column("strategy_outcomes", "execution_mode", existing_type=sa.String(16), nullable=False)
    op.alter_column("strategy_outcomes", "execution_venue", existing_type=sa.String(32), nullable=False)

    op.add_column("strategy_outcomes", sa.Column("snapshot_missing_reasons", postgresql.JSONB(), nullable=True))

    for column_name in _SNAPSHOT_COLUMNS:
        op.alter_column(
            "strategy_outcomes", column_name, existing_type=postgresql.JSONB(), nullable=True
        )

    op.create_check_constraint(
        "ck_strategy_outcomes_is_backtest_matches_mode",
        "strategy_outcomes",
        "is_backtest = (execution_mode = 'backtest')",
    )
    op.create_check_constraint(
        "ck_strategy_outcomes_mode_venue_pairing",
        "strategy_outcomes",
        _MODE_VENUE_PAIRING,
    )
    op.create_check_constraint(
        "ck_strategy_outcomes_backtest_requires_all_snapshots",
        "strategy_outcomes",
        "execution_mode <> 'backtest' OR ("
        + " AND ".join(f"{c} IS NOT NULL" for c in _SNAPSHOT_COLUMNS)
        + ")",
    )
    # COALESCE matters here: Postgres treats a NULL CHECK expression as passing (not
    # failing), so with snapshot_missing_reasons itself NULL (the common case — no snapshot
    # missing), "snapshot_missing_reasons ? 'key'" would evaluate to NULL rather than false,
    # silently letting a NULL snapshot with NO reasons object at all slip past. Coalescing to
    # '{}'::jsonb makes the ? operator evaluate to a real false when there's no reasons
    # object, so a NULL snapshot with nothing explaining it is correctly rejected.
    reason_clauses = " AND ".join(
        f"({c} IS NOT NULL OR COALESCE(snapshot_missing_reasons, '{{}}'::jsonb) ? '{c}')" for c in _SNAPSHOT_COLUMNS
    )
    op.create_check_constraint(
        "ck_strategy_outcomes_null_snapshot_has_reason",
        "strategy_outcomes",
        reason_clauses,
    )


def _migrate_strategy_outcomes_down() -> None:
    op.drop_constraint("ck_strategy_outcomes_null_snapshot_has_reason", "strategy_outcomes", type_="check")
    op.drop_constraint("ck_strategy_outcomes_backtest_requires_all_snapshots", "strategy_outcomes", type_="check")
    op.drop_constraint("ck_strategy_outcomes_mode_venue_pairing", "strategy_outcomes", type_="check")
    op.drop_constraint("ck_strategy_outcomes_is_backtest_matches_mode", "strategy_outcomes", type_="check")

    for column_name in _SNAPSHOT_COLUMNS:
        # Fails if any row written after upgrade() actually has a NULL here —
        # see module docstring's downgrade note.
        op.alter_column(
            "strategy_outcomes", column_name, existing_type=postgresql.JSONB(), nullable=False
        )
    op.drop_column("strategy_outcomes", "snapshot_missing_reasons")
    op.drop_column("strategy_outcomes", "execution_venue")
    op.drop_column("strategy_outcomes", "execution_mode")


def upgrade() -> None:
    _create_ledger_tables()
    _migrate_strategy_outcomes_up()


def downgrade() -> None:
    _migrate_strategy_outcomes_down()
    _drop_ledger_tables()
