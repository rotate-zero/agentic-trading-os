"""
Connection control for pure market-data providers (Polygon.io, for now).
Separate from app/api/routes/broker.py on purpose: that file is scoped to
IBKR's specific "external Gateway app, 2FA, manual connect" model, which
doesn't apply here at all — Polygon is just an API-key-authenticated
cloud service, so it auto-connects on app startup if a key is configured
(see app/main.py's lifespan), rather than requiring a manual step.
These routes exist for status checks, adding/removing symbols beyond
whatever auto-subscribed at startup, and reconnecting without restarting
the whole server.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.broker_adapters.base import SymbolNotFoundError
from app.broker_adapters.polygon_provider import PolygonAdapter
from app.event_bus.bus import get_event_bus
from app.services import broker_registry
from app.services.tick_ingest import TickIngestBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.post("/connect")
async def connect() -> dict:
    existing = broker_registry.get_active_adapter()
    if existing is not None and existing.is_connected():
        return {"status": "already_connected"}

    try:
        provider = PolygonAdapter()
    except ValueError as exc:  # missing API key
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await provider.connect()
    bridge = TickIngestBridge(provider, get_event_bus())
    broker_registry.set_active(provider, bridge)
    return {"status": "connected", "note": "15-min-delayed free tier, polling-based — not real-time"}


@router.post("/subscribe")
async def subscribe(symbol: str) -> dict:
    provider = broker_registry.get_active_adapter()
    if provider is None or not provider.is_connected():
        raise HTTPException(status_code=400, detail="Not connected — call POST /market-data/connect first")
    try:
        await provider.subscribe([symbol])
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "subscribed", "symbol": symbol}


@router.post("/unsubscribe")
async def unsubscribe(symbol: str) -> dict:
    provider = broker_registry.get_active_adapter()
    if provider is None:
        raise HTTPException(status_code=400, detail="Not connected")
    await provider.unsubscribe([symbol])
    return {"status": "unsubscribed", "symbol": symbol}


@router.get("/status")
async def status() -> dict:
    provider = broker_registry.get_active_adapter()
    return {"connected": provider is not None and provider.is_connected()}


@router.post("/disconnect")
async def disconnect() -> dict:
    provider = broker_registry.get_active_adapter()
    if provider is not None:
        await provider.disconnect()
    broker_registry.clear_active()
    return {"status": "disconnected"}
