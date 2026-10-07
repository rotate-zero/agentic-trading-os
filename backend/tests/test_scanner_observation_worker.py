from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import app.scanner.runner as runner_module
from app.db.session import SessionLocal
from app.scanner.scanner import ScannerObservationWorker
from app.scanner.universe import DbUniverseProvider, add_symbol_to_universe, remove_symbol_from_universe


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.waiters: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(seconds=self.seconds)

    async def wait(self, delay: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiters.append((self.seconds + delay, future))
        try:
            await future
        finally:
            self.waiters = [(due, item) for due, item in self.waiters if item is not future]

    def advance(self, seconds: float) -> None:
        self.seconds += seconds
        for due, future in tuple(self.waiters):
            if due <= self.seconds and not future.done():
                future.set_result(None)


class Universe:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        self.reads = 0

    def get_core_universe(self) -> list[str]:
        self.reads += 1
        return list(self.symbols)


async def until(predicate) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=3)


def make_worker(clock: Clock, universe: Universe, *, eligible=lambda: True, scan=None):
    if scan is None:
        scan = lambda symbols, **weights: ([], list(symbols))
    return ScannerObservationWorker(
        eligible=eligible,
        universe_provider=universe,
        scan=scan,
        settings=SimpleNamespace(
            scanner_weight_rvol=2.0,
            scanner_weight_gap=0.0,
            scanner_weight_session_change=0.0,
            scanner_weight_premarket_volume_ratio=0.0,
        ),
        monotonic=clock.monotonic,
        wait=clock.wait,
        now=clock.now,
    )


async def test_first_cycle_fixed_deadlines_and_ineligible_periods():
    clock = Clock()
    universe = Universe(["AAPL"])
    admitted = True
    worker = make_worker(clock, universe, eligible=lambda: admitted)
    assert worker.get_snapshot().last_success_at is None
    await worker.start()
    await worker.start()
    await until(lambda: universe.reads == 1 and not worker.get_snapshot().cycle_running)
    first = worker.get_snapshot()
    assert first.running and first.last_success_at == clock.now()
    assert first.universe == ("AAPL",) and first.skipped == ("AAPL",)

    await until(lambda: any(due == 60 for due, _ in clock.waiters))
    clock.advance(59)
    await asyncio.sleep(0)
    assert universe.reads == 1
    admitted = False
    clock.advance(1)
    await until(lambda: any(due == 120 for due, _ in clock.waiters))
    assert universe.reads == 1 and worker.get_snapshot().last_success_at == first.last_success_at
    admitted = True
    clock.advance(60)
    await until(lambda: universe.reads == 2 and not worker.get_snapshot().cycle_running)
    assert worker.get_snapshot().last_success_at == clock.now()
    await worker.stop()
    await worker.stop()
    assert not worker.get_snapshot().running and not clock.waiters


async def test_cold_start_empty_universe_is_a_success_without_fallback():
    clock = Clock()
    universe = Universe([])
    observed_inputs: list[list[str]] = []

    def scan(symbols, **weights):
        observed_inputs.append(symbols)
        return [], []

    worker = make_worker(clock, universe, scan=scan)
    assert worker.get_snapshot().last_success_at is None
    await worker.start()
    await until(lambda: worker.get_snapshot().last_success_at is not None)
    snapshot = worker.get_snapshot()
    assert observed_inputs == [[]]
    assert snapshot.universe == snapshot.results == snapshot.skipped == ()
    assert snapshot.last_error is None and snapshot.last_success_at == clock.now()
    await worker.stop()


async def test_blocked_read_coalesces_deadlines_and_stop_drains_without_publication():
    clock = Clock()
    release = threading.Event()

    class BlockedUniverse(Universe):
        def get_core_universe(self):
            self.reads += 1
            if not release.wait(timeout=20):
                raise TimeoutError("test read was not released")
            return list(self.symbols)

    universe = BlockedUniverse(["AAPL"])
    worker = make_worker(clock, universe)
    await worker.start()
    try:
        await until(lambda: universe.reads == 1)
        assert worker.get_snapshot().cycle_running
        clock.advance(600)
        await asyncio.sleep(0)
        assert universe.reads == 1  # no overlap while the read is blocked
        stopping = asyncio.create_task(worker.stop())
        await until(lambda: not worker.get_snapshot().running)
        assert not stopping.done()  # stop drains the thread rather than abandoning it
        release.set()
        await until(stopping.done)
        await stopping
    finally:
        release.set()
        await worker.stop()
    assert worker.get_snapshot().last_success_at is None
    assert worker.get_snapshot().last_attempt_at is not None
    assert not worker.get_snapshot().cycle_running
    clock.advance(600)
    await asyncio.sleep(0)
    assert universe.reads == 1


async def test_slow_cycle_allows_one_catchup_and_restart_has_one_timer():
    clock = Clock()
    universe = Universe(["AAPL"])
    calls: list[tuple[str, ...]] = []

    def scan(symbols, **weights):
        calls.append(tuple(symbols))
        if len(calls) == 1:
            clock.advance(600)
        return [], []

    worker = make_worker(clock, universe, scan=scan)
    await worker.start()
    await until(lambda: len(calls) == 2 and any(due == 660 for due, _ in clock.waiters))
    assert universe.reads == 2  # ten missed deadlines made only one catch-up
    await worker.stop()
    await worker.start()
    await worker.start()
    await until(lambda: len(calls) == 3 and any(due == 660 for due, _ in clock.waiters))
    assert len(clock.waiters) == 1
    await worker.stop()


async def test_cancelled_stop_drains_old_generation_before_restart():
    clock = Clock()
    release = threading.Event()

    class DeferredUniverse(Universe):
        def get_core_universe(self):
            self.reads += 1
            if self.reads == 1:
                assert release.wait(timeout=20)
                return ["AAPL"]
            return ["TSLA"]

    universe = DeferredUniverse([])
    worker = make_worker(clock, universe)
    await worker.start()
    try:
        await until(lambda: universe.reads == 1)
        stopping = asyncio.create_task(worker.stop())
        await until(lambda: not worker.get_snapshot().running)
        restarting = asyncio.create_task(worker.start())
        stopping.cancel()
        await asyncio.sleep(0)
        assert not restarting.done() and worker.get_snapshot().last_success_at is None
        release.set()
        await until(lambda: stopping.done() and restarting.done())
        with pytest.raises(asyncio.CancelledError):
            await stopping
        await restarting
        await until(lambda: universe.reads == 2 and worker.get_snapshot().last_success_at is not None)
        assert worker.get_snapshot().universe == ("TSLA",)
        assert worker.get_snapshot().skipped == ("TSLA",)
    finally:
        release.set()
        await worker.stop()


async def test_real_runner_universe_edits_empty_success_failures_and_immutable_result(monkeypatch):
    clock = Clock()
    universe = Universe(["AAPL", "ZERO", "MISS"])
    owning_thread = threading.get_ident()
    snapshots = {
        "AAPL": {"1m": {"candle_ts": clock.now().isoformat(), "close": 100, "features": {"rvol": 3.0}}},
        "ZERO": {"1m": {"candle_ts": clock.now().isoformat(), "close": 100, "features": {}}},
    }

    class FeatureEngine:
        def get_snapshot(self, symbol=None):
            assert threading.get_ident() == owning_thread
            return {symbol: snapshots[symbol]} if symbol in snapshots else {}

    monkeypatch.setattr(runner_module, "get_feature_engine", lambda: FeatureEngine())
    worker = make_worker(clock, universe, scan=runner_module.run_scan)
    await worker.start()
    await until(lambda: worker.get_snapshot().last_success_at is not None)
    first = worker.get_snapshot()
    assert first.universe == ("AAPL", "ZERO", "MISS")
    assert [(r.symbol, r.score, r.inputs_available) for r in first.results] == [
        ("AAPL", 6.0, 1), ("ZERO", 0.0, 0)
    ]
    assert first.skipped == ("MISS",)
    with pytest.raises(TypeError):
        first.results[0].features["rvol"] = 999
    with pytest.raises(AttributeError):
        first.results[0].score = 999
    snapshots["AAPL"]["1m"]["features"]["rvol"] = 99
    assert first.results[0].features["rvol"] == 3.0

    class Failure(Exception):
        pass

    def failed_read():
        raise Failure("database unavailable")

    universe.get_core_universe = failed_read
    await until(lambda: any(due == 60 for due, _ in clock.waiters))
    clock.advance(60)
    await until(lambda: worker.get_snapshot().last_error is not None)
    failed = worker.get_snapshot()
    assert failed.last_success_at == first.last_success_at
    assert failed.last_attempt_at == clock.now()
    assert failed.universe == first.universe and failed.results == first.results
    assert failed.skipped == first.skipped
    assert "database unavailable" in failed.last_error

    universe.get_core_universe = lambda: ["AAPL"]
    monkeypatch.setattr(runner_module, "get_feature_engine", lambda: (_ for _ in ()).throw(Failure("score unavailable")))
    await until(lambda: any(due == 120 for due, _ in clock.waiters))
    clock.advance(60)
    await until(lambda: worker.get_snapshot().last_attempt_at == clock.now() and worker.get_snapshot().last_error is not None)
    assert "score unavailable" in worker.get_snapshot().last_error
    assert worker.get_snapshot().last_success_at == first.last_success_at
    assert worker.get_snapshot().results == first.results

    universe.get_core_universe = lambda: []
    monkeypatch.setattr(runner_module, "get_feature_engine", lambda: FeatureEngine())
    await until(lambda: any(due == 180 for due, _ in clock.waiters))
    clock.advance(60)
    await until(lambda: worker.get_snapshot().last_success_at == clock.now())
    empty = worker.get_snapshot()
    assert empty.universe == empty.results == empty.skipped == ()
    assert empty.last_error is None and empty.last_success_at is not None
    await worker.stop()


async def test_edit_during_read_changes_only_next_captured_cycle():
    clock = Clock()
    release = threading.Event()

    class CapturingUniverse(Universe):
        def get_core_universe(self):
            captured = list(self.symbols)
            self.reads += 1
            if self.reads == 1:
                assert release.wait(timeout=20)
            return captured

    universe = CapturingUniverse(["AAPL"])
    worker = make_worker(clock, universe)
    await worker.start()
    try:
        await until(lambda: universe.reads == 1)
        universe.symbols = ["TSLA"]
        release.set()
        await until(lambda: worker.get_snapshot().last_success_at is not None)
        assert worker.get_snapshot().universe == ("AAPL",)
        await until(lambda: any(due == 60 for due, _ in clock.waiters))
        clock.advance(60)
        await until(lambda: worker.get_snapshot().last_success_at == clock.now())
        assert worker.get_snapshot().universe == ("TSLA",)
    finally:
        release.set()
        await worker.stop()


async def test_real_postgres_provider_rereads_committed_universe_edits():
    """Run only against an isolated, migrated local PostgreSQL test database."""
    ticker = "ZZWKR"
    clock = Clock()
    owning_thread = threading.get_ident()
    session_threads: list[int] = []

    def session_factory():
        session_threads.append(threading.get_ident())
        return SessionLocal()

    provider = DbUniverseProvider(session_factory)
    assert ticker not in provider.get_core_universe()
    worker = make_worker(clock, provider)
    await worker.start()
    try:
        await until(lambda: worker.get_snapshot().last_success_at is not None)
        assert ticker not in worker.get_snapshot().universe
        add_symbol_to_universe(SessionLocal, ticker)
        await until(lambda: any(due == 60 for due, _ in clock.waiters))
        clock.advance(60)
        await until(lambda: worker.get_snapshot().last_success_at == clock.now())
        assert ticker in worker.get_snapshot().universe
        assert ticker in worker.get_snapshot().skipped
        assert remove_symbol_from_universe(SessionLocal, ticker)
        await until(lambda: any(due == 120 for due, _ in clock.waiters))
        clock.advance(60)
        await until(lambda: worker.get_snapshot().last_success_at == clock.now())
        assert ticker not in worker.get_snapshot().universe
        assert session_threads and all(thread_id != owning_thread for thread_id in session_threads[1:])
    finally:
        await worker.stop()
        remove_symbol_from_universe(SessionLocal, ticker)
        with SessionLocal() as session:
            session.execute(text("DELETE FROM symbols WHERE ticker = :ticker AND is_backtest = false"), {"ticker": ticker})
            session.commit()
