"""
Daily Levels' persisted identity table — confirmed decision #63 (Stage 2
of docs/architecture/daily-levels-design.md). See that doc's §4 for the
full "persistent level_id, price-proximity reconciled, never rank-based"
design, and app/feature_engine/engine.py's `_reconcile_and_persist_daily_levels`
for the engine logic that reads/writes this table.

One table, not two like `trading_intelligence.py`'s pair — Daily Levels
has no equivalent to `level_interaction_events`' append-only touch log
yet; that's Stage 3's concern (LevelInteractionEngine reading
`daily_levels`, once built, would write its OWN events keyed by this
table's `level_id`, into the EXISTING `level_interaction_events` table —
not a new one). This table's only job is answering "is today's clustered
zone near $150.18 the same physical zone as yesterday's $150.21 one,"
which is a single current-state-per-level_id question, not an event log.

`status` distinguishes a level still being confirmed day-over-day
(`active`) from one that stopped matching (`archived`) — archived rows
are kept, not deleted, so a level's full price history stays queryable
even after it stops appearing in the live `daily_levels` list (design
doc §4's own "unmatched survivor is archived, not deleted" language).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Identity, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DailyLevelState(Base):
    """One row per (symbol, level_id) — overwritten in place while
    `status == "active"`, same "current-state checkpoint, not a log"
    shape as `LevelInteractionState`. `level_id` is generated from this
    row's own DB identity at first-mint time (`f"{symbol}-DL-{self.id}"`,
    set once, never recomputed) — globally unique with zero extra
    bookkeeping, and stable for the row's entire life regardless of how
    its price drifts or its rank among other levels changes."""

    __tablename__ = "daily_levels_state"
    __table_args__ = (UniqueConstraint("symbol_id", "level_id", name="uq_daily_level_state_symbol_level_id"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), nullable=False)
    level_id: Mapped[str] = mapped_column(String(64), nullable=False)

    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    strength: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_candle_count: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active | archived
    first_seen_day: Mapped[date] = mapped_column(Date, nullable=False)
    # Bumped every day this level_id gets re-matched to a fresh cluster —
    # NOT bumped while archived, so "how long has this genuinely persisted"
    # stays answerable from first_seen_day/last_confirmed_day alone.
    last_confirmed_day: Mapped[date] = mapped_column(Date, nullable=False)
    archived_day: Mapped[date | None] = mapped_column(Date, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
