"""
Manual connection control for Phase 3. There's no "connect on FastAPI
startup" — IBKR requires Gateway/TWS to already be running and logged in
externally (see backend/README.md's IBKR setup section) before this
backend can connect to it, and auto-connecting on app startup would hang
or crash startup if Gateway isn't up yet. Explicit routes keep that under
your control.

Deliberately does NOT include order placement/cancellation routes — see
app/broker_adapters/base.py's BrokerAdapter docstring for why.

IBKR registers as BOTH streaming and historical when connected (it's
capable of both — a full BrokerAdapter, confirmed decision #28) via
broker_registry.take_over_streaming(), which safely disconnects whatever
was previously streaming (Finnhub/Polygon, likely auto-connected at
startup) rather than letting two providers push ticks onto the bus at
once. Connecting IBKR is always a deliberate manual action, so it's
allowed to take over — unlike the automatic startup ordering in
app/main.py, which needs a tie-breaking rule instead of "whoever asked."
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.broker_adapters.base import SymbolNotFoundError
from app.broker_adapters.ibkr_adapter import IBKRAdapter
from app.event_bus.bus import get_event_bus
from app.services import broker_registry
from app.services.tick_ingest import TickIngestBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/broker", tags=["broker"])


@router.post("/connect")
async def connect() -> dict:
    existing = broker_registry.get_streaming_provider()
    if isinstance(existing, IBKRAdapter) and existing.is_connected():
        return {"status": "already_connected"}

    adapter = IBKRAdapter()
    try:
        await adapter.connect()
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as a clear HTTP error
        logger.exception("IBKR connect failed")
        raise HTTPException(
            status_code=502,
            detail=(
                f"IBKR connect failed: {exc}. Is IB Gateway running and logged in? "
                "See backend/README.md's IBKR connection setup section."
            ),
        ) from exc

    bridge = TickIngestBridge(adapter, get_event_bus())
    await broker_registry.take_over_streaming(adapter, bridge)
    broker_registry.set_historical_provider(adapter)
    return {"status": "connected"}


@router.post("/subscribe")
async def subscribe(symbol: str) -> dict:
    adapter = broker_registry.get_streaming_provider()
    if adapter is None or not adapter.is_connected():
        raise HTTPException(status_code=400, detail="Not connected — call POST /broker/connect first")
    try:
        await adapter.subscribe([symbol])
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "subscribed", "symbol": symbol}


@router.post("/unsubscribe")
async def unsubscribe(symbol: str) -> dict:
    adapter = broker_registry.get_streaming_provider()
    if adapter is None:
        raise HTTPException(status_code=400, detail="Not connected")
    await adapter.unsubscribe([symbol])
    return {"status": "unsubscribed", "symbol": symbol}


@router.get("/status")
async def status() -> dict:
    adapter = broker_registry.get_streaming_provider()
    return {"connected": adapter is not None and adapter.is_connected()}


@router.post("/disconnect")
async def disconnect() -> dict:
    adapter = broker_registry.get_streaming_provider()
    if adapter is not None:
        await adapter.disconnect()
    broker_registry.clear_streaming_provider()
    broker_registry.clear_historical_provider()
    return {"status": "disconnected"}
