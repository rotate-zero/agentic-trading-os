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
    EventType.CONTEXT_CHANGED: "intelligence.context",  # confirmed decision #126 — ContextEngine
    # already publishes this (decisions #92/#96); this is the missing routing entry
    # decision #125 found absent. Two envelope shapes reach this one channel, same
    # "one channel, distinguish by envelope.symbol" convention MarketStateChanged
    # already uses (decision #91): symbol unset = global/calendar (evaluate_all()),
    # symbol=<ticker> = per-symbol fundamentals/news (evaluate_for_symbol()).
    EventType.MARKET_STATE_CHANGED: "intelligence.market-state",  # same gap-shape as
    # #126, one engine later — MarketStateEngine already publishes this (decision
    # #91's per-symbol shape, #93/#97's build), this is the missing routing entry.
    # Two envelope shapes share this one channel (decision #91: "no new EventType
    # needed — envelope.symbol distinguishes the two shapes"), but unlike
    # ContextChanged the cross-symbol shape does NOT leave `symbol` unset — it's
    # always populated: envelope.symbol == "__MARKET__" (the real ticker for the
    # per-symbol shape, engine.py's own `_CROSS_SYMBOL_SENTINEL`). A subscriber
    # tells the two apart by comparing envelope.symbol to that literal sentinel,
    # never by checking for null/absent.
    EventType.OPPORTUNITY_CREATED: "opportunity.new",
    EventType.OPPORTUNITY_SELECTED: "opportunity.selected",
    EventType.ORDER_APPROVED: "orders.status",
    EventType.PLAN_REJECTED: "orders.status",
    EventType.ORDER_FILLED: "orders.status",
    EventType.GOVERNOR_DECISION: "orders.status",
    # Decision #174: TRADE_PLANNED/ORDER_STATUS_CHANGED were
    # missing purely because decision #171 landed after this table was last
    # edited (confirmed by direct read before this change, not assumed) —
    # not a deliberate exclusion. Both share "orders.status" with their four
    # siblings above rather than a separate channel: TradePlanned is a
    # pre-order "plan" (not yet an order), so a case for splitting it off
    # exists, but this channel is already a mixed-type stream distinguished
    # by envelope.event_type (approval vs rejection vs fill vs decision all
    # already share it), and no frontend consumer exists yet that would
    # benefit from planning events arriving separately from order events —
    # this delivery's own panel wants exactly the single chronological feed
    # a shared channel gives it for free. Splitting later costs one routing
    # line and one channel-string change in the hook; nothing else would
    # depend on the pairing. Judgment call, made and recorded here rather
    # than deferred silently (see this delivery's decision log entry).
    EventType.TRADE_PLANNED: "orders.status",
    EventType.ORDER_STATUS_CHANGED: "orders.status",
    # POSITION_CLOSED: forward-declared when this line was first written
    # (decision #170's enum member existed but neither a CRITICAL_EVENT_TYPES
    # entry nor a payload model did). Both now exist (decision #173,
    # `portfolio-state-engine`: PositionClosed payload model added to
    # execution.py, EventType.POSITION_CLOSED added to CRITICAL_EVENT_TYPES
    # in envelope.py) — confirmed by direct diff against this delivery's own
    # earlier pull, re-verified again here. Still effectively inert in a
    # running system, per that same decision's own words: "No production
    # implementation of this Protocol ships here" / "Production
    # PositionLedgerPort adapter / startup / outcome recovery: not wired" —
    # so nothing instantiates the worker that would publish it. This routing
    # line needed no change either way; only this comment did.
    EventType.POSITION_CLOSED: "orders.status",
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
