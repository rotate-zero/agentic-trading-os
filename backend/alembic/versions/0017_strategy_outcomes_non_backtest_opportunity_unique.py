"""Partial unique index: one non-backtest strategy outcome per opportunity.

Implements the optional database guard named in
`docs/architecture/execution-engine-design.md` section 6.7.1 (decision #186's
follow-up): `strategy_outcomes(opportunity_id) WHERE is_backtest IS FALSE` is
unique, so a duplicate simulated/paper/live outcome is structurally impossible
even for a second writer. Backtest rows (`is_backtest IS TRUE`) are outside the
predicate and keep sharing an opportunity ID across runs.

Additive and data-preserving: no row is rewritten, deleted or chosen. If the
table already holds duplicate non-backtest opportunity IDs, the upgrade refuses
to run and names them; an operator must resolve them deliberately. Downgrade
drops only this index.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

INDEX = "uq_strategy_outcomes_non_backtest_opportunity"
_SAMPLE = 10


def upgrade() -> None:
    bind = op.get_bind()
    # Same lock CREATE INDEX takes: blocks concurrent writes between the check and the build.
    op.execute("LOCK TABLE strategy_outcomes IN SHARE MODE")
    total = bind.execute(sa.text(
        "SELECT count(*) FROM (SELECT opportunity_id FROM strategy_outcomes "
        "WHERE is_backtest IS FALSE GROUP BY opportunity_id HAVING count(*) > 1) d"
    )).scalar()
    if total:
        sample = bind.execute(sa.text(
            "SELECT opportunity_id, count(*) AS n FROM strategy_outcomes WHERE is_backtest IS FALSE "
            "GROUP BY opportunity_id HAVING count(*) > 1 ORDER BY n DESC, opportunity_id LIMIT :k"
        ), {"k": _SAMPLE}).all()
        listing = ", ".join(f"{row[0]} (x{row[1]})" for row in sample)
        more = f" (first {_SAMPLE} of {total} shown)" if total > _SAMPLE else ""
        raise RuntimeError(
            f"Cannot upgrade 0017: {total} non-backtest opportunity_id value(s) already have more than one "
            f"strategy_outcomes row{more}: {listing}. No row was changed or deleted. Resolve the duplicates "
            f"deliberately (decide which outcome is authoritative), then re-run the upgrade."
        )
    op.create_index(
        INDEX,
        "strategy_outcomes",
        ["opportunity_id"],
        unique=True,
        postgresql_where=sa.text("is_backtest IS FALSE"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="strategy_outcomes")
