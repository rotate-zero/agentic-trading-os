"""Simulated EOD exit request, immutable fallback slot and dispatch claim (decision #185).

Additive over 0015: `exit_requests` may now carry an `eod_flatten` original
request with an immutable placement window, a durable expiry timestamp and a
first-wins stop/target fallback; `orders` gain a dispatch-claim marker.
Existing stop/target rows and orders are untouched and keep decision #184
behavior. Downgrade refuses to run when it would discard EOD/fallback/dispatch
evidence.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_EOD_GROUP = (
    "(exit_reason = 'eod_flatten' AND eod_flatten_at IS NOT NULL AND eod_close_at IS NOT NULL"
    " AND eod_flatten_at < eod_close_at"
    " AND (eod_expired_at IS NULL OR eod_expired_at >= eod_close_at))"
    " OR (exit_reason <> 'eod_flatten' AND eod_flatten_at IS NULL AND eod_close_at IS NULL"
    " AND eod_expired_at IS NULL)"
)
_FALLBACK_GROUP = (
    "(fallback_reason IS NULL AND fallback_trigger_price IS NULL AND fallback_trigger_ts IS NULL)"
    " OR (exit_reason = 'eod_flatten' AND fallback_reason IN ('stop', 'target')"
    " AND fallback_trigger_price IS NOT NULL AND fallback_trigger_price > 0"
    " AND fallback_trigger_price <> CAST('NaN' AS numeric)"
    " AND fallback_trigger_ts IS NOT NULL)"
)

_REQUESTS_GUARD = """
CREATE FUNCTION exit_requests_guard_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.position_id IS DISTINCT FROM OLD.position_id
       OR NEW.exit_reason IS DISTINCT FROM OLD.exit_reason
       OR NEW.trigger_price IS DISTINCT FROM OLD.trigger_price
       OR NEW.trigger_ts IS DISTINCT FROM OLD.trigger_ts
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.eod_flatten_at IS DISTINCT FROM OLD.eod_flatten_at
       OR NEW.eod_close_at IS DISTINCT FROM OLD.eod_close_at THEN
        RAISE EXCEPTION 'exit request original fields and EOD bounds are immutable';
    END IF;
    IF OLD.eod_expired_at IS NOT NULL AND NEW.eod_expired_at IS DISTINCT FROM OLD.eod_expired_at THEN
        RAISE EXCEPTION 'exit request EOD expiry is immutable once recorded';
    END IF;
    IF OLD.fallback_reason IS NOT NULL AND (
           NEW.fallback_reason IS DISTINCT FROM OLD.fallback_reason
           OR NEW.fallback_trigger_price IS DISTINCT FROM OLD.fallback_trigger_price
           OR NEW.fallback_trigger_ts IS DISTINCT FROM OLD.fallback_trigger_ts) THEN
        RAISE EXCEPTION 'exit request fallback is immutable once recorded';
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql
"""

_ORDERS_GUARD = """
CREATE FUNCTION orders_guard_dispatch_marker() RETURNS trigger AS $$
BEGIN
    IF OLD.exit_dispatch_started_at IS NOT NULL
       AND NEW.exit_dispatch_started_at IS DISTINCT FROM OLD.exit_dispatch_started_at THEN
        RAISE EXCEPTION 'order dispatch marker is immutable once recorded';
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql
"""


def upgrade():
    op.add_column("orders", sa.Column("exit_dispatch_started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_orders_dispatch_marker_close_only", "orders",
        "exit_dispatch_started_at IS NULL OR position_effect = 'close'",
    )
    op.create_check_constraint(
        "ck_orders_unsent_expiry_has_no_marker", "orders",
        "NOT (reject_reason = 'eod_window_closed' AND exit_dispatch_started_at IS NOT NULL)",
    )

    for name in ("eod_flatten_at", "eod_close_at", "eod_expired_at", "fallback_trigger_ts"):
        op.add_column("exit_requests", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("exit_requests", sa.Column("fallback_reason", sa.String(16), nullable=True))
    op.add_column("exit_requests", sa.Column("fallback_trigger_price", sa.Numeric(18, 6), nullable=True))
    op.drop_constraint("ck_exit_requests_reason", "exit_requests", type_="check")
    op.create_check_constraint(
        "ck_exit_requests_reason", "exit_requests", "exit_reason IN ('stop', 'target', 'eod_flatten')"
    )
    op.create_check_constraint("ck_exit_requests_eod_group", "exit_requests", _EOD_GROUP)
    op.create_check_constraint("ck_exit_requests_fallback_group", "exit_requests", _FALLBACK_GROUP)

    op.execute(_REQUESTS_GUARD)
    op.execute(
        "CREATE TRIGGER trg_exit_requests_immutable BEFORE UPDATE ON exit_requests "
        "FOR EACH ROW EXECUTE FUNCTION exit_requests_guard_immutable()"
    )
    op.execute(_ORDERS_GUARD)
    op.execute(
        "CREATE TRIGGER trg_orders_dispatch_marker_immutable BEFORE UPDATE ON orders "
        "FOR EACH ROW EXECUTE FUNCTION orders_guard_dispatch_marker()"
    )


def downgrade():
    evidence = op.get_bind().execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM exit_requests WHERE exit_reason = 'eod_flatten' "
        "OR eod_flatten_at IS NOT NULL OR eod_close_at IS NOT NULL OR eod_expired_at IS NOT NULL "
        "OR fallback_reason IS NOT NULL OR fallback_trigger_price IS NOT NULL OR fallback_trigger_ts IS NOT NULL) "
        "OR EXISTS(SELECT 1 FROM orders WHERE exit_dispatch_started_at IS NOT NULL "
        "OR reject_reason = 'eod_window_closed' OR exit_reason = 'eod_flatten')"
    )).scalar()
    if evidence:
        raise RuntimeError(
            "Cannot downgrade 0016: EOD requests, fallback observations, expiry or dispatch-claim "
            "evidence would be lost"
        )
    op.execute("DROP TRIGGER trg_orders_dispatch_marker_immutable ON orders")
    op.execute("DROP FUNCTION orders_guard_dispatch_marker()")
    op.execute("DROP TRIGGER trg_exit_requests_immutable ON exit_requests")
    op.execute("DROP FUNCTION exit_requests_guard_immutable()")
    op.drop_constraint("ck_exit_requests_fallback_group", "exit_requests", type_="check")
    op.drop_constraint("ck_exit_requests_eod_group", "exit_requests", type_="check")
    op.drop_constraint("ck_exit_requests_reason", "exit_requests", type_="check")
    op.create_check_constraint("ck_exit_requests_reason", "exit_requests", "exit_reason IN ('stop', 'target')")
    for name in ("fallback_trigger_price", "fallback_reason", "fallback_trigger_ts",
                 "eod_expired_at", "eod_close_at", "eod_flatten_at"):
        op.drop_column("exit_requests", name)
    op.drop_constraint("ck_orders_unsent_expiry_has_no_marker", "orders", type_="check")
    op.drop_constraint("ck_orders_dispatch_marker_close_only", "orders", type_="check")
    op.drop_column("orders", "exit_dispatch_started_at")
