"""
Connection control for Polygon specifically. Separate from
app/api/routes/broker.py on purpose: that file is scoped to IBKR's
specific "external Gateway app, 2FA, manual connect" model, which doesn't
apply here — Polygon is just an API-key-authenticated cloud service, so
it auto-connects on app startup if a key is configured (see
app/main.py's lifespan).

Keeps its own module-level reference to the connected PolygonAdapter
(rather than only going through broker_registry) because Polygon can
fill one or both registry roles depending on whether Finnhub is also
connected (confirmed decision #33) — subscribe/unsubscribe/disconnect
need to operate on "the Polygon instance," which isn't always the same
as "whichever provider currently holds the streaming role."
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

_provider: PolygonAdapter | None = None


async def connect_polygon() -> PolygonAdapter:
    """
    Shared connect logic — used by both POST /market-data/connect and
    app/main.py's auto-connect-on-startup, so this module's _provider
    stays correct regardless of which one actually triggers the
    connection. Without this, main.py creating its own PolygonAdapter
    directly (as an earlier version of this file did) would auto-connect
    successfully while leaving every /market-data/* route operating on a
    still-None _provider — confirmed as a real bug via an actual startup
    test, not caught by unit tests alone since they call the route
    directly rather than going through main.py's lifespan.
    """
    global _provider
    if _provider is not None and _provider.is_connected():
        return _provider

    provider = PolygonAdapter()
    await provider.connect()
    _provider = provider
    broker_registry.set_historical_provider(provider)

    if broker_registry.get_streaming_provider() is None:
        bridge = TickIngestBridge(provider, get_event_bus())
        await broker_registry.take_over_streaming(provider, bridge)

    return provider


@router.post("/connect")
async def connect() -> dict:
    if _provider is not None and _provider.is_connected():
        return {"status": "already_connected"}

    try:
        await connect_polygon()
    except ValueError as exc:  # missing API key
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    note = (
        "connected as both historical and streaming-fallback (15-min delayed — no faster source configured)"
        if broker_registry.get_streaming_provider() is _provider
        else "connected as historical only — a faster streaming source is already active"
    )
    return {"status": "connected", "note": note}


@router.post("/subscribe")
async def subscribe(symbol: str) -> dict:
    if _provider is None or not _provider.is_connected():
        raise HTTPException(status_code=400, detail="Not connected — call POST /market-data/connect first")
    try:
        await _provider.subscribe([symbol])
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "subscribed", "symbol": symbol}


@router.post("/unsubscribe")
async def unsubscribe(symbol: str) -> dict:
    if _provider is None:
        raise HTTPException(status_code=400, detail="Not connected")
    await _provider.unsubscribe([symbol])
    return {"status": "unsubscribed", "symbol": symbol}


@router.get("/status")
async def status() -> dict:
    return {
        "connected": _provider is not None and _provider.is_connected(),
        "role": (
            "historical+streaming"
            if _provider is not None and broker_registry.get_streaming_provider() is _provider
            else "historical" if _provider is not None else None
        ),
    }


@router.post("/disconnect")
async def disconnect() -> dict:
    global _provider
    if _provider is not None:
        await _provider.disconnect()
        if broker_registry.get_historical_provider() is _provider:
            broker_registry.clear_historical_provider()
        if broker_registry.get_streaming_provider() is _provider:
            broker_registry.clear_streaming_provider()
    _provider = None
    return {"status": "disconnected"}
