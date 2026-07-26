"""
Shared registry for whichever BrokerAdapter is currently connected.
Exists so app/api/routes/broker.py (connect/disconnect) and
app/api/routes/market.py (candle backfill) agree on the same adapter
instance, instead of each route file keeping its own module-level global
that the other can't see.

Deliberately a single global slot, not a dict of adapters — Phase 3 is
"one broker adapter, one symbol at a time" by design (system-design.md
§7's Phase 3 exit criterion). Multi-broker support isn't a Phase 3
concern.
"""
from __future__ import annotations

from app.broker_adapters.base import BrokerAdapter
from app.services.ibkr_ingest import IBKRIngestBridge

_adapter: BrokerAdapter | None = None
_bridge: IBKRIngestBridge | None = None


def set_active(adapter: BrokerAdapter, bridge: IBKRIngestBridge | None = None) -> None:
    global _adapter, _bridge
    _adapter = adapter
    _bridge = bridge


def clear_active() -> None:
    global _adapter, _bridge
    _adapter = None
    _bridge = None


def get_active_adapter() -> BrokerAdapter | None:
    return _adapter
