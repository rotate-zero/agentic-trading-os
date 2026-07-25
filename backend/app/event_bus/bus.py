"""
Event Bus — lightweight, in-process, typed pub/sub. Not a full
event-sourcing system, not a message broker. See
docs/architecture/system-design.md §4.4.

Two dispatch lanes, not a general priority system (confirmed decision #9):
execution-critical events (OrderFilled, PlanRejected, GovernorDecision,
OrderApproved) get their own queue + consumer task, isolated from the
normal-lane queue used by everything else (PriceUpdated, MarketStateChanged,
OpportunityCreated, ...). This means a burst of price ticks — or a slow
normal-lane subscriber — can never delay a risk/execution event, without
building a 5-tier priority framework nobody's demonstrated a need for yet.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from app.schemas.events.envelope import EventEnvelope, EventType

logger = logging.getLogger(__name__)

Handler = Callable[[EventEnvelope], Awaitable[None] | None]

# Subscribing with this key means "call me for every event type" — used by
# the WebSocket Gateway, which re-publishes a filtered subset to clients.
WILDCARD = "__all__"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType | str, list[Handler]] = defaultdict(list)
        self._critical_queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        self._normal_queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        self._consumer_tasks: list[asyncio.Task] = []
        self._started = False

    # --- subscription -----------------------------------------------------

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        """Subscribe to every event type, regardless of lane. See WILDCARD."""
        self._subscribers[WILDCARD].append(handler)

    # --- publish ------------------------------------------------------------

    async def publish(self, envelope: EventEnvelope) -> None:
        queue = self._critical_queue if envelope.is_critical else self._normal_queue
        await queue.put(envelope)

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._consumer_tasks = [
            asyncio.create_task(self._consume(self._critical_queue, lane="critical"), name="event-bus-critical"),
            asyncio.create_task(self._consume(self._normal_queue, lane="normal"), name="event-bus-normal"),
        ]
        logger.info("EventBus started: 2 dispatch lanes (critical, normal)")

    async def stop(self) -> None:
        for task in self._consumer_tasks:
            task.cancel()
        for task in self._consumer_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._consumer_tasks = []
        self._started = False
        logger.info("EventBus stopped")

    # --- internals ------------------------------------------------------

    async def _consume(self, queue: asyncio.Queue[EventEnvelope], *, lane: str) -> None:
        try:
            while True:
                envelope = await queue.get()
                handlers = list(self._subscribers.get(envelope.event_type, [])) + list(
                    self._subscribers.get(WILDCARD, [])
                )
                if handlers:
                    await asyncio.gather(*(self._safe_call(h, envelope, lane) for h in handlers))
                queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _safe_call(self, handler: Handler, envelope: EventEnvelope, lane: str) -> None:
        try:
            result = handler(envelope)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — one bad subscriber must not break the bus
            logger.exception(
                "Subscriber raised while handling %s on %s lane", envelope.event_type, lane
            )

    # --- observability (used by GET /health) --------------------------------

    def queue_depths(self) -> dict[str, int]:
        return {"critical": self._critical_queue.qsize(), "normal": self._normal_queue.qsize()}


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
