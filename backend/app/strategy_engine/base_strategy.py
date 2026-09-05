"""
Strategy Engine — base interface. `Strategy(ABC)`, `StrategyConfig`,
`Opportunity`, `ScheduleTrigger`. This is Stage 1 (`strategy-engine-
design.md` §12) — the first application code in `strategy_engine/`.
Everything here is locked by decisions #87-89 and system-design.md §4.8;
this module doesn't invent new design, it implements what those already
decided. See `strategy-engine-design.md` §1-4 for the full reasoning —
only the parts genuinely left open by those docs (noted inline below)
are decided fresh here, in decision #99.

Anatomy every strategy's `evaluate()` follows (§1): GATE (cheap
precondition) → MATCH (does the pattern hold) → SCORE (confidence) →
PROPOSE (an Opportunity). Nothing in this file enforces those four
stages structurally — they're a convention each `Strategy` subclass's
own `evaluate()` follows, same as the design doc's own illustrative
example. `orb_strategy.py` is the first concrete example.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState


# --- ScheduleTrigger ---------------------------------------------------
#
# Answers "when does evaluate() even get invoked" (§2a) — timing, never
# market condition. system-design.md §4.8's interface names three
# constructors by example: every_candle(), after_time("09:35"),
# on_event("VolumeSpike"). Modeled as one small frozen dataclass with a
# `kind` discriminator plus three factory functions, rather than three
# separate classes — there's no behavior difference between them yet
# (nothing reads `.kind` today; the Strategy Scheduler that will is real,
# separate, later work — see the module docstring's own scope note
# below), so three classes would be speculative structure for a
# distinction that doesn't do anything yet.
#
# NOT BUILT HERE: the actual Strategy Scheduler that reads `.trigger`
# off a live population of strategies and decides when to call
# `evaluate()` in the live pipeline. §2a describes it as "already the
# existing design, unchanged" by analogy to the Scanner's own cadence
# mechanism, but no such module exists in code yet — wiring `evaluate()`
# into the live event bus is downstream integration work, same category
# as the Strategy Gate's declarative environmental conditions (§2b) and
# the Opportunity Engine/Decision Engine consuming `OpportunityCreated`.
# `ScheduleTrigger` exists so `Strategy.trigger` has something real to
# hold and `orb_strategy.py` can declare its own timing intent — every
# GATE/MATCH/SCORE/PROPOSE function underneath is fully unit-testable
# today regardless of whether a live scheduler exists yet, same
# "pure logic first, wiring later" split momentum_strategy.py's
# (discarded) docstring already used this reasoning for.
@dataclass(frozen=True)
class ScheduleTrigger:
    kind: Literal["every_candle", "after_time", "on_event"]
    timeframe: str | None = None  # every_candle(timeframe=...)
    at: str | None = None  # after_time("09:30") — "HH:MM", ET, same convention as MarketClock
    until: str | None = None  # after_time(..., until="09:45") — optional, open-ended if None
    event_name: str | None = None  # on_event("VolumeSpike")


def every_candle(timeframe: str = "1m") -> ScheduleTrigger:
    """Fire on every closed candle for the given timeframe. Default "1m"
    — deliberately not bare `every_candle()` with no timeframe: every
    real strategy needs to know WHICH candle stream it's watching, and
    an implicit default silently picked per-call site would be exactly
    the kind of ambiguity §1's "one template to write every strategy
    against" tries to avoid. system-design.md §4.8's `every_candle()`
    example (no args) is the illustrative shorthand; "1m" is what it
    resolves to in practice for every strategy planned so far."""
    return ScheduleTrigger(kind="every_candle", timeframe=timeframe)


def after_time(at: str, until: str | None = None) -> ScheduleTrigger:
    """Fire once the clock passes `at` (ET, "HH:MM"), optionally only
    until `until`. NOT used by ORB — see orb_strategy.py's own module
    docstring for why a strategy that needs to both accumulate an
    opening range AND watch for a later breakout needs `every_candle`,
    not a single time-boxed window, correcting §1's own illustrative
    example. Kept here because the interface names it and a future
    strategy (e.g. one that only ever acts in a fixed window) may
    genuinely want it."""
    return ScheduleTrigger(kind="after_time", at=at, until=until)


def on_event(event_name: str) -> ScheduleTrigger:
    """Fire off a named event (e.g. "VolumeSpike") rather than a candle
    close or a clock time. Not used by any Stage-1 strategy; kept here
    because the interface names it (system-design.md §4.8)."""
    return ScheduleTrigger(kind="on_event", event_name=event_name)


# --- StrategyConfig ------------------------------------------------------
#
# §3, verbatim shape. A `Strategy` subclass is a family ("what market
# behavior are we trying to exploit"); its tunable numbers live here,
# versioned and immutable — a threshold change mints a new `version`,
# never edits one in place (§3's own "load-bearing, not a style
# preference" framing — blending pre/post-change outcome history under
# one identity would corrupt exactly the comparison Performance
# Intelligence (§5) exists to make).
class StrategyConfig(BaseModel):
    strategy_name: str  # family, e.g. "ORB"
    version: str  # "orb_v1" — immutable once minted
    params: dict  # e.g. {"or_minutes": 15, "target_r_multiple": 2.0}
    gate_conditions: dict = {}  # §2b — declarative environmental preconditions, evaluated by the
    # (not-yet-built) Scheduler before evaluate() is even called. Empty dict, not a required
    # field with no default — most v1 strategies don't need any yet, same as allows_waiting below.
    allows_waiting: bool = False  # §8 — v1 default; every planned v1 strategy acts immediately.
    active_from: datetime
    active_to: datetime | None = None
    rationale: str = ""


# --- Opportunity -----------------------------------------------------------
#
# §4. Deliberately NO `symbol` field — same convention `FeatureSet` and
# `MarketState`/`CrossSymbolState` already use (schemas/events/
# features.py, market_state.py): symbol lives on the EventEnvelope once
# this is published as `OpportunityCreated`, never duplicated onto the
# payload where it could disagree with the envelope's own value. The
# illustrative JSON in system-design.md §4.8 shows a `"symbol": "NVDA"`
# key for readability; the real locked shape in §4 (the Python class
# this implements verbatim) never lists one — this file follows §4, the
# more recent and more precise of the two.
class Opportunity(BaseModel):
    strategy: str
    version: str  # StrategyConfig.version this Opportunity was produced under
    direction: Literal["BUY", "SELL"]
    confidence: float
    structural_invalidation: float  # price at which the strategy's own thesis is falsified
    structural_target: float
    evidence: dict  # {"conditions": {...MATCH-stage values...}, "reason": "...", "basis": "live"|"closed"}
    status: Literal["potential", "waiting", "actionable", "expired"] = "actionable"
    wait_reason: str | None = None
    wait_expires_at: datetime | None = None
    setup_detected_at: datetime
    confirmed_at: datetime | None = None
    decided_at: datetime | None = None


# --- Strategy ----------------------------------------------------------
#
# system-design.md §4.8's ABC, with four assumptions the discarded
# momentum_strategy.py/vwap_strategy.py had to guess at now decided for
# real, since this file is that real base_strategy.py:
#
#   1. `__init__(self, config: StrategyConfig)` / `self.config` — yes.
#      evaluate() needs config-driven params (or_minutes, thresholds)
#      from somewhere; a per-instance config is the only sensible home.
#   2. `every_candle(timeframe=...)` takes a `timeframe` kwarg (see the
#      factory function's own docstring above) — the documented
#      no-args example was shorthand, not a literal no-parameters
#      signature.
#   3. `context: ContextChanged`, not the doc's illustrative `Context` —
#      `ContextChanged` (schemas/events/context.py, decision #92) is the
#      real, already-built schema; there is no separate `Context` type
#      anywhere in the codebase.
#   4. `evaluate()` takes an explicit `symbol: str` — the illustrative
#      3-arg signature (system-design.md §4.8) has no way for a
#      strategy to know which symbol it's being asked about at all.
#      That's fine for a stateless strategy (the discarded
#      momentum_strategy.py never referenced `symbol` once — every
#      value it needed already arrived scoped to one symbol via
#      market_state/features/context), but ORB genuinely needs
#      day-scoped per-symbol memory (the opening range itself) and
#      can't derive "which symbol's memory" from nothing. Every
#      comparable engine in this codebase (FeatureEngine,
#      MarketStateEngine, LevelInteractionEngine, ScannerRunner) is a
#      SINGLETON serving the whole symbol universe with internal state
#      keyed by symbol — not one instance per symbol — so `Strategy`
#      follows that same established shape rather than inventing a new
#      one, and `symbol` has to arrive as a real parameter for that
#      internal keying to be possible. Explicit parameter, not an
#      instance attribute captured at construction time, for the same
#      reason MarketClock is injected rather than read from global
#      state (§7): evaluate() stays a pure function of its inputs,
#      identically callable live or from a future Backtest Runner.
class Strategy(ABC):
    name: str
    trigger: ScheduleTrigger

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    @abstractmethod
    async def evaluate(
        self,
        symbol: str,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,
    ) -> Opportunity | None:
        """GATE → MATCH → SCORE → PROPOSE (§1). Returns None at any GATE
        or MATCH failure, an Opportunity once a setup is confirmed.

        Backtest-safety constraint, non-negotiable (§7): must derive
        "now" from `features.candle_ts` (or `market_state.candle_ts`),
        never `datetime.now()`. No `if backtesting:` branches — this
        method must run byte-identical live and in a future Backtest
        Runner."""
        raise NotImplementedError
