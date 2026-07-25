"""
Dev-only routes that exist solely to prove the Phase 2 exit criterion:
"a dummy event round-trips through the Event Bus." Not part of the
trading pipeline — safe to delete once Phase 3 adapters make this
redundant, but cheap to leave as a standing smoke test until then.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.event_bus.bus import get_event_bus
from app.event_bus.events import make_envelope
from app.schemas.events.dev import DevPing
from app.schemas.events.envelope import EventType
from app.schemas.events.execution import GovernorDecision

router = APIRouter(prefix="/dev", tags=["dev"])


class DummyEventRequest(BaseModel):
    message: str = "hello from the normal lane"


@router.post("/dummy-event")
async def publish_dummy_event(body: DummyEventRequest) -> dict:
    """Publishes a DevPing on the NORMAL lane. Watch it arrive on the
    `dev.ping` WebSocket channel."""
    bus = get_event_bus()
    envelope = make_envelope(EventType.DEV_PING, DevPing(message=body.message, lane="normal"))
    await bus.publish(envelope)
    return {"published": envelope.model_dump(mode="json")}


@router.post("/critical-event")
async def publish_critical_event() -> dict:
    """Publishes a GovernorDecision on the CRITICAL lane, to demonstrate
    lane isolation from market-data volume (confirmed decision #9)."""
    bus = get_event_bus()
    envelope = make_envelope(
        EventType.GOVERNOR_DECISION,
        GovernorDecision(action="rejected", reasons=["dev smoke test — critical lane"]),
    )
    await bus.publish(envelope)
    return {"published": envelope.model_dump(mode="json")}
