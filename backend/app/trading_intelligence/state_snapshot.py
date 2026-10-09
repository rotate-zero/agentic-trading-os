"""
Snapshot capture — the read-side contract used by Backtest Runner and the
simulated OutcomeRecorder for StrategyOutcome entry and exit fields
(decision #98, strategy-engine-design.md §5).

**What this is, and isn't.** This module is the CAPTURE MECHANISM only —
a pure read of whatever MarketStateEngine/ContextEngine currently know
for a symbol, shaped to drop straight into those four `StrategyOutcome`
fields. It does not:
  - create the `strategy_outcomes` table (migration 0008 built this at
    decision #120 — the "no migration exists yet" framing this
    docstring originally carried is stale as of #120 and corrected
    here),
  - write anything, ever (no session, no persistence),
  - know what "entry" or "exit" means; the caller chooses capture time.
Backtest Runner captures both halves for its own completed fill path. The
simulated OutcomeRecorder captures at the first entry fill and near closure,
using NULL with a reason if a half is unavailable. This module remains a
read-only mechanism shared by those callers.

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

**Detached and JSON-safe (`outcome-snapshot-json-serialization`).** Both
capture functions return a rebuilt copy that is valid JSON, so neither the
engines' live cached state nor a database JSONB column can be changed through
it. Aware datetimes become UTC ISO strings with a `Z` suffix and `date` becomes
`YYYY-MM-DD`; any other value the contract cannot store raises
`SnapshotSerializationError` here, at capture, rather than failing later inside
a persistence transaction. See `to_json_safe_snapshot` and
execution-engine-design.md §6.7.1 F.

**Honest state over fabricated state** (strategy-engine-design.md §11)
governs every field here: a symbol MarketStateEngine/ContextEngine
haven't computed anything for yet returns `None` for that half of the
capture, never a zero-filled or otherwise fabricated placeholder that
would look like real data to a later reader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.context_engine.engine import get_context_engine
from app.market_state_engine.engine import get_market_state_engine


class SnapshotSerializationError(ValueError):
    """A captured snapshot holds a value the snapshot contract cannot store as JSON.

    Raised at capture time, before anything is persisted, so a bad value can
    never reach a JSONB column or leave a half-written outcome row. The message
    names the path and the offending *type* only — never the value itself.
    """


def _json_safe(value: Any, path: str, active: set[int]) -> Any:
    # Exact JSON scalars pass through unchanged (bool is checked by type, not
    # isinstance, so only real bool/int/float/str/None are accepted here).
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise SnapshotSerializationError(f"{path}: non-finite float is not valid JSON")
        return value
    # datetime must be tested before date: datetime is a date subclass.
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            # Same stance as stored_history._to_utc: refuse rather than guess a zone.
            raise SnapshotSerializationError(f"{path}: timezone-naive datetime is not supported")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        if id(value) in active:
            raise SnapshotSerializationError(f"{path}: circular reference")
        active.add(id(value))
        try:
            if isinstance(value, dict):
                out = {}
                for key, item in value.items():
                    if type(key) is not str:
                        raise SnapshotSerializationError(f"{path}: non-string key of type {type(key).__name__}")
                    out[key] = _json_safe(item, f"{path}.{key}", active)
                return out
            return [_json_safe(item, f"{path}[{index}]", active) for index, item in enumerate(value)]
        finally:
            active.discard(id(value))
    raise SnapshotSerializationError(f"{path}: unsupported value of type {type(value).__name__}")


def to_json_safe_snapshot(value: Any, *, name: str = "snapshot") -> Any:
    """Return a detached, JSON-safe deep copy of a captured snapshot.

    - Containers are rebuilt, so later mutation of the engines' cached state
      cannot change a captured snapshot, nor the reverse.
    - Nested structure, nulls and exact JSON scalars are preserved as they are.
    - Aware datetimes become UTC ISO strings with a ``Z`` suffix (the repo's
      existing wire form); ``date`` becomes ``YYYY-MM-DD``.
    - Anything else (naive datetimes, Decimal, sets, bytes, arbitrary objects,
      non-finite floats, non-string keys, cycles) raises
      :class:`SnapshotSerializationError`. Nothing is stringified, dropped or
      replaced with a made-up value.
    """
    return _json_safe(value, name, set())


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

    The result is a detached, JSON-safe copy; raises
    `SnapshotSerializationError` for a value the contract cannot store.
    """
    snapshot = get_market_state_engine().get_snapshot(symbol)
    symbol_state = snapshot["symbols"].get(symbol)
    if symbol_state is None:
        return None
    return to_json_safe_snapshot({**symbol_state, "market": snapshot["market"]}, name="market_state")


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

    The result is a detached, JSON-safe copy (aware datetimes as UTC `Z`
    strings, `date` as ISO); raises `SnapshotSerializationError` for a value
    the contract cannot store.
    """
    snapshot = get_context_engine().get_snapshot(symbol)
    symbol_context = snapshot["symbols"].get(symbol)
    if symbol_context is None:
        return None
    return to_json_safe_snapshot(symbol_context["providers"], name="context")


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
