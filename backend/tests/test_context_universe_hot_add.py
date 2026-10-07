"""context-universe-hot-add: a symbol added to the persisted scanner universe
gets per-symbol context from the RUNNING ContextEngine without a restart.

Real PostgreSQL for the universe path (`scanner_universe_symbols` /
`symbols`, the real `add_symbol_to_universe` / `remove_symbol_from_universe` /
`ContextEngine._load_scanner_universe_symbols`), in a scratch database
created and dropped for this module so the shared `trading_workspace`
universe is never touched. Providers are controlled fakes (no Finnhub, no
news, no network); the engine's session-boundary loop is parked behind a fake
clock. Timing is controlled with threading/asyncio events around the real
universe read, not sleeps.

Run serially, like the rest of the DB-backed tests.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import app.api.routes.scanner as scanner_routes
import app.context_engine.engine as engine_module
from app.context_engine.engine import ContextEngine
from app.context_engine.provider import ContextProvider, SymbolContextProvider
from app.core.config import get_settings
from app.event_bus.bus import EventBus, get_event_bus
from app.main import app as fastapi_app
from app.scanner.universe import add_symbol_to_universe, list_universe_symbols, remove_symbol_from_universe
from app.schemas.events.envelope import EventType

BACKEND = Path(__file__).resolve().parents[1]
DB = f"ctx_hot_add_{uuid.uuid4().hex[:8]}"
SYMBOLS = ("ZAA", "ZAB", "ZAC", "ZAD")  # valid ticker format, not real symbols


# --- scratch database -----------------------------------------------------------


def _admin():
    s = get_settings()
    conn = psycopg2.connect(host=s.postgres_host, port=s.postgres_port, user=s.postgres_user,
                            password=s.postgres_password, dbname="postgres")
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def scratch_sessions():
    conn = _admin()
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{DB}"')
    try:
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND,
                                env={**os.environ, "POSTGRES_DB": DB}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        s = get_settings()
        url = f"postgresql+psycopg2://{s.postgres_user}:{s.postgres_password}@{s.postgres_host}:{s.postgres_port}/{DB}"
        db_engine = create_engine(url, future=True)
        try:
            yield sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
        finally:
            db_engine.dispose()
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        conn.close()


@pytest.fixture
def sessions(scratch_sessions, monkeypatch):
    """Empty persisted universe in the scratch DB, with both code paths that
    open a universe session (the engine's read, the route's write) pointed at it."""
    with scratch_sessions.begin() as session:
        session.execute(text("DELETE FROM scanner_universe_symbols"))
    monkeypatch.setattr(engine_module, "SessionLocal", scratch_sessions)
    monkeypatch.setattr(scanner_routes, "SessionLocal", scratch_sessions)
    return scratch_sessions


def universe(sessions) -> list[str]:
    return [row["symbol"] for row in list_universe_symbols(sessions)]


# --- controlled providers / helpers ----------------------------------------------


class _Calendar(ContextProvider):
    name = "calendar"

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self) -> dict:
        self.calls += 1
        return {"session": "open"}


class _SymbolProvider(SymbolContextProvider):
    name = "fundamentals"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate(self, symbol: str) -> dict:
        self.calls.append(symbol)
        return {"seen": symbol}


class _ParkedClock:
    """Session-boundary loop sleeps for an hour: global scheduling never fires mid-test."""

    def next_session_boundary(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(hours=1)


class _Gate:
    """Wraps the engine's REAL universe read: runs it, then (for the first
    `block_calls` calls) holds the result in its worker thread until released."""

    def __init__(self, engine: ContextEngine, block_calls: int) -> None:
        self.original = engine._load_scanner_universe_symbols
        self.block_calls = block_calls
        self.entered = threading.Semaphore(0)
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0
        engine._load_scanner_universe_symbols = self  # type: ignore[method-assign]

    def __call__(self) -> list[str]:
        result = self.original()
        with self._lock:
            self.calls += 1
            should_block = self.calls <= self.block_calls
        self.entered.release()
        if should_block and not self.release.wait(timeout=10):
            raise TimeoutError("test never released the universe read")
        return result


async def until(predicate, timeout: float = 3.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout=timeout)


async def wait_entered(gate: _Gate, count: int) -> None:
    """Polls the semaphore without the default thread pool, which the blocked reads may be occupying."""
    for _ in range(count):
        await until(lambda: gate.entered.acquire(blocking=False), timeout=5)


@pytest.fixture
async def bus():
    event_bus = EventBus()
    await event_bus.start()
    yield event_bus
    await event_bus.stop()


@pytest.fixture
def calendar():
    return _Calendar()


@pytest.fixture
def symbol_provider():
    return _SymbolProvider()


@pytest.fixture
def make_engine(bus, calendar, symbol_provider):
    created: list[ContextEngine] = []

    def factory() -> ContextEngine:
        engine = ContextEngine(bus, providers=[calendar], symbol_providers=[symbol_provider], clock=_ParkedClock())
        created.append(engine)
        return engine

    yield factory


@pytest.fixture
def received(bus):
    events: list = []
    bus.subscribe(EventType.CONTEXT_CHANGED, lambda env: events.append(env))
    return events


def symbol_events(received, symbol: str) -> list:
    return [env for env in received if env.symbol == symbol]


@pytest.fixture
async def started(make_engine, sessions):
    """Engine started against the empty scratch universe, bootstrap settled."""
    engine = make_engine()
    engine.start()
    await engine._bootstrap_task
    yield engine
    await engine.stop()


# --- 1. initial ContextChanged + snapshot ------------------------------------------


async def test_added_symbol_gets_initial_context_changed_and_appears_in_snapshot(started, sessions, received, symbol_provider):
    engine = started
    assert engine._symbol_tasks == {}
    assert "ZAA" not in engine.get_snapshot()["symbols"]

    add_symbol_to_universe(sessions, "ZAA")
    assert await engine.refresh_symbol_loops() == ["ZAA"]

    await until(lambda: symbol_events(received, "ZAA"))
    event = symbol_events(received, "ZAA")[0]
    assert event.payload["providers"] == {"fundamentals": {"seen": "ZAA"}}  # existing per-symbol event shape
    snapshot = engine.get_snapshot("ZAA")["symbols"]["ZAA"]
    assert snapshot["providers"]["fundamentals"] == {"seen": "ZAA"}
    assert snapshot["providers"]["calendar"] == {"session": "open"}  # merged with the global path, as before
    assert "ZAA" in engine.get_snapshot()["symbols"]
    assert symbol_provider.calls == ["ZAA"]


async def test_hot_added_symbol_rides_the_existing_symbol_cadence(started, sessions, symbol_provider, monkeypatch):
    assert engine_module._SYMBOL_LOOP_INTERVAL_SECONDS == 15 * 60  # the unchanged decision-#96 cadence
    monkeypatch.setattr(engine_module, "_SYMBOL_LOOP_INTERVAL_SECONDS", 0.02)  # same loop, shrunk for the test

    add_symbol_to_universe(sessions, "ZAA")
    await started.refresh_symbol_loops()

    await until(lambda: symbol_provider.calls.count("ZAA") >= 3)


# --- 2. one loop per symbol ----------------------------------------------------------


async def test_repeated_refresh_creates_one_loop_per_symbol(started, sessions, symbol_provider):
    engine = started
    add_symbol_to_universe(sessions, "ZAA")
    add_symbol_to_universe(sessions, "ZAB")

    assert sorted(await engine.refresh_symbol_loops()) == ["ZAA", "ZAB"]
    first_tasks = dict(engine._symbol_tasks)
    assert set(first_tasks) == {"ZAA", "ZAB"}

    assert await engine.refresh_symbol_loops() == []
    assert await engine.refresh_symbol_loops() == []
    assert engine._symbol_tasks == first_tasks
    assert all(engine._symbol_tasks[s] is first_tasks[s] for s in first_tasks)
    await until(lambda: sorted(symbol_provider.calls) == ["ZAA", "ZAB"])
    await asyncio.sleep(0.05)
    assert sorted(symbol_provider.calls) == ["ZAA", "ZAB"]  # exactly one initial evaluation each


async def test_concurrent_refreshes_create_one_loop_per_symbol(started, sessions, symbol_provider):
    engine = started
    add_symbol_to_universe(sessions, "ZAA")
    add_symbol_to_universe(sessions, "ZAB")
    gate = _Gate(engine, block_calls=4)

    refreshes = [asyncio.create_task(engine.refresh_symbol_loops()) for _ in range(4)]
    await wait_entered(gate, 4)  # all four reads hold the same universe at once
    assert engine._symbol_tasks == {}
    gate.release.set()
    results = await asyncio.gather(*refreshes)

    assert sorted(symbol for started_symbols in results for symbol in started_symbols) == ["ZAA", "ZAB"]  # each once
    assert set(engine._symbol_tasks) == {"ZAA", "ZAB"}
    await until(lambda: sorted(symbol_provider.calls) == ["ZAA", "ZAB"])
    await asyncio.sleep(0.05)
    assert sorted(symbol_provider.calls) == ["ZAA", "ZAB"]


# --- 3. existing loops and global scheduling intact ----------------------------------


async def test_refresh_leaves_existing_loops_and_global_loop_untouched(make_engine, sessions, calendar, symbol_provider):
    add_symbol_to_universe(sessions, "ZAA")
    engine = make_engine()
    engine.start()
    try:
        await engine._bootstrap_task
        await until(lambda: calendar.calls == 1 and symbol_provider.calls == ["ZAA"])
        global_task, bootstrap_task, zaa_task = engine._task, engine._bootstrap_task, engine._symbol_tasks["ZAA"]

        add_symbol_to_universe(sessions, "ZAB")
        assert await engine.refresh_symbol_loops() == ["ZAB"]
        await until(lambda: "ZAB" in symbol_provider.calls)

        assert engine._task is global_task and not global_task.done()
        assert engine._bootstrap_task is bootstrap_task
        assert engine._symbol_tasks["ZAA"] is zaa_task and not zaa_task.done()
        assert calendar.calls == 1  # global calendar evaluation not re-fired by refresh
        assert symbol_provider.calls == ["ZAA", "ZAB"]  # ZAA not re-evaluated either
    finally:
        await engine.stop()


# --- 4. removal does not stop a loop ---------------------------------------------------


async def test_removal_from_universe_keeps_the_existing_loop_until_stop(make_engine, sessions, symbol_provider):
    add_symbol_to_universe(sessions, "ZAA")
    engine = make_engine()
    engine.start()
    await engine._bootstrap_task
    await until(lambda: "ZAA" in engine.get_snapshot()["symbols"])
    zaa_task = engine._symbol_tasks["ZAA"]

    assert remove_symbol_from_universe(sessions, "ZAA") is True
    assert universe(sessions) == []
    assert await engine.refresh_symbol_loops() == []

    assert engine._symbol_tasks["ZAA"] is zaa_task and not zaa_task.done()
    assert "ZAA" in engine.get_snapshot()["symbols"]  # context still served

    await engine.stop()
    assert zaa_task.cancelled() and engine._symbol_tasks == {}  # normal shutdown ends it


# --- 5. bootstrap / addition races, stop during refresh ----------------------------------


async def test_refresh_during_bootstrap_does_not_duplicate_loops(make_engine, sessions, symbol_provider):
    add_symbol_to_universe(sessions, "ZAA")
    engine = make_engine()
    gate = _Gate(engine, block_calls=1)  # only the bootstrap read blocks
    engine.start()
    try:
        await wait_entered(gate, 1)  # bootstrap holds the pre-addition universe [ZAA]
        add_symbol_to_universe(sessions, "ZAB")
        assert sorted(await engine.refresh_symbol_loops()) == ["ZAA", "ZAB"]  # refresh wins the race
        tasks = dict(engine._symbol_tasks)

        gate.release.set()  # bootstrap now completes with its stale [ZAA]
        await engine._bootstrap_task

        assert engine._symbol_tasks == tasks
        assert all(engine._symbol_tasks[s] is tasks[s] for s in tasks)
        await until(lambda: sorted(symbol_provider.calls) == ["ZAA", "ZAB"])
        await asyncio.sleep(0.05)
        assert sorted(symbol_provider.calls) == ["ZAA", "ZAB"]
    finally:
        gate.release.set()
        await engine.stop()


async def test_addition_after_bootstrap_read_is_picked_up_by_refresh(make_engine, sessions):
    engine = make_engine()
    gate = _Gate(engine, block_calls=1)
    engine.start()
    try:
        await wait_entered(gate, 1)  # bootstrap read the EMPTY universe
        add_symbol_to_universe(sessions, "ZAA")
        gate.release.set()
        await engine._bootstrap_task
        assert engine._symbol_tasks == {}  # the snapshot-at-startup limitation, exactly as before

        assert await engine.refresh_symbol_loops() == ["ZAA"]
    finally:
        gate.release.set()
        await engine.stop()


async def test_stop_during_refresh_settles_and_creates_no_loops(started, sessions, symbol_provider):
    engine = started
    add_symbol_to_universe(sessions, "ZAA")
    gate = _Gate(engine, block_calls=1)

    refresh = asyncio.create_task(engine.refresh_symbol_loops())
    await wait_entered(gate, 1)
    assert len(engine._refresh_tasks) == 1

    await asyncio.wait_for(engine.stop(), timeout=2)  # does not wait for the blocked read
    assert engine._refresh_tasks == set()
    assert await refresh == []  # caller sees a no-op, not an error

    gate.release.set()  # the orphaned read finishes AFTER shutdown
    await asyncio.sleep(0.05)
    assert engine._symbol_tasks == {} and symbol_provider.calls == []


async def test_refresh_after_stop_or_before_start_is_a_noop(make_engine, sessions, symbol_provider):
    add_symbol_to_universe(sessions, "ZAA")
    engine = make_engine()
    reads = _Gate(engine, block_calls=0)

    assert await engine.refresh_symbol_loops() == []  # never started
    engine.start()
    await engine._bootstrap_task
    await engine.stop()
    calls_after_bootstrap = reads.calls

    assert await engine.refresh_symbol_loops() == []  # stopped: cannot revive the engine
    assert reads.calls == calls_after_bootstrap  # and does not even read
    assert engine._symbol_tasks == {} and engine._task is None


async def test_stale_generation_cannot_track_symbols_after_restart(make_engine, sessions):
    engine = make_engine()
    engine.start()
    await engine._bootstrap_task
    stale = engine._lifecycle_generation
    await engine.stop()
    engine.start()
    try:
        await engine._bootstrap_task
        assert engine._track_symbols(["ZAA"], stale) == []
        assert engine._symbol_tasks == {}
        assert engine._track_symbols(["ZAA"], engine._lifecycle_generation) == ["ZAA"]
    finally:
        await engine.stop()


async def test_cancelled_caller_does_not_lose_a_committed_addition(started, sessions, symbol_provider):
    engine = started
    add_symbol_to_universe(sessions, "ZAA")
    gate = _Gate(engine, block_calls=1)

    caller = asyncio.create_task(engine.refresh_symbol_loops())
    await wait_entered(gate, 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    gate.release.set()  # the owned refresh still completes

    await until(lambda: "ZAA" in engine._symbol_tasks)
    await until(lambda: symbol_provider.calls == ["ZAA"])


# --- 6. failure: engine level -----------------------------------------------------------


async def test_failed_refresh_preserves_tracking_and_a_later_refresh_retries(make_engine, sessions, symbol_provider, caplog):
    add_symbol_to_universe(sessions, "ZAA")
    engine = make_engine()
    engine.start()
    try:
        await engine._bootstrap_task
        zaa_task = engine._symbol_tasks["ZAA"]
        healthy = engine._load_scanner_universe_symbols

        def broken() -> list[str]:
            raise RuntimeError("injected universe read failure")

        engine._load_scanner_universe_symbols = broken  # type: ignore[method-assign]
        add_symbol_to_universe(sessions, "ZAB")
        with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="injected"):
            await engine.refresh_symbol_loops()
        assert "ContextEngine universe refresh failed" in caplog.text
        assert set(engine._symbol_tasks) == {"ZAA"} and engine._symbol_tasks["ZAA"] is zaa_task
        assert not zaa_task.done() and engine._refresh_tasks == set()

        engine._load_scanner_universe_symbols = healthy  # type: ignore[method-assign]
        assert await engine.refresh_symbol_loops() == ["ZAB"]
    finally:
        await engine.stop()


# --- 7. route integration ------------------------------------------------------------------


@pytest.fixture
def client_for_app():
    transport = httpx.ASGITransport(app=fastapi_app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def expose(monkeypatch):
    def _expose(engine) -> None:
        monkeypatch.setattr(fastapi_app.state, "context_engine", engine, raising=False)

    return _expose


async def test_route_addition_triggers_refresh_and_keeps_response_contract(started, sessions, expose, client_for_app, received):
    expose(started)
    async with client_for_app as client:
        response = await client.post("/scanner/universe", json={"symbol": "zab"})

    assert response.status_code == 200
    assert response.json() == {"symbol": "ZAB", "added": True}  # unchanged contract
    assert universe(sessions) == ["ZAB"]
    assert set(started._symbol_tasks) == {"ZAB"}
    await until(lambda: symbol_events(received, "ZAB"))
    assert "ZAB" in started.get_snapshot()["symbols"]


async def test_route_invalid_symbol_still_400_and_does_not_refresh(started, sessions, expose, client_for_app):
    expose(started)
    gate = _Gate(started, block_calls=0)
    async with client_for_app as client:
        response = await client.post("/scanner/universe", json={"symbol": "123"})

    assert response.status_code == 400
    assert gate.calls == 0  # nothing committed, nothing refreshed
    assert started._symbol_tasks == {}


async def test_refresh_failure_does_not_fail_the_committed_addition_and_next_addition_retries(
    started, sessions, expose, client_for_app, received, caplog
):
    add_symbol_to_universe(sessions, "ZAA")
    await started.refresh_symbol_loops()
    zaa_task = started._symbol_tasks["ZAA"]
    expose(started)
    healthy = started._load_scanner_universe_symbols

    def broken() -> list[str]:
        raise RuntimeError("injected universe read failure")

    started._load_scanner_universe_symbols = broken  # type: ignore[method-assign]
    async with client_for_app as client:
        with caplog.at_level(logging.WARNING):
            failed = await client.post("/scanner/universe", json={"symbol": "ZAB"})
        assert failed.status_code == 200 and failed.json() == {"symbol": "ZAB", "added": True}
        assert "ZAB" in universe(sessions)  # committed
        assert "committed but the ContextEngine refresh failed" in caplog.text
        assert set(started._symbol_tasks) == {"ZAA"} and started._symbol_tasks["ZAA"] is zaa_task
        assert not zaa_task.done()

        started._load_scanner_universe_symbols = healthy  # type: ignore[method-assign]
        retried = await client.post("/scanner/universe", json={"symbol": "ZAB"})  # idempotent re-add retries the refresh

    assert retried.status_code == 200 and retried.json() == {"symbol": "ZAB", "added": True}
    assert set(started._symbol_tasks) == {"ZAA", "ZAB"}
    await until(lambda: symbol_events(received, "ZAB"))


async def test_route_never_creates_or_starts_an_engine_when_none_is_exposed(sessions, client_for_app, monkeypatch):
    monkeypatch.delattr(fastapi_app.state, "context_engine", raising=False)
    assert engine_module._context_engine is None
    async with client_for_app as client:
        response = await client.post("/scanner/universe", json={"symbol": "ZAA"})

    assert response.status_code == 200 and response.json() == {"symbol": "ZAA", "added": True}
    assert universe(sessions) == ["ZAA"]
    assert engine_module._context_engine is None  # the route did not call get_context_engine()


async def test_route_with_a_stopped_engine_commits_and_starts_nothing(make_engine, sessions, expose, client_for_app):
    engine = make_engine()
    engine.start()
    await engine._bootstrap_task
    await engine.stop()
    expose(engine)
    async with client_for_app as client:
        response = await client.post("/scanner/universe", json={"symbol": "ZAA"})

    assert response.status_code == 200 and universe(sessions) == ["ZAA"]
    assert engine._symbol_tasks == {} and engine._task is None


async def test_route_removal_contract_unchanged_and_keeps_loop(started, sessions, expose, client_for_app):
    expose(started)
    async with client_for_app as client:
        await client.post("/scanner/universe", json={"symbol": "ZAA"})
        zaa_task = started._symbol_tasks["ZAA"]
        response = await client.delete("/scanner/universe/ZAA")

    assert response.status_code == 200 and response.json() == {"symbol": "ZAA", "removed": True}
    assert started._symbol_tasks["ZAA"] is zaa_task and not zaa_task.done()


# --- 8. lifespan wiring (real main.py lifespan, controlled engine) -----------------------


async def test_real_lifespan_exposes_the_running_engine_and_clears_it_at_shutdown(sessions, calendar, symbol_provider):
    bus = get_event_bus()  # the same singleton main.py's lifespan starts and uses
    engine = ContextEngine(bus, providers=[calendar], symbol_providers=[symbol_provider], clock=_ParkedClock())
    engine_module._context_engine = engine  # get_context_engine(bus) in the lifespan returns this controlled engine
    transport = httpx.ASGITransport(app=fastapi_app)

    async with fastapi_app.router.lifespan_context(fastapi_app):
        assert fastapi_app.state.context_engine is engine
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/scanner/universe", json={"symbol": "ZAA"})
        assert response.status_code == 200
        await until(lambda: "ZAA" in engine.get_snapshot()["symbols"])
        assert set(engine._symbol_tasks) == {"ZAA"}

    assert fastapi_app.state.context_engine is None  # cleared; engine stopped
    assert engine._symbol_tasks == {} and engine._task is None
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        late = await client.post("/scanner/universe", json={"symbol": "ZAB"})
    assert late.status_code == 200 and "ZAB" in universe(sessions)
    assert engine._symbol_tasks == {}  # nothing started on a stopped engine
    assert engine_module._context_engine is engine  # and no replacement was created
