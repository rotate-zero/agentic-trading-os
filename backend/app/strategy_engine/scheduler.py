"""
Strategy Scheduler — Stage 2 wiring (strategy-engine-design.md §10 D10,
decisions #112/#114). The first thing in this codebase that actually calls
Strategy.evaluate() against live data — base_strategy.py's own "NOT BUILT
HERE" note on this exact module is what this file resolves.

**Locked scope (D10, decision #112).** Wiring only:
  - instantiate the 7 built v1 strategies against their `default_config()`,
  - read each one's `ScheduleTrigger` and subscribe to the matching live
    event,
  - call `evaluate()` with real `MarketState`/`ContextChanged`/`FeatureSet`,
  - publish whatever comes back as `OpportunityCreated`.

Explicitly OUT of scope, decided directly by Saqib and not silently
resolved here:
  - **`active_from`/`active_to`** — D14/decision #116, canonically closed:
    `_default_registry()` passes "now, at Scheduler construction time" as
    an honest default with zero behavioral effect today, and this module
    must not compare `candle_ts` against either field. Do not reopen this
    just because `gate_conditions` (below) is now enforced in the same
    handler — they are separate items, closed on separate terms.
  - **Opportunity Engine ranking (§9)** — every `Opportunity` any strategy
    returns gets published, independently, with no dedup or ranking.

**`gate_conditions` (§2b) — NOW enforced, decision #117.** D10 originally
deferred this alongside `active_from`/`active_to`; this build closes it
on its own, `active_from`/`active_to` remains separately deferred (see
above). `app/strategy_engine/gate_conditions.py` holds the actual
registry/check/validation — see that module's own docstring for the
full v1-scope and extensibility reasoning, AND for the architectural
rule decision #118 made explicit: `gate_conditions.py` and this class
are the SOLE authority for interpreting `gate_conditions` — no
strategy may independently interpret the dict or invent gate semantics
of its own. Two things worth stating here rather than only there:

  1. **Every registered strategy's `gate_conditions` is validated once,
     at `StrategyScheduler.__init__` time, before anything else** —
     an unrecognized key or value raises `ValueError` and the Scheduler
     fails to construct. This deliberately differs from the
     `after_time`/`on_event` precedent just below ("accepted into the
     registry but logged as unreachable") — see
     `gate_conditions.py`'s own docstring for why an unrecognized gate
     condition is a materially worse failure mode than an unwired
     trigger kind, not the same one.
  2. **This closes a real, currently-live gap for 3 of the 7 strategies,
     not merely a redundancy for the other 4.** Checked directly against
     every strategy file, not assumed: `gap_strategy.py`,
     `momentum_strategy.py`, and `volume_spike_strategy.py` each already
     call `MarketClock.is_regular_session()` inline in their own GATE
     step (genuinely redundant with the new central check now — left in
     place FOR NOW; tracked for removal, §10 D16, not bundled into this
     change); `orb_strategy.py` never calls it directly but is
     effectively self-gating anyway via `minutes_since_open()` returning
     0 whenever the market isn't open. But `first_pullback_strategy.py`,
     `reversal_strategy.py`, and `vwap_strategy.py` all declare
     `gate_conditions={"session": "regular"}` and have **no session
     check anywhere in their own `evaluate()`** — before this change,
     that declared precondition was enforced by nothing at all. The
     central check below is these three strategies' only session gate,
     not a second layer on top of an existing one.
  3. **Sole enforcement authority, decision #118.** `gate_conditions.py`
     and this class are the ONLY code allowed to interpret
     `gate_conditions` — a `Strategy` subclass may declare it on its own
     `StrategyConfig`, but must never independently interpret the dict
     or enforce a gate itself. The 4 strategies' own inline
     `is_regular_session()` calls above are a pre-existing, tolerated
     exception (they predate this rule), not a template — tracked for
     removal in §10 D16 precisely so "session" has exactly one place it
     can ever be interpreted. No strategy built after this point should
     add a second one.

**Why the live trigger is `MarketStateChanged`, not `FeaturesUpdated` —
found by testing, not assumed up front.** The first version of this
module subscribed directly to `FeaturesUpdated`, on the reasoning that
"every_candle" means "react to the event that signals a candle closed."
A real-engine integration test caught why that's wrong:
`MarketStateEngine` doesn't compute synchronously inside its own
`FeaturesUpdated` handler — that handler only enqueues a debounced
recompute (`core/debounce_scheduler.py`), and the actual compute +
`asyncio.to_thread` persist + cache happens later, in a separate
`_worker_loop` task. A Scheduler reacting to the SAME raw `FeaturesUpdated`
event has no ordering guarantee that MarketStateEngine's cache is
populated yet — in practice it almost never is, since the worker task
needs its own turn on the event loop plus a real DB round-trip.
`MarketStateEngine._worker_loop` already documents the fix inline: "Cache
before publish (decision #98) — a subscriber reacting to the event that's
about to go out can immediately call get_snapshot() and see this exact
state, never a stale prior value racing the event." That guarantee is
attached to `MarketStateChanged`, not `FeaturesUpdated` — so this module
subscribes to the former. `_on_market_state_changed` below reads
`market_state` directly from that event's own payload (no `get_snapshot()`
round-trip needed for it at all).

**Why `FeatureSet` is cached from `FeaturesUpdated` directly, not
reconstructed from `FeatureEngine.get_snapshot()`.** `MarketStateChanged`'s
payload is only a `MarketState` — it doesn't carry the `FeatureSet` that
produced it, so `evaluate()`'s `features` argument has to come from
somewhere else. `FeatureEngine.get_snapshot()` (decision #47) looked like
the obvious source, but its shape only exposes `candle_ts`/`close`/
`features`/`daily_levels` — it deliberately omits `open`/`high`/`low`/
`volume` (those were added later, decision #99, specifically so ORB could
compute a real opening-range wick high/low; `get_snapshot()`'s shape
predates that and was never extended to carry them). Reconstructing a
`FeatureSet` from `get_snapshot()` would silently hand every strategy
`open=None, high=None, low=None, volume=None` even when real values exist
— exactly the "silently understates the range" failure mode decision #99's
own schema comment warns about, just introduced one layer further down by
this module instead. `_on_features_updated` below only caches the REAL,
complete `FeaturesUpdated` payload (keyed by `(symbol, timeframe)`, only
for timeframes some registered strategy actually watches) — no lossy
round-trip. `_on_market_state_changed` reads that cache when it fires.

Ordering is safe: `_on_features_updated`'s cache write is a plain dict
assignment with no `await` before it, so it completes essentially
immediately within the same `FeaturesUpdated` dispatch that also kicks off
MarketStateEngine's debounced recompute — long before that recompute's
worker task, `asyncio.to_thread` persist, and eventual `MarketStateChanged`
publish can possibly happen. By the time `_on_market_state_changed` fires
for a given candle, the matching `FeatureSet` is already cached.

**Cross-symbol synthesis is explicitly excluded.** `MarketStateEngine`
also publishes a synthesized SPY/QQQ/IWM composite under the sentinel
symbol `"__MARKET__"` (`market_state_engine/engine.py`'s
`_CROSS_SYMBOL_SENTINEL`, not exported — duplicated here as a literal
rather than importing a private name across modules; promoting it to a
shared public constant would be a trivial follow-up if this ever drifts).
That payload is a `CrossSymbolState`, not a `MarketState`, and isn't a
real per-symbol strategy trigger — `_on_market_state_changed` skips it by
symbol before attempting to parse anything.

**Debounce and "every_candle" semantics.** `MarketStateEngine`'s debounce
(`min_interval=1.0s`) only throttles bursts; at the normal one-candle-per-
minute cadence it never coalesces two real candles into one
`MarketStateChanged`, so triggering off it still means "once per closed
1m candle" in the ordinary case. The rare case where a burst DOES cause
two `FeaturesUpdated` to collapse into one recompute is handled honestly,
not silently: that candle simply doesn't get its own strategy pass,
consistent with reacting to what MarketStateEngine actually computed
rather than to a schedule this module invents independently of it.

**Concurrency model: sequential, inline in the MarketStateChanged handler
— no queue, no background worker task.** Every v1 strategy's `evaluate()`
is pure computation over already-computed inputs, so decision #84's
`asyncio.to_thread()` cancellation hazard doesn't apply here. `stop()` is
correspondingly trivial — kept as `async def` only for interface
consistency with every other engine's `start()`/`stop()` pair.

**Exception isolation, and why it can't rely on `EventBus._safe_call`
alone.** The bus already catches whatever a subscriber's handler raises
(`_safe_call`, `event_bus/bus.py`) — but that protects OTHER subscribers
from a Scheduler-wide failure, not one strategy's failure from cutting off
every strategy after it in this handler's own loop.
`_on_market_state_changed` wraps each `evaluate()` call in its own
try/except for exactly that reason.

**`after_time`/`on_event` triggers: registered, but no live dispatch path
exists for either yet.** No v1 strategy declares one (all 7 use
`every_candle("1m")`). A strategy declaring either kind is accepted into
the registry but logged as a loud warning at construction time — it will
never actually be called — rather than silently pretending support that
isn't there.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.context_engine.engine import get_context_engine
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.schemas.events.context import ContextChanged
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine import (
    first_pullback_strategy,
    gap_strategy,
    momentum_strategy,
    orb_strategy,
    reversal_strategy,
    volume_spike_strategy,
    vwap_strategy,
)
from app.strategy_engine.base_strategy import Strategy
from app.strategy_engine.gate_conditions import gate_conditions_satisfied, validate_gate_conditions

logger = logging.getLogger(__name__)

# market_state_engine/engine.py's _CROSS_SYMBOL_SENTINEL — see module
# docstring's "Cross-symbol synthesis is explicitly excluded" section for
# why this is a duplicated literal rather than a cross-module import.
_CROSS_SYMBOL_SENTINEL = "__MARKET__"


def _default_registry(active_from: datetime) -> list[Strategy]:
    """The 7 v1 strategies (trading-intelligence-architecture.md §8),
    each built from its own module's `default_config()`. Declaration
    order here is deliberate and fixed: `_on_market_state_changed` calls
    `evaluate()` in registry order, so this order is also the order two
    strategies that both fire on the same candle get published in — no
    ordering SEMANTICS implied (Opportunity Engine's ranking, §9, is what
    will eventually decide precedence), just reproducibility."""
    return [
        orb_strategy.ORBStrategy(orb_strategy.default_config(active_from)),
        gap_strategy.GapStrategy(gap_strategy.default_config(active_from)),
        volume_spike_strategy.VolumeSpikeStrategy(volume_spike_strategy.default_config(active_from)),
        first_pullback_strategy.FirstPullbackStrategy(first_pullback_strategy.default_config(active_from)),
        reversal_strategy.ReversalStrategy(reversal_strategy.default_config(active_from)),
        momentum_strategy.MomentumStrategy(momentum_strategy.default_config(active_from)),
        vwap_strategy.VWAPStrategy(vwap_strategy.default_config(active_from)),
    ]


class StrategyScheduler:
    """See module docstring for the full design reasoning. Constructed
    with an explicit `strategies` list in tests; `get_strategy_scheduler()`
    below is what `main.py` actually calls, building the real 7-strategy
    registry."""

    def __init__(self, bus: EventBus, strategies: list[Strategy] | None = None) -> None:
        self._bus = bus
        self._strategies = (
            strategies if strategies is not None else _default_registry(datetime.now(timezone.utc))
        )

        # gate_conditions validation (decision #117) — fails loudly,
        # before anything else, for any strategy declaring a
        # precondition this build can't actually check. See
        # gate_conditions.py's own docstring, and this module's
        # docstring's "gate_conditions — NOW enforced" section, for why
        # this is a hard construction-time failure rather than a
        # logged-and-continue warning (deliberately NOT the same
        # treatment as the unwired-trigger-kind case just below).
        for strategy in self._strategies:
            validate_gate_conditions(strategy.name, strategy.config.gate_conditions)

        self._every_candle_by_timeframe: dict[str, list[Strategy]] = defaultdict(list)
        for strategy in self._strategies:
            trigger = strategy.trigger
            if trigger.kind == "every_candle":
                self._every_candle_by_timeframe[trigger.timeframe].append(strategy)
            else:
                logger.warning(
                    "StrategyScheduler: %s declares trigger.kind=%r — no live dispatch path "
                    "exists for this trigger kind yet (only every_candle is wired). This "
                    "strategy is registered but will never actually be called until that's "
                    "built (see module docstring's 'after_time/on_event' section).",
                    strategy.name,
                    trigger.kind,
                )

        # (symbol, timeframe) -> most recent FeatureSet seen on
        # FeaturesUpdated, for timeframes some registered strategy
        # actually watches. See module docstring for why this is cached
        # rather than reconstructed from FeatureEngine.get_snapshot().
        self._latest_features: dict[tuple[str, str], FeatureSet] = {}

    def start(self) -> None:
        self._bus.subscribe(EventType.FEATURES_UPDATED, self._on_features_updated)
        self._bus.subscribe(EventType.MARKET_STATE_CHANGED, self._on_market_state_changed)
        logger.info(
            "StrategyScheduler started — %d strategies registered (%s)",
            len(self._strategies),
            ", ".join(s.name for s in self._strategies),
        )

    async def stop(self) -> None:
        """No background task, no queue — see module docstring's
        concurrency section for why there's nothing to drain here."""
        logger.info("StrategyScheduler stopped")

    # --- Event Bus subscribers -------------------------------------------

    async def _on_features_updated(self, envelope: EventEnvelope) -> None:
        """Caches the real FeaturesUpdated payload — does not evaluate
        any strategy. See module docstring: MarketStateChanged (below) is
        the actual trigger, so evaluate() always sees an already-fresh
        MarketState; this handler's only job is making sure the matching
        FeatureSet is available, complete, when that fires."""
        symbol = envelope.symbol
        if symbol is None:
            return
        timeframe = envelope.payload.get("timeframe")
        if timeframe not in self._every_candle_by_timeframe:
            return  # no registered strategy watches this timeframe — don't bother caching
        self._latest_features[(symbol, timeframe)] = FeatureSet.model_validate(envelope.payload)

    async def _on_market_state_changed(self, envelope: EventEnvelope) -> None:
        symbol = envelope.symbol
        if symbol is None or symbol == _CROSS_SYMBOL_SENTINEL:
            return  # cross-symbol synthetic composite — not a real per-symbol trigger

        market_state = MarketState.model_validate(envelope.payload)
        timeframe = market_state.timeframe
        strategies = self._every_candle_by_timeframe.get(timeframe)
        if not strategies:
            return  # no registered strategy watches this timeframe

        features = self._latest_features.get((symbol, timeframe))
        context = self._read_context(symbol)
        if features is None or context is None:
            # Honest absence, not a fabricated call. `features` absent
            # means no FeaturesUpdated for this exact (symbol, timeframe)
            # has been seen yet (shouldn't happen in steady state, since
            # MarketStateChanged is itself derived from one — but a
            # process that starts mid-session with a warm MarketState
            # cache and a cold FeatureSet cache is a real, if narrow,
            # startup-ordering case). `context` absent means
            # ContextEngine hasn't evaluated this symbol yet — see
            # `_read_context`'s own docstring.
            logger.debug(
                "StrategyScheduler skipping %s @ %s — %s not available",
                symbol,
                timeframe,
                "features" if features is None else "context",
            )
            return

        for strategy in strategies:
            # gate_conditions (§2b, decision #117) — evaluated centrally,
            # BEFORE evaluate() is even called, per §2b's own text. Own
            # try/except, isolated from evaluate()'s below, so a bug in
            # the gate check itself can't cut off every strategy after
            # it in this loop any more than a bug inside evaluate() can
            # (same reasoning as the existing isolation just below).
            try:
                if not gate_conditions_satisfied(strategy.config.gate_conditions, market_state.candle_ts):
                    logger.debug(
                        "StrategyScheduler skipping %s for %s @ %s — gate_conditions %r not "
                        "satisfied for candle_ts=%s (honest gate skip, not an error)",
                        strategy.name,
                        symbol,
                        timeframe,
                        strategy.config.gate_conditions,
                        market_state.candle_ts,
                    )
                    continue
            except Exception:  # noqa: BLE001 — a gate-check bug must not stop the rest either
                logger.exception(
                    "StrategyScheduler: gate_conditions check raised for %s on %s — skipped, "
                    "other strategies this candle are unaffected",
                    strategy.name,
                    symbol,
                )
                continue

            try:
                opportunity = await strategy.evaluate(symbol, market_state, features, context)
            except Exception:  # noqa: BLE001 — one bad strategy must not stop the rest
                logger.exception(
                    "StrategyScheduler: %s raised during evaluate() for %s — skipped, "
                    "other strategies this candle are unaffected",
                    strategy.name,
                    symbol,
                )
                continue

            if opportunity is not None:
                await self._bus.publish(
                    make_envelope(EventType.OPPORTUNITY_CREATED, opportunity, symbol=symbol)
                )
                logger.info(
                    "OpportunityCreated: %s %s %s confidence=%.2f",
                    strategy.name,
                    symbol,
                    opportunity.direction,
                    opportunity.confidence,
                )

    # --- ContextChanged reconstruction (see module docstring) -----------

    @staticmethod
    def _read_context(symbol: str) -> ContextChanged | None:
        snapshot: dict[str, Any] = get_context_engine().get_snapshot(symbol)
        raw = snapshot["symbols"].get(symbol)
        if raw is None:
            # Absent whenever ContextEngine hasn't run evaluate_for_symbol()
            # for this symbol yet (its own get_snapshot() docstring) — true
            # even if global-only context (e.g. CalendarProvider) already
            # exists, since "symbols" is keyed by per-symbol evaluation
            # having happened at least once. Inherited limitation from
            # ContextEngine's existing design (decisions #92/#96), not
            # introduced here.
            return None
        return ContextChanged.model_validate({"providers": raw["providers"]})


_strategy_scheduler: StrategyScheduler | None = None


def get_strategy_scheduler(bus: EventBus | None = None) -> StrategyScheduler:
    global _strategy_scheduler
    if _strategy_scheduler is None:
        _strategy_scheduler = StrategyScheduler(bus if bus is not None else get_event_bus())
    return _strategy_scheduler
