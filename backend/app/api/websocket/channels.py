"""
WebSocket Gateway — a thin re-publisher sitting on top of the Event Bus,
not a separate source of truth (§4.4, §4.12). It's just one more Event
Bus subscriber; if the UI disappeared entirely, the backend pipeline
would still function identically.

Topic-tagged envelopes, e.g.:
  {"channel": "market.tick", "symbol": "NVDA", "payload": {...}}
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.websocket.manager import ConnectionManager, get_connection_manager
from app.event_bus.bus import EventBus, get_event_bus
from app.schemas.events.envelope import EventEnvelope, EventType

logger = logging.getLogger(__name__)

router = APIRouter()

# Event type -> outbound channel name. Mirrors the examples in §4.12.
# Event types not listed here simply aren't re-published to the frontend
# yet — that's a deliberate, additive mapping, not a limitation to work
# around.
EVENT_TO_CHANNEL: dict[EventType, str] = {
    EventType.PRICE_UPDATED: "market.tick",
    EventType.CANDLE_CLOSED: "market.candle",
    EventType.PRICE_SNAPSHOT: "market.tick.snapshot",  # decision #72 — deliberately its own channel,
    # NOT reused on "market.tick": useLatestPrices (the watchlist) already listens there at raw tick
    # frequency, and collapsing the two would silently throttle the watchlist down to 5s too.
    EventType.FEATURES_UPDATED: "features.updated",  # confirmed decision #47
    EventType.LEVEL_INTERACTION_CHANGED: "intelligence.level",  # confirmed decision #47
    EventType.OPPORTUNITY_CREATED: "opportunity.new",
    EventType.OPPORTUNITY_SELECTED: "opportunity.selected",
    EventType.ORDER_APPROVED: "orders.status",
    EventType.PLAN_REJECTED: "orders.status",
    EventType.ORDER_FILLED: "orders.status",
    EventType.GOVERNOR_DECISION: "orders.status",
    EventType.DEV_PING: "dev.ping",
}


class WebSocketGateway:
    """Subscribes to the Event Bus on startup; republishes to WS clients."""

    def __init__(self, bus: EventBus, manager: ConnectionManager) -> None:
        self._bus = bus
        self._manager = manager

    def attach(self) -> None:
        self._bus.subscribe_all(self._on_event)

    async def _on_event(self, envelope: EventEnvelope) -> None:
        channel = EVENT_TO_CHANNEL.get(envelope.event_type)
        if channel is None:
            return  # not (yet) re-published to the frontend
        await self._manager.broadcast(
            channel,
            {
                "symbol": envelope.symbol,
                "event_type": envelope.event_type.value,
                "payload": envelope.payload,
                "timestamp": envelope.timestamp.isoformat(),
            },
        )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    manager = get_connection_manager()
    await manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive_json()
            action = message.get("action")
            channel = message.get("channel")
            if action == "subscribe" and channel:
                manager.subscribe(websocket, channel)
                await websocket.send_json({"channel": "_meta", "subscribed": channel})
            elif action == "unsubscribe" and channel:
                manager.unsubscribe(websocket, channel)
                await websocket.send_json({"channel": "_meta", "unsubscribed": channel})
            else:
                await websocket.send_json({"channel": "_meta", "error": "expected {action, channel}"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


_gateway: WebSocketGateway | None = None


def get_gateway() -> WebSocketGateway:
    global _gateway
    if _gateway is None:
        _gateway = WebSocketGateway(get_event_bus(), get_connection_manager())
    return _gateway
