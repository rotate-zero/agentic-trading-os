"""
Snapshot capture — the read-side CONTRACT a future Execution/Position
Monitor will call at `entry_filled_at`/`exit_filled_at` to populate
`StrategyOutcome.market_state_at_entry`/`_at_exit` and
`context_at_entry`/`_at_exit` (decision #89's locked shape,
strategy-engine-design.md §5). Confirmed decision #98, M4.

**What this is, and isn't.** This module is the CAPTURE MECHANISM only —
a pure read of whatever MarketStateEngine/ContextEngine currently know
for a symbol, shaped to drop straight into those four `StrategyOutcome`
fields. It does not:
  - create the `strategy_outcomes` table (no migration exists yet —
    system-design.md §4.13 lists the shape, not a built table),
  - write anything, ever (no session, no persistence),
  - know what "entry" or "exit" means (no Execution Engine, no Position
    Monitor, no fill event exists yet to call this from).
The actual production call sites are real, later work — building them
now just to exercise this function would be exactly the scope this
build (M4) was deliberately kept narrow to avoid; see
strategy-engine-design.md §10's own staged plan. What matters today is
that the CONTRACT is real and tested against real MarketState/Context
data, not a stub — so whichever module calls this later (Execution
Engine, Position Monitor, a Backtest Runner) has a stable, already-
proven function to call rather than reinventing this read against two
engines' internals itself.

**Why this lives here, not inside context_engine/ or
market_state_engine/.** This module reads BOTH engines' public
`get_snapshot()` contracts (decision #98) — it belongs one layer above
either, not folded into one calling into the other. Same layering
`level_interaction_engine.py` already established by living in
`trading_intelligence/`, one level above the engines it depends on;
same "read-only, owns nothing" shape `trading-intelligence-
architecture.md` §15 describes for World View, applied at function
scope instead of a class, since a full WorldView composite remains
explicitly not built (§15: "it hasn't yet").

**Boundary this module does NOT cross (Saqib, M4 scope discussion):**
Market State Engine and Context Engine stay mutually unaware of each
other and of Strategy Engine. This module is the one place their two
outputs get read together — it depends on both of them; neither of
them, nor `strategy_engine/` (which doesn't exist yet), depends on
this. `evaluate_market_state_snapshot`/`evaluate_context_snapshot`
below are trivial wrappers over each engine's own `get_snapshot()` —
no new coupling, no strategy-specific assumption baked into either
engine to make this work.

**Honest state over fabricated state** (strategy-engine-design.md §11)
governs every field here: a symbol MarketStateEngine/ContextEngine
haven't computed anything for yet returns `None` for that half of the
capture, never a zero-filled or otherwise fabricated placeholder that
would look like real data to a later reader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.context_engine.engine import get_context_engine
from app.market_state_engine.engine import get_market_state_engine


def capture_market_state_snapshot(symbol: str) -> dict[str, Any] | None:
    """Shape matches `StrategyOutcome.market_state_at_entry`/`_at_exit`
    (decision #89): the symbol's own per-symbol `MarketState` fields,
    plus a nested `"market"` key carrying the always-on SPY/QQQ/IWM
    `CrossSymbolState` composite — so one outcome-record snapshot
    captures both "this symbol's own state" and "what the broad market
    was doing" at the same instant, without the caller making two
    separate lookups or a second decision about whether to include
    cross-symbol data.

    Returns `None` only if MarketStateEngine has never computed a
    `MarketState` for this symbol at all — never a partially-filled
    guess. The `"market"` key inside a real result can independently be
    `None` if SPY/QQQ/IWM haven't all reported yet — that's a separate,
    already-existing honesty rule (`MarketStateEngine._compute_cross_
    symbol`), preserved here rather than papered over.
    """
    snapshot = get_market_state_engine().get_snapshot(symbol)
    symbol_state = snapshot["symbols"].get(symbol)
    if symbol_state is None:
        return None
    return {**symbol_state, "market": snapshot["market"]}


def capture_context_snapshot(symbol: str) -> dict[str, Any] | None:
    """Shape matches `StrategyOutcome.context_at_entry`/`_at_exit`
    (decision #89) — the same provider-merged dict `ContextEngine.
    get_snapshot()` already returns for a symbol (global + per-symbol
    providers, decision #96's internal split resolved transparently by
    that method, not re-done here).

    Returns `None` only if ContextEngine has never run its per-symbol
    providers (Fundamentals/News) for this symbol — the global path
    alone (Calendar) isn't treated as "context for this symbol" on its
    own, matching `get_snapshot()`'s own "absent means not-yet"
    convention.
    """
    snapshot = get_context_engine().get_snapshot(symbol)
    symbol_context = snapshot["symbols"].get(symbol)
    if symbol_context is None:
        return None
    return symbol_context["providers"]


@dataclass(frozen=True)
class StrategyOutcomeSnapshots:
    """One call, both halves. This is the actual contract point a future
    Execution/Position Monitor fill handler calls — once, at
    `entry_filled_at`, and again, separately, at `exit_filled_at`
    (strategy-engine-design.md §5). This type doesn't know which side
    it's for; the caller decides that by WHEN it calls
    `capture_strategy_outcome_snapshots`, the same way `StrategyOutcome`
    itself doesn't give `_at_entry`/`_at_exit` a different shape, only a
    different capture time."""

    market_state: dict[str, Any] | None
    context: dict[str, Any] | None


def capture_strategy_outcome_snapshots(symbol: str) -> StrategyOutcomeSnapshots:
    """Convenience wrapper over both capture functions above — the one
    call a future fill handler actually needs to make."""
    return StrategyOutcomeSnapshots(
        market_state=capture_market_state_snapshot(symbol),
        context=capture_context_snapshot(symbol),
    )
