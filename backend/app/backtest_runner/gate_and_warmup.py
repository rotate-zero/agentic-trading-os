"""
gate_and_warmup — combines the real `gate_conditions_satisfied()` check
(`strategy_engine/gate_conditions.py`, unmodified, imported only) with
D17's snapshot-availability requirement, into one entry-allowed decision
the Backtest Runner can act on without re-deriving either rule itself.

**D17, restated for this module specifically.** §5 locks
`market_state_at_entry`/`context_at_entry` as REQUIRED `dict` fields on
`StrategyOutcome` — no `| None`. `state_snapshot.py`'s
`capture_market_state_snapshot()`/`capture_context_snapshot()` can each
honestly return `None`. This module's `check_entry_allowed()` is D17
option (a), applied: a simulated entry is only allowed once BOTH capture
calls return real, non-`None` dicts — checked directly against the real
capture functions (which, per `engine_singleton_guard.py`, resolve
through whichever engines are currently installed as the process
singleton), never estimated via a fixed "wait N candles" guess. If either
is `None`, no entry is allowed and no `StrategyOutcome` may be recorded
for that signal — full stop, not a fabricated `{}` standing in for the
missing one (Unit 2's proof run already showed this resolves from candle
0 in the fixture-backed replay, since `MarketStateEngine`'s first-ever
debounce trigger runs synchronously and `FixtureBacktestContextProvider`
always evaluates before this module ever reads it — but this module
enforces the check unconditionally regardless of that empirical finding,
since a different replay setup could still hit real cold-start absence).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.strategy_engine.base_strategy import StrategyConfig
from app.strategy_engine.gate_conditions import gate_conditions_satisfied
from app.trading_intelligence.state_snapshot import capture_context_snapshot, capture_market_state_snapshot

__all__ = ["EntryGateResult", "check_entry_allowed"]


@dataclass(frozen=True)
class EntryGateResult:
    allowed: bool
    reason: str  # short, human-readable — for the run's own per-candle audit trail, not persisted anywhere itself
    market_state_snapshot: dict[str, Any] | None
    context_snapshot: dict[str, Any] | None


def check_entry_allowed(strategy_config: StrategyConfig, candle_ts: datetime, symbol: str) -> EntryGateResult:
    """Two checks, gate_conditions first (cheap, no DB/singleton read
    needed) then D17 (reads through the real capture functions). Always
    returns a result — never raises for an honestly-disallowed entry;
    `reason` explains which check failed."""
    if not gate_conditions_satisfied(strategy_config.gate_conditions, candle_ts):
        return EntryGateResult(allowed=False, reason="gate_conditions not satisfied", market_state_snapshot=None, context_snapshot=None)

    market_state_snapshot = capture_market_state_snapshot(symbol)
    context_snapshot = capture_context_snapshot(symbol)
    if market_state_snapshot is None or context_snapshot is None:
        missing = ", ".join(
            name
            for name, snap in (("market_state", market_state_snapshot), ("context", context_snapshot))
            if snap is None
        )
        return EntryGateResult(
            allowed=False,
            reason=f"D17: {missing} snapshot not yet available",
            market_state_snapshot=market_state_snapshot,
            context_snapshot=context_snapshot,
        )

    return EntryGateResult(allowed=True, reason="ok", market_state_snapshot=market_state_snapshot, context_snapshot=context_snapshot)
