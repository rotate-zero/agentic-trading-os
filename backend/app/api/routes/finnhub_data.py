"""
Connection control for Finnhub specifically. Same auto-connect-on-startup
pattern as app/api/routes/market_data.py (Polygon) — Finnhub is also just
an API-key-authenticated cloud service, no manual-connect requirement
like IBKR has.

Only ever registers as the STREAMING provider — never historical.
Finnhub's free tier can't serve historical stock candles at all
(HistoricalDataUnavailableError, confirmed decision #32), so registering
it as historical would just mean GET /market/candles fails the moment
Finnhub happens to be connected. Polygon (app/api/routes/market_data.py)
is the historical provider; the two are complementary, not
interchangeable — see confirmed decision #33.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.broker_adapters.base import SymbolNotFoundError
from app.broker_adapters.finnhub_provider import FinnhubAdapter
from app.event_bus.bus import get_event_bus
from app.services import broker_registry
from app.services.tick_ingest import TickIngestBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/finnhub", tags=["finnhub"])

_provider: FinnhubAdapter | None = None


async def connect_finnhub() -> FinnhubAdapter:
    """
    Shared connect logic — used by both POST /finnhub/connect and
    app/main.py's auto-connect-on-startup, so this module's _provider
    stays correct regardless of which one triggers the connection. Same
    bug class this fixes as market_data.py's connect_polygon() — see its
    docstring.
    """
    global _provider
    if _provider is not None and _provider.is_connected():
        return _provider

    provider = FinnhubAdapter()  # raises ValueError if no API key configured
    await provider.connect()  # raises on a real connection failure — not swallowed here
    _provider = provider

    bridge = TickIngestBridge(provider, get_event_bus())
    await broker_registry.take_over_streaming(provider, bridge)
    return provider


@router.post("/connect")
async def connect() -> dict:
    if _provider is not None and _provider.is_connected():
        return {"status": "already_connected"}

    try:
        await connect_finnhub()
    except ValueError as exc:  # missing API key
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as a clear HTTP error
        logger.exception("Finnhub connect failed")
        raise HTTPException(status_code=502, detail=f"Finnhub connect failed: {exc}") from exc

    return {"status": "connected", "note": "real-time WebSocket — genuinely live, not delayed"}


@router.post("/subscribe")
async def subscribe(symbol: str) -> dict:
    if _provider is None or not _provider.is_connected():
        raise HTTPException(status_code=400, detail="Not connected — call POST /finnhub/connect first")
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
    return {"connected": _provider is not None and _provider.is_connected()}


@router.post("/disconnect")
async def disconnect() -> dict:
    global _provider
    if _provider is not None:
        await _provider.disconnect()
        if broker_registry.get_streaming_provider() is _provider:
            broker_registry.clear_streaming_provider()
    _provider = None
    return {"status": "disconnected"}
