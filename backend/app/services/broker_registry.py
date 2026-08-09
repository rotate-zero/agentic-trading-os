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
"""
from __future__ import annotations

from app.broker_adapters.base import MarketDataProvider
from app.services.tick_ingest import TickIngestBridge

_streaming_provider: MarketDataProvider | None = None
_streaming_bridge: TickIngestBridge | None = None
_historical_provider: MarketDataProvider | None = None


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
    """
    global _streaming_provider, _streaming_bridge
    old = _streaming_provider
    if old is not None and old is not new_provider and _historical_provider is not old:
        await old.disconnect()
    # Stop the OLD bridge's background flush loop (tick_ingest.py) regardless
    # of whether the old provider itself got disconnected above — a provider
    # kept alive for the historical role still shouldn't have two
    # TickIngestBridge instances both registered as its on_tick callback
    # (the second registration just silently overwrites the first's, but the
    # first's own flush loop would otherwise run forever, doing nothing
    # useful, until process exit).
    if _streaming_bridge is not None and _streaming_bridge is not bridge:
        _streaming_bridge.stop()
    _streaming_provider = new_provider
    _streaming_bridge = bridge


def clear_streaming_provider() -> None:
    global _streaming_provider, _streaming_bridge
    if _streaming_bridge is not None:
        _streaming_bridge.stop()
    _streaming_provider = None
    _streaming_bridge = None


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
    """Test-fixture convenience — resets both roles at once."""
    clear_streaming_provider()
    clear_historical_provider()
