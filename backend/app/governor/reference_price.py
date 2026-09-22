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

from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventEnvelope, EventType

logger = logging.getLogger(__name__)


class ReferencePriceTracker:
    def __init__(self) -> None:
        self._last_price: dict[str, float] = {}

    def start(self, bus: EventBus) -> None:
        bus.subscribe(EventType.PRICE_UPDATED, self._on_price_updated)
        logger.info("ReferencePriceTracker started — subscribed to PriceUpdated")

    def stop(self) -> None:
        """No background task, no queue — genuinely a no-op, present for
        interface consistency with every other component's start()/stop()
        pair (same reasoning OpportunityCache.stop() documents)."""
        logger.info("ReferencePriceTracker stopped")

    def _on_price_updated(self, envelope: EventEnvelope) -> None:
        if envelope.symbol is None:
            return
        price = envelope.payload.get("price")
        if price is None:
            return
        self._last_price[envelope.symbol] = float(price)

    def get(self, symbol: str) -> float | None:
        """None = honest absence — no PriceUpdated for this symbol has
        been observed yet by this process. rules.py's rule 5 treats this
        as `no_reference_price`, never a guessed/zero-filled value."""
        return self._last_price.get(symbol)
