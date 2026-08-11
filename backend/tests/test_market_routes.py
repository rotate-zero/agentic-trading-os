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
        r = client.get("/market/candles", params={"symbol": "__TEST_NOHISTORY__"})
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


def test_market_candles_rejects_4h_cleanly():
    """4h is deliberately unsupported (confirmed-decisions.md) — this must
    be a clean 400 like any other unsupported timeframe, not the unhandled
    500 that used to happen when this reached polygon_provider.py's
    _polygon_params_for() with no "4h" entry in its mapping."""
    broker_registry.set_historical_provider(_FakeConnectedAdapter())
    try:
        with TestClient(app) as client:
            r = client.get("/market/candles", params={"symbol": "NVDA", "timeframe": "4h"})
        assert r.status_code == 400
        assert "4h" in r.json()["detail"]
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
                params={"symbol": "__TEST_UNEXPECTED__"},
                headers={"Origin": "http://localhost:5173"},
            )
        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert "simulated unexpected error" in r.json()["detail"]
    finally:
        broker_registry.clear_all()


def _db_available() -> bool:
    from sqlalchemy import text

    from app.db.session import SessionLocal

    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — this IS the availability check
        return False


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
def test_market_candles_serves_self_recorded_data_with_no_provider_connected():
    """
    The actual feature this file's other tests didn't cover before now:
    confirmed decision #42's CandleRecorder means /market/candles no
    longer depends on an external provider AT ALL for a symbol this app
    has already recorded 1m candles for — proven here through the real
    route, with broker_registry deliberately empty, not just at the
    candle_store function level (see test_candle_recorder.py for that).
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.event_bus.bus import EventBus
    from app.event_bus.events import make_envelope
    from app.schemas.events.envelope import EventType
    from app.schemas.events.market_data import CandleClosed
    from app.services.candle_recorder import CandleRecorder

    ticker = "ZZTESTREC01"
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"), {"t": ticker})
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()

    broker_registry.clear_all()  # no provider connected at all — this must still work

    async def _seed():
        bus = EventBus()
        await bus.start()
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            candle_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=1)
            await bus.publish(
                make_envelope(
                    EventType.CANDLE_CLOSED,
                    CandleClosed(timeframe="1m", open=10.0, high=11.0, low=9.0, close=10.5, volume=100, candle_ts=candle_ts),
                    symbol=ticker,
                )
            )
            import asyncio

            await asyncio.sleep(0.3)
        finally:
            recorder.stop()
            await bus.stop()

    import asyncio

    asyncio.run(_seed())

    try:
        with TestClient(app) as client:
            r = client.get("/market/candles", params={"symbol": ticker, "count": 5, "timeframe": "1m"})
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == ticker
        assert len(body["candles"]) == 1
        assert body["candles"][0]["open"] == 10.0
        assert body["candles"][0]["close"] == 10.5
    finally:
        session = SessionLocal()
        try:
            session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"), {"t": ticker})
            session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
            session.commit()
        finally:
            session.close()
        broker_registry.clear_all()


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
def test_market_candles_serves_aggregated_5m_from_self_recorded_1m_with_no_provider_connected():
    """
    The actual feature this delivery adds: /market/candles?timeframe=5m
    must work off real self-recorded 1m rows via candle_aggregator, with
    zero provider connected — same "self-recorded is enough on its own"
    guarantee as the 1m case above, extended to a timeframe the recorder
    never writes directly.

    Session-bucket-boundary correctness itself is already covered
    exhaustively in test_candle_aggregator.py against controlled synthetic
    timestamps; MarketClock.session_bounds is faked out here specifically
    so THIS test — which seeds real "now"-based timestamps so they land
    inside the route's real DB query window — isn't also at the mercy of
    whether it happens to run during actual market hours.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.event_bus.bus import EventBus
    from app.event_bus.events import make_envelope
    from app.schemas.events.envelope import EventType
    from app.schemas.events.market_data import CandleClosed
    from app.services import candle_aggregator
    from app.services.candle_recorder import CandleRecorder

    ticker = "ZZTESTAGG01"
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"), {"t": ticker})
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()

    broker_registry.clear_all()  # no provider connected — aggregation alone must be enough

    # Five consecutive real minutes ending "now" — guaranteed to land
    # inside the route's own [start, end] query window (computed from real
    # wall-clock time), regardless of what time this test happens to run.
    now_floor = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candle_times = [now_floor - timedelta(minutes=(4 - i)) for i in range(5)]

    class _FakeOneBigSessionClock:
        """Forces all five seeded timestamps into exactly one 5-minute
        bucket by anchoring the fake session to the first candle's own
        timestamp — sidesteps needing this test to run during a real
        session at all."""

        def session_bounds(self, ts):
            return candle_times[0], candle_times[0] + timedelta(hours=1)

    async def _seed():
        bus = EventBus()
        await bus.start()
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            for i, ts in enumerate(candle_times):
                await bus.publish(
                    make_envelope(
                        EventType.CANDLE_CLOSED,
                        CandleClosed(
                            timeframe="1m",
                            open=10.0 + i,
                            high=10.5 + i,
                            low=9.5 + i,
                            close=10.2 + i,
                            volume=100,
                            candle_ts=ts,
                        ),
                        symbol=ticker,
                    )
                )
            import asyncio

            await asyncio.sleep(0.3)
        finally:
            recorder.stop()
            await bus.stop()

    import asyncio

    asyncio.run(_seed())

    real_get_market_clock = candle_aggregator.get_market_clock
    candle_aggregator.get_market_clock = lambda: _FakeOneBigSessionClock()
    try:
        with TestClient(app) as client:
            r = client.get("/market/candles", params={"symbol": ticker, "count": 5, "timeframe": "5m"})
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == ticker
        assert len(body["candles"]) == 1
        bar = body["candles"][0]
        assert bar["open"] == 10.0  # first 1m candle's open
        assert bar["close"] == 14.2  # last 1m candle's close (10.2 + 4)
        assert bar["high"] == 14.5  # max across all five, not just first/last
        assert bar["low"] == 9.5  # min across all five
        assert bar["volume"] == 500
    finally:
        candle_aggregator.get_market_clock = real_get_market_clock
        session = SessionLocal()
        try:
            session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"), {"t": ticker})
            session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
            session.commit()
        finally:
            session.close()
        broker_registry.clear_all()
