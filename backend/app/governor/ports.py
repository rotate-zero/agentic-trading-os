"""
Narrow repository/read Protocols the authorizer stub depends on, plus the
plain dataclasses passed across them. No concrete implementation lives
here — that's deliberate.

Why Protocols instead of importing real ORM/session code (fork 1,
resolved 2026-09-22, Saqib): the canonical `trades` ledger table and the
real Portfolio State engine both belong to modules this task's file
boundary explicitly forbids touching (`backend/app/models/**`, any
Alembic migration, `backend/app/portfolio_state/**` — see AGENTS.md and
this delivery's own decision entry). The sibling `execution-ledger-and-
venue` task owns the concrete ledger; a third module (not this task)
owns Portfolio State's real accounting. `AuthorizerStub` (engine.py) is
built against these two narrow interfaces so it doesn't need either to
exist yet, and a thin adapter can satisfy both once the real
implementations land — Python's `Protocol` is structural, so nothing
here requires the eventual concrete class to import this module at all.

This task's own tests exercise `AuthorizerStub` against in-memory/
fake implementations of both Protocols (see backend/tests/
test_governor_engine.py) — a deliberate, explicit departure from this
project's usual "real Postgres 16, never mocks" testing philosophy,
scoped narrowly to the persistence/portfolio-state SEAM this task
does not own the far side of. Everything this task DOES own outright
(the rule pipeline itself, config validation) is still tested as pure,
DB-free logic per the usual convention (rules.py / test_governor_rules.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol


# --- Portfolio State read side -----------------------------------------


@dataclass(frozen=True)
class OpenExposure:
    """One open position or in-flight entry order Portfolio State is
    currently tracking for this execution_mode, as of the snapshot's
    as_of timestamp. Raw, not pre-aggregated — the daily-loss gate's own
    formula (I15, rules.py) does the aggregation itself, so it can
    reproduce exactly which inputs are unknown, per AC #17's own case
    table (missing mark, missing stop, a gap through the stop, ...).
    """

    symbol: str
    direction: Literal["BUY", "SELL"]
    qty: int
    avg_entry_price: float | None  # None only if genuinely not yet known (e.g. order not yet filled)
    stop: float | None  # structural_invalidation for this position/entry; None = unknown
    mark: float | None  # last known price; None = unknown
    unrealized_pnl: float | None  # None whenever `mark` is None — Portfolio State's own honest-absence convention
    is_in_flight: bool  # True = OrderApproved sent, not yet filled; False = a filled, open position


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Everything the authorizer's rule pipeline (rules.py) needs to read
    from Portfolio State for one decision. `exposures` covers BOTH open
    positions and in-flight entries, scoped to this `execution_mode` only
    (paper/live/backtest exposure never leaks into a simulated decision,
    matching I6's mode-isolation intent even though this slice only ever
    runs execution_mode == "simulated")."""

    execution_mode: str
    trading_day: date
    as_of: datetime
    realized_pnl_today: float  # signed; realized_loss_today = max(0, -realized_pnl_today) per I15
    exposures: tuple[OpenExposure, ...] = ()


class PortfolioStateReader(Protocol):
    def get_snapshot(self, execution_mode: str, trading_day: date) -> PortfolioSnapshot:
        """Synchronous, in-memory-speed read — same "point-in-time
        snapshot, no I/O surprises" contract every other engine's
        get_snapshot() in this codebase already offers
        (MarketStateEngine, ContextEngine, LevelInteractionEngine,
        OpportunityCache)."""
        ...


# --- Trade ledger write side --------------------------------------------


@dataclass(frozen=True)
class TradeDecisionRecord:
    """Everything the authorizer stub commits for ONE OpportunityCreated
    evaluation, win or lose. Mirrors §6.2's "COMMIT trade row (decision=
    rejected/approved, reasons, limits_snapshot, thesis_snapshot)" —
    `opportunity_id`/`client_order_id`/`qty`/`reference_price` are only
    ever populated when `decision == "approved"` (EX-9: opportunity_id is
    minted at acceptance, not before)."""

    symbol: str
    strategy: str
    strategy_version: str
    direction: Literal["BUY", "SELL"]
    decision: Literal["approved", "rejected"]
    reasons: list[str]
    limits_snapshot: dict[str, float | int]
    execution_mode: str | None
    market_state_snapshot_present: bool
    context_snapshot_present: bool
    structural_invalidation: float
    structural_target: float
    confidence_at_signal: float
    setup_detected_at: datetime
    decided_at: datetime
    opportunity_id: str | None = None
    client_order_id: str | None = None
    qty: int | None = None
    reference_price: float | None = None


@dataclass(frozen=True)
class TradeDecisionCommitResult:
    committed_at: datetime
    opportunity_id: str | None = None


class LedgerCommitError(Exception):
    """Raised by a TradeLedgerPort implementation when a commit could not
    be durably persisted. The authorizer stub treats this as fatal for
    the current OpportunityCreated: no TradePlanned/GovernorDecision/
    OrderApproved/PlanRejected is published (fork 1: "no event
    publication ... after a required persistence failure"), the
    exception is logged loudly, and the worker moves on to the next
    queued item — one bad commit must not wedge the whole engine."""


class TradeLedgerPort(Protocol):
    def commit_decision(self, record: TradeDecisionRecord) -> TradeDecisionCommitResult:
        """Durably persist one decision BEFORE any event referencing it is
        published (I8: "COMMIT before ANY venue call" — the authorizer's
        own analogue is "COMMIT before ANY publish"). Raises
        LedgerCommitError on failure; must not raise for an ordinary
        rejected decision, only for a genuine persistence fault."""
        ...
