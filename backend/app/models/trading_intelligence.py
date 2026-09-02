"""
Level Interaction Engine's persisted tables — confirmed decision #46.
See docs/architecture/trading-intelligence-architecture.md §4 for the
"has memory, rebuilt from persisted history on startup" pattern
`level_interaction_state` follows, and
app/trading_intelligence/level_interaction_engine.py for the engine that
reads/writes these.

Market State Engine does NOT follow this same restart pattern, despite
an earlier version of this docstring claiming it was "specified to" —
decision #91 explicitly revises that for Market State specifically: v1's
rolling window is short enough that a cold start on restart is an
accepted simplification, not something its own table
(`market_state_history`, app/models/market_state.py) needs to help
reconstruct. Corrected here per decision #93, in the same change that
built that table.

Two tables, deliberately split by lifecycle, not one:
- `level_interaction_state` — one row per (symbol, timeframe, level_key),
  overwritten in place. This is the restart checkpoint: on boot, the
  engine has no in-memory state at all, and needs to know "was AMD
  already mid-touch on SMA-9 when the process went down" without
  replaying an entire day of candles to reconstruct it.
- `level_interaction_events` — append-only, one row per CONCLUDED touch
  (rejected, conquered, or an unresolved cold-start edge case). This is
  the actual analytical log — what a future Strategy Engine reads, and
  exactly the shape Saqib flagged as useful for training-data purposes.
  `market_state_history` mirrors this table's role, not
  `level_interaction_state`'s — see its own module docstring.

No range partitioning here unlike `candles` (app/models/market_data.py) —
these grow at touch-rate (a handful of zone transitions per symbol per
level per day), not tick/candle-rate; nowhere near the volume that
partitioning was solving for.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LevelInteractionState(Base):
    """Current zone + in-progress touch (if any) for one (symbol, timeframe,
    level_key). `level_key` is whatever key FeatureEngine published under
    `FeaturesUpdated.features` — e.g. "sma_9" — deliberately not a
    hardcoded enum, so this table (and the engine) needs zero changes when
    EMA/VWAP/pivots start publishing under their own keys later."""

    __tablename__ = "level_interaction_state"
    __table_args__ = (
        UniqueConstraint("symbol_id", "timeframe", "level_key", name="uq_level_state_symbol_tf_key"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    level_key: Mapped[str] = mapped_column(String(32), nullable=False)

    trading_day: Mapped[date] = mapped_column(Date, nullable=False)
    touch_count_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    zone: Mapped[str] = mapped_column(String(16), nullable=False)  # below | inside_aura | above
    zone_entered_ts: Mapped[datetime] = mapped_column(nullable=False)

    # Populated only while zone == "inside_aura" (an active, unresolved touch).
    touch_anchor_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    touch_entered_ts: Mapped[datetime | None] = mapped_column()
    touch_entered_from: Mapped[str | None] = mapped_column(String(8))  # below | above | NULL (cold-start — see engine)

    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class LevelInteractionEvent(Base):
    """One row per concluded touch. `outcome` is NULL for the rare
    cold-start case where this process's very first observation of a
    (symbol, timeframe, level_key) was already inside the Aura — with no
    known entry side, "rejected vs. conquered" isn't a meaningful
    classification for that specific touch (see engine docstring)."""

    __tablename__ = "level_interaction_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    level_key: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_day: Mapped[date] = mapped_column(Date, nullable=False)

    outcome: Mapped[str | None] = mapped_column(String(16))  # rejected | conquered | NULL
    entered_from: Mapped[str | None] = mapped_column(String(8))  # below | above | NULL (cold-start)
    exited_to: Mapped[str] = mapped_column(String(8), nullable=False)  # below | above
    entered_ts: Mapped[datetime | None] = mapped_column()  # NULL for a gap-through touch — see engine
    exited_ts: Mapped[datetime] = mapped_column(nullable=False)
    seconds_in_zone: Mapped[int] = mapped_column(Integer, nullable=False)

    anchor_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    distance_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)

    # dwell = normal case, at least one candle closed inside the Aura first.
    # gap = price closed on one side, then the opposite side, with no candle
    #   ever closing inside the Aura in between — see engine docstring.
    # cold_start_unknown_origin = this process's first-ever observation of
    #   this (symbol, timeframe, level_key) was already inside the Aura.
    observed_via: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
