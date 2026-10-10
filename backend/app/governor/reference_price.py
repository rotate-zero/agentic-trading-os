"""
ReferencePriceTracker — rule 5's "a reference price exists (the last
observed trade price for the symbol, from the same stream the venue fills
against)" (§6.2). A small, self-contained, in-memory last-trade-price
cache subscribing to `PriceUpdated`, following the exact same "each
engine keeps its own state cache" pattern MarketStateEngine/
LevelInteractionEngine/ContextEngine already use for their own live
reads — deliberately NOT reading another engine's snapshot for this,
since none of them expose "last trade price" today and PriceUpdated is
itself the ground-truth stream (system-design.md §10.3) every consumer,
including the eventual real venue, is meant to subscribe to independently.

No worker task, no queue, no I/O — the subscriber callback below is a
single in-memory dict write, exactly the "no background task needed"
shape OpportunityCache's own docstring documents for the same reason.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventEnvelope, EventType
from app.trade_planning.plan import ReferenceObservation

logger = logging.getLogger(__name__)


class ReferencePriceTracker:
    def __init__(self) -> None:
        self._last_price: dict[str, ReferenceObservation] = {}
        self._bus: EventBus | None = None

    def start(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.PRICE_UPDATED, self._on_price_updated)
        logger.info("ReferencePriceTracker started — subscribed to PriceUpdated")

    def stop(self) -> None:
        if self._bus is not None:
            self._bus.unsubscribe(EventType.PRICE_UPDATED, self._on_price_updated)
            self._bus = None
        self._last_price.clear()
        logger.info("ReferencePriceTracker stopped")

    def _on_price_updated(self, envelope: EventEnvelope) -> None:
        if self._bus is None or envelope.symbol is None:
            return
        price = envelope.payload.get("price")
        if price is None:
            return
        source_time = envelope.payload.get("exchange_ts")
        if isinstance(source_time, str):
            try:
                source_time = datetime.fromisoformat(source_time)
            except ValueError:
                source_time = None
        # Unavailable/invalid clock evidence is absent, never receive time.
        if not isinstance(source_time, datetime) or source_time.tzinfo is None or source_time.utcoffset() is None:
            source_time = None
        self._last_price[envelope.symbol] = ReferenceObservation(
            price=float(price), observed_at=envelope.timestamp, exchange_ts=source_time,
        )

    def get(self, symbol: str) -> float | None:
        """None = honest absence — no PriceUpdated for this symbol has
        been observed yet by this process. rules.py's rule 5 treats this
        as `no_reference_price`, never a guessed/zero-filled value."""
        observation = self.get_observation(symbol)
        return observation.price if observation is not None else None

    def get_observation(self, symbol: str) -> ReferenceObservation | None:
        """Last delivered price and its independent local/source clocks.

        Selection remains arrival-ordered, including late source ticks.
        """
        return self._last_price.get(symbol)
