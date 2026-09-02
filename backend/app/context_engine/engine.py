"""
ContextEngine — thin aggregator over independent ContextProvider
instances (trading-intelligence-architecture.md §5, decision #90). Calls
every registered provider, merges their output into one ContextChanged
event keyed by provider name, publishes it. Adding a new context
dimension later means writing one new provider and registering it here —
not touching this aggregator (§5's whole point).

v1 trigger (decision #92): CalendarProvider is the only registered
provider until M0's Finnhub spike results unblock FundamentalsProvider/
NewsFlagProvider (M0-SPIKE-NOTES.md). Its own stated cadence is "changes
on session/day boundaries" (§5) — this loop follows that literally,
sleeping until MarketClock.next_session_boundary() and re-evaluating
there, rather than an arbitrary poll interval. This is a v1-scoped
choice, not a permanent architecture: §5 is explicit that providers
refresh at whatever cadence their own underlying reality changes at, so
once Fundamentals (weekly/daily batches) and News (rolling window) join,
each is more likely to want its own cadence-specific trigger than to be
forced onto the boundary loop that only ever suited Calendar. Revisit
this loop's shape at that point rather than assuming it as-is.

Cancellation note: unlike LevelInteractionEngine (decision #84), this
loop's only per-iteration work is an in-memory provider call plus one
bus.publish() — nothing here blocks on a to_thread DB write mid-
iteration, so a plain task.cancel() + await is sufficient; #84's
poison-pill drain doesn't apply.

Shutdown ordering — one departure from decision #47's precedent, worth
stating explicitly rather than copying that pattern by default:
LevelInteractionEngine et al. are FeaturesUpdated *subscribers*, so they
stop AFTER the bus — stopping the bus first bounds their drain to a
fixed backlog instead of an ever-refilling queue (#47). ContextEngine
subscribes to nothing; it only publishes. So it stops BEFORE the bus
instead, in main.py's shutdown — nothing depends on draining a backlog,
and stopping first avoids publishing into a bus already mid-shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.context_engine.provider import ContextProvider
from app.context_engine.providers.calendar import CalendarProvider
from app.core.market_clock import MarketClock, get_market_clock
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.schemas.events.context import ContextChanged
from app.schemas.events.envelope import EventType

logger = logging.getLogger(__name__)


class ContextEngine:
    def __init__(
        self,
        bus: EventBus,
        providers: list[ContextProvider] | None = None,
        clock: MarketClock | None = None,
    ) -> None:
        self._bus = bus
        self._providers = providers if providers is not None else [CalendarProvider()]
        self._clock = clock or get_market_clock()
        self._task: asyncio.Task | None = None

    async def evaluate_all(self) -> ContextChanged:
        """Call every registered provider, merge output by name, publish
        as one ContextChanged. `symbol` stays unset on the envelope —
        every v1 provider is market-wide, not symbol-specific (§10.1)."""
        results: dict[str, dict] = {}
        for provider in self._providers:
            results[provider.name] = await provider.evaluate()
        payload = ContextChanged(providers=results)
        await self._bus.publish(make_envelope(EventType.CONTEXT_CHANGED, payload))
        return payload

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="context-engine")
        logger.info(
            "ContextEngine started — providers=%s, session-boundary triggered",
            [p.name for p in self._providers],
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("ContextEngine stopped")

    async def _loop(self) -> None:
        # Fire once immediately so state isn't stale until the first
        # boundary, then re-evaluate at every session/day boundary
        # thereafter — see module docstring re: why boundary-driven, not
        # a poll interval.
        await self.evaluate_all()
        while True:
            boundary = self._clock.next_session_boundary()
            now = datetime.now(boundary.tzinfo)
            sleep_seconds = max((boundary - now).total_seconds(), 0)
            await asyncio.sleep(sleep_seconds)
            await self.evaluate_all()


_context_engine: ContextEngine | None = None


def get_context_engine(bus: EventBus | None = None) -> ContextEngine:
    """Lazy singleton, same pattern and reasoning as
    level_interaction_engine.get_level_interaction_engine()."""
    global _context_engine
    if _context_engine is None:
        _context_engine = ContextEngine(bus or get_event_bus())
    return _context_engine
