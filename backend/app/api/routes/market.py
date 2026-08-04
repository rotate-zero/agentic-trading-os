"""
Candle backfill and live-symbol subscription over a provider-agnostic
route — the frontend shouldn't need to know whether Finnhub, Polygon, or
(eventually) IBKR is the currently active source. GET /candles reads from
the HISTORICAL role; POST /subscribe acts on the STREAMING role
(confirmed decision #33) — same split reasoning as everywhere else this
distinction shows up.

Response shape for /candles is deliberately the same {"symbol",
"candles": [...]} shape the frontend mock-swap was designed around, so
useLiveCandles is a drop-in regardless of which backend source is behind
it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.broker_adapters.base import HistoricalDataUnavailableError, SymbolNotFoundError
from app.services import broker_registry

router = APIRouter(prefix="/market", tags=["market"])

# How much wall-clock history to request per timeframe for a `count`-sized
# backfill. Rough on purpose — IBKR trims to actual trading-session data
# regardless, so asking for "too much" wall-clock time just means the
# request covers extra non-trading hours, not extra rows.
_MINUTES_PER_UNIT = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 60 * 24}


@router.get("/candles")
async def get_candles(
    symbol: str = Query(...),
    count: int = Query(240, le=1000),
    timeframe: str = Query("1m"),
) -> dict:
    adapter = broker_registry.get_historical_provider()
    if adapter is None or not adapter.is_connected():
        raise HTTPException(
            status_code=400,
            detail=(
                "No historical provider connected — call POST /market-data/connect "
                "(Polygon) or POST /broker/connect (IBKR, once available). Finnhub "
                "cannot serve this (see GET /finnhub/status)."
            ),
        )

    if timeframe not in _MINUTES_PER_UNIT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported timeframe {timeframe!r} (supported: {sorted(_MINUTES_PER_UNIT)})",
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=_MINUTES_PER_UNIT[timeframe] * count)

    try:
        candles = await adapter.get_historical(symbol, timeframe, start, end)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HistoricalDataUnavailableError as exc:
        # Shouldn't normally happen — the historical role is only ever
        # supposed to hold a provider that can actually do this — but
        # handled explicitly rather than surfacing as a raw 500 if it
        # somehow does (e.g. a future provider registered incorrectly).
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    return {
        "symbol": symbol,
        "candles": [c.model_dump(mode="json") for c in candles[-count:]],
    }


@router.post("/subscribe")
async def subscribe(symbol: str = Query(...)) -> dict:
    """
    Subscribes on whichever provider currently holds the streaming role
    — added specifically for the frontend swap, so a symbol switch in the
    UI can call one route regardless of whether Finnhub, Polygon, or
    (eventually) IBKR is actually connected. Provider-specific routes
    (/finnhub/subscribe, /market-data/subscribe, /broker/subscribe) still
    exist for manual/debug use; this is the one real consumers should use.
    """
    provider = broker_registry.get_streaming_provider()
    if provider is None or not provider.is_connected():
        raise HTTPException(
            status_code=400,
            detail="No streaming provider connected — nothing is currently live.",
        )
    try:
        await provider.subscribe([symbol])
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "subscribed", "symbol": symbol}
