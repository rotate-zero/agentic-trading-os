"""
Route-level tests for the not-connected and bad-symbol error paths.
Uses a fake adapter injected directly into broker_registry rather than a
real IBKRAdapter/PolygonAdapter, so these run without any live connection.

GET /market/candles reads from the HISTORICAL role; POST /broker/subscribe
reads from the STREAMING role (confirmed decision #33) — tests below set
up whichever role each route actually reads from, not just "connect
something."
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.broker_adapters.base import SymbolNotFoundError
from app.main import app
from app.services import broker_registry


class _FakeConnectedAdapter:
    """Just enough surface for the routes under test — not a real
    BrokerAdapter subclass, since these tests only exercise the route
    layer's error handling, not the adapter's own interface compliance
    (that's covered in test_ibkr_adapter.py). Needs disconnect() since
    main.py's lifespan shutdown calls it on whatever's in the registry."""

    def is_connected(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def subscribe(self, symbols: list[str]) -> None:
        raise SymbolNotFoundError(symbols[0])

    async def get_historical(self, symbol: str, timeframe: str, start, end):
        raise SymbolNotFoundError(symbol)


def test_market_candles_requires_connection():
    broker_registry.clear_all()
    with TestClient(app) as client:
        r = client.get("/market/candles", params={"symbol": "NVDA"})
    assert r.status_code == 400
    assert "connected" in r.json()["detail"].lower()


def test_market_candles_returns_400_for_unresolvable_symbol():
    broker_registry.set_historical_provider(_FakeConnectedAdapter())
    try:
        with TestClient(app) as client:
            r = client.get("/market/candles", params={"symbol": "NOTREAL"})
        assert r.status_code == 400
        assert "NOTREAL" in r.json()["detail"]
    finally:
        broker_registry.clear_all()


@pytest.mark.asyncio
async def test_broker_subscribe_returns_400_for_unresolvable_symbol():
    fake = _FakeConnectedAdapter()
    await broker_registry.take_over_streaming(fake)
    try:
        with TestClient(app) as client:
            r = client.post("/broker/subscribe", params={"symbol": "NOTREAL"})
        assert r.status_code == 400
        assert "NOTREAL" in r.json()["detail"]
    finally:
        broker_registry.clear_all()


def test_market_candles_rejects_unsupported_timeframe():
    broker_registry.set_historical_provider(_FakeConnectedAdapter())
    try:
        with TestClient(app) as client:
            r = client.get("/market/candles", params={"symbol": "NVDA", "timeframe": "3m"})
        assert r.status_code == 400
    finally:
        broker_registry.clear_all()
