"""
The authorizer stub's rule pipeline — §6.2's numbered rule list, evaluated
in order, short-circuiting at the first failing rule. Pure, DB-free, no
event-bus/asyncio/Settings-singleton dependency: every input arrives as a
plain argument, so this is directly unit-testable (AGENTS.md testing
philosophy: "pure/DB-free tests for pure functions") without a running
EventBus, a real Postgres, or fake ports — see test_governor_rules.py.

Rule order (this task's scope item 1, restated precisely):
  0. execution_mode == "simulated" (never falls back — AC #3, #4)
  1. regular session (MarketClock.is_regular_session)
  2. opportunity.status == "actionable"
  3. pre-trade snapshot gate — both market_state and context snapshots present
  4. no open position/in-flight entry for the symbol; open + in-flight < max_concurrent_positions
  5. reference price exists; stop geometry valid; qty = floor(notional / reference_price) >= 1
  6. daily-loss gate (I15) — realized + unrealized/open-risk + the candidate's own stop-out loss

NOT rule 0-6: reduce-only (exits only — out of scope here), authorization-
gate replay checks (execution_engine's own concern, not the authorizer's).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.governor.ports import PortfolioSnapshot

# --- inputs ---------------------------------------------------------------


@dataclass(frozen=True)
class OpportunityView:
    """The subset of Opportunity (base_strategy.py) rules.py actually
    reads — kept as its own small type rather than importing the full
    Pydantic Opportunity model here, so this module has zero dependency
    on strategy_engine's own schema evolving underneath it. engine.py
    (the only caller) is responsible for the Opportunity -> OpportunityView
    translation."""

    strategy: str
    version: str
    direction: Literal["BUY", "SELL"]
    confidence: float
    structural_invalidation: float
    structural_target: float
    status: Literal["potential", "waiting", "actionable", "expired"]
    setup_detected_at: datetime


@dataclass(frozen=True)
class LimitsSnapshot:
    max_concurrent_positions: int
    fixed_notional_usd: float
    daily_loss_cap_usd: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "max_concurrent_positions": self.max_concurrent_positions,
            "fixed_notional_usd": self.fixed_notional_usd,
            "daily_loss_cap_usd": self.daily_loss_cap_usd,
        }


@dataclass(frozen=True)
class AuthorizationContext:
    symbol: str
    opportunity: OpportunityView
    execution_mode: str | None
    is_regular_session: bool
    market_state_snapshot_present: bool
    context_snapshot_present: bool
    portfolio: PortfolioSnapshot
    reference_price: float | None
    now: datetime


# --- output -----------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizationResult:
    decision: Literal["approved", "rejected"]
    reasons: list[str]
    qty: int | None = None
    reference_price: float | None = None


_TERMINAL_EXPOSURE_UNKNOWN = object()  # sentinel: at least one open_exposure_loss term is unknown


def _open_exposure_loss(portfolio: PortfolioSnapshot) -> float | None:
    """I15's "Σ over open positions AND in-flight entries of
    max(qty × |avg_entry − stop|, −unrealized_pnl)" — None (UNKNOWN) if
    ANY exposure has a missing mark or a missing stop (AC #17's own
    "missing mark"/"missing stop" cases)."""
    total = 0.0
    for exposure in portfolio.exposures:
        if exposure.stop is None or exposure.avg_entry_price is None or exposure.unrealized_pnl is None:
            return None
        loss_if_stopped = exposure.qty * abs(exposure.avg_entry_price - exposure.stop)
        term = max(loss_if_stopped, -exposure.unrealized_pnl)
        total += term
    return total


def evaluate_authorization(ctx: AuthorizationContext, limits: LimitsSnapshot) -> AuthorizationResult:
    reasons: list[str]

    # Rule 0 — execution_mode gate (AC #3, #4): only "simulated" proceeds,
    # every other value (paper/live/backtest/unknown/None) rejects with
    # the same reason and never falls back to a different mode.
    if ctx.execution_mode != "simulated":
        return AuthorizationResult(decision="rejected", reasons=["execution_mode_not_permitted"])

    # Rule 1 — regular session only.
    if not ctx.is_regular_session:
        return AuthorizationResult(decision="rejected", reasons=["outside_regular_session"])

    # Rule 2 — actionable only (not potential/waiting/expired).
    if ctx.opportunity.status != "actionable":
        return AuthorizationResult(decision="rejected", reasons=["not_actionable"])

    # Rule 3 — pre-trade snapshot gate. Missing EITHER snapshot rejects;
    # the fallback for a snapshot missing at FILL time is out of scope
    # here (EX-7 / OutcomeRecorder territory).
    if not ctx.market_state_snapshot_present:
        return AuthorizationResult(decision="rejected", reasons=["snapshot_unavailable:market_state"])
    if not ctx.context_snapshot_present:
        return AuthorizationResult(decision="rejected", reasons=["snapshot_unavailable:context"])

    # Rule 4 — max concurrent positions / symbol-busy.
    symbol_busy = any(e.symbol == ctx.symbol for e in ctx.portfolio.exposures)
    if symbol_busy:
        return AuthorizationResult(decision="rejected", reasons=["symbol_busy"])
    total_open = len(ctx.portfolio.exposures)
    if total_open >= limits.max_concurrent_positions:
        return AuthorizationResult(decision="rejected", reasons=["max_concurrent_positions"])

    # Rule 5 — reference price, stop geometry, fixed-notional sizing.
    if ctx.reference_price is None:
        return AuthorizationResult(decision="rejected", reasons=["no_reference_price"])
    reference_price = ctx.reference_price
    stop = ctx.opportunity.structural_invalidation
    if ctx.opportunity.direction == "BUY":
        valid_geometry = stop < reference_price
    else:
        valid_geometry = stop > reference_price
    if not valid_geometry:
        return AuthorizationResult(decision="rejected", reasons=["invalid_stop_geometry"])
    qty = math.floor(limits.fixed_notional_usd / reference_price)
    if qty < 1:
        return AuthorizationResult(decision="rejected", reasons=["notional_below_one_share"])

    # Rule 6 — daily-loss gate (I15). Realized loss + open exposure loss
    # (including in-flight entries) + the CANDIDATE trade's own stop-out
    # loss, all against the same cap. A missing mark/stop anywhere in the
    # existing book makes the whole open_exposure_loss term UNKNOWN,
    # treated as unbounded (reject) — implemented as documented, not
    # silently softened (per this task's own standing instruction).
    realized_loss_today = max(0.0, -ctx.portfolio.realized_pnl_today)
    open_exposure_loss = _open_exposure_loss(ctx.portfolio)
    if open_exposure_loss is None:
        return AuthorizationResult(decision="rejected", reasons=["loss_exposure_unknown"])
    candidate_loss = qty * abs(reference_price - stop)
    if realized_loss_today + open_exposure_loss >= limits.daily_loss_cap_usd:
        return AuthorizationResult(decision="rejected", reasons=["daily_loss_cap_reached"])
    if realized_loss_today + open_exposure_loss + candidate_loss > limits.daily_loss_cap_usd:
        return AuthorizationResult(decision="rejected", reasons=["projected_loss_exceeds_daily_cap"])

    return AuthorizationResult(decision="approved", reasons=[], qty=qty, reference_price=reference_price)
