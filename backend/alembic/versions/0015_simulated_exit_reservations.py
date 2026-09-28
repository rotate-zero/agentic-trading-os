"""Durable stop/target observations and position-bound close orders."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_orders_position_id", "orders", "positions", ["position_id"], ["position_id"])
    op.create_table(
        "exit_requests",
        sa.Column("position_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("positions.position_id"), primary_key=True),
        sa.Column("exit_reason", sa.String(16), nullable=False),
        sa.Column("trigger_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("trigger_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("exit_reason IN ('stop', 'target')", name="ck_exit_requests_reason"),
        sa.CheckConstraint("trigger_price > 0", name="ck_exit_requests_price"),
    )
    op.create_index(
        "uq_orders_active_exit_per_position", "orders", ["position_id"], unique=True,
        postgresql_where=sa.text("position_effect = 'close' AND status IN ('approved','submitted','partially_filled','unknown')"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM exit_requests) OR EXISTS(SELECT 1 FROM orders WHERE position_id IS NOT NULL)")).scalar():
        raise RuntimeError("Cannot downgrade 0015 with durable exit requests or position-linked orders")
    op.drop_index("uq_orders_active_exit_per_position", table_name="orders")
    op.drop_table("exit_requests")
    op.drop_constraint("fk_orders_position_id", "orders", type_="foreignkey")
    op.drop_column("orders", "position_id")
