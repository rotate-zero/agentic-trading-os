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

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.broker_adapters.base import SymbolNotFoundError
from app.broker_adapters.finnhub_provider import FinnhubAdapter
from app.backtest_runner.engine_singleton_guard import finnhub_connection_slot, replay_slot_busy
from app.event_bus.bus import get_event_bus
from app.services import broker_registry
from app.services.tick_ingest import TickIngestBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/finnhub", tags=["finnhub"])

_provider: FinnhubAdapter | None = None
_owner_lock = asyncio.Lock()


async def connect_finnhub() -> FinnhubAdapter:
    """
    Shared connect logic — used by both POST /finnhub/connect and
    app/main.py's auto-connect-on-startup, so this module's _provider
    stays correct regardless of which one triggers the connection. Same
    bug class this fixes as market_data.py's connect_polygon() — see its
    docstring.
    """
    global _provider
    async with _owner_lock:
        if _provider is not None and _provider.is_streaming_active():
            return _provider
        provider = FinnhubAdapter()  # raises ValueError if no API key configured
        _provider = provider  # replay guard sees the in-flight initial connect
        bridge: TickIngestBridge | None = None
        try:
            if replay_slot_busy():
                raise HTTPException(status_code=409, detail="Finnhub cannot connect during a backtest replay")
            async with finnhub_connection_slot():
                await provider.connect()  # initial failures remain caller-visible
                bridge = TickIngestBridge(provider, get_event_bus())
                await broker_registry.take_over_streaming(provider, bridge)
        except BaseException:
            if bridge is not None and broker_registry.get_streaming_provider() is not provider:
                await bridge.aclose()
            await provider.disconnect()
            if _provider is provider:
                _provider = None
            raise
        return provider


@router.post("/connect")
async def connect() -> dict:
    if (_provider is not None and _provider.is_connected()
            and broker_registry.get_streaming_provider() is _provider):
        return {"status": "already_connected"}

    try:
        provider = await connect_finnhub()
    except HTTPException:
        raise
    except ValueError as exc:  # missing API key
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 — transport details may contain the token
        logger.warning("Finnhub connect failed")
        raise HTTPException(status_code=502, detail="Finnhub connect failed") from None

    if not provider.is_connected():
        return {"status": "reconnecting"}
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


def is_connected() -> bool:
    """Public accessor for other modules that need to know whether this
    process currently has a live Finnhub connection — e.g. the Backtest
    Runner trigger route refusing to run while live data is flowing (see
    backend/app/api/routes/backtest.py). Mirrors the same check GET
    /finnhub/status already returns; exposed as a plain function so a
    caller outside this router doesn't need to build a request against
    its own app just to ask a question this module already knows the
    answer to."""
    return _provider is not None and _provider.is_connected()


def is_streaming_active() -> bool:
    """Replay exclusion includes a pending connection and an outage retry."""
    if _provider is not None and _provider.is_streaming_active():
        return True
    owner = broker_registry.get_streaming_provider()
    return (getattr(owner, "provider_id", None) == "finnhub"
            and callable(getattr(owner, "is_streaming_active", None))
            and owner.is_streaming_active())


@router.post("/disconnect")
async def disconnect() -> dict:
    global _provider
    if _provider is not None:
        _provider.cancel_pending_connect()
    async with _owner_lock:
        if _provider is not None:
            await _provider.disconnect()
            if broker_registry.get_streaming_provider() is _provider:
                broker_registry.clear_streaming_provider()
        _provider = None
    await broker_registry.settle_retired_bridges()
    return {"status": "disconnected"}
