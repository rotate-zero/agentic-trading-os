from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.backtest_runner.ibkr_historical import (
    IBKRHistoricalConnectionError,
    IBKRHistoricalContractError,
    IBKRHistoricalIncompleteError,
    IBKRHistoricalMalformedBarError,
    IBKRHistoricalNoDataError,
    IBKRHistoricalPacingError,
    IBKRHistoricalPermissionError,
    IBKRHistoricalTimeoutError,
    acquire_ibkr_replay_data,
)
from app.broker_adapters.base import SymbolNotFoundError


def _bar(ts, *, close=100.0, volume=1000):
    return SimpleNamespace(
        date=ts,
        open=close - 0.2,
        high=close + 0.3,
        low=close - 0.4,
        close=close,
        volume=volume,
    )


class FakeIBKRAdapter:
    instances: list["FakeIBKRAdapter"] = []
    responses: list[list] = []
    error_on_request: tuple[int, str] | None = None
    qualification_error: Exception | None = None
    request_error: Exception | None = None
    connect_error: Exception | None = None
    sleep_seconds: float = 0.0
    emit_disconnect = False

    @classmethod
    def reset(cls):
        cls.instances = []
        cls.responses = []
        cls.error_on_request = None
        cls.qualification_error = None
        cls.request_error = None
        cls.connect_error = None
        cls.sleep_seconds = 0.0
        cls.emit_disconnect = False

    def __init__(self, *, host, port, client_id):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.connected = False
        self.disconnect_calls = 0
        self.request_calls: list[dict] = []
        self.error_listeners = []
        self.disconnect_listeners = []
        self.raise_request_errors = False
        self.subscribe_calls = 0
        self.on_tick_calls = 0
        self.place_order_calls = 0
        self.cancel_order_calls = 0
        type(self).instances.append(self)

    async def connect(self):
        if type(self).connect_error is not None:
            raise type(self).connect_error
        self.connected = True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def set_raise_request_errors(self, enabled):
        self.raise_request_errors = enabled

    def add_error_listener(self, callback):
        self.error_listeners.append(callback)

    def remove_error_listener(self, callback):
        self.error_listeners.remove(callback)

    def add_disconnect_listener(self, callback):
        self.disconnect_listeners.append(callback)

    def remove_disconnect_listener(self, callback):
        self.disconnect_listeners.remove(callback)

    async def qualify_historical_contract(self, symbol):
        if type(self).qualification_error is not None:
            raise type(self).qualification_error
        return SimpleNamespace(symbol=symbol, conId=12345)

    async def request_historical_chunk(self, contract, **kwargs):
        self.request_calls.append(kwargs)
        if type(self).sleep_seconds:
            await asyncio.sleep(type(self).sleep_seconds)
        if type(self).emit_disconnect:
            for listener in list(self.disconnect_listeners):
                listener()
        if type(self).error_on_request is not None:
            code, message = type(self).error_on_request
            for listener in list(self.error_listeners):
                listener(1, code, message, contract)
        if type(self).request_error is not None:
            raise type(self).request_error
        if type(self).responses:
            return type(self).responses.pop(0)
        return []


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeIBKRAdapter.reset()


async def _acquire(start, end, **overrides):
    kwargs = {
        "symbol": "AAPL",
        "start": start,
        "end": end,
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 77,
        "daily_lookback_days": 180,
        "premarket_lookback_days": 0,
        "market_timezone": "America/New_York",
        "adapter_factory": FakeIBKRAdapter,
    }
    kwargs.update(overrides)
    return await acquire_ibkr_replay_data(**kwargs)


@pytest.mark.asyncio
async def test_successful_chunks_normalize_filter_sort_and_deduplicate():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    end = start + timedelta(hours=26)
    boundary = start + timedelta(days=1)
    FakeIBKRAdapter.responses = [
        [
            _bar(boundary, close=102),
            _bar(start + timedelta(minutes=1), close=101),
            _bar(start - timedelta(minutes=1), close=99),
        ],
        [
            _bar(end, close=105),
            _bar(end - timedelta(minutes=1), close=104),
            _bar(boundary, close=102),
        ],
        [_bar(date(2026, 1, 2), close=98)],
    ]

    dataset = await _acquire(start, end)
    candles = await dataset.provider.get_historical("AAPL", "1m", start, end)

    assert [c.close for c in candles] == [101.0, 102.0, 104.0]
    assert [c.candle_ts for c in candles] == sorted(c.candle_ts for c in candles)
    assert all(c.candle_ts.tzinfo is timezone.utc for c in candles)
    assert dataset.data_version == "ibkr:TRADES:1m-ext:1d-rth"
    assert dataset.contract_con_id == 12345

    daily = await dataset.provider.get_historical(
        "AAPL",
        "1d",
        start - timedelta(days=180),
        end,
    )
    assert len(daily) == 1
    assert daily[0].candle_ts.tzinfo is timezone.utc
    assert daily[0].candle_ts.astimezone().tzinfo is not None

    adapter = FakeIBKRAdapter.instances[0]
    assert adapter.disconnect_calls == 1
    assert adapter.raise_request_errors is True
    assert adapter.subscribe_calls == 0
    assert adapter.on_tick_calls == 0
    assert adapter.place_order_calls == 0
    assert adapter.cancel_order_calls == 0


@pytest.mark.asyncio
async def test_acquires_configured_premarket_and_daily_auxiliary_ranges():
    start = datetime(2026, 3, 18, 13, 30, tzinfo=timezone.utc)
    end = start + timedelta(hours=6)
    primary_bar = _bar(start + timedelta(minutes=1))
    # 15 calendar days plus six hours => 16 serial one-day-or-smaller 1m requests.
    FakeIBKRAdapter.responses = [[] for _ in range(15)] + [[primary_bar], [_bar(date(2026, 3, 17))]]

    dataset = await _acquire(start, end, premarket_lookback_days=5)
    adapter = FakeIBKRAdapter.instances[0]

    assert len(adapter.request_calls) == 17
    minute_calls = adapter.request_calls[:-1]
    assert all(call["timeframe"] == "1m" and call["use_rth"] is False for call in minute_calls)
    assert minute_calls[0]["end"] == start - timedelta(days=14)
    assert minute_calls[-1]["end"] == end
    daily_call = adapter.request_calls[-1]
    assert daily_call["timeframe"] == "1d"
    assert daily_call["use_rth"] is True
    assert daily_call["duration_str"] == "181 D"

    auxiliary = await dataset.provider.get_historical(
        "AAPL", "1m", start - timedelta(days=15), start
    )
    primary = await dataset.provider.get_historical("AAPL", "1m", start, end)
    assert auxiliary == []
    assert len(primary) == 1


@pytest.mark.asyncio
async def test_conflicting_boundary_duplicate_is_rejected_as_incomplete():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    end = start + timedelta(hours=26)
    boundary = start + timedelta(days=1)
    FakeIBKRAdapter.responses = [
        [_bar(start, close=100), _bar(boundary, close=101)],
        [_bar(boundary, close=999)],
        [_bar(date(2026, 1, 2))],
    ]

    with pytest.raises(IBKRHistoricalIncompleteError):
        await _acquire(start, end)

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        (354, "Requested market data is not subscribed", IBKRHistoricalPermissionError),
        (162, "Historical data request pacing violation", IBKRHistoricalPacingError),
    ],
)
async def test_permission_and_pacing_events_are_failures_and_disconnect(code, message, expected):
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    FakeIBKRAdapter.error_on_request = (code, message)
    FakeIBKRAdapter.responses = [[_bar(start)], [_bar(date(2026, 1, 2))]]

    with pytest.raises(expected):
        await _acquire(start, start + timedelta(hours=1))

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1


@pytest.mark.asyncio
async def test_unresolved_contract_disconnects():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    FakeIBKRAdapter.qualification_error = SymbolNotFoundError("AAPL")

    with pytest.raises(IBKRHistoricalContractError):
        await _acquire(start, start + timedelta(hours=1))

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1


@pytest.mark.asyncio
async def test_timeout_disconnects_and_is_not_reported_as_no_data():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    FakeIBKRAdapter.sleep_seconds = 0.1

    with pytest.raises(IBKRHistoricalTimeoutError):
        await _acquire(
            start,
            start + timedelta(hours=1),
            request_timeout_seconds=0.01,
        )

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1


@pytest.mark.asyncio
async def test_disconnect_event_fails_acquisition_and_cleans_up():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    FakeIBKRAdapter.emit_disconnect = True
    FakeIBKRAdapter.responses = [[_bar(start)], [_bar(date(2026, 1, 2))]]

    with pytest.raises(IBKRHistoricalConnectionError):
        await _acquire(start, start + timedelta(hours=1))

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1


@pytest.mark.asyncio
async def test_zero_primary_bars_is_not_a_successful_empty_run():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    FakeIBKRAdapter.responses = [[], [_bar(date(2026, 1, 2))]]

    with pytest.raises(IBKRHistoricalNoDataError):
        await _acquire(start, start + timedelta(hours=1))

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1


@pytest.mark.asyncio
async def test_timezone_naive_intraday_bar_is_rejected_and_disconnects():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    FakeIBKRAdapter.responses = [
        [_bar(datetime(2026, 1, 5, 14, 31))],
        [_bar(date(2026, 1, 2))],
    ]

    with pytest.raises(IBKRHistoricalMalformedBarError):
        await _acquire(start, start + timedelta(hours=1))

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1


@pytest.mark.asyncio
async def test_connection_failure_still_runs_disconnect_cleanup():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    FakeIBKRAdapter.connect_error = ConnectionRefusedError("gateway unavailable")

    with pytest.raises(IBKRHistoricalConnectionError):
        await _acquire(start, start + timedelta(hours=1))

    assert FakeIBKRAdapter.instances[0].disconnect_calls == 1
