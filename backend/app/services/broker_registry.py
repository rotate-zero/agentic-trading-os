"""
Shared registry for whichever provider is currently connected — a full
BrokerAdapter (IBKR) or, once one exists, a data-only MarketDataProvider
(Polygon, Databento, ...). Exists so app/api/routes/broker.py
(connect/disconnect) and app/api/routes/market.py (candle backfill) agree
on the same instance, instead of each route file keeping its own
module-level global that the other can't see.

Typed against MarketDataProvider, not BrokerAdapter — GET /market/candles
only ever needs is_connected()/get_historical(), both on the base
interface, so it shouldn't require a full broker to be sitting behind it.
A BrokerAdapter instance (like IBKRAdapter) satisfies this type fine,
since BrokerAdapter extends MarketDataProvider (confirmed decision #28).

Deliberately a single global slot, not a dict — Phase 3 is "one
provider, one symbol at a time" by design (system-design.md §7's Phase 3
exit criterion). Multi-provider support isn't a Phase 3 concern.
"""
from __future__ import annotations

from app.broker_adapters.base import MarketDataProvider
from app.services.tick_ingest import TickIngestBridge

_adapter: MarketDataProvider | None = None
_bridge: TickIngestBridge | None = None


def set_active(adapter: MarketDataProvider, bridge: TickIngestBridge | None = None) -> None:
    global _adapter, _bridge
    _adapter = adapter
    _bridge = bridge


def clear_active() -> None:
    global _adapter, _bridge
    _adapter = None
    _bridge = None


def get_active_adapter() -> MarketDataProvider | None:
    return _adapter
