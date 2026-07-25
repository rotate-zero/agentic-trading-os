"""
Event envelope + the EventType vocabulary.
See docs/architecture/system-design.md §10.1 (envelope) and §4.4 (dispatch lanes).

The Pydantic models under schemas/events/ are the actual contract (§10.2) —
this module and the payload files are what code should import; the tables
in the docs are a readable mirror of this, not the other way around.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    # Market data
    PRICE_UPDATED = "PriceUpdated"
    CANDLE_CLOSED = "CandleClosed"
    FEATURES_UPDATED = "FeaturesUpdated"

    # State intelligence
    MARKET_STATE_CHANGED = "MarketStateChanged"
    CONTEXT_CHANGED = "ContextChanged"

    # Decision intelligence
    OPPORTUNITY_CREATED = "OpportunityCreated"
    OPPORTUNITY_SELECTED = "OpportunitySelected"
    TRADE_PLANNED = "TradePlanned"
    GOVERNOR_DECISION = "GovernorDecision"
    ORDER_APPROVED = "OrderApproved"
    PLAN_REJECTED = "PlanRejected"

    # Execution / positions
    ORDER_FILLED = "OrderFilled"
    POSITION_ADJUSTED = "PositionAdjusted"
    POSITION_CLOSED = "PositionClosed"

    # Dev-only — used to prove the Phase 2 round-trip (Event Bus -> WebSocket
    # Gateway -> client). Not part of the real trading event vocabulary in §10.3.
    DEV_PING = "DevPing"


# Execution-critical lane per docs/decisions/confirmed-decisions.md #9.
# Everything else rides the normal lane.
CRITICAL_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.ORDER_FILLED,
        EventType.PLAN_REJECTED,
        EventType.GOVERNOR_DECISION,
        EventType.ORDER_APPROVED,
    }
)


class EventEnvelope(BaseModel):
    """
    Every event crossing the Event Bus, regardless of type, is wrapped the
    same way (§10.1). `payload` is intentionally a plain dict here — the Bus
    itself stays generic and doesn't need to know every payload shape;
    individual schemas/events/*.py models are what publishers validate
    against before wrapping, and what subscribers can parse back out.
    """

    event_type: EventType
    version: int = 1
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_critical(self) -> bool:
        return self.event_type in CRITICAL_EVENT_TYPES
