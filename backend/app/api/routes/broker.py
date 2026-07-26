"""
Manual connection control for Phase 3. There's no "connect on FastAPI
startup" — IBKR requires Gateway/TWS to already be running and logged in
externally (see backend/README.md's IBKR setup section) before this
backend can connect to it, and auto-connecting on app startup would hang
or crash startup if Gateway isn't up yet. Explicit routes keep that under
your control.

Deliberately does NOT include order placement/cancellation routes — see
app/broker_adapters/base.py's BrokerAdapter docstring for why.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.broker_adapters.base import SymbolNotFoundError
from app.broker_adapters.ibkr_adapter import IBKRAdapter
from app.event_bus.bus import get_event_bus
from app.services import broker_registry
from app.services.ibkr_ingest import IBKRIngestBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/broker", tags=["broker"])


@router.post("/connect")
async def connect() -> dict:
    existing = broker_registry.get_active_adapter()
    if existing is not None and existing.is_connected():
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

    bridge = IBKRIngestBridge(adapter, get_event_bus())
    broker_registry.set_active(adapter, bridge)
    return {"status": "connected"}


@router.post("/subscribe")
async def subscribe(symbol: str) -> dict:
    adapter = broker_registry.get_active_adapter()
    if adapter is None or not adapter.is_connected():
        raise HTTPException(status_code=400, detail="Not connected — call POST /broker/connect first")
    try:
        await adapter.subscribe([symbol])
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "subscribed", "symbol": symbol}


@router.post("/unsubscribe")
async def unsubscribe(symbol: str) -> dict:
    adapter = broker_registry.get_active_adapter()
    if adapter is None:
        raise HTTPException(status_code=400, detail="Not connected")
    await adapter.unsubscribe([symbol])
    return {"status": "unsubscribed", "symbol": symbol}


@router.get("/status")
async def status() -> dict:
    adapter = broker_registry.get_active_adapter()
    return {"connected": adapter is not None and adapter.is_connected()}


@router.post("/disconnect")
async def disconnect() -> dict:
    adapter = broker_registry.get_active_adapter()
    if adapter is not None:
        await adapter.disconnect()
    broker_registry.clear_active()
    return {"status": "disconnected"}
