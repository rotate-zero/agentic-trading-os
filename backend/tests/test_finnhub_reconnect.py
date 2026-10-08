"""Controlled transport regressions for the Finnhub connection owner."""
import asyncio
import json

import pytest
import websockets
from fastapi import HTTPException

from app.api.routes import backtest, finnhub_data, market
from app.backtest_runner.engine_singleton_guard import finnhub_connection_slot, install_replay_engines
from app.broker_adapters.finnhub_provider import FinnhubAdapter
from app.services import broker_registry


class Socket:
    def __init__(self, *, fail_symbols=()):
        self.sent = []
        self.closed = False
        self.fail_symbols = set(fail_symbols)
        self.messages = asyncio.Queue()

    async def send(self, raw):
        message = json.loads(raw)
        if message["symbol"] in self.fail_symbols:
            raise RuntimeError("send failed")
        self.sent.append(message)

    async def close(self):
        self.closed = True

    def drop(self, clean=False):
        self.messages.put_nowait(None if clean else websockets.ConnectionClosed(None, None))

    def trade(self, symbol="AAPL"):
        self.messages.put_nowait(json.dumps({"type": "trade", "data": [
            {"s": symbol, "p": 1.0, "v": 1, "t": 1735689600000}
        ]}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.messages.get()
        if message is None:
            raise StopAsyncIteration
        if isinstance(message, Exception):
            raise message
        return message


class Transport:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def connect(self, url):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Wait:
    def __init__(self):
        self.delays = []
        self.queue = asyncio.Queue()

    async def __call__(self, delay):
        self.delays.append(delay)
        await self.queue.get()

    def release(self):
        self.queue.put_nowait(None)


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


@pytest.mark.asyncio
async def test_remote_closure_retries_and_restores_without_duplicate_callbacks():
    first, second = Socket(), Socket()
    wait = Wait()
    transport = Transport(first, ConnectionError("down"), second)
    adapter = FinnhubAdapter(api_key="fake", connect_ws=transport.connect,
                             retry_wait=wait, retry_initial_seconds=1,
                             retry_max_seconds=2)
    received = []
    adapter.on_tick(received.append)
    await adapter.connect()
    await adapter.subscribe(["AAPL", "MSFT"])
    first.drop()
    await until(lambda: len(wait.delays) == 1)
    assert adapter.is_connected() is False
    assert adapter.get_subscription_snapshot() == ()
    assert adapter.is_streaming_active()
    wait.release()
    await until(lambda: len(wait.delays) == 2)
    assert wait.delays == [1, 2]
    wait.release()
    await until(lambda: adapter.is_connected())
    assert adapter.get_subscription_snapshot() == ("AAPL", "MSFT")
    assert second.sent == first.sent
    second.trade()
    await until(lambda: len(received) == 1)
    first.trade()  # an obsolete socket cannot publish
    await asyncio.sleep(0)
    assert len(received) == 1
    await adapter.disconnect()
    assert not adapter.is_streaming_active()


@pytest.mark.asyncio
async def test_clean_close_unsubscribe_during_outage_and_manual_stop():
    first, second = Socket(), Socket()
    wait = Wait()
    adapter = FinnhubAdapter(api_key="fake", connect_ws=Transport(first, second).connect,
                             retry_wait=wait, retry_initial_seconds=1,
                             retry_max_seconds=2)
    await adapter.connect()
    await adapter.subscribe(["AAPL", "MSFT"])
    first.drop(clean=True)
    await until(lambda: len(wait.delays) == 1)
    await adapter.unsubscribe(["MSFT"])
    wait.release()
    await until(lambda: adapter.is_connected())
    assert second.sent == [{"type": "subscribe", "symbol": "AAPL"}]
    await adapter.disconnect()
    assert adapter.get_subscription_snapshot() == ()


@pytest.mark.asyncio
async def test_initial_failure_visible_and_does_not_retry():
    wait = Wait()
    transport = Transport(ConnectionError("wss://ws.finnhub.io?token=secret"))
    adapter = FinnhubAdapter(api_key="fake", connect_ws=transport.connect,
                             retry_wait=wait, retry_initial_seconds=1)
    with pytest.raises(ConnectionError) as exc:
        await adapter.connect()
    assert "secret" not in str(exc.value)
    assert transport.calls == 1 and wait.delays == []
    assert not adapter.is_streaming_active()


@pytest.mark.parametrize("initial,maximum", [(0, 1), (-1, 1), (2, 1), (float("inf"), float("inf"))])
def test_retry_delay_configuration_rejects_invalid_bounds(initial, maximum):
    with pytest.raises(ValueError):
        FinnhubAdapter(api_key="fake", retry_initial_seconds=initial,
                       retry_max_seconds=maximum)


@pytest.mark.asyncio
async def test_retry_delay_caps_after_repeated_failures():
    socket = Socket()
    wait = Wait()
    adapter = FinnhubAdapter(api_key="fake", connect_ws=Transport(
        socket, ConnectionError("one"), ConnectionError("two"), ConnectionError("three")
    ).connect, retry_wait=wait, retry_initial_seconds=1, retry_max_seconds=2)
    await adapter.connect()
    socket.drop()
    for attempt in range(3):
        await until(lambda: len(wait.delays) == attempt + 1)
        wait.release()
    await until(lambda: len(wait.delays) == 4)
    assert wait.delays == [1, 2, 2, 2]
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_partial_restore_does_not_claim_complete_inventory():
    first, partial, restored = Socket(), Socket(fail_symbols={"MSFT"}), Socket()
    wait = Wait()
    adapter = FinnhubAdapter(api_key="fake", connect_ws=Transport(first, partial, restored).connect,
                             retry_wait=wait, retry_initial_seconds=1)
    await adapter.connect()
    await adapter.subscribe(["AAPL", "MSFT"])
    first.drop()
    await until(lambda: len(wait.delays) == 1)
    wait.release()
    await until(lambda: len(wait.delays) == 2)
    assert partial.closed and not adapter.is_connected()
    assert adapter.get_subscription_snapshot() == ()
    wait.release()
    await until(lambda: adapter.is_connected())
    assert adapter.get_subscription_snapshot() == ("AAPL", "MSFT")
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_subscription_status_exposes_current_requests_only():
    first, second = Socket(), Socket()
    wait = Wait()
    adapter = FinnhubAdapter(api_key="fake", connect_ws=Transport(first, second).connect,
                             retry_wait=wait, retry_initial_seconds=1)
    await adapter.connect()
    await broker_registry.take_over_streaming(adapter)
    await adapter.subscribe(["AAPL"])
    first.drop()
    await until(lambda: len(wait.delays) == 1)
    body = await market.get_subscription_status()
    assert body["inventory"]["availability"] == "unavailable"
    assert body["inventory"]["symbols"] is None
    wait.release()
    await until(lambda: adapter.is_connected())
    body = await market.get_subscription_status()
    assert body["inventory"]["symbols"] == ["AAPL"]
    assert body["capacity"] == {"status": "unknown", "limit": None}
    assert body["delivery"] == {"status": "unknown"}
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_replay_rejects_reconnect_owner_until_manual_disconnect():
    first = Socket()
    wait = Wait()
    adapter = FinnhubAdapter(api_key="fake", connect_ws=Transport(first).connect,
                             retry_wait=wait, retry_initial_seconds=1)
    await adapter.connect()
    finnhub_data._provider = adapter
    await broker_registry.take_over_streaming(adapter)
    first.drop()
    await until(lambda: len(wait.delays) == 1)
    with pytest.raises(HTTPException) as exc:
        backtest._reject_if_live_data_connected()
    assert exc.value.status_code == 409
    await finnhub_data.disconnect()
    backtest._reject_if_live_data_connected()


@pytest.mark.asyncio
async def test_repeated_route_connect_and_takeover_retire_one_owner(monkeypatch):
    first, second = Socket(), Socket()
    wait = Wait()
    transport = Transport(first, second)
    adapters = []

    def build():
        adapter = FinnhubAdapter(api_key="fake", connect_ws=transport.connect,
                                 retry_wait=wait, retry_initial_seconds=1)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(finnhub_data, "FinnhubAdapter", build)
    owner = await finnhub_data.connect_finnhub()
    assert await finnhub_data.connect_finnhub() is owner
    assert len(adapters) == 1 and len(owner._tick_callbacks) == 1
    first.drop()
    await until(lambda: len(wait.delays) == 1)
    assert await finnhub_data.connect_finnhub() is owner
    assert (await finnhub_data.connect()) == {"status": "reconnecting"}
    assert len(adapters) == 1 and len(owner._tick_callbacks) == 1

    class Replacement:
        async def disconnect(self):
            pass

    replacement = Replacement()
    await broker_registry.take_over_streaming(replacement)
    assert first.closed and not owner.is_streaming_active()
    wait.release()
    await asyncio.sleep(0)
    assert transport.calls == 1  # retired owner cannot use the second socket
    assert broker_registry.get_streaming_provider() is replacement
    next_owner = await finnhub_data.connect_finnhub()
    assert next_owner is not owner and broker_registry.get_streaming_provider() is next_owner
    assert transport.calls == 2 and len(next_owner._tick_callbacks) == 1
    await finnhub_data.disconnect()
    assert broker_registry.get_streaming_provider() is None


@pytest.mark.asyncio
async def test_disconnect_during_backoff_settles_and_does_not_reopen():
    first, second = Socket(), Socket()
    wait = Wait()
    transport = Transport(first, second)
    adapter = FinnhubAdapter(api_key="fake", connect_ws=transport.connect,
                             retry_wait=wait, retry_initial_seconds=1)
    await adapter.connect()
    first.drop(clean=True)
    await until(lambda: len(wait.delays) == 1)
    await adapter.disconnect()
    wait.release()
    await asyncio.sleep(0)
    assert transport.calls == 1 and adapter._listen_task is None
    assert not adapter.is_streaming_active()


@pytest.mark.asyncio
async def test_disconnect_during_restore_cancels_pending_send():
    first = Socket()
    sending = asyncio.Event()
    release = asyncio.Event()

    class SlowSocket(Socket):
        async def send(self, raw):
            sending.set()
            await release.wait()
            await super().send(raw)

    second = SlowSocket()
    wait = Wait()
    adapter = FinnhubAdapter(api_key="fake", connect_ws=Transport(first, second).connect,
                             retry_wait=wait, retry_initial_seconds=1)
    await adapter.connect()
    await adapter.subscribe(["AAPL"])
    first.drop()
    await until(lambda: len(wait.delays) == 1)
    wait.release()
    await sending.wait()
    await adapter.disconnect()
    release.set()
    assert second.closed and adapter._listen_task is None
    assert not adapter.is_streaming_active() and adapter.get_subscription_snapshot() == ()


@pytest.mark.asyncio
async def test_initial_connect_in_flight_excludes_replay_and_disconnect_settles():
    started = asyncio.Event()

    async def connect(_url):
        started.set()
        await asyncio.Event().wait()

    adapter = FinnhubAdapter(api_key="fake", connect_ws=connect)
    finnhub_data._provider = adapter
    task = asyncio.create_task(adapter.connect())
    await started.wait()
    with pytest.raises(HTTPException) as exc:
        backtest._reject_if_live_data_connected()
    assert exc.value.status_code == 409
    await adapter.disconnect()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not adapter.is_streaming_active()
    backtest._reject_if_live_data_connected()


@pytest.mark.asyncio
async def test_route_disconnect_cancels_initial_connect(monkeypatch):
    started = asyncio.Event()

    async def pending(_url):
        started.set()
        await asyncio.Event().wait()

    adapter = FinnhubAdapter(api_key="fake", connect_ws=pending)
    monkeypatch.setattr(finnhub_data, "FinnhubAdapter", lambda: adapter)
    task = asyncio.create_task(finnhub_data.connect_finnhub())
    await started.wait()
    assert finnhub_data.is_streaming_active()
    assert (await finnhub_data.disconnect()) == {"status": "disconnected"}
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finnhub_data._provider is None and adapter._listen_task is None


@pytest.mark.asyncio
async def test_replay_install_rechecks_finnhub_owner_after_route_check():
    socket = Socket()
    adapter = FinnhubAdapter(api_key="fake", connect_ws=Transport(socket).connect)
    await adapter.connect()
    finnhub_data._provider = adapter
    with pytest.raises(HTTPException) as exc:
        async with install_replay_engines(feature_engine=object(),
                                          level_interaction_engine=object(),
                                          market_state_engine=object(),
                                          context_engine=object()):
            raise AssertionError("replay must not install")
    assert exc.value.status_code == 409
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_new_finnhub_connect_rejected_during_replay_slot(monkeypatch):
    transport = Transport(Socket())
    adapter = FinnhubAdapter(api_key="fake", connect_ws=transport.connect)
    monkeypatch.setattr(finnhub_data, "FinnhubAdapter", lambda: adapter)
    async with finnhub_connection_slot():
        with pytest.raises(HTTPException) as exc:
            await finnhub_data.connect_finnhub()
    assert exc.value.status_code == 409
    assert transport.calls == 0 and finnhub_data._provider is None


@pytest.mark.asyncio
async def test_route_connect_failure_does_not_expose_token(monkeypatch, caplog):
    transport = Transport(ConnectionError("wss://ws.finnhub.io?token=private-key"))
    adapter = FinnhubAdapter(api_key="private-key", connect_ws=transport.connect)
    monkeypatch.setattr(finnhub_data, "FinnhubAdapter", lambda: adapter)
    with pytest.raises(HTTPException) as exc:
        await finnhub_data.connect()
    assert exc.value.status_code == 502
    assert "private-key" not in str(exc.value.detail)
    assert "private-key" not in caplog.text
    assert finnhub_data._provider is None and transport.calls == 1
