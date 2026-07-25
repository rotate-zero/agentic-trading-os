from __future__ import annotations

from fastapi import APIRouter

from app.core.market_clock import get_market_clock
from app.event_bus.bus import get_event_bus

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    clock = get_market_clock()
    bus = get_event_bus()
    return {
        "status": "ok",
        "market_session": clock.current_session().value,
        "market_open": clock.is_market_open(),
        "event_bus_queue_depths": bus.queue_depths(),
    }
