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

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.broker_adapters.base import HistoricalDataUnavailableError, SymbolNotFoundError
from app.core.market_clock import get_market_clock
from app.services import broker_registry, candle_aggregator, candle_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["market"])

# How much wall-clock history to request per timeframe for a `count`-sized
# backfill. Rough on purpose — IBKR trims to actual trading-session data
# regardless, so asking for "too much" wall-clock time just means the
# request covers extra non-trading hours, not extra rows.
#
# "4h" deliberately excluded (confirmed-decisions.md): the regular session
# is 6.5h, which doesn't divide evenly by 4h, and it isn't a timeframe
# anything in this app actually uses yet — rejecting it cleanly here (a
# normal 400, same as any other unsupported timeframe) beats inventing a
# bucket definition nobody's confirmed. polygon_provider.py also has no "4h"
# entry in its own mapping; that's now unreachable dead code via this route,
# not a live bug, but worth knowing about if something ever calls that
# adapter directly instead of through here.
_MINUTES_PER_UNIT = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 60 * 24}


@router.get("/candles")
async def get_candles(
    symbol: str = Query(...),
    count: int = Query(240, le=1000),
    timeframe: str = Query("1m"),
) -> dict:
    if timeframe not in _MINUTES_PER_UNIT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported timeframe {timeframe!r} (supported: {sorted(_MINUTES_PER_UNIT)})",
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=_MINUTES_PER_UNIT[timeframe] * count)

    # Self-recorded first (confirmed decision #42's CandleRecorder) — real
    # data this app has already seen and persisted, at zero cost and no
    # provider round-trip. For "1m" specifically this is the ONLY source
    # that can ever exist at all on a free-tier Polygon/Massive plan (see
    # confirmed decision #39) — Polygon is only ever reached below as a
    # fallback for a symbol/range genuinely nothing has been recorded for
    # yet (a brand-new ticker, or a freshly-migrated/empty database). A
    # partial match (self-recorded history shorter than the requested
    # range) is returned as-is rather than topped up from Polygon — Polygon
    # structurally can't fill a 1m gap anyway, and attempting a merge for
    # timeframes it CAN serve would be complexity with no current benefit.
    #
    # get_recorded_candles is a sync DB call (app/db/session.py: sync
    # engine by design) — to_thread keeps it off the event loop, same
    # pattern PolygonAdapter already uses for its own sync REST calls. A
    # DB that's unreachable is treated as "nothing recorded yet," not a
    # hard failure — logged, not raised, so the request still falls
    # through to whatever external provider is connected instead of 500ing
    # over what's still an optional enhancement path at this phase.
    try:
        recorded = await asyncio.to_thread(candle_store.get_recorded_candles, symbol, timeframe, start, end)
    except Exception:  # noqa: BLE001 — see comment above
        logger.exception("candle_store.get_recorded_candles failed for %s — falling through to external provider", symbol)
        recorded = []

    if recorded:
        return {
            "symbol": symbol,
            "candles": [c.model_dump(mode="json") for c in recorded[-count:]],
        }

    # 5m/15m/1h: never self-recorded directly (see _MINUTES_PER_UNIT's "4h"
    # comment — the recorder only ever writes "1m" rows), but derivable from
    # whatever 1m history IS recorded. Tried before falling through to an
    # external provider — this is real, session-aware data built from ticks
    # this app actually saw, strictly better than Polygon's free-tier
    # intraday (which is paywalled entirely — see confirmed decision #39 —
    # so would just fail below anyway). "1d" deliberately excluded: it stays
    # sourced from Polygon's real daily EOD bars rather than reconstructed
    # from however much 1m history happens to be sitting in this database.
    if timeframe in candle_aggregator.AGGREGATABLE_TIMEFRAMES:
        try:
            aggregated = await asyncio.to_thread(candle_aggregator.aggregate_from_recorded, symbol, timeframe, start, end)
        except Exception:  # noqa: BLE001 — same "log, fall through" reasoning as the self-recorded lookup above
            logger.exception("candle_aggregator.aggregate_from_recorded failed for %s — falling through to external provider", symbol)
            aggregated = []
        if aggregated:
            return {
                "symbol": symbol,
                "candles": [c.model_dump(mode="json") for c in aggregated[-count:]],
            }

    adapter = broker_registry.get_historical_provider()
    if adapter is None or not adapter.is_connected():
        raise HTTPException(
            status_code=400,
            detail=(
                "Nothing recorded yet for this symbol/range and no historical provider "
                "connected — call POST /market-data/connect (Polygon) or POST /broker/connect "
                "(IBKR, once available). Finnhub cannot serve this (see GET /finnhub/status)."
            ),
        )

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


@router.get("/feed-status")
async def get_feed_status(symbol: str = Query(...)) -> dict:
    """
    Confirmed decision #44's still-open after-hours item: the 16:00-20:00
    AFTER_HOURS boundary (market_clock.py) is the common industry
    convention, never verified against what Finnhub/IBKR actually deliver
    on this account — CandleRecorder simply won't have rows past wherever
    the live feed actually stops, regardless of where that boundary is
    drawn. That's a real-feed check, not something this sandbox can run
    (no network route to Finnhub here) — this route is the TOOL for
    running it, not the check itself: point it at a real symbol during an
    actual live session and read `staleness_seconds` directly instead of
    querying the `candles` table by hand.

    `staleness_seconds` is `None` until at least one 1m candle has been
    recorded for the symbol at all (same "absent means not-yet, not
    zero" convention used throughout this codebase — see e.g.
    FeatureEngine's warm-up returning None, not 0.0). A small, expected
    value (roughly one candle-width) during AFTER_HOURS confirms the feed
    is still genuinely live all the way through this window; a value that
    stops growing past a certain wall-clock time — well before 20:00 —
    is exactly the signal decision #44 flagged as unverified.
    """
    clock = get_market_clock()
    now = datetime.now(timezone.utc)

    # Same log-not-raise posture as GET /candles above — a DB hiccup here
    # should report "unknown," not 500 a route that exists purely to help
    # diagnose something else.
    try:
        latest = await asyncio.to_thread(candle_store.get_latest_recorded_candle, symbol, "1m")
    except Exception:  # noqa: BLE001 — see comment above
        logger.exception("candle_store.get_latest_recorded_candle failed for %s", symbol)
        latest = None

    staleness_seconds = None
    latest_candle_ts = None
    if latest is not None:
        latest_candle_ts = latest.candle_ts.isoformat()
        staleness_seconds = round((now - latest.candle_ts).total_seconds(), 1)

    return {
        "symbol": symbol,
        "market_session": clock.current_session().value,
        "checked_at": now.isoformat(),
        "latest_recorded_candle_ts": latest_candle_ts,
        "staleness_seconds": staleness_seconds,
    }
