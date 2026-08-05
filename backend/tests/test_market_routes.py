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


def test_market_subscribe_requires_connection():
    broker_registry.clear_all()
    with TestClient(app) as client:
        r = client.post("/market/subscribe", params={"symbol": "NVDA"})
    assert r.status_code == 400
    assert "connected" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_market_subscribe_returns_400_for_unresolvable_symbol():
    """Same fake, but via the generic provider-agnostic route rather than
    /broker/subscribe specifically — this is the one the frontend
    actually calls, and it needs to behave the same way."""
    fake = _FakeConnectedAdapter()
    await broker_registry.take_over_streaming(fake)
    try:
        with TestClient(app) as client:
            r = client.post("/market/subscribe", params={"symbol": "NOTREAL"})
        assert r.status_code == 400
        assert "NOTREAL" in r.json()["detail"]
    finally:
        broker_registry.clear_all()


@pytest.mark.asyncio
async def test_market_subscribe_succeeds_for_valid_symbol():
    class _AlwaysSucceeds(_FakeConnectedAdapter):
        async def subscribe(self, symbols: list[str]) -> None:
            pass  # no exception — a resolvable symbol

    fake = _AlwaysSucceeds()
    await broker_registry.take_over_streaming(fake)
    try:
        with TestClient(app) as client:
            r = client.post("/market/subscribe", params={"symbol": "NVDA"})
        assert r.status_code == 200
        assert r.json() == {"status": "subscribed", "symbol": "NVDA"}
    finally:
        broker_registry.clear_all()


@pytest.mark.asyncio
async def test_unexpected_exception_still_gets_cors_headers_and_clean_json():
    """
    The actual reported bug: a real, unanticipated exception from
    get_historical() (my tests only ever mocked Polygon's client, never
    hit a genuinely unexpected error type from the real API) fell through
    every try/except in the route and, per a known FastAPI/Starlette
    gotcha, bypassed CORSMiddleware entirely — the browser reported this
    as a CORS policy violation with zero useful detail, when the real
    problem was a plain unhandled exception. Proving the fix here means
    actually checking the CORS header is present, not just that a JSON
    body comes back — a naive fix could return clean JSON while still
    missing the header and not actually solve the reported symptom.
    """

    class _ThrowsSomethingUnexpected(_FakeConnectedAdapter):
        async def get_historical(self, symbol: str, timeframe: str, start, end):
            raise RuntimeError("simulated unexpected error — e.g. a real Polygon API failure")

    broker_registry.set_historical_provider(_ThrowsSomethingUnexpected())
    try:
        # raise_server_exceptions=False: TestClient's default (True) is a
        # debug convenience that re-raises exceptions in the test process
        # itself for easier debugging — bypassing any registered
        # exception handler entirely. That's TestClient-specific test
        # behavior, not how a real uvicorn server behaves (which always
        # returns whatever the middleware stack produces, handler
        # included). Disabling it here is what actually exercises the
        # handler under test, matching production behavior.
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/market/candles",
                params={"symbol": "NVDA"},
                headers={"Origin": "http://localhost:5173"},
            )
        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert "simulated unexpected error" in r.json()["detail"]
    finally:
        broker_registry.clear_all()
