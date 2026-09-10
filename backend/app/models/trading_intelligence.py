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

Two more tables, added by decision #120 (Performance Intelligence's
persistence layer, strategy-engine-design.md §5/§7, shape locked by
decision #89) — placed in this same file on the same "analytical/
append-only, training-data-shaped" reasoning as the two tables above,
not because they share a lifecycle with `level_interaction_state`/
`level_interaction_events` specifically:
- `strategy_outcomes` — one row per CLOSED trade, append-only, the
  paired Pydantic contract is `app.schemas.performance.StrategyOutcome`.
  Written by `app.trading_intelligence.performance.record_strategy_outcome()`
  — no real caller wired yet (Execution Engine/Position Monitor don't
  exist), same "build the stable contract now, real callers plug in
  later" precedent decision #98 already used for the read side
  (`state_snapshot.py`).
- `backtests` — one row per (strategy_version, config_hash) per
  walk-forward fold, the paired Pydantic contract is
  `app.schemas.performance.BacktestRun`. No Backtest Runner exists yet
  to write these (§7: "not built now") — same precedent as above.

These two tables establish two conventions genuinely new to this
codebase, confirmed absent by grepping every existing model/migration
before writing either: JSONB for dict-shaped fields (`evidence`,
`market_state_at_entry`/`_at_exit`, `context_at_entry`/`_at_exit` —
`postgresql.JSONB`, not plain `JSON`, for GIN-indexability if a future
query needs it, at zero extra cost today) and native PostgreSQL UUID
primary/foreign keys (`outcome_id`, `run_id`, and the UUID-shaped
reference columns below) — every table elsewhere in this codebase uses
an `Integer`/`BigInteger` `Identity()` autoincrement PK instead.
`symbol_universe` (a homogeneous list of strings, not a dict) uses
`postgresql.ARRAY(String)` rather than JSONB, to keep JSONB reserved for
genuinely dict-shaped fields as the two tables' own decision entry
states explicitly.

`strategy_outcomes.backtest_run_id` is a real, enforced
`ForeignKey("backtests.run_id")` — `backtests` is created in the same
migration (0008), so nothing prevents enforcing it. `feature_snapshot_id`
and `opportunity_id` are plain nullable UUID columns with NO FK
constraint: `feature_snapshots` and any `opportunities` table do not
exist anywhere in this codebase (confirmed by grep — both are
docs-only, future work), and building either as a side effect of this
task was explicitly out of scope. §5's own text calls this
"referenced not duplicated" — the UUID is stored honestly as a
reference with nothing yet to enforce it against.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    BigInteger,
    Date,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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


class BacktestRunRecord(Base):
    """One row per (strategy_version, config_hash) per walk-forward
    fold — §7 (decision #89), shape locked field-for-field. Paired
    Pydantic contract: `app.schemas.performance.BacktestRun` (that
    module's own docstring explains why this file's class is named
    `BacktestRunRecord`, not `BacktestRun` — avoiding a same-name
    collision with the Pydantic contract in any module that needs to
    import both). Created before `StrategyOutcomeRecord` in this same
    migration (0008) specifically so `strategy_outcomes.backtest_run_id`
    can be a real, enforced FK to this table's `run_id`. No Backtest
    Runner writes to this table yet (§7: "not built now")."""

    __tablename__ = "backtests"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    sweep_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Homogeneous list of tickers, not dict-shaped — postgresql.ARRAY, not JSONB
    # (this file's own module docstring explains the JSONB-vs-ARRAY split).
    symbol_universe: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    date_range_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_range_end: Mapped[date] = mapped_column(Date, nullable=False)
    data_version: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(32), nullable=False)
    walk_forward_fold: Mapped[int | None] = mapped_column(Integer)
    is_holdout: Mapped[bool] = mapped_column(Boolean, nullable=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class StrategyOutcomeRecord(Base):
    """One row per CLOSED trade, append-only — §5 (decision #89), shape
    locked field-for-field across all five of §5's own inline groups
    (Identity & Versioning, Timing, Ledger, Thesis, Evidence — this
    file's own module docstring notes the pre-existing minor mismatch
    between §5's "six groups" prose and its five-lettered code). Paired
    Pydantic contract: `app.schemas.performance.StrategyOutcome`, whose
    own docstring covers naming (`StrategyOutcomeRecord` here vs.
    `StrategyOutcome` there vs. the unrelated, narrower
    `StrategyOutcomeSnapshots` in `state_snapshot.py`) and the
    unresolved §5-vs-#98 nullability discrepancy (D17) in full — not
    repeated here. Written exclusively via
    `app.trading_intelligence.performance.record_strategy_outcome()`,
    which asserts `entry_qty == exit_qty` before this row is ever
    constructed — this class itself has no way to enforce that
    cross-field invariant at the ORM/DB level and doesn't try to."""

    __tablename__ = "strategy_outcomes"

    # --- A. Identity & Versioning ---
    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)  # auto | manual — Literal enforced by Pydantic, not a DB CHECK (matches this file's existing string-enum columns)
    is_backtest: Mapped[bool] = mapped_column(Boolean, nullable=False)
    backtest_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtests.run_id"), nullable=True
    )

    # --- B. Timing ---
    trading_day: Mapped[date] = mapped_column(Date, nullable=False)
    setup_detected_at: Mapped[datetime] = mapped_column(nullable=False)
    signal_confirmed_at: Mapped[datetime | None] = mapped_column()
    decided_at: Mapped[datetime | None] = mapped_column()
    entry_filled_at: Mapped[datetime] = mapped_column(nullable=False)
    exit_filled_at: Mapped[datetime] = mapped_column(nullable=False)
    holding_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- C. Ledger --- entry_qty == exit_qty enforced by record_strategy_outcome(), not this table
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY | SELL
    entry_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    entry_qty: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    exit_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    exit_qty: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    commission_total: Mapped[float | None] = mapped_column(Numeric(18, 6))
    slippage_entry: Mapped[float | None] = mapped_column(Numeric(18, 6))
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)  # net of commission_total
    realized_r: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(16), nullable=False)  # target | stop | time | eod_flatten | manual | reversal

    # --- D. Thesis ---
    structural_invalidation: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    structural_target: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    final_stop: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    final_target: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    confidence_at_signal: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)

    # --- E. Evidence --- JSONB (this file's own module docstring explains why JSONB, not JSON)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    market_state_at_entry: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context_at_entry: Mapped[dict] = mapped_column(JSONB, nullable=False)
    market_state_at_exit: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context_at_exit: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # No FK: feature_snapshots doesn't exist yet anywhere in this codebase (confirmed by grep).
    feature_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
