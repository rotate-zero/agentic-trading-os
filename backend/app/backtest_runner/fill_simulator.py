"""
fill_simulator — v1's deliberately simple fill model (see this task's
own brief, and `strategy-engine-design.md` §7). Pure functions only: no
DB access, no EventBus, no wall-clock. Every timestamp compared or
returned here comes from the replayed candle sequence or from
`MarketClock` applied to those candles — never `datetime.now()`.

**The model, stated plainly (all documented simplifications, not bugs):**
  - Entry: next candle's OPEN after an `Opportunity` with
    `status == "actionable"` is produced. No slippage, no partial fills.
  - Exit: walk forward from the entry candle. Exit at `structural_target`
    if touched (checked via candle high/low, not just close), at
    `structural_invalidation` ("stop") if touched. If a single candle
    touches BOTH in the same bar, STOP WINS — the conservative v1
    convention Saqib confirmed. If neither is touched by the real
    regular-session close for the entry's trading day (day-trading only,
    §5/D8 — no overnight holds), force `exit_reason="eod_flatten"` at
    that candle's close.
  - Position: `entry_qty = exit_qty = 1` always — no sizing model.
  - Costs: `commission_total`/`slippage_entry` are the caller's job to
    leave `None` (this module doesn't set them) — honest absence, never
    invented. `realized_pnl` is therefore trivially net of nothing, not
    because costs are zero but because none are modeled yet — see
    `compute_realized_pnl`'s own docstring.

**What this module refuses to do rather than guess.** If the provided
candle sequence runs out before the entry trading day's real regular-
session close is reached, that is NOT the same condition as "walked all
the way to close and found no target/stop" — mislabeling it
`eod_flatten` would fabricate a session-close exit that never actually
happened in the replay. `simulate_exit()` raises
`InsufficientReplayDataError` instead. A caller (the Backtest Runner)
choosing to simply not record an outcome for that entry, rather than
retry with more data, is a reasonable v1 response to this — but it's a
different, honest condition from `eod_flatten` and this module keeps them
distinct rather than collapsing one into the other for caller convenience.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from app.broker_adapters.base import Candle
from app.core.market_clock import MarketClock, get_market_clock
from app.strategy_engine.base_strategy import Opportunity

__all__ = [
    "EntryFill",
    "ExitFill",
    "InsufficientReplayDataError",
    "simulate_entry",
    "simulate_exit",
    "compute_realized_r",
    "compute_realized_pnl",
    "regular_session_close_utc",
]

# Same ET zone / close-time constants `core/market_clock.py` itself uses
# (`_MARKET_CLOSE = time(16, 0)`, half-day close at 13:00 — confirmed by
# reading that module directly). MarketClock exposes no public "close
# instant for trading_day X" accessor, only session-membership checks
# (`is_market_open`, `current_session`, ...), so `regular_session_close_utc`
# below derives one FROM MarketClock's own real `is_half_day()` — reusing
# real holiday/half-day logic rather than re-deciding it — instead of
# adding a new method to `market_clock.py` itself (out of scope: "don't
# redesign existing engines merely to make v1 replay easier" applies
# equally to MarketClock).
_ET = ZoneInfo("America/New_York")
_REGULAR_CLOSE = time(16, 0)
_HALF_DAY_CLOSE = time(13, 0)


class InsufficientReplayDataError(RuntimeError):
    """See module docstring's final section. Raised, never silently
    downgraded to a fabricated `eod_flatten`."""


@dataclass(frozen=True)
class EntryFill:
    entry_price: float
    entry_ts: datetime
    entry_candle_index: int  # index into the candle list this fill was simulated against


@dataclass(frozen=True)
class ExitFill:
    exit_price: float
    exit_ts: datetime
    exit_reason: Literal["target", "stop", "eod_flatten"]  # v1 never produces "time"/"manual"/"reversal" — see StrategyOutcome.exit_reason's fuller enum
    exit_candle_index: int


def regular_session_close_utc(clock: MarketClock, trading_day: date) -> datetime:
    """The real regular-session close instant for `trading_day`, in UTC —
    16:00 ET normally, 13:00 ET on a real half-day (`clock.is_half_day()`,
    real logic, not reimplemented here)."""
    close_time = _HALF_DAY_CLOSE if clock.is_half_day(trading_day) else _REGULAR_CLOSE
    return datetime.combine(trading_day, close_time, tzinfo=_ET).astimezone(timezone.utc)


def simulate_entry(opportunity: Opportunity, candles: list[Candle], signal_index: int) -> EntryFill | None:
    """`candles[signal_index]` is the candle whose replay produced
    `opportunity` (i.e. `market_state.candle_ts == candles[signal_index].candle_ts`
    at the moment `Strategy.evaluate()` returned it). Fills at
    `candles[signal_index + 1].open`.

    Returns `None` — never a fabricated fill — when either:
      - `opportunity.status != "actionable"` ("potential"/"waiting"
        Opportunities aren't ready to act on; "expired" ones are past
        acting on), or
      - there is no next candle in the provided sequence (the replay
        simply doesn't extend far enough to fill this signal — an honest
        "can't tell," not a manufactured price).
    """
    if opportunity.status != "actionable":
        return None
    next_index = signal_index + 1
    if next_index >= len(candles):
        return None
    fill_candle = candles[next_index]
    return EntryFill(entry_price=fill_candle.open, entry_ts=fill_candle.candle_ts, entry_candle_index=next_index)


def _target_touched(opportunity: Opportunity, candle: Candle) -> bool:
    if opportunity.direction == "BUY":
        return candle.high >= opportunity.structural_target
    return candle.low <= opportunity.structural_target


def _stop_touched(opportunity: Opportunity, candle: Candle) -> bool:
    if opportunity.direction == "BUY":
        return candle.low <= opportunity.structural_invalidation
    return candle.high >= opportunity.structural_invalidation


def simulate_exit(
    opportunity: Opportunity,
    entry_fill: EntryFill,
    candles: list[Candle],
    clock: MarketClock | None = None,
) -> ExitFill:
    """Walk forward from `entry_fill.entry_candle_index + 1`, checking
    each candle's high/low against `opportunity.structural_target`/
    `structural_invalidation`. See module docstring for the
    stop-wins-on-tie convention and the `eod_flatten`-vs-
    `InsufficientReplayDataError` distinction — both are real, deliberate
    behavior, not edge cases glossed over.

    `candles` should be the FULL replayed sequence (same list
    `simulate_entry` was given is fine) — this function locates the
    relevant slice itself via `entry_fill.entry_candle_index`.
    """
    clock = clock or get_market_clock()
    entry_day = clock.trading_day(entry_fill.entry_ts)
    session_close = regular_session_close_utc(clock, entry_day)

    last_same_day_index: int | None = None
    i = entry_fill.entry_candle_index + 1
    while i < len(candles):
        candle = candles[i]
        if clock.trading_day(candle.candle_ts) != entry_day:
            break  # crossed into the next trading day without exiting — handled below
        last_same_day_index = i

        target_touched = _target_touched(opportunity, candle)
        stop_touched = _stop_touched(opportunity, candle)
        if stop_touched:
            # Stop wins on a same-candle tie with target — checked first,
            # deliberately, so the tie case falls out of this ordering
            # naturally rather than needing a separate branch.
            return ExitFill(opportunity.structural_invalidation, candle.candle_ts, "stop", i)
        if target_touched:
            return ExitFill(opportunity.structural_target, candle.candle_ts, "target", i)
        if candle.candle_ts >= session_close:
            return ExitFill(candle.close, candle.candle_ts, "eod_flatten", i)
        i += 1

    # Ran out of same-day candles without hitting target/stop/close.
    if last_same_day_index is None:
        raise InsufficientReplayDataError(
            f"simulate_exit(): no candle after the entry fill (index {entry_fill.entry_candle_index}) "
            f"belongs to the entry's own trading day ({entry_day}) — cannot determine an exit."
        )
    last_candle = candles[last_same_day_index]
    if last_candle.candle_ts >= session_close:
        return ExitFill(last_candle.close, last_candle.candle_ts, "eod_flatten", last_same_day_index)
    raise InsufficientReplayDataError(
        f"simulate_exit(): replay data for {entry_day} ends at {last_candle.candle_ts.isoformat()} "
        f"without reaching regular-session close ({session_close.isoformat()}) — this fixture doesn't "
        "extend far enough to honestly determine this trade's exit. Not treated as eod_flatten: that "
        "would fabricate a session-close exit that never actually appeared in the replay."
    )


def compute_realized_r(opportunity: Opportunity, entry_fill: EntryFill, exit_fill: ExitFill) -> float:
    """(exit pnl per unit) / (planned risk per unit at signal time —
    `|entry_price - structural_invalidation|`, NOT the actual stop-fill
    price, since R is defined against the THESIS's planned risk).
    Direction-aware for both legs."""
    risk_per_unit = abs(entry_fill.entry_price - opportunity.structural_invalidation)
    if risk_per_unit == 0:
        raise ValueError(
            "compute_realized_r(): structural_invalidation equals entry_price — zero planned risk, "
            "realized_r is undefined (division by zero), not silently reported as 0.0 or inf."
        )
    pnl_per_unit = (
        exit_fill.exit_price - entry_fill.entry_price
        if opportunity.direction == "BUY"
        else entry_fill.entry_price - exit_fill.exit_price
    )
    return pnl_per_unit / risk_per_unit


def compute_realized_pnl(opportunity: Opportunity, entry_fill: EntryFill, exit_fill: ExitFill, qty: float = 1.0) -> float:
    """`(entry_qty = exit_qty = 1` in v1 — see module docstring)`. Net of
    commission_total by construction, per `StrategyOutcome.realized_pnl`'s
    own contract — but that's because v1 leaves `commission_total = None`
    (nothing modeled, nothing to subtract), NOT because this function
    asserts costs are zero. A future caller that starts modeling
    commission must subtract it explicitly wherever it constructs the
    `StrategyOutcome`, not silently rely on this function having already
    done so."""
    pnl_per_unit = (
        exit_fill.exit_price - entry_fill.entry_price
        if opportunity.direction == "BUY"
        else entry_fill.entry_price - exit_fill.exit_price
    )
    return pnl_per_unit * qty
