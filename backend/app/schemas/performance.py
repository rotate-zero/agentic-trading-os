"""
Performance Intelligence's persisted-record schemas — `StrategyOutcome`
and `BacktestRun`. Shape locked by decision #89, strategy-engine-
design.md §5 (StrategyOutcome) and §7 (BacktestRun). This module is the
Pydantic half of the persistence layer built by decision #120; see
app/models/trading_intelligence.py for the paired ORM tables
(`strategy_outcomes`, `backtests`) and
app/trading_intelligence/performance.py for the write path
(`record_strategy_outcome`).

**Why this lives here, not in `schemas/events/`.** Every schema under
`schemas/events/` is an Event Bus payload — published via `EventBus`,
carried by an `EventEnvelope`, with a corresponding `EventType`
(`MarketState`, `ContextChanged`, `FeatureSet`, ...). Neither
`StrategyOutcome` nor `BacktestRun` is ever published as an event — both
are persistence/API-facing domain contracts, written directly to
Postgres by `record_strategy_outcome()` (and, for `BacktestRun`, by a
future Backtest Runner — §7, not built now) and read back by direct
query. Filing them under `schemas/events/` would misrepresent them as
event payloads. `Opportunity` (the other nearby domain schema) lives
inline in `strategy_engine/base_strategy.py` instead, since it's owned
by and constructed exclusively inside that one engine; `StrategyOutcome`
and `BacktestRun` don't belong to any one engine the way `Opportunity`
belongs to Strategy Engine, so a standalone `schemas/performance.py` —
named for the subsystem the design doc and decision log already call
"Performance Intelligence" — fits better than folding either into an
unrelated engine module.

**Naming collision check, done explicitly (decision #89/#98 already
share a naming space here).** `app/trading_intelligence/state_snapshot.py`
already defines `StrategyOutcomeSnapshots` — a narrow, 2-field
`@dataclass(frozen=True)` bundle (`market_state`, `context`) returned by
`capture_strategy_outcome_snapshots()`. That is NOT this module's
`StrategyOutcome` — it's the upstream read-side capture contract (#98,
M4) that a future Execution Engine/Position Monitor fill handler will
call to help POPULATE four of `StrategyOutcome`'s own fields
(`market_state_at_entry`/`_at_exit`, `context_at_entry`/`_at_exit`) at
`entry_filled_at`/`exit_filled_at`. `StrategyOutcomeSnapshots` is
untouched, unrenamed, and not imported here — this module doesn't
depend on it, and nothing about this schema's shape was changed to
accommodate it (see the discrepancy note below).

**A real, deliberately UNRESOLVED discrepancy between this schema and
`state_snapshot.py`'s current capture behavior — recorded here, not
silently patched.** §5 (decision #89) locks `market_state_at_entry`,
`market_state_at_exit`, `context_at_entry`, and `context_at_exit` as
REQUIRED `dict` fields (no `| None`) on `StrategyOutcome`. But
`state_snapshot.py`'s own `capture_market_state_snapshot()`/
`capture_context_snapshot()` can each honestly return `None` — for a
symbol MarketStateEngine/ContextEngine haven't computed anything for
yet (cold start), per that module's own "honest state over fabricated
state" docstring. That means a real future caller (Execution Engine/
Position Monitor) COULD receive `None` from the capture contract at
exactly the moment it needs to construct a `StrategyOutcome`, and would
have no honest non-`None` value to put in a field this schema requires.
This task is a persistence-layer build, not an architecture revision —
per Saqib's explicit instruction, the fix is NOT to weaken §5's locked
contract to paper over what `state_snapshot.py` can produce. The gap is
real, genuinely unresolved, and tracked as new open item **D17**
(strategy-engine-design.md §10): whichever future module wires a real
caller to `record_strategy_outcome()` will need to either (a) only call
it once both snapshots are confirmed non-`None`, or (b) trigger a real
revisit of §5's nullability — not decided here, and no code in this
module resolves it either way. This module implements §5 exactly as
locked: all four fields stay required.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class StrategyOutcome(BaseModel):
    """One row per closed trade — §5 (decision #89), field-for-field.
    Persisted to `strategy_outcomes` (app/models/trading_intelligence.py)
    via `record_strategy_outcome()`
    (app/trading_intelligence/performance.py). Never blended live/
    backtest data in one query without filtering on `is_backtest`; never
    used to store a computed rank/aggregate — see this module's own
    docstring and §5's "atomic outcomes, not a stored rank" framing.

    §5's own code block groups these fields under five inline comment
    headers (A-E below); decision #89's prose separately describes "six
    field groups" under three pillars (Ledger/Evidence/Provenance), with
    `is_backtest`/`backtest_run_id` called out as a distinct "Backtest
    Provenance" sixth group. The actual locked Python shape (which this
    class implements verbatim) keeps those two fields inside group A
    (Identity & Versioning) rather than a separate lettered group — a
    pre-existing minor grouping-label mismatch between §5's prose and
    its own code, not introduced or resolved by this build. Recorded
    here factually; not this task's job to pick one framing and rewrite
    the other.
    """

    # --- A. Identity & Versioning ---
    outcome_id: UUID
    opportunity_id: UUID
    schema_version: int = Field(
        description=(
            "Shape of THIS record (system-design.md §10.2's additive-field rule: a new "
            "OPTIONAL field doesn't bump this; removing a field or changing its meaning "
            "does). Distinct from strategy_version below — the two are never conflated: "
            "this describes the StrategyOutcome SCHEMA's own shape, strategy_version "
            "describes which immutable StrategyConfig version produced the trade being "
            "recorded. A schema migration can happen without any strategy changing at "
            "all, and vice versa."
        )
    )
    strategy_name: str
    strategy_version: str  # §3's immutable StrategyConfig.version — never blended across versions in a query
    symbol: str
    origin: Literal["auto", "manual"]  # mirrors trades.origin (trading-intelligence-architecture.md §18); trades table not yet built
    is_backtest: bool  # §7 — never blended with live rows in the same query without filtering on this
    backtest_run_id: UUID | None = Field(
        default=None,
        description=(
            "FK -> backtests.run_id, enforced at the DB level (backtests is built in this "
            "same migration). None for every live trade — a live StrategyOutcome has no "
            "backtest run to reference. Required, in practice, whenever is_backtest=True, "
            "but that pairing isn't asserted by this schema (or by record_strategy_outcome) "
            "— left to the DB's real FK constraint plus this schema's own honest optionality, "
            "not a second, redundant application-level check."
        ),
    )

    # --- B. Timing --- all instants UTC; trading_day is the one ET-calendar concession
    trading_day: date  # covers entry AND exit — day-trading only, no overnight holds (§5; see D8, strategy-engine-design.md §10, for when this stops being true)
    setup_detected_at: datetime
    signal_confirmed_at: datetime | None = None  # Opportunity.confirmed_at, renamed at the persistence boundary only
    decided_at: datetime | None = None  # Decision Engine acted
    entry_filled_at: datetime
    exit_filled_at: datetime
    holding_seconds: int  # stored convenience, same precedent as realized_r below (derivable, kept for query ergonomics)

    # --- C. Ledger ---
    direction: Literal["BUY", "SELL"]
    entry_price: float  # avg/VWAP fill if more than one partial
    entry_qty: float
    exit_price: float  # avg/VWAP fill
    exit_qty: float  # INVARIANT: entry_qty == exit_qty for a fully closed row — asserted at write time by record_strategy_outcome(), not just documented here (§11)
    commission_total: float | None = Field(
        default=None,
        description="None if the broker adapter doesn't surface it yet — honest state, never estimated (§11).",
    )
    slippage_entry: float | None = Field(
        default=None,
        description=(
            "entry_price - TradePlanned.entry (trading-intelligence-architecture.md §11's event) — "
            "separates execution quality from strategy edge. Nullable until Execution Engine exists "
            "to produce a TradePlanned event to diff against."
        ),
    )
    realized_pnl: float = Field(
        description=(
            "NET of commission_total. Stated explicitly because this is exactly the kind of field "
            "where an undocumented meaning change later would be a real schema_version bump, not a "
            "footnote — gross_pnl may be added later if isolating commission drag from strategy edge "
            "becomes useful, but that's not this field and not v1."
        )
    )
    realized_r: float
    exit_reason: Literal["target", "stop", "time", "eod_flatten", "manual", "reversal"] = Field(
        description=(
            "eod_flatten is distinct from a thesis-driven 'time' exit: the day-trading rule forces "
            "every position closed by session end regardless of thesis, a materially different signal "
            "from a strategy that decided on its own it had waited long enough."
        )
    )

    # --- D. Thesis ---
    structural_invalidation: float
    structural_target: float
    final_stop: float  # Trade Planning's actual number, post-refinement
    final_target: float
    confidence_at_signal: float

    # --- E. Evidence --- interpretation, not measurement (§11); raw feature values live in
    # feature_snapshots (system-design.md §4.13), referenced not duplicated
    evidence: dict = Field(
        description="§4's structured conditions — the strategy's own reasoning, never a dumping ground for arbitrary market data (§11)."
    )
    market_state_at_entry: dict = Field(
        description=(
            "trend_score, volatility_score, etc. (decision #91's per-symbol scores) captured at "
            "entry_filled_at, not setup_detected_at — see §5's own note on why _at_entry, not "
            "_at_signal. A dict here, not a typed model, since the dimension set is still growing. "
            "Required per §5 — see this module's own docstring for the known, unresolved gap "
            "between this requirement and state_snapshot.py's (#98) honest-None capture behavior (D17)."
        )
    )
    context_at_entry: dict = Field(description="gap day?, session type, VIX regime — see market_state_at_entry's note on required-vs-capturable (D17).")
    market_state_at_exit: dict = Field(description="Same shape as _at_entry, captured at exit_filled_at. See market_state_at_entry's D17 note.")
    context_at_exit: dict = Field(description="Same shape as context_at_entry, captured at exit_filled_at. See market_state_at_entry's D17 note.")
    feature_snapshot_id: UUID | None = Field(
        default=None,
        description=(
            "FK -> feature_snapshots, for full traceability back to the exact FeatureSet without "
            "duplicating it into this row. No FK constraint enforced at the DB level: feature_snapshots "
            "does not exist yet anywhere in this codebase (confirmed by grep — only referenced in docs "
            "as future work, system-design.md §4.13). Stored as a plain UUID, 'referenced not duplicated' "
            "per §5's own text, without inventing the missing table as a side effect of this task."
        ),
    )


class BacktestRun(BaseModel):
    """One row per (strategy_version, config_hash) per walk-forward
    fold — §7 (decision #89), field-for-field. Persisted to `backtests`
    (app/models/trading_intelligence.py). Not one row per whole
    grid-search sweep: `walk_forward_fold`/`is_holdout` only mean
    something at this granularity (§7). No Backtest Runner exists to
    write these yet (§7: "not built now") — this schema and its table
    are the locked target shape a future Runner writes into, same
    "build the stable contract now, real callers plug in later"
    precedent decision #98 already used for `state_snapshot.py`'s read
    side.
    """

    run_id: UUID
    sweep_id: UUID = Field(
        description="Groups every run belonging to the same grid-search session — a report pulls 'all runs in this sweep,' not an inline candidate list on one bloated row."
    )
    strategy_name: str
    strategy_version: str  # §3's immutable version being tested
    config_hash: str = Field(
        description="Sub-version identifier for tuning within strategy_version, pre-promotion — never itself a promoted StrategyConfig."
    )
    symbol_universe: list[str]
    date_range_start: date
    date_range_end: date
    data_version: str = Field(description="Market-data snapshot/provider version this run read from.")
    feature_version: str = Field(
        description=(
            "Feature Engine version this run computed indicators with. Without this and "
            "data_version, two backtests can look identical and produce different results for "
            "reasons that have nothing to do with the strategy being tested — a reproducibility "
            "gap closed here rather than after Stage 1."
        )
    )
    walk_forward_fold: int | None = None
    is_holdout: bool = Field(description="In-sample vs. out-of-sample — required, not inferred.")
    created_at: datetime
