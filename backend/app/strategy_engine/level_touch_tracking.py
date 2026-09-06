"""
Shared touch-resolution reconstruction for First Pullback and Reversal
(decisions #107/#108) — the one piece of `LevelInteractionEngine`'s own
rejected-vs-conquered classification these two strategies need but
can't get directly (strategy-engine-design.md §16, D9): that
classification only ever exists on the transient `LevelInteractionChanged`
event, and nothing wires `on_event(...)` triggers to a live Scheduler yet
(`base_strategy.py`'s own "NOT BUILT HERE" note). Both strategies instead
poll `get_level_interaction_engine().get_snapshot(symbol)` directly — the
same free-function-singleton pattern `orb_strategy.py` already uses for
`get_market_clock()` — and reconstruct the resolution themselves, one
candle late.

Deliberately its own module, not duplicated per-strategy: two
independent copies of "what counts as rejected vs. conquered" is exactly
the failure mode the design review (and `level_interaction_engine.py`'s
own module docstring) warns against — a second, subtly different
definition drifting from the engine's authoritative one. Every rule
below is checked against that engine's actual implementation, not
guessed at:

- **Normal resolution** (watched `inside_aura` -> resolved to a steady
  zone): REJECTED if the resolved zone equals `entered_from`, CONQUERED
  if it's the opposite side — the exact rule `level_interaction_engine.
  py`'s own module docstring defines. `entered_from` is never re-derived
  here; it's read verbatim from `get_snapshot()`'s own `holding` dict,
  captured at the candle the touch started.

- **Gap-through** (a steady zone jumps straight to the OPPOSITE steady
  zone, `inside_aura` never observed in between): unconditionally
  CONQUERED, matching the engine's own "no candle ever closed inside the
  Aura in between" definition. The engine still counts this as a real
  touch (`touch_count_today` increments); a strategy that only started
  "watching" on `inside_aura` observations would never see it at all,
  which is exactly the gap the design review caught. Detected here by
  comparing this PROCESS's own last-observed zone to the current one —
  not anything the engine's snapshot exposes directly, since a
  gap-through by definition never has a `holding` entry to read from.

- **Cold-start-unknown-origin** (`entered_from is None`): the engine
  itself refuses to classify this case (its own first-ever observation
  of a level already inside the aura, decision #46) — this module does
  the same, returning `None` rather than guessing a direction, whether
  the unknown origin is the ENGINE's own cold start or just this
  strategy PROCESS starting to watch mid-touch with nothing of its own
  to compare against yet.

**Staleness is the caller's job, not this module's.** `get_snapshot()`'s
`last_applied_candle_ts` (decision #108) tells a caller whether an entry
reflects the candle currently being evaluated; `observe_resolution()`
below assumes it's already been handed a fresh entry. A caller that
detects a stale/missing entry must skip calling this function entirely
for that candle (not call it with a `None` entry and treat the result as
meaningful) — feeding a stale read through the tracker would silently
corrupt `LevelTouchState.last_zone` with data older than what the
strategy is nominally evaluating, and could cause the NEXT, genuinely
fresh candle to be compared against a zone one step older than it
should be, hiding a real transition. Both `first_pullback_strategy.py`
and `reversal_strategy.py` check freshness first and return `None`
before ever touching this module on a stale candle — see either file's
own GATE section.

Kept small and self-contained on purpose (design review point 3): once
real `LevelInteractionChanged` event wiring exists, this whole module
can be deleted and both call sites replaced with a direct read of the
event's own `status` field, without touching anything else in either
strategy's GATE/MATCH/SCORE/PROPOSE logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

TouchResolution = Literal["rejected", "conquered"]


@dataclass
class LevelTouchState:
    """Private per-(symbol, level_key) memory. Nothing here duplicates
    engine state — `last_zone` is this PROCESS's own observation
    history, kept only because gap-throughs skip `inside_aura` entirely
    and this strategy would otherwise never see them (module docstring).
    `entered_from`/`anchor_price` mirror the engine's own values for the
    touch currently being watched, captured once at touch start and
    consumed (cleared) the candle it resolves — never independently
    re-derived. Bundled together rather than tracked separately because
    both are captured at, and only meaningful from, the exact same
    moment (touch start) via the exact same mechanism (`get_snapshot()`'s
    `holding` dict, gone once the touch resolves) — not because they're
    part of the resolution rule itself.

    One instance per (symbol, level_key) a strategy has ever evaluated;
    callers own the dict keyed that way, same shape `orb_strategy.py`'s
    `_ORBState` dict already establishes for per-symbol strategy state."""

    trading_day: date | None = None
    last_zone: str | None = None
    entered_from: str | None = None
    anchor_price: float | None = None


def observe_resolution(
    state: LevelTouchState,
    entry: dict[str, Any] | None,
) -> tuple[TouchResolution | None, str | None, float | None]:
    """Call once per candle for a given (symbol, level_key), and only
    with a FRESH entry (caller's staleness check already passed — see
    module docstring). Returns `(resolution, current_zone, anchor_price)`:

    - `entry is None` (level not tracked yet, e.g. insufficient candles
      for the underlying indicator to warm up): `(None, None, None)`,
      and `state` is left untouched — an honest "nothing observed," not
      a phantom zone change.
    - `resolution` is non-`None` exactly on the candle a touch resolves
      in a classifiable way; every other candle (still forming, no
      transition, level not yet tracked, or an unclassifiable cold
      start) returns `None` regardless of `current_zone`.
    - `anchor_price` is only ever non-`None` in the same call where
      `resolution` is non-`None` — the level's own value captured when
      THIS resolving touch began, straight from the engine's `holding`
      dict at that time, never re-derived or re-read at resolution
      (`holding` no longer exists on `entry` by then).

    Mutates `state` in place unconditionally whenever `entry` is
    present, regardless of what a strategy's own GATE/MATCH does with
    the result — the zone history has to stay complete or a later
    gap-through could be missed."""
    if entry is None:
        return None, None, None

    current_zone: str = entry["zone"]
    trading_day = date.fromisoformat(entry["trading_day"])

    if state.trading_day != trading_day:
        # New day (or this process's first-ever observation) — nothing
        # from a prior session is a meaningful comparison point. Same
        # rollover discipline `_ORBState`/the engine's own
        # `touch_count_today` already use.
        state.trading_day = trading_day
        state.last_zone = None
        state.entered_from = None
        state.anchor_price = None

    previous_zone = state.last_zone
    state.last_zone = current_zone

    if previous_zone is None:
        # This process's first observation of this level today. If
        # already `inside_aura`, bootstrap `entered_from`/`anchor_price`
        # straight from the engine's own record — it may already have a
        # real touch in progress this process didn't personally watch
        # begin, and the engine's value is authoritative regardless.
        # `entered_from is None` here means genuinely unknown origin
        # (engine cold start OR this process started watching with no
        # prior zone either way) — the eventual resolution will
        # honestly decline to classify it.
        if current_zone == "inside_aura":
            holding = entry.get("holding") or {}
            state.entered_from = holding.get("entered_from")
            state.anchor_price = holding.get("anchor_price")
        return None, current_zone, None

    if previous_zone == current_zone:
        return None, current_zone, None  # no transition this candle

    if current_zone == "inside_aura":
        # Touch just started — resolution comes on a later candle.
        holding = entry.get("holding") or {}
        state.entered_from = holding.get("entered_from")
        state.anchor_price = holding.get("anchor_price")
        return None, current_zone, None

    if previous_zone == "inside_aura":
        # Normal resolution.
        entered_from = state.entered_from
        anchor_price = state.anchor_price
        state.entered_from = None  # consumed — this touch is done either way
        state.anchor_price = None
        if entered_from is None:
            return None, current_zone, None  # cold-start-unknown-origin — unclassifiable, honest
        resolution: TouchResolution = "rejected" if current_zone == entered_from else "conquered"
        return resolution, current_zone, anchor_price

    # previous_zone and current_zone are the two different steady sides
    # (below/above), `inside_aura` never observed in between — gap-through,
    # unconditionally conquered by the engine's own definition. No
    # `holding` ever existed for this transition, so `anchor_price` is
    # genuinely unavailable — a caller needing an invalidation reference
    # for a gap-through resolution has no engine-sourced anchor to use
    # (see either strategy's own PROPOSE section for how this is handled).
    state.entered_from = None
    state.anchor_price = None
    return "conquered", current_zone, None
