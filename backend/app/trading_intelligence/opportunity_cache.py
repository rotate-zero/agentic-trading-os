"""
OpportunityCache — the Stage 2 (decision #112/D10) read-side for
OpportunityCreated. Passive cache ONLY: subscribes to
EventType.OPPORTUNITY_CREATED (schemas/events/envelope.py — the event
type itself already existed before this file; see decision #114 for
why), keeps the latest Opportunity per (symbol, strategy) pair in
memory, and exposes get_snapshot() in the same shape/honesty convention
MarketStateEngine.get_snapshot()/ContextEngine.get_snapshot() already
established (decision #98).

Deliberately named opportunity_cache.py, NOT opportunity_engine.py —
that name is reserved for the real Opportunity Engine (strategy-engine-
design.md §9, cross-strategy ranking across every live Opportunity),
which is explicitly OUT of scope for Stage 2 (decision #112/D10). This
module does no ranking, no filtering, no cross-strategy reasoning of any
kind — it answers exactly one question, "what's the most recent thing
each strategy said about each symbol," nothing more.

Built against the CONTRACT, not an implementation: as of this file, no
Scheduler exists anywhere in this codebase that actually calls a
Strategy's evaluate() and publishes OpportunityCreated — that's Track
A's parallel-session work (decision #112/D10's own scope), not yet
merged when this file was written. This cache only depends on two
things that already exist independent of the Scheduler:
EventType.OPPORTUNITY_CREATED (schemas/events/envelope.py) and
Opportunity's own shape (strategy_engine/base_strategy.py). Nothing
here imports from, or assumes anything about, a specific Scheduler
implementation.

Payload shape — an assumption, stated explicitly rather than silently
relied on: this cache expects envelope.payload to be an
Opportunity.model_dump(mode="json") dict, the same make_envelope()
convention (event_bus/events.py) every other event in this codebase
already follows for its own payload. Deliberately NOT re-validated with
Opportunity.model_validate() on the way in — mirrors the exact trust
boundary MarketStateEngine._on_features_updated and
LevelInteractionEngine's own queue ingestion already extend to their
own upstream publisher (both read raw envelope.payload dict keys
directly, no round-trip pydantic re-parse): this cache isn't
Opportunity's schema owner, base_strategy.py is, and introducing a
second validation point here that no other subscriber in this codebase
has would be new, unproven ceremony, not a real safety net — a
malformed payload surfaces immediately as a missing/wrong-shaped value
the first time anything reads it back out of get_snapshot(), the same
failure visibility every other subscriber's raw-dict trust already has.

Retention decision (Stage 2 delivery, decision #114) — the LAST
Opportunity per (symbol, strategy) pair, not a rolling list of the last
K per symbol. Nothing downstream reads this cache yet (Opportunity
Engine's ranking logic, §9, doesn't exist), so there's no real consumer
to justify picking any particular K — a rolling list would be an
arbitrary number invented with no evidence to check it against. One
current value per (symbol, strategy) also matches the shape every
existing engine's own get_snapshot() already uses for "what does this
process currently believe" (MarketState is one current score per
symbol, not a history of scores; Context is one current provider-map
per symbol) — this cache's honest current-belief answer, per strategy,
follows the same established shape rather than inventing a new one.
Revisit only once the Opportunity Engine (§9) actually needs more than
"the latest," not speculatively.

No worker task, no queue, no DB (Stage 2/D10 scope explicitly excludes
persistence for this cache — that's Performance Intelligence's
strategy_outcomes table, decision #89, a completely separate concern).
The subscriber callback below does a single in-memory dict write and
nothing else — no I/O, no `asyncio.to_thread` — so, unlike
MarketStateEngine/LevelInteractionEngine (decision #84's poison-pill
worker, needed because THEIR callbacks do blocking DB writes), there is
nothing here that could outlive a plain, synchronous handler. start()/
stop() still exist, for interface consistency with every other engine
main.py's lifespan already manages (decision #47's engine-lifecycle
pattern) — stop() is a genuine no-op today, documented as such rather
than pretending there's a drain to perform.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.event_bus.bus import EventBus, get_event_bus
from app.schemas.events.envelope import EventEnvelope, EventType

logger = logging.getLogger(__name__)


class OpportunityCache:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        # (symbol, strategy_name) -> the exact envelope.payload dict last
        # received for that pair (an Opportunity.model_dump(mode="json")
        # shape, per this module's own docstring) — mutated only from
        # inside _on_opportunity_created, called synchronously by the
        # EventBus's own dispatch loop (event_bus/bus.py's
        # _consume/_safe_call), never concurrently with itself (asyncio,
        # single-threaded event loop) — no lock needed, same
        # single-writer discipline every other engine in this codebase
        # already follows for its own in-memory caches
        # (MarketStateEngine._latest_market_state, ContextEngine's
        # _latest_by_symbol).
        self._latest: dict[tuple[str, str], dict[str, Any]] = {}
        self._received_at: dict[tuple[str, str], datetime] = {}

    def start(self) -> None:
        self._bus.subscribe(EventType.OPPORTUNITY_CREATED, self._on_opportunity_created)
        logger.info("OpportunityCache started — subscribed to OpportunityCreated")

    async def stop(self) -> None:
        """No background task, no queue, nothing to drain (see module
        docstring) — genuinely a no-op. Async, and present at all, so
        main.py's lifespan can `await opportunity_cache.stop()` the
        same way it awaits every other engine's stop(), without a
        special case for this one."""
        logger.info("OpportunityCache stopped")

    # --- Event Bus subscriber -----------------------------------------------
    #
    # Deliberately a plain, synchronous function, not `async def` — there
    # is no `await` anywhere in this body (see module docstring: no
    # I/O). event_bus/bus.py's Handler type accepts either; EventBus.
    # _safe_call only awaits the result if it's actually a coroutine.
    def _on_opportunity_created(self, envelope: EventEnvelope) -> None:
        symbol = envelope.symbol
        if symbol is None:
            # Opportunity itself carries no `symbol` field by design
            # (base_strategy.py's own docstring: "lives on the
            # EventEnvelope once published, never duplicated onto the
            # payload") — an OpportunityCreated with no envelope symbol
            # is malformed at the publisher, not something this cache
            # can recover a symbol for. Dropped, logged, never guessed.
            logger.warning("OpportunityCreated received with no envelope.symbol — dropped")
            return

        strategy_name = envelope.payload.get("strategy")
        if not strategy_name:
            logger.warning(
                "OpportunityCreated for %s received with no 'strategy' key in payload — dropped",
                symbol,
            )
            return

        key = (symbol, strategy_name)
        self._latest[key] = envelope.payload
        self._received_at[key] = datetime.now(timezone.utc)

    # --- read-side snapshot ---------------------------------------------------

    def get_snapshot(self, symbol: str | None = None) -> dict[str, Any]:
        """
        Current cached Opportunities, synchronous, in-memory — no I/O.
        Same motivation and shape-convention as
        MarketStateEngine.get_snapshot()/ContextEngine.get_snapshot()
        (decision #98): a consumer gets a point-in-time read without
        subscribing to OpportunityCreated and waiting for the next
        publish.

        Shape: {"symbols": {ticker: {strategy_name: {...Opportunity
        fields (strategy_engine/base_strategy.py's Opportunity model,
        exactly as published), "received_at": iso str}}}}.

        - `received_at` is this process's own wall-clock read of when it
          received the event — deliberately separate from Opportunity's
          own `setup_detected_at`/`confirmed_at`/`decided_at` domain
          timestamps, same "evaluated_at is wall-clock, not a domain
          timestamp" distinction ContextEngine.get_snapshot() already
          draws for its own field of the same purpose.
        - symbol=None: every (symbol, strategy) pair this process has
          received at least one OpportunityCreated for.
        - symbol="<ticker>": "symbols" has at most one entry, keyed by
          that ticker, holding every strategy that's fired for it so
          far.
        - A (symbol, strategy) pair this process has never received an
          OpportunityCreated for is simply absent — honest state over
          fabricated state, same convention every other engine's
          get_snapshot() in this codebase already follows. This is NOT
          pre-populated for the configured symbol universe or the set
          of registered strategies; only what's real right now.
        """
        symbols: dict[str, Any] = {}
        for (sym, strategy_name), payload in self._latest.items():
            if symbol is not None and sym != symbol:
                continue
            entry = dict(payload)
            entry["received_at"] = self._received_at[(sym, strategy_name)].isoformat()
            symbols.setdefault(sym, {})[strategy_name] = entry
        return {"symbols": symbols}


_opportunity_cache: OpportunityCache | None = None


def get_opportunity_cache(bus: EventBus | None = None) -> OpportunityCache:
    """Lazy singleton, same pattern as get_level_interaction_engine()/
    get_market_state_engine()/get_context_engine()."""
    global _opportunity_cache
    if _opportunity_cache is None:
        _opportunity_cache = OpportunityCache(bus or get_event_bus())
    return _opportunity_cache
