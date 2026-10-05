"""A blocked World View performance read must not stall the ASGI event loop.

``GET /intelligence/world-view`` is served by the real route and the real
``WorldView`` facade.  Only its *sources* are controlled doubles: Market State
and Context are replaced by symbol-scoped stubs, the two synchronous
Performance Intelligence query functions are replaced by a gated double that
blocks inside the worker thread started by ``asyncio.to_thread``, and the
Portfolio State reader is either absent, unavailable or a restored (database
free) ``PortfolioState``.

These tests need neither PostgreSQL, the application lifespan, external
providers nor a broker.  ``httpx.ASGITransport`` does not run the lifespan.
They prove event-loop responsiveness and the response contract only; they do
**not** validate the SQL behind the performance queries (see
``test_world_view.py`` and ``test_outcome_read_path_integration.py`` for
the real-PostgreSQL coverage).

Synchronization is event driven: the blocked worker announces itself to the
event loop with ``call_soon_threadsafe`` into ``asyncio.Event`` objects, every
wait is bounded, and every test releases the gate and settles its pending
requests in a ``finally`` block (the fixture releases once more at teardown).
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

import app.world_view.composite as composite
from app.main import app
from app.portfolio_state.accounting import PositionState
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.ports import LedgerState
from app.trading_intelligence.performance_queries import (
    HourlyWinRate,
    SessionTypeExpectancy,
)

# A worker that is never released gives up after this long, so a broken test
# (or a regression that runs the read on the event loop) fails instead of
# hanging.  Waits performed by the test itself are bounded separately below.
_WORKER_GIVE_UP_SECONDS = 3.0
_WAIT_SECONDS = 5.0

AS_OF = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
POSITION_ID = UUID("12345678-1234-5678-1234-567812345678")

# Distinct live and backtest populations: a blended or swapped result cannot
# match these by accident.
_LIVE_HOURLY = [HourlyWinRate(hour_et=10, total_trades=4, win_count=3, win_rate=0.75)]
_BACKTEST_HOURLY = [HourlyWinRate(hour_et=11, total_trades=20, win_count=9, win_rate=0.45)]
_LIVE_SESSION = [SessionTypeExpectancy(session_type="open", trade_count=4, expectancy_r=0.5)]
_BACKTEST_SESSION = [
    SessionTypeExpectancy(session_type="power_hour", trade_count=18, expectancy_r=0.25),
    SessionTypeExpectancy(session_type=None, trade_count=2, expectancy_r=-0.5),
]
_EXPECTED_PERFORMANCE = {
    "live": {
        "hourly_win_rates": [
            {"hour_et": 10, "total_trades": 4, "win_count": 3, "win_rate": 0.75}
        ],
        "session_expectancy": [
            {"session_type": "open", "trade_count": 4, "expectancy_r": 0.5}
        ],
    },
    "backtest": {
        "hourly_win_rates": [
            {"hour_et": 11, "total_trades": 20, "win_count": 9, "win_rate": 0.45}
        ],
        "session_expectancy": [
            {"session_type": "power_hour", "trade_count": 18, "expectancy_r": 0.25},
            {"session_type": None, "trade_count": 2, "expectancy_r": -0.5},
        ],
    },
}
_EXPECTED_QUERY_CALLS = {
    ("hourly_win_rates", False),
    ("hourly_win_rates", True),
    ("session_expectancy", False),
    ("session_expectancy", True),
}


class _PerformanceGate:
    """Blocked synchronous performance reads with loop-visible signals."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()
        self._release = threading.Event()
        self._lock = threading.Lock()
        self._inside = 0
        self.threads: set[int] = set()
        self.calls: list[tuple[str, bool]] = []
        self.one_blocked = asyncio.Event()
        self.two_blocked = asyncio.Event()

    @property
    def loop_thread(self) -> int:
        return self._loop_thread

    @property
    def released(self) -> bool:
        return self._release.is_set()

    def release(self) -> None:
        self._release.set()  # idempotent; safe from tests, finally blocks and teardown

    def _announce(self, event: asyncio.Event) -> None:
        try:
            self._loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # loop already closed during a failed teardown
            pass

    def _blocked_read(self, name: str, rows_by_population: dict[bool, list], *, is_backtest: bool):
        with self._lock:
            self._inside += 1
            self.threads.add(threading.get_ident())
            self.calls.append((name, is_backtest))
            inside = self._inside
        if inside >= 1:
            self._announce(self.one_blocked)
        if inside >= 2:
            self._announce(self.two_blocked)
        try:
            if not self._release.wait(timeout=_WORKER_GIVE_UP_SECONDS):
                raise TimeoutError(f"test never released the {name} read")
            return list(rows_by_population[is_backtest])
        finally:
            with self._lock:
                self._inside -= 1

    def get_win_rate_by_hour(self, *, is_backtest: bool = False):
        return self._blocked_read(
            "hourly_win_rates", {False: _LIVE_HOURLY, True: _BACKTEST_HOURLY}, is_backtest=is_backtest
        )

    def get_expectancy_by_session_type(self, *, is_backtest: bool = False):
        return self._blocked_read(
            "session_expectancy", {False: _LIVE_SESSION, True: _BACKTEST_SESSION}, is_backtest=is_backtest
        )


class _ScopedSource:
    """Symbol-scoped Market State / Context stand-in with a recorded call log."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.requested: list[str | None] = []

    def get_snapshot(self, symbol=None):
        self.requested.append(symbol)
        return {"engine": self.name, "requested_symbol": symbol, "symbols": {} if symbol is None else {symbol: {"seen": True}}}


class _UnavailableReader:
    def __init__(self) -> None:
        self.calls = 0

    def get_snapshot(self):
        self.calls += 1
        return None


def _restored_flat_portfolio() -> PortfolioState:
    portfolio = PortfolioState("simulated")
    portfolio._install_state(LedgerState("simulated", 1, AS_OF))  # real read cache, no database
    return portfolio


def _restored_open_portfolio() -> PortfolioState:
    position = PositionState(
        position_id=POSITION_ID,
        trade_id=UUID("12345678-1234-5678-1234-567812345679"),
        execution_mode="simulated",
        execution_venue="simulated",
        symbol="AAPL",
        side="BUY",
        qty=3,
        avg_price=Decimal("101.2300"),
        opened_at=AS_OF,
        stop=Decimal("98.005"),
        target=None,
    )
    portfolio = PortfolioState("simulated")
    portfolio._install_state(LedgerState("simulated", 1, AS_OF, positions=(position,)))
    return portfolio


@pytest.fixture
async def gate(monkeypatch):
    """Install gated performance reads and scoped sources; always release."""
    gate = _PerformanceGate(asyncio.get_running_loop())
    market = _ScopedSource("market")
    context = _ScopedSource("context")
    monkeypatch.setattr(composite, "get_market_state_engine", lambda: market)
    monkeypatch.setattr(composite, "get_context_engine", lambda: context)
    monkeypatch.setattr(composite, "get_win_rate_by_hour", gate.get_win_rate_by_hour)
    monkeypatch.setattr(composite, "get_expectancy_by_session_type", gate.get_expectancy_by_session_type)
    monkeypatch.setattr(app.state, "world_view_portfolio_reader", None, raising=False)
    gate.market = market
    gate.context = context
    try:
        yield gate
    finally:
        gate.release()


@asynccontextmanager
async def _client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _settle(tasks: list[asyncio.Task]) -> None:
    """Wait (bounded) for requests to finish; cancel and drain stragglers."""
    _, still_pending = await asyncio.wait(tasks, timeout=_WAIT_SECONDS)
    for task in still_pending:
        task.cancel()
    if still_pending:
        await asyncio.gather(*still_pending, return_exceptions=True)


def _world_view_request(client: httpx.AsyncClient, symbol: str | None = None) -> asyncio.Task:
    params = {} if symbol is None else {"symbol": symbol}
    return asyncio.create_task(client.get("/intelligence/world-view", params=params))


async def test_blocked_performance_read_leaves_health_responsive(gate):
    async with _client() as client:
        world_task = _world_view_request(client, "AAPL")
        tasks = [world_task]
        try:
            await asyncio.wait_for(gate.one_blocked.wait(), timeout=_WAIT_SECONDS)
            assert not gate.released

            health = await asyncio.wait_for(client.get("/health"), timeout=2)

            # /health answered while the read is still blocked: the request
            # was neither finished nor failed, and nothing had released it.
            assert not world_task.done(), "World View finished/failed before /health answered"
            assert not gate.released
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
        finally:
            gate.release()
            await _settle(tasks)

        response = world_task.result()

    assert response.status_code == 200
    assert response.json()["symbol"] == "AAPL"
    assert response.json()["performance"] == _EXPECTED_PERFORMANCE
    # The blocked read ran in a worker thread, never on the event-loop thread.
    assert gate.threads and gate.loop_thread not in gate.threads


async def test_two_concurrent_requests_reach_their_reads_before_release(gate):
    async with _client() as client:
        first = _world_view_request(client, "AAA")
        second = _world_view_request(client, "BBB")
        tasks = [first, second]
        try:
            # Both handlers are genuinely inside a blocked performance read at
            # the same time (in two distinct worker threads) before any release.
            await asyncio.wait_for(gate.two_blocked.wait(), timeout=_WAIT_SECONDS)
            assert not gate.released
            assert not first.done() and not second.done()
            assert len(gate.threads) == 2 and gate.loop_thread not in gate.threads
        finally:
            gate.release()
            await _settle(tasks)

        first_response = first.result()
        second_response = second.result()

    assert first_response.status_code == second_response.status_code == 200
    first_body, second_body = first_response.json(), second_response.json()

    # Each response carries its own symbol-scoped envelopes, never the other's.
    for body, symbol in ((first_body, "AAA"), (second_body, "BBB")):
        assert body["symbol"] == symbol
        assert body["market_state"] == {
            "engine": "market", "requested_symbol": symbol, "symbols": {symbol: {"seen": True}},
        }
        assert body["context"] == {
            "engine": "context", "requested_symbol": symbol, "symbols": {symbol: {"seen": True}},
        }
        assert body["performance"] == _EXPECTED_PERFORMANCE
    assert sorted(gate.market.requested, key=str) == ["AAA", "BBB"]
    assert sorted(gate.context.requested, key=str) == ["AAA", "BBB"]
    # Four reads per request: both queries for each of the two populations.
    assert len(gate.calls) == 8
    assert set(gate.calls) == _EXPECTED_QUERY_CALLS


@pytest.mark.parametrize(
    ("portfolio_mode", "expected_portfolio"),
    [
        ("absent", None),
        ("unavailable", None),
        (
            "flat",
            {
                "execution_mode": "simulated",
                "snapshot_time": "2026-10-05T12:00:00+00:00",
                "positions": [],
                "in_flight_order_count": 0,
            },
        ),
        (
            "open",
            {
                "execution_mode": "simulated",
                "snapshot_time": "2026-10-05T12:00:00+00:00",
                "positions": [
                    {
                        "position_id": str(POSITION_ID),
                        "symbol": "AAPL",
                        "side": "BUY",
                        "remaining_quantity": 3,
                        "average_entry": "101.2300",
                        "stop": "98.005",
                        "target": None,
                    }
                ],
                "in_flight_order_count": 0,
            },
        ),
    ],
)
async def test_released_reads_keep_response_contract(gate, monkeypatch, portfolio_mode, expected_portfolio):
    reader = {
        "absent": None,
        "unavailable": _UnavailableReader(),
        "flat": _restored_flat_portfolio(),
        "open": _restored_open_portfolio(),
    }[portfolio_mode]
    monkeypatch.setattr(app.state, "world_view_portfolio_reader", reader, raising=False)

    async with _client() as client:
        scoped = _world_view_request(client, "MSFT")
        unscoped = _world_view_request(client)  # symbol omitted
        tasks = [scoped, unscoped]
        try:
            await asyncio.wait_for(gate.two_blocked.wait(), timeout=_WAIT_SECONDS)
        finally:
            gate.release()
            await _settle(tasks)
        scoped_response, unscoped_response = scoped.result(), unscoped.result()

    assert scoped_response.status_code == unscoped_response.status_code == 200
    scoped_body, unscoped_body = scoped_response.json(), unscoped_response.json()
    for body in (scoped_body, unscoped_body):
        assert set(body) == {"symbol", "market_state", "context", "performance", "portfolio"}

    # symbol scopes Market State and Context only; an omitted symbol is echoed as null.
    assert scoped_body["symbol"] == "MSFT"
    assert scoped_body["market_state"]["requested_symbol"] == "MSFT"
    assert scoped_body["context"]["requested_symbol"] == "MSFT"
    assert unscoped_body["symbol"] is None
    assert unscoped_body["market_state"] == {"engine": "market", "requested_symbol": None, "symbols": {}}
    assert unscoped_body["context"] == {"engine": "context", "requested_symbol": None, "symbols": {}}

    # Performance is system-wide with separate live and backtest populations,
    # identical for both requests; they are never blended or swapped.
    assert scoped_body["performance"] == unscoped_body["performance"] == _EXPECTED_PERFORMANCE
    live, backtest = scoped_body["performance"]["live"], scoped_body["performance"]["backtest"]
    assert live != backtest
    assert set(live) == set(backtest) == {"hourly_win_rates", "session_expectancy"}
    assert set(gate.calls) == _EXPECTED_QUERY_CALLS and len(gate.calls) == 8

    # Portfolio availability is reported honestly and is not symbol-scoped.
    assert scoped_body["portfolio"] == unscoped_body["portfolio"] == expected_portfolio
    if portfolio_mode == "unavailable":
        assert reader.calls == 2
