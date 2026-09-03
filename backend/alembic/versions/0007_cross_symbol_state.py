"""cross_symbol_state (Market State Engine M3, decision #91's shape, this build #97)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-03

Extends `market_state_history` (0005) with `CrossSymbolState`'s 7 fields
rather than a new table — decision #91: "no separate table," same
reasoning `strategy_outcomes` already applies to live/backtest rows
sharing one schema distinguished by a flag rather than split into two
tables. Here the distinguishing flag is `symbol_id` itself: the
`__MARKET__` sentinel row (symbol_id resolving to that ticker in
`symbols`) populates the 7 new columns and leaves the 4 original
per-symbol score columns NULL; every other row does the reverse. See
app/market_state_engine/engine.py's `_persist`/`_persist_cross_symbol`
for the write-time assertion enforcing that split (decision #89's
entry_qty==exit_qty precedent, application-level, not a DB CHECK
constraint here).

The 4 original per-symbol score columns (`trend_score`,
`volatility_regime_score`, `volume_regime_score`,
`vwap_relationship_score`) are relaxed from NOT NULL to nullable for
this reason — they were never optional for a per-symbol row, only for
the now-possible cross-symbol row that doesn't have them at all.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_CROSS_SYMBOL_COLUMNS = (
    "spy_direction_score",
    "qqq_direction_score",
    "iwm_direction_score",
    "trend_alignment_score",
    "risk_on_score",
    "qqq_leadership_score",
    "iwm_confirmation_score",
)

_RELAXED_PER_SYMBOL_COLUMNS = (
    "trend_score",
    "volatility_regime_score",
    "volume_regime_score",
    "vwap_relationship_score",
)


def upgrade() -> None:
    for column_name in _NEW_CROSS_SYMBOL_COLUMNS:
        op.add_column(
            "market_state_history",
            sa.Column(column_name, sa.Numeric(6, 2), nullable=True),
        )
    for column_name in _RELAXED_PER_SYMBOL_COLUMNS:
        op.alter_column("market_state_history", column_name, nullable=True)


def downgrade() -> None:
    for column_name in _RELAXED_PER_SYMBOL_COLUMNS:
        op.alter_column("market_state_history", column_name, nullable=False)
    for column_name in reversed(_NEW_CROSS_SYMBOL_COLUMNS):
        op.drop_column("market_state_history", column_name)
