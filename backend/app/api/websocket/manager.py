"""
Connection registry for the single multiplexed WebSocket connection.
See docs/architecture/system-design.md §4.12. Frontend subscribes/
unsubscribes to channels per symbol as widgets mount/unmount — this class
just tracks who's listening to what and fans out broadcasts.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._channel_subscribers: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("WebSocket connected (%d total)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        for subscribers in self._channel_subscribers.values():
            subscribers.discard(websocket)
        logger.info("WebSocket disconnected (%d total)", len(self._connections))

    def subscribe(self, websocket: WebSocket, channel: str) -> None:
        self._channel_subscribers[channel].add(websocket)

    def unsubscribe(self, websocket: WebSocket, channel: str) -> None:
        self._channel_subscribers[channel].discard(websocket)

    async def broadcast(self, channel: str, message: dict) -> None:
        subscribers = self._channel_subscribers.get(channel, set())
        if not subscribers:
            return
        envelope = {"channel": channel, **message}
        stale: list[WebSocket] = []
        for ws in subscribers:
            try:
                await ws.send_json(envelope)
            except Exception:  # noqa: BLE001 — a dead socket must not break the broadcast
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

    def subscriber_counts(self) -> dict[str, int]:
        return {channel: len(subs) for channel, subs in self._channel_subscribers.items()}


_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
