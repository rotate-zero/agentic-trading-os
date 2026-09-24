"""Durable authorization records and approved entry reservations.

Revision ID: 0014
Revises: 0013

Legacy rows are preserved without inventing missing approval terms.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    # Rejections retain the requested label even if it is absent or unknown;
    # approved trades still need valid, fully populated mode/venue labels.
    op.alter_column("trades", "execution_mode", existing_type=sa.String(16), type_=sa.String(), nullable=True)
    op.alter_column("trades", "execution_venue", existing_type=sa.String(32), nullable=True)
    op.create_check_constraint("ck_trades_approved_execution_labels", "trades",
        "decision = 'rejected' OR (execution_mode IS NOT NULL AND execution_venue IS NOT NULL "
        "AND execution_mode IN ('backtest', 'simulated', 'paper', 'live'))")
    op.add_column("trades", sa.Column("decision_record", postgresql.JSONB(none_as_null=True), nullable=True))
    op.create_table(
        "trade_reservations",
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trades.trade_id"), primary_key=True),
        sa.Column("client_order_id", sa.String(128), nullable=False, unique=True),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("reference_price", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("qty > 0", name="ck_trade_reservations_qty"),
        sa.CheckConstraint("reference_price > 0 AND reference_price NOT IN ('NaN', 'Infinity', '-Infinity')",
                           name="ck_trade_reservations_price"),
        sa.CheckConstraint("client_order_id = trade_id::text || chr(58) || 'entry'", name="ck_trade_reservations_client_id"),
    )


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM trade_reservations) OR "
        "EXISTS (SELECT 1 FROM trades WHERE decision_record IS NOT NULL OR execution_mode IS NULL "
        "OR execution_venue IS NULL OR length(execution_mode) > 16)"
    )).scalar():
        raise RuntimeError("Cannot downgrade 0014 with durable authorization records or reservations")
    op.drop_table("trade_reservations")
    op.drop_column("trades", "decision_record")
    op.drop_constraint("ck_trades_approved_execution_labels", "trades", type_="check")
    op.alter_column("trades", "execution_mode", existing_type=sa.String(), type_=sa.String(16), nullable=False)
    op.alter_column("trades", "execution_venue", existing_type=sa.String(32), nullable=False)
