"""
Typed helpers for constructing EventEnvelopes. Publishers should use these
(or the equivalent pattern for new event types) rather than building a raw
EventEnvelope by hand — this is what keeps "the Pydantic model in
schemas/events/ is the actual contract" (§10.2) true in practice, not just
in the docs.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.events.envelope import EventEnvelope, EventType


def make_envelope(
    event_type: EventType,
    payload: BaseModel,
    *,
    symbol: str | None = None,
    version: int = 1,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        version=version,
        symbol=symbol,
        payload=payload.model_dump(mode="json"),
    )
