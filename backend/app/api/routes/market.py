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
from typing import Protocol

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.broker_adapters.base import HistoricalDataUnavailableError, SubscriptionInventory, SymbolNotFoundError
from app.core.market_clock import get_market_clock
from app.services import broker_registry, candle_aggregator, candle_store, live_tick_relay
from app.services.protected_feed_reconciliation import ProtectedFeedSnapshot

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
    # `ge=1` (alongside the pre-existing `le=1000`) — same
    # `Query(..., ge=1, le=N)` bounding convention GET /scanner/state's
    # `top_n` and GET /intelligence/execution-orders' `limit` already use.
    # Zero/negative previously slipped through FastAPI's own validation
    # entirely: `count=0` builds a zero-width `start`..`end` range and then
    # `recorded[-0:]` (Python slice quirk — `[-0:]` means "from index 0",
    # i.e. the WHOLE list, not "the last 0 items") silently returned every
    # recorded candle instead of none; a negative `count` produces an
    # inverted `start > end` range and a `recorded[-count:]` positive-index
    # slice with its own misleading behavior. Both are now a clean 422 at
    # the request-validation layer, before any of that logic runs.
    count: int = Query(240, ge=1, le=1000),
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


@router.post("/active-symbols")
async def set_active_symbols(symbols: list[str] = Body(..., embed=True)) -> dict:
    """
    Sets the small (max 8), actively tick-monitored symbol set LiveTickRelay
    uses for throttled 5s PriceSnapshot fluidity (decision #72) — intended
    caller is whatever process decides "these are the most-active symbols
    right now" (eventually Market Scanner; a manual call meanwhile, since
    Scanner itself isn't built yet). Deliberately separate from POST
    /subscribe: that call tells a MarketDataProvider to start streaming a
    symbol at all; this call is a much narrower "of the symbols already
    streaming, which ones should also get throttled tick-fluidity
    snapshots" — a symbol can be subscribed without being in this set, and
    should be for anything beyond the top 8.
    """
    relay = live_tick_relay.get_live_tick_relay()
    try:
        relay.set_active_symbols(symbols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "active_symbols": relay.get_active_symbols()}


@router.get("/active-symbols")
async def get_active_symbols() -> dict:
    relay = live_tick_relay.get_live_tick_relay()
    return {"active_symbols": relay.get_active_symbols()}


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


# Fixed wording, returned on every response so no consumer can read the
# inventory as more than it is.
_SUBSCRIPTION_STATUS_NOTE = (
    "Inventory is the active streaming provider adapter's own local record of "
    "subscribe/unsubscribe calls that returned without raising. It is not "
    "provider acknowledgement, not proof that ticks are being delivered, and "
    "not evidence of how many subscriptions the account may hold; capacity and "
    "delivery are unknown."
)


def _subscription_status_body(
    *,
    status: str,
    reason: str | None,
    provider: dict | None,
    connected: bool | None,
    inventory_reason: str | None,
    symbols: list[str] | None,
) -> dict:
    return {
        "status": status,
        "reason": reason,
        "provider": provider,
        "connected": connected,
        "inventory": {
            "availability": "available" if symbols is not None else "unavailable",
            "reason": inventory_reason,
            "basis": "locally_tracked_requests",
            "count": len(symbols) if symbols is not None else None,
            "symbols": symbols,
        },
        # No concrete runtime evidence exists in this repository for either
        # (scanner-design.md §7, §18.6 CAP1): never inferred from a provider
        # name or from documentation.
        "capacity": {"status": "unknown", "limit": None},
        "delivery": {"status": "unknown"},
        "note": _SUBSCRIPTION_STATUS_NOTE,
    }


@router.get("/subscription-status")
async def get_subscription_status() -> dict:
    """
    Read-only diagnostic: what the CURRENT streaming provider's adapter has
    locally recorded as subscribed (task `provider-subscription-diagnostics`).

    Strictly a registry read. It never constructs, connects, subscribes,
    unsubscribes or disconnects anything, awaits nothing, and makes no
    network request — the adapter's snapshot is an in-memory copy.

    `status` says whether a streaming provider is registered at all
    ("unavailable" + reason `no_streaming_provider` otherwise). `inventory`
    is a separate verdict: "unavailable" (never an empty confirmed list) when
    the provider is disconnected — any retained local record is not an active
    inventory — when
    the adapter has no snapshot capability (existing doubles, future
    providers), or when the snapshot read fails or returns something other
    than a sequence of strings. Distinct from GET /market/feed-status, which
    measures recorded-candle age for one symbol and says nothing about
    subscriptions.
    """
    provider = broker_registry.get_streaming_provider()
    if provider is None:
        return _subscription_status_body(
            status="unavailable",
            reason="no_streaming_provider",
            provider=None,
            connected=None,
            inventory_reason="no_streaming_provider",
            symbols=None,
        )

    provider_id = getattr(provider, "provider_id", None)
    identity = {
        "id": provider_id if isinstance(provider_id, str) and provider_id else "unknown",
        "class_name": type(provider).__name__,
    }

    try:
        connected: bool | None = bool(provider.is_connected())
    except Exception:  # noqa: BLE001 — a diagnostic must not 500 on a misbehaving provider
        logger.exception("subscription-status: is_connected() failed for %s", identity["class_name"])
        connected = None

    def body(inventory_reason: str | None, symbols: list[str] | None) -> dict:
        return _subscription_status_body(
            status="available",
            reason=None,
            provider=identity,
            connected=connected,
            inventory_reason=inventory_reason,
            symbols=symbols,
        )

    if connected is None:
        return body("connection_state_unknown", None)
    if not connected:
        return body("provider_not_connected", None)
    if not isinstance(provider, SubscriptionInventory):
        return body("inventory_not_supported", None)

    try:
        raw = provider.get_subscription_snapshot()
    except Exception:  # noqa: BLE001 — see above
        logger.exception("subscription-status: snapshot failed for %s", identity["class_name"])
        return body("snapshot_failed", None)

    if not isinstance(raw, (tuple, list, frozenset, set)) or not all(isinstance(s, str) for s in raw):
        logger.warning("subscription-status: %s returned an invalid snapshot", identity["class_name"])
        return body("snapshot_failed", None)

    # Fresh, deterministically ordered list: the response never shares
    # structure with anything the adapter holds.
    return body(None, sorted(raw))


# --- GET /market/protected-feed-status -------------------------------------
# Task `protected-feed-reconciliation-status`.

_PROTECTED_FEED_NOTE = (
    "Request evidence only. The protected-feed owner re-checks about every "
    "interval_seconds, so a change in held positions or working orders can take "
    "up to one interval to be requested. A symbol listed as locally present was "
    "found in the provider adapter's own record; one listed as request returned "
    "had a subscribe call that returned without raising. Neither is provider "
    "acknowledgement, proof that ticks are arriving, or confirmed protection."
)


class ProtectedFeedStatusReader(Protocol):
    """The only thing this route may use from the reconciler: one synchronous,
    I/O-free snapshot read. It cannot start a cycle, read the database,
    subscribe or contact a provider through this route."""

    def get_snapshot(self) -> ProtectedFeedSnapshot: ...


def get_protected_feed_status_reader(request: Request) -> ProtectedFeedStatusReader | None:
    """Optional lifespan-owned object on `app.state`, `None` when absent. Never
    constructs, starts or looks up a reconciler itself."""
    return getattr(request.app.state, "protected_feed_status_reader", None)


def _protected_feed_utcnow() -> datetime:
    # Module-level so tests can pin the server read clock.
    return datetime.now(timezone.utc)


def _protected_feed_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _protected_feed_body(*, status: str, reason: str | None, reconciler: dict | None, protected_set: dict | None,
                         provider: dict | None, requests: dict | None) -> dict:
    return {
        "status": status,
        "reason": reason,
        "reconciler": reconciler,
        "protected_set": protected_set,
        "provider": provider,
        "requests": requests,
        "note": _PROTECTED_FEED_NOTE,
        "read_at": _protected_feed_iso(_protected_feed_utcnow()),
    }


def _protected_feed_provider(identity) -> dict | None:
    if identity is None:
        return None
    return {"id": identity.provider_id, "class_name": identity.class_name}


def _project_protected_feed(snapshot: ProtectedFeedSnapshot) -> dict:
    if snapshot.attempts_completed == 0:
        state = "never_attempted" if snapshot.attempts_started == 0 else "first_attempt_in_progress"
    else:
        state = snapshot.last_cycle_outcome or "interrupted"

    read = snapshot.last_set_read
    latest_read_failed = snapshot.last_read_outcome == "failed"
    protected_set = {
        # "never_read" is distinct from a successful empty set (count 0).
        "availability": "read" if read is not None else "never_read",
        "read_at": _protected_feed_iso(read.read_at) if read is not None else None,
        "symbols": list(read.symbols) if read is not None else None,
        "count": len(read.symbols) if read is not None else None,
        "latest_read": snapshot.last_read_outcome,
        "latest_read_attempt_at": _protected_feed_iso(snapshot.last_read_attempt_at),
        # Fixed code only; raw exception detail stays in the backend logs.
        "latest_read_error": "protected_set_read_failed" if latest_read_failed else None,
    }

    requests = None
    record = snapshot.request_record
    if record is not None:
        counts = {"locally_present": 0, "request_returned": 0, "request_failed": 0, "no_outcome": 0}
        entries = []
        for item in record.requests:
            counts[item.outcome] = counts.get(item.outcome, 0) + 1
            entries.append({"symbol": item.symbol, "outcome": item.outcome, "error_class": item.error_class})
        requests = {
            "recorded_at": _protected_feed_iso(record.recorded_at),
            "provider": _protected_feed_provider(record.provider),
            "inventory_available": record.inventory_available,
            "basis": "request_evidence_only",
            "counts": counts,
            "entries": entries,
        }

    reconciler = {
        "state": state,
        "running": snapshot.running,
        "cycle_in_progress": snapshot.cycle_in_progress,
        "interval_seconds": snapshot.interval_seconds,
        "attempts_started": snapshot.attempts_started,
        "attempts_completed": snapshot.attempts_completed,
        "last_attempt_at": _protected_feed_iso(snapshot.last_attempt_at),
        "last_completed_at": _protected_feed_iso(snapshot.last_completed_at),
        "last_cycle_outcome": snapshot.last_cycle_outcome,
    }
    provider = _protected_feed_provider(snapshot.last_provider)
    if provider is not None:
        provider["connected"] = snapshot.last_provider_connected
    return _protected_feed_body(
        status="available", reason=None, reconciler=reconciler,
        protected_set=protected_set, provider=provider, requests=requests,
    )


@router.get("/protected-feed-status")
async def get_protected_feed_status(
    reader: ProtectedFeedStatusReader | None = Depends(get_protected_feed_status_reader),
) -> dict:
    """Read-only view of the protected-symbol subscription owner's last attempt.

    Exactly one synchronous `get_snapshot()` per request: no reconciliation
    cycle, database read, subscribe, unsubscribe or provider connection is
    started here. With no reader installed (`reconciler_not_installed`) the
    answer is "unavailable" — distinct from an installed owner that has not
    yet attempted (`reconciler.state == "never_attempted"`). A failed
    protected-set read never appears as an empty set: the last successful read
    is kept, with its own timestamp, next to the latest read outcome. Error
    detail is limited to fixed codes and coarse classes.
    """
    if reader is None:
        return _protected_feed_body(
            status="unavailable", reason="reconciler_not_installed",
            reconciler=None, protected_set=None, provider=None, requests=None,
        )
    try:
        return _project_protected_feed(reader.get_snapshot())
    except Exception:  # noqa: BLE001 — a diagnostic must not 500 on a misbehaving reader
        logger.exception("protected-feed-status: snapshot read failed")
        return _protected_feed_body(
            status="unavailable", reason="snapshot_read_failed",
            reconciler=None, protected_set=None, provider=None, requests=None,
        )
