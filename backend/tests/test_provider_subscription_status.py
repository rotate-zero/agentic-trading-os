"""
Tests for task `provider-subscription-diagnostics`: the optional read-only
`get_subscription_snapshot()` capability on the real Finnhub / Polygon / IBKR
adapters and the read-only `GET /market/subscription-status` route.

The adapters under test are the REAL classes. Only their transports are
replaced:
  - Finnhub: `websockets.connect` returns a recording fake socket.
  - Polygon: the REST client's `get_aggs` is a recording fake and the poll
    interval is an hour, so the poll loop never reaches the (fake) client.
  - IBKR: the `ib_async.IB` instance's connect/disconnect/isConnected/
    qualify/reqMktData/cancelMktData are replaced with recorders.
No test reaches a real provider, needs credentials, or touches the network.

What is asserted is deliberately narrow: the inventory is the adapter's
LOCAL record. Nothing here claims provider acknowledgement, delivery or
capacity — and several tests assert the route never claims them either.
"""
from __future__ import annotations

import asyncio
import json
import socket

import pytest
import websockets
from fastapi.testclient import TestClient

from app.api.routes import market
from app.broker_adapters.base import (
    MarketDataProvider,
    SubscriptionInventory,
    SymbolNotFoundError,
)
from app.broker_adapters.finnhub_provider import FinnhubAdapter
from app.broker_adapters.ibkr_adapter import IBKRAdapter
from app.broker_adapters.polygon_provider import PolygonAdapter
from app.main import app
from app.services import broker_registry

NOTE_FRAGMENTS = ("not provider acknowledgement", "capacity and delivery are unknown")


@pytest.fixture(autouse=True)
def _clean_registry():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


# --- transports ------------------------------------------------------------


class FakeFinnhubSocket:
    """Recording stand-in for the websockets client connection. Iterating it
    blocks until `drop()` is called, which then raises ConnectionClosed —
    exactly what FinnhubAdapter._listen treats as an unexpected close."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.fail_sends_for: set[str] = set()
        self._wake = asyncio.Event()

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        if message.get("symbol") in self.fail_sends_for:
            raise RuntimeError("send failed")
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    def drop(self) -> None:
        self._wake.set()

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        await self._wake.wait()
        raise websockets.ConnectionClosed(None, None)
        yield  # pragma: no cover — makes this an async generator


async def _connected_finnhub(monkeypatch) -> tuple[FinnhubAdapter, FakeFinnhubSocket]:
    sock = FakeFinnhubSocket()

    async def fake_connect(url, *args, **kwargs):
        return sock

    monkeypatch.setattr("app.broker_adapters.finnhub_provider.websockets.connect", fake_connect)
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    await adapter.connect()
    return adapter, sock


class PolygonRest:
    def __init__(self) -> None:
        self.calls = 0

    def get_aggs(self, **kwargs):
        self.calls += 1
        return []


async def _connected_polygon() -> tuple[PolygonAdapter, PolygonRest]:
    adapter = PolygonAdapter(api_key="test-key-not-real", poll_interval_seconds=3600)
    rest = PolygonRest()
    adapter._client.get_aggs = rest.get_aggs
    await adapter.connect()
    await asyncio.sleep(0)  # let the poll loop start (empty set) and park on its sleep
    return adapter, rest


class IbHarness:
    def __init__(self, adapter: IBKRAdapter) -> None:
        self.connected = False
        self.requested: list[str] = []
        self.cancelled: list[str] = []
        self.qualified: list[str] = []
        ib = adapter._ib

        async def connect_async(*args, **kwargs):
            self.connected = True

        def disconnect():
            self.connected = False

        async def qualify(*contracts):
            self.qualified.extend(c.symbol for c in contracts)
            return [None if c.symbol == "BAD" else c for c in contracts]

        ib.connectAsync = connect_async
        ib.disconnect = disconnect
        ib.isConnected = lambda: self.connected
        ib.qualifyContractsAsync = qualify
        ib.reqMktData = lambda contract, *a, **k: self.requested.append(contract.symbol)
        ib.cancelMktData = lambda contract: self.cancelled.append(contract.symbol)


async def _connected_ibkr() -> tuple[IBKRAdapter, IbHarness]:
    adapter = IBKRAdapter(host="127.0.0.1", port=4002, client_id=77)
    harness = IbHarness(adapter)
    await adapter.connect()
    return adapter, harness


def _status() -> dict:
    # No lifespan: the route is a pure registry read and needs none.
    response = TestClient(app).get("/market/subscription-status")
    assert response.status_code == 200
    return response.json()


# --- adapters: subscription changes ----------------------------------------


async def test_finnhub_snapshot_tracks_changes_sorted_and_sends_nothing_itself(monkeypatch):
    adapter, sock = await _connected_finnhub(monkeypatch)
    assert adapter.get_subscription_snapshot() == ()
    assert isinstance(adapter, SubscriptionInventory)

    await adapter.subscribe(["MSFT", "AAPL"])
    await adapter.subscribe(["AAPL", "NVDA"])  # AAPL duplicate: existing behavior is no second send
    assert adapter.get_subscription_snapshot() == ("AAPL", "MSFT", "NVDA")
    assert [m["symbol"] for m in sock.sent] == ["MSFT", "AAPL", "NVDA"]

    await adapter.unsubscribe(["MSFT", "NOT-HELD"])  # unknown symbol: existing behavior is no send
    assert adapter.get_subscription_snapshot() == ("AAPL", "NVDA")
    assert sock.sent[-1] == {"type": "unsubscribe", "symbol": "MSFT"}

    sent_before = list(sock.sent)
    for _ in range(5):
        adapter.get_subscription_snapshot()
    assert sock.sent == sent_before  # reading is not a transport action
    await adapter.disconnect()


async def test_finnhub_failed_send_is_not_recorded(monkeypatch):
    adapter, sock = await _connected_finnhub(monkeypatch)
    sock.fail_sends_for = {"BBB"}
    with pytest.raises(RuntimeError):
        await adapter.subscribe(["AAA", "BBB", "CCC"])
    # AAA was sent and recorded; BBB failed and was not; CCC was never attempted.
    assert adapter.get_subscription_snapshot() == ("AAA",)
    await adapter.disconnect()


async def test_polygon_snapshot_tracks_changes_and_never_polls_on_read():
    adapter, rest = await _connected_polygon()
    assert isinstance(adapter, SubscriptionInventory)
    assert adapter.get_subscription_snapshot() == ()

    await adapter.subscribe(["TSLA", "AMD", "TSLA"])
    assert adapter.get_subscription_snapshot() == ("AMD", "TSLA")
    adapter._last_bar_ts["AMD"] = 1
    await adapter.unsubscribe(["AMD", "UNKNOWN"])
    assert adapter.get_subscription_snapshot() == ("TSLA",)
    assert "AMD" not in adapter._last_bar_ts  # existing unsubscribe behavior preserved

    for _ in range(5):
        adapter.get_subscription_snapshot()
    assert rest.calls == 0
    await adapter.disconnect()


async def test_ibkr_snapshot_tracks_changes_and_failed_qualification_is_not_recorded():
    adapter, ib = await _connected_ibkr()
    assert isinstance(adapter, SubscriptionInventory)
    assert adapter.get_subscription_snapshot() == ()

    await adapter.subscribe(["QQQ", "AAPL"])
    await adapter.subscribe(["AAPL"])  # duplicate: existing behavior is no second request
    assert adapter.get_subscription_snapshot() == ("AAPL", "QQQ")
    assert ib.requested == ["QQQ", "AAPL"]

    with pytest.raises(SymbolNotFoundError):
        await adapter.subscribe(["BAD"])
    assert adapter.get_subscription_snapshot() == ("AAPL", "QQQ")
    assert "BAD" not in ib.requested

    await adapter.unsubscribe(["QQQ", "NOT-HELD"])
    assert adapter.get_subscription_snapshot() == ("AAPL",)
    assert ib.cancelled == ["QQQ"]

    requested, cancelled, qualified = list(ib.requested), list(ib.cancelled), list(ib.qualified)
    for _ in range(5):
        adapter.get_subscription_snapshot()
    assert (ib.requested, ib.cancelled, ib.qualified) == (requested, cancelled, qualified)
    await adapter.disconnect()


# --- adapters: copies --------------------------------------------------------


async def test_snapshots_are_independent_immutable_copies(monkeypatch):
    finnhub, _ = await _connected_finnhub(monkeypatch)
    polygon, _rest = await _connected_polygon()
    ibkr, _ib = await _connected_ibkr()

    for adapter, internal in (
        (finnhub, finnhub._symbols),
        (polygon, polygon._symbols),
        (ibkr, ibkr._contracts),
    ):
        await adapter.subscribe(["BBB", "AAA"])
        snap = adapter.get_subscription_snapshot()
        assert isinstance(snap, tuple)
        assert snap is not internal
        assert snap == ("AAA", "BBB")
        with pytest.raises(AttributeError):
            snap.append("ZZZ")  # type: ignore[attr-defined]

        await adapter.subscribe(["CCC"])
        assert snap == ("AAA", "BBB")  # an earlier snapshot never changes
        assert adapter.get_subscription_snapshot() == ("AAA", "BBB", "CCC")

        list(snap)  # consumer-side copies/mutation leave the adapter's own state alone
        sorted_copy = sorted(snap)
        sorted_copy.append("ZZZ")
        assert adapter.get_subscription_snapshot() == ("AAA", "BBB", "CCC")

    for adapter in (finnhub, polygon, ibkr):
        await adapter.disconnect()


# --- adapters + route: disconnect semantics ---------------------------------


async def test_finnhub_disconnect_clears_old_session_record_and_route_reports_unavailable(monkeypatch):
    adapter, _sock = await _connected_finnhub(monkeypatch)
    await adapter.subscribe(["AAPL", "MSFT"])
    await broker_registry.take_over_streaming(adapter)
    assert _status()["inventory"]["symbols"] == ["AAPL", "MSFT"]

    await adapter.disconnect()
    # The old socket's requests cannot suppress requests on a later socket.
    assert adapter.get_subscription_snapshot() == ()
    body = _status()
    assert body["status"] == "available"
    assert body["connected"] is False
    assert body["inventory"]["availability"] == "unavailable"
    assert body["inventory"]["reason"] == "provider_not_connected"
    assert body["inventory"]["count"] is None and body["inventory"]["symbols"] is None


async def test_finnhub_unexpected_socket_close_reports_unavailable(monkeypatch):
    adapter, sock = await _connected_finnhub(monkeypatch)
    await adapter.subscribe(["AAPL"])
    await broker_registry.take_over_streaming(adapter)
    assert _status()["inventory"]["availability"] == "available"

    sock.drop()
    await asyncio.sleep(0.05)  # let _listen observe the close
    assert adapter.is_connected() is False
    assert adapter.get_subscription_snapshot() == ()
    body = _status()
    assert body["connected"] is False
    assert body["inventory"]["availability"] == "unavailable"
    assert body["inventory"]["reason"] == "provider_not_connected"
    await adapter.disconnect()


async def test_polygon_and_ibkr_disconnect_report_unavailable_not_empty():
    polygon, _rest = await _connected_polygon()
    await polygon.subscribe(["AAPL"])
    await broker_registry.take_over_streaming(polygon)
    assert _status()["inventory"]["symbols"] == ["AAPL"]
    await polygon.disconnect()
    body = _status()
    assert polygon.get_subscription_snapshot() == ("AAPL",)
    assert body["inventory"]["availability"] == "unavailable"
    assert body["inventory"]["symbols"] is None  # never []

    ibkr, ib = await _connected_ibkr()
    await ibkr.subscribe(["MSFT"])
    await broker_registry.take_over_streaming(ibkr)
    body = _status()
    assert body["provider"] == {"id": "ibkr", "class_name": "IBKRAdapter"}
    assert body["inventory"]["symbols"] == ["MSFT"]
    await ibkr.disconnect()
    assert ibkr.get_subscription_snapshot() == ()  # old IB session is gone
    body = _status()
    assert body["connected"] is False
    assert body["inventory"]["availability"] == "unavailable"


async def test_same_instance_reconnect_can_request_finnhub_and_ibkr_again(monkeypatch):
    finnhub, old_socket = await _connected_finnhub(monkeypatch)
    await finnhub.subscribe(["AAPL"])
    await finnhub.disconnect()
    assert finnhub.get_subscription_snapshot() == ()
    new_socket = FakeFinnhubSocket()

    async def reconnect_socket(*args, **kwargs):
        return new_socket

    monkeypatch.setattr("app.broker_adapters.finnhub_provider.websockets.connect", reconnect_socket)
    await finnhub.connect()
    await finnhub.subscribe(["AAPL"])
    assert old_socket.sent == [{"type": "subscribe", "symbol": "AAPL"}]
    assert new_socket.sent == [{"type": "subscribe", "symbol": "AAPL"}]
    await finnhub.disconnect()

    ibkr, harness = await _connected_ibkr()
    await ibkr.subscribe(["MSFT"])
    await ibkr.disconnect()
    assert ibkr.get_subscription_snapshot() == ()
    await ibkr.connect()
    await ibkr.subscribe(["MSFT"])
    assert harness.requested == ["MSFT", "MSFT"]
    await ibkr.disconnect()


async def test_connected_provider_with_no_subscriptions_is_a_genuine_empty_inventory(monkeypatch):
    adapter, _sock = await _connected_finnhub(monkeypatch)
    await broker_registry.take_over_streaming(adapter)
    body = _status()
    assert body["inventory"] == {
        "availability": "available",
        "reason": None,
        "basis": "locally_tracked_requests",
        "count": 0,
        "symbols": [],
    }
    await adapter.disconnect()


# --- provider replacement ----------------------------------------------------


async def test_provider_replacement_reports_the_new_provider_only(monkeypatch):
    finnhub, sock = await _connected_finnhub(monkeypatch)
    await finnhub.subscribe(["AAPL"])
    await broker_registry.take_over_streaming(finnhub)
    assert _status()["provider"]["id"] == "finnhub"

    polygon, _rest = await _connected_polygon()
    await polygon.subscribe(["MSFT"])
    await broker_registry.take_over_streaming(polygon)  # existing behavior: retires Finnhub

    body = _status()
    assert body["provider"] == {"id": "polygon", "class_name": "PolygonAdapter"}
    assert body["inventory"]["symbols"] == ["MSFT"]
    assert sock.closed is True and finnhub.is_connected() is False
    await polygon.disconnect()


async def test_replacing_a_provider_that_stays_connected_for_history_does_not_leak_its_inventory(monkeypatch):
    ibkr, ib = await _connected_ibkr()
    await ibkr.subscribe(["IBKRONLY"])
    await broker_registry.take_over_streaming(ibkr)
    broker_registry.set_historical_provider(ibkr)

    finnhub, _sock = await _connected_finnhub(monkeypatch)
    await finnhub.subscribe(["FHONLY"])
    await broker_registry.take_over_streaming(finnhub)

    assert ibkr.is_connected() is True  # kept alive for the historical role (existing behavior)
    assert ib.cancelled == []  # replacement and reads cancelled nothing at IBKR
    body = _status()
    assert body["provider"]["id"] == "finnhub"
    assert body["inventory"]["symbols"] == ["FHONLY"]
    await finnhub.disconnect()
    await ibkr.disconnect()


async def test_replacement_by_unsupported_provider_and_clearing(monkeypatch):
    finnhub, _sock = await _connected_finnhub(monkeypatch)
    await finnhub.subscribe(["AAPL"])
    await broker_registry.take_over_streaming(finnhub)

    class Plain:
        def is_connected(self) -> bool:
            return True

        async def disconnect(self) -> None:
            pass

    await broker_registry.take_over_streaming(Plain())  # type: ignore[arg-type]
    body = _status()
    assert body["provider"] == {"id": "unknown", "class_name": "Plain"}
    assert body["inventory"]["reason"] == "inventory_not_supported"

    broker_registry.clear_streaming_provider()
    body = _status()
    assert body["status"] == "unavailable" and body["reason"] == "no_streaming_provider"


# --- unsupported / missing / misbehaving providers ---------------------------


def test_no_streaming_provider_is_an_explicit_unavailable_state():
    body = _status()
    assert body["status"] == "unavailable"
    assert body["reason"] == "no_streaming_provider"
    assert body["provider"] is None and body["connected"] is None
    assert body["inventory"]["availability"] == "unavailable"
    assert body["inventory"]["reason"] == "no_streaming_provider"
    assert body["inventory"]["count"] is None and body["inventory"]["symbols"] is None
    assert body["capacity"] == {"status": "unknown", "limit": None}
    assert body["delivery"] == {"status": "unknown"}


def test_no_streaming_provider_under_the_real_lifespan():
    # conftest blanks the API keys, so the real startup registers no provider.
    with TestClient(app) as client:
        body = client.get("/market/subscription-status").json()
    assert body["status"] == "unavailable"
    assert body["inventory"]["symbols"] is None


async def test_existing_double_without_the_capability_is_unavailable_not_empty():
    class _FakeConnectedAdapter:  # same shape as test_market_routes.py's double
        def is_connected(self) -> bool:
            return True

        async def disconnect(self) -> None:
            pass

        async def subscribe(self, symbols):
            raise SymbolNotFoundError(symbols[0])

    await broker_registry.take_over_streaming(_FakeConnectedAdapter())  # type: ignore[arg-type]
    body = _status()
    assert body["status"] == "available" and body["connected"] is True
    assert body["provider"] == {"id": "unknown", "class_name": "_FakeConnectedAdapter"}
    assert body["inventory"]["availability"] == "unavailable"
    assert body["inventory"]["reason"] == "inventory_not_supported"
    assert body["inventory"]["symbols"] is None  # not []


async def test_market_data_provider_subclass_without_the_capability_still_works():
    class Minimal(MarketDataProvider):
        async def connect(self): ...
        async def disconnect(self): ...
        def is_connected(self): return True
        async def subscribe(self, symbols): ...
        async def unsubscribe(self, symbols): ...
        async def get_historical(self, symbol, timeframe, start, end): return []
        def on_tick(self, callback): ...

    provider = Minimal()  # constructible: the capability is not abstract
    assert not isinstance(provider, SubscriptionInventory)
    await broker_registry.take_over_streaming(provider)
    assert _status()["inventory"]["reason"] == "inventory_not_supported"


@pytest.mark.parametrize(
    "returned",
    [None, "AAPL", [1, 2], ["AAPL", None], {"AAPL": 1}, object()],
)
async def test_invalid_snapshot_values_are_unavailable(returned):
    class Odd:
        def is_connected(self) -> bool:
            return True

        async def disconnect(self) -> None:
            pass

        def get_subscription_snapshot(self):
            return returned

    await broker_registry.take_over_streaming(Odd())  # type: ignore[arg-type]
    body = _status()
    assert body["inventory"]["availability"] == "unavailable"
    assert body["inventory"]["reason"] == "snapshot_failed"


async def test_raising_snapshot_and_raising_is_connected_do_not_fail_the_route():
    class RaisingSnapshot:
        def is_connected(self) -> bool:
            return True

        async def disconnect(self) -> None:
            pass

        def get_subscription_snapshot(self):
            raise RuntimeError("boom")

    await broker_registry.take_over_streaming(RaisingSnapshot())  # type: ignore[arg-type]
    assert _status()["inventory"]["reason"] == "snapshot_failed"

    class RaisingConnected:
        def is_connected(self) -> bool:
            raise RuntimeError("boom")

        async def disconnect(self) -> None:
            pass

    await broker_registry.take_over_streaming(RaisingConnected())  # type: ignore[arg-type]
    body = _status()
    assert body["connected"] is None
    assert body["inventory"]["reason"] == "connection_state_unknown"


async def test_route_copies_and_orders_whatever_the_adapter_returns():
    internal = ["ZZZ", "AAA", "MMM"]

    class Leaky:
        def is_connected(self) -> bool:
            return True

        async def disconnect(self) -> None:
            pass

        def get_subscription_snapshot(self):
            return internal  # a (non-conforming) adapter handing out its own list

    await broker_registry.take_over_streaming(Leaky())  # type: ignore[arg-type]
    body = await market.get_subscription_status()
    assert body["inventory"]["symbols"] == ["AAA", "MMM", "ZZZ"]
    assert body["inventory"]["symbols"] is not internal
    body["inventory"]["symbols"].append("MUTATED")
    assert internal == ["ZZZ", "AAA", "MMM"]  # untouched and not re-sorted in place


# --- ordering, labelling, capacity ------------------------------------------


async def test_response_order_is_deterministic_and_repeatable(monkeypatch):
    adapter, _sock = await _connected_finnhub(monkeypatch)
    await adapter.subscribe(["NVDA", "AAPL", "TSLA", "MSFT"])
    await broker_registry.take_over_streaming(adapter)
    first, second = _status(), _status()
    assert first == second
    assert first["inventory"]["symbols"] == ["AAPL", "MSFT", "NVDA", "TSLA"]
    assert first["inventory"]["count"] == 4
    await adapter.disconnect()


async def test_every_response_labels_the_inventory_and_never_claims_capacity_or_delivery(monkeypatch):
    adapter, _sock = await _connected_finnhub(monkeypatch)
    many = [f"S{i:03d}" for i in range(120)]
    await adapter.subscribe(many)
    await broker_registry.take_over_streaming(adapter)

    bodies = [_status()]
    broker_registry.clear_streaming_provider()
    bodies.append(_status())
    for body in bodies:
        assert body["inventory"]["basis"] == "locally_tracked_requests"
        assert body["capacity"] == {"status": "unknown", "limit": None}
        assert body["delivery"] == {"status": "unknown"}
        for fragment in NOTE_FRAGMENTS:
            assert fragment in body["note"]
    assert bodies[0]["inventory"]["count"] == 120  # many symbols never imply a limit was hit or exists
    await adapter.disconnect()


# --- no network, no provider construction, no state mutation ----------------


async def test_reads_construct_connect_and_mutate_nothing(monkeypatch):
    finnhub, sock = await _connected_finnhub(monkeypatch)
    await finnhub.subscribe(["AAPL", "MSFT"])
    await broker_registry.take_over_streaming(finnhub)
    broker_registry.set_historical_provider(finnhub)

    forbidden: list[str] = []

    def forbid(name):
        def _raise(*args, **kwargs):
            forbidden.append(name)
            raise AssertionError(f"{name} must not be called by a diagnostic read")
        return _raise

    sent_before = list(sock.sent)
    symbols_before = set(finnhub._symbols)
    # Scoped, so fixture teardown (which legitimately clears the registry)
    # sees the real functions again.
    with monkeypatch.context() as guard:
        for cls in (FinnhubAdapter, PolygonAdapter, IBKRAdapter):
            guard.setattr(cls, "__init__", forbid(f"{cls.__name__}()"))
        for name in ("connect", "disconnect", "subscribe", "unsubscribe"):
            guard.setattr(finnhub, name, forbid(f"adapter.{name}"))
        for name in (
            "take_over_streaming",
            "set_historical_provider",
            "clear_streaming_provider",
            "clear_historical_provider",
        ):
            guard.setattr(broker_registry, name, forbid(f"broker_registry.{name}"))
        guard.setattr(socket.socket, "connect", forbid("socket.connect"))
        guard.setattr(socket.socket, "connect_ex", forbid("socket.connect_ex"))
        guard.setattr(socket, "create_connection", forbid("socket.create_connection"))

        for _ in range(3):
            body = _status()

    assert body["inventory"]["symbols"] == ["AAPL", "MSFT"]
    assert forbidden == []
    assert sock.sent == sent_before and sock.closed is False
    assert finnhub._symbols == symbols_before
    assert broker_registry.get_streaming_provider() is finnhub
    assert broker_registry.get_historical_provider() is finnhub
    assert finnhub.is_connected() is True
    await finnhub.disconnect()


async def test_no_provider_read_constructs_and_connects_nothing(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not be called by a diagnostic read")

    with monkeypatch.context() as guard:
        for cls in (FinnhubAdapter, PolygonAdapter, IBKRAdapter):
            guard.setattr(cls, "__init__", boom)
        guard.setattr(socket.socket, "connect", boom)
        guard.setattr(socket, "create_connection", boom)
        guard.setattr(broker_registry, "take_over_streaming", boom)
        body = _status()
    assert body["status"] == "unavailable"
    assert broker_registry.get_streaming_provider() is None
    assert broker_registry.get_historical_provider() is None


# --- neighbours --------------------------------------------------------------


def test_route_table_adds_one_get_and_keeps_existing_market_routes():
    methods: dict[str, set[str]] = {}
    for route in app.routes:
        if getattr(route, "path", "").startswith("/market/"):
            methods.setdefault(route.path, set()).update(route.methods or set())
    assert methods["/market/subscription-status"] == {"GET"}
    assert "GET" in methods["/market/feed-status"]
    assert "POST" in methods["/market/subscribe"]
    assert "POST" in methods["/market/active-symbols"] and "GET" in methods["/market/active-symbols"]
    assert "GET" in methods["/market/candles"]
