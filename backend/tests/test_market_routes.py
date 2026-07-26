"""
Route-level tests for the not-connected and bad-symbol error paths.
Uses a fake adapter injected directly into broker_registry rather than a
real IBKRAdapter, so these run without any live Gateway.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.broker_adapters.base import SymbolNotFoundError
from app.main import app
from app.services import broker_registry


class _FakeConnectedAdapter:
    """Just enough surface for the routes under test — not a real
    BrokerAdapter subclass, since these tests only exercise the route
    layer's error handling, not the adapter's own interface compliance
    (that's covered in test_ibkr_adapter.py)."""

    def is_connected(self) -> bool:
        return True

    async def subscribe(self, symbols: list[str]) -> None:
        raise SymbolNotFoundError(symbols[0])

    async def get_historical(self, symbol: str, timeframe: str, start, end):
        raise SymbolNotFoundError(symbol)


def test_market_candles_requires_connection():
    broker_registry.clear_active()
    with TestClient(app) as client:
        r = client.get("/market/candles", params={"symbol": "NVDA"})
    assert r.status_code == 400
    assert "Not connected" in r.json()["detail"]


def test_market_candles_returns_400_for_unresolvable_symbol():
    broker_registry.set_active(_FakeConnectedAdapter())
    try:
        with TestClient(app) as client:
            r = client.get("/market/candles", params={"symbol": "NOTREAL"})
        assert r.status_code == 400
        assert "NOTREAL" in r.json()["detail"]
    finally:
        broker_registry.clear_active()


def test_broker_subscribe_returns_400_for_unresolvable_symbol():
    broker_registry.set_active(_FakeConnectedAdapter())
    try:
        with TestClient(app) as client:
            r = client.post("/broker/subscribe", params={"symbol": "NOTREAL"})
        assert r.status_code == 400
        assert "NOTREAL" in r.json()["detail"]
    finally:
        broker_registry.clear_active()


def test_market_candles_rejects_unsupported_timeframe():
    broker_registry.set_active(_FakeConnectedAdapter())
    try:
        with TestClient(app) as client:
            r = client.get("/market/candles", params={"symbol": "NVDA", "timeframe": "3m"})
        assert r.status_code == 400
    finally:
        broker_registry.clear_active()
