"""Selection journal and candidate acceptance claims (D2, decision-selection-audit-contract).

Revision ID: 0018
Revises: 0017

Additive: two new tables, no existing row is read, rewritten or backfilled.
Legacy trades keep no claim. UPDATE is refused on both tables by trigger;
the application never deletes either. Downgrade refuses while either table
holds rows, so audit evidence is never discarded silently.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_MODES = "('backtest', 'simulated', 'paper', 'live')"

_GUARD = """
CREATE FUNCTION decision_audit_refuse_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% rows are append-only and cannot be updated', TG_TABLE_NAME;
END
$$ LANGUAGE plpgsql
"""


def upgrade():
    op.create_table(
        "selection_attempts",
        sa.Column("selection_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("shadow", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("selected_candidate_id", sa.String(128), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(f"execution_mode IN {_MODES}", name="ck_selection_attempts_mode"),
        sa.CheckConstraint("result IN ('selected', 'abstained')", name="ck_selection_attempts_result"),
        sa.CheckConstraint("(result = 'selected') = (selected_candidate_id IS NOT NULL)",
                           name="ck_selection_attempts_selected_candidate"),
        sa.CheckConstraint("selected_candidate_id IS NULL OR length(selected_candidate_id) > 0",
                           name="ck_selection_attempts_candidate_id"),
        sa.CheckConstraint("length(policy_version) > 0", name="ck_selection_attempts_policy_version"),
        sa.CheckConstraint("schema_version >= 1", name="ck_selection_attempts_schema_version"),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'object'", name="ck_selection_attempts_evidence_object"),
        sa.UniqueConstraint("selection_id", "execution_mode", "selected_candidate_id", "shadow",
                            name="uq_selection_attempts_acceptance_target"),
    )
    op.create_table(
        "candidate_acceptances",
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("candidate_id", sa.String(128), nullable=False),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trades.trade_id"), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_shadow", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("execution_mode", "candidate_id", name="pk_candidate_acceptances"),
        sa.UniqueConstraint("trade_id", name="uq_candidate_acceptances_trade_id"),
        sa.ForeignKeyConstraint(
            ["selection_id", "execution_mode", "candidate_id", "selection_shadow"],
            ["selection_attempts.selection_id", "selection_attempts.execution_mode",
             "selection_attempts.selected_candidate_id", "selection_attempts.shadow"],
            name="fk_candidate_acceptances_selected_attempt"),
        sa.CheckConstraint(f"execution_mode IN {_MODES}", name="ck_candidate_acceptances_mode"),
        sa.CheckConstraint("NOT selection_shadow", name="ck_candidate_acceptances_non_shadow"),
        sa.CheckConstraint("length(candidate_id) > 0", name="ck_candidate_acceptances_candidate_id"),
    )
    op.execute(_GUARD)
    for table in ("selection_attempts", "candidate_acceptances"):
        op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION decision_audit_refuse_update()")


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM selection_attempts) OR EXISTS (SELECT 1 FROM candidate_acceptances)"
    )).scalar():
        raise RuntimeError("Cannot downgrade 0018: selection journal or candidate acceptance evidence would be lost")
    op.execute("DROP TRIGGER trg_candidate_acceptances_append_only ON candidate_acceptances")
    op.execute("DROP TRIGGER trg_selection_attempts_append_only ON selection_attempts")
    op.execute("DROP FUNCTION decision_audit_refuse_update()")
    op.drop_table("candidate_acceptances")
    op.drop_table("selection_attempts")
