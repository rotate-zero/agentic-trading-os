"""
Shared registry for currently-connected market-data/broker providers.

Two independent named roles, not one slot (confirmed decision #33):
  - streaming: whichever provider feeds the live tick pipeline
    (TickIngestBridge -> Event Bus -> WebSocket Gateway). Finnhub when
    connected (genuinely real-time); Polygon as a fallback (delayed) if
    Finnhub isn't connected.
  - historical: whichever provider serves GET /market/candles backfill.
    Polygon, since Finnhub's free tier can't serve this at all
    (confirmed decision #32).

A single provider can fill both roles if it's capable of both —
IBKRAdapter, once connected, takes over both, since it's a full
BrokerAdapter with no reason not to. Finnhub only ever registers as
streaming; get_historical() on it would just raise
HistoricalDataUnavailableError.

Replaces the original single-slot design (set_active/get_active_adapter/
clear_active) now that the trigger for needing more than one slot has
actually fired: Finnhub (streaming-only) and Polygon (historical-capable,
streaming-capable-but-delayed) both need to be connected at once, each
doing the job it's actually good at.

A third, separately-typed role was added for the Execution Engine
(EX-3, decision #170):
  - execution: THE OrderVenue the Execution Engine places/cancels
    orders through (app/broker_adapters/order_venue.py). Never a
    MarketDataProvider, never shared with the streaming/historical
    slots above — set_execution_venue() fails closed if the venue's
    supported_modes doesn't include the configured execution_mode
    (core/config.py).
"""
from __future__ import annotations

from collections.abc import Callable

from app.broker_adapters.base import MarketDataProvider
from app.broker_adapters.order_venue import OrderVenue
from app.core.config import get_settings
from app.services.tick_ingest import BridgeState, TickIngestBridge

_streaming_provider: MarketDataProvider | None = None
_streaming_bridge: TickIngestBridge | None = None
# Bridges whose admission is already shut off (stop() ran synchronously) but
# whose owned tasks have not yet been awaited. Filled by the synchronous
# clear path and by takeover; drained by settle_retired_bridges().
_retired_bridges: list[TickIngestBridge] = []
_historical_provider: MarketDataProvider | None = None
_protected_feed_wake: Callable[[], None] | None = None


def register_protected_feed_wake(callback: Callable[[], None]) -> None:
    """One lifecycle-owned, nonblocking signal for the protected-feed owner."""
    global _protected_feed_wake
    if _protected_feed_wake is not None and _protected_feed_wake is not callback:
        raise RuntimeError("protected-feed wake owner is already registered")
    _protected_feed_wake = callback


def unregister_protected_feed_wake(callback: Callable[[], None]) -> None:
    global _protected_feed_wake
    if _protected_feed_wake is callback:
        _protected_feed_wake = None


def request_protected_feed_reconcile() -> None:
    callback = _protected_feed_wake
    if callback is not None:
        callback()

# Third role, added for the Execution Engine (EX-3, decision #170) —
# deliberately a THIRD, separately-typed global, not folded into
# _streaming_provider/_historical_provider. An OrderVenue is never a
# MarketDataProvider (order_venue.py's ABC doesn't inherit from it, and
# vice versa), so there is no slot an IBKRAdapter connecting for market
# data could ever fill by accident (design doc §6.4, I1).
_execution_venue: OrderVenue | None = None


class UnsupportedExecutionModeError(RuntimeError):
    """Raised by set_execution_venue() when the venue's supported_modes
    doesn't include the configured execution_mode — fail closed (I6,
    AC #5), never register a venue that can't actually serve the
    configured mode."""


def _retire_bridge(bridge: TickIngestBridge) -> None:
    """Synchronous half of retirement: admission off NOW, settle later."""
    bridge.stop()
    if bridge.state is not BridgeState.RETIRED and not any(b is bridge for b in _retired_bridges):
        _retired_bridges.append(bridge)


async def settle_retired_bridges() -> None:
    """Awaits every bridge retired so far (idempotent; no-op when none).
    The lifecycle boundaries that call it: take_over_streaming, the
    provider disconnect routes, and lifespan shutdown."""
    for bridge in list(_retired_bridges):
        await bridge.aclose()
    _retired_bridges[:] = [b for b in _retired_bridges if b.state is not BridgeState.RETIRED]


async def retire_streaming_bridge() -> None:
    """Retires and settles the currently registered bridge WITHOUT changing
    any provider role (lifespan shutdown: providers are disconnected by the
    caller; the registry itself is left as-is)."""
    if _streaming_bridge is not None:
        _retire_bridge(_streaming_bridge)
    await settle_retired_bridges()


async def take_over_streaming(
    new_provider: MarketDataProvider, bridge: TickIngestBridge | None = None
) -> None:
    """
    Makes new_provider the streaming provider, safely retiring whatever
    was there before. If the previous streaming provider is a different
    instance and ISN'T also the current historical provider, it gets
    disconnected — otherwise it would keep silently pushing ticks onto
    the Event Bus alongside the new provider, and nothing downstream
    would know two sources were feeding it at once. If it's still needed
    for the historical role, it's left connected; only its streaming
    "ownership" changes.

    The previous TickIngestBridge is retired in every case (decision slug
    retired-tick-bridge-isolation): admission is shut off synchronously,
    then its owned tasks are settled, and only THEN does the registry
    expose the new provider/bridge or wake the protected-feed owner. A
    historical-only provider that stays connected therefore can no longer
    publish live events through its old bridge. If old.disconnect() raises,
    the old registered bridge and provider remain unchanged; the uninstalled
    candidate bridge is retired so it cannot publish outside the registry.
    """
    global _streaming_provider, _streaming_bridge
    try:
        old = _streaming_provider
        if old is not None and old is not new_provider and _historical_provider is not old:
            await old.disconnect()
        # Read AFTER the await: a clear/takeover may have run while disconnecting.
        if _streaming_bridge is not None and _streaming_bridge is not bridge:
            _retire_bridge(_streaming_bridge)
        await settle_retired_bridges()
        _streaming_provider = new_provider
        _streaming_bridge = bridge
        request_protected_feed_reconcile()
    except BaseException:
        # The caller may have constructed an admitting bridge before this
        # call. If installation failed, it must not keep publishing outside
        # the registry (including when old.disconnect() raised).
        if bridge is not None and _streaming_bridge is not bridge:
            await bridge.aclose()
        raise


def clear_streaming_provider() -> None:
    """Synchronous (callers include sync fixtures). Admission of the cleared
    bridge is shut off immediately; its task cleanup is awaited at the next
    lifecycle boundary (settle_retired_bridges())."""
    global _streaming_provider, _streaming_bridge
    if _streaming_bridge is not None:
        _retire_bridge(_streaming_bridge)
    _streaming_provider = None
    _streaming_bridge = None
    request_protected_feed_reconcile()


def get_streaming_provider() -> MarketDataProvider | None:
    return _streaming_provider


def set_historical_provider(provider: MarketDataProvider) -> None:
    global _historical_provider
    _historical_provider = provider


def clear_historical_provider() -> None:
    global _historical_provider
    _historical_provider = None


def get_historical_provider() -> MarketDataProvider | None:
    return _historical_provider


def set_execution_venue(venue: OrderVenue) -> None:
    """Registers `venue` as THE execution venue. Refuses (fail closed,
    I6, AC #5) a venue whose `supported_modes` excludes the currently
    configured `execution_mode` — this check happens here, once, at
    registration time, rather than being re-checked by every caller of
    `get_execution_venue()`."""
    global _execution_venue
    configured_mode = get_settings().execution_mode
    if configured_mode not in venue.supported_modes:
        raise UnsupportedExecutionModeError(
            f"venue {venue.venue_id!r} supports {sorted(venue.supported_modes)}, "
            f"which does not include the configured execution_mode={configured_mode!r}"
        )
    _execution_venue = venue


def clear_execution_venue() -> None:
    global _execution_venue
    _execution_venue = None


def get_execution_venue() -> OrderVenue | None:
    return _execution_venue


def get_all_active_providers() -> list[MarketDataProvider]:
    """For lifecycle management (main.py shutdown) — every distinct
    connected provider, deduplicated by identity, since one instance can
    fill both roles at once (e.g. IBKR) and must not be disconnected
    twice."""
    result: list[MarketDataProvider] = []
    for provider in (_streaming_provider, _historical_provider):
        if provider is not None and provider not in result:
            result.append(provider)
    return result


def clear_all() -> None:
    """Test-fixture convenience — resets all three roles at once."""
    clear_streaming_provider()
    clear_historical_provider()
    clear_execution_venue()
    global _protected_feed_wake
    _protected_feed_wake = None
