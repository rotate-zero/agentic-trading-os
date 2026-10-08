"""Opt-in scanner lifespan, session admission and replay isolation."""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

import app.main as main_module
from app.backtest_runner.engine_singleton_guard import finnhub_connection_slot, install_replay_engines
from app.core.config import Settings
from app.core.market_clock import MarketClock
from app.event_bus.bus import get_event_bus
from app.feature_engine.engine import get_feature_engine
from app.main import app as fastapi_app
from app.scanner.scanner import ScannerObservationWorker
from app.schemas.events.features import FeatureSet

OPEN = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
LIVE_CANDLE = datetime(2026, 10, 8, 14, 29, tzinfo=timezone.utc)
REPLAY_CANDLE = datetime(2026, 6, 3, 14, 29, tzinfo=timezone.utc)
SYMBOL = "ZZLIFE"


async def until(predicate, timeout=4):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(poll(), timeout)


async def status():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi_app),
                                base_url="http://test") as client:
        response = await client.get("/scanner/observation")
    assert response.status_code == 200
    return response.json()


class Timer:
    def __init__(self):
        self.seconds = 0.0
        self.waiters = []

    def monotonic(self):
        return self.seconds

    def now(self):
        return OPEN

    async def wait(self, delay):
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.seconds + delay, future))
        try:
            await future
        finally:
            self.waiters = [(due, item) for due, item in self.waiters if item is not future]

    def advance(self, seconds):
        self.seconds += seconds
        for due, future in tuple(self.waiters):
            if due <= self.seconds and not future.done():
                future.set_result(None)


class Universe:
    def __init__(self):
        self.reads = 0

    def get_core_universe(self):
        self.reads += 1
        return [SYMBOL]


class BlockingUniverse(Universe):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def get_core_universe(self):
        self.reads += 1
        self.entered.set()
        assert self.release.wait(5), "test failed to release blocked universe read"
        return [SYMBOL]


def settings(monkeypatch, *, enabled=True, sessions=("open",)):
    value = Settings(_env_file=None, scanner_observation_enabled=enabled,
                     scanner_observation_sessions=list(sessions),
                     finnhub_api_key=None, polygon_api_key=None)
    monkeypatch.setattr(main_module, "get_settings", lambda: value)
    monkeypatch.setattr(main_module, "_scanner_observation_now", lambda: OPEN)
    return value


def controlled_worker(monkeypatch, universe=None):
    universe = universe or Universe()
    timer = Timer()
    workers = []

    def factory(*, eligible, settings):
        worker = ScannerObservationWorker(
            eligible=eligible, settings=settings, universe_provider=universe,
            monotonic=timer.monotonic, wait=timer.wait, now=timer.now,
        )
        workers.append(worker)
        return worker

    monkeypatch.setattr(main_module, "ScannerObservationWorker", factory)
    return timer, universe, workers


def seed_live_feature():
    engine = get_feature_engine(get_event_bus())
    engine._latest[(SYMBOL, "1m")] = FeatureSet(
        timeframe="1m", candle_ts=LIVE_CANDLE, close=100.0, features={"rvol": 2.0},
    )
    return engine


@pytest.mark.parametrize("kwargs", [
    {"scanner_observation_enabled": True},
    {"scanner_observation_enabled": True, "scanner_observation_sessions": []},
    {"scanner_observation_enabled": True, "scanner_observation_sessions": ["closed"]},
    {"scanner_observation_sessions": ["fictional"]},
    {"scanner_observation_sessions": ["open", "open"]},
    {"scanner_observation_enabled": True, "execution_mode": "paper",
     "scanner_observation_sessions": ["open"]},
])
def test_invalid_configuration_fails_before_startup(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


def test_default_is_disabled_and_valid_selection_is_explicit():
    default = Settings(_env_file=None)
    assert not default.scanner_observation_enabled and default.scanner_observation_sessions == []
    selected = Settings(_env_file=None, scanner_observation_enabled=True,
                        scanner_observation_sessions=["pre_market", "open", "lunch",
                                                      "power_hour", "after_hours"])
    assert len(selected.scanner_observation_sessions) == 5


def test_documented_environment_opt_in_parses_exact_sessions(monkeypatch):
    monkeypatch.setenv("SCANNER_OBSERVATION_ENABLED", "true")
    monkeypatch.setenv("SCANNER_OBSERVATION_SESSIONS", '["open","lunch","power_hour"]')
    value = Settings(_env_file=None)
    assert value.scanner_observation_enabled
    assert [session.value for session in value.scanner_observation_sessions] == [
        "open", "lunch", "power_hour",
    ]


@pytest.mark.parametrize("instant,selected,expected", [
    (OPEN, ("open",), True),
    (OPEN, ("pre_market",), False),
    (datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc), ("pre_market",), True),
    (datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc), ("open",), False),
    (datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc), ("open",), False),
    (datetime(2026, 11, 27, 17, 30, tzinfo=timezone.utc), ("lunch",), True),
    (datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc), ("after_hours",), False),
    (datetime(2029, 10, 8, 14, 30, tzinfo=timezone.utc), ("open",), False),
])
def test_market_clock_admission_selected_sessions_and_coverage(monkeypatch, instant, selected, expected):
    monkeypatch.setattr(main_module, "get_market_clock", lambda: MarketClock())
    monkeypatch.setattr(main_module, "_scanner_observation_now", lambda: instant)
    assert main_module._scanner_observation_eligible(frozenset(selected)) is expected


async def test_disabled_lifespan_keeps_route_unavailable_and_does_not_start_worker(monkeypatch):
    settings(monkeypatch, enabled=False)
    monkeypatch.setattr(main_module, "ScannerObservationWorker",
                        lambda **_: pytest.fail("disabled mode constructed a worker"))
    async with fastapi_app.router.lifespan_context(fastapi_app):
        assert fastapi_app.state.execution_startup_status["status"] == "ready"
        assert fastapi_app.state.scanner_observation_reader is None
        assert (await status())["status"] == "unavailable"
    assert fastapi_app.state.scanner_observation_reader is None


async def test_enabled_lifespan_publishes_real_observation_and_clears_reader(monkeypatch):
    settings(monkeypatch)
    timer, universe, workers = controlled_worker(monkeypatch)
    live = seed_live_feature()
    async with fastapi_app.router.lifespan_context(fastapi_app):
        await until(lambda: workers and workers[0].get_snapshot().last_success_at is not None)
        assert get_feature_engine() is live
        assert fastapi_app.state.scanner_observation_reader is workers[0]
        body = await status()
        assert body["status"] == "available"
        assert body["observation"]["results"][0]["symbol"] == SYMBOL
        assert body["observation"]["results"][0]["source_candle_ts"] == "2026-10-08T14:29:00Z"
        assert universe.reads == 1
        await until(lambda: timer.waiters)
    assert fastapi_app.state.scanner_observation_reader is None
    assert not workers[0].get_snapshot().running and not timer.waiters
    assert (await status())["status"] == "unavailable"


async def test_excluded_session_skips_until_selected_session_is_due(monkeypatch):
    settings(monkeypatch, sessions=("pre_market",))
    timer, universe, workers = controlled_worker(monkeypatch)
    seed_live_feature()
    instant = [OPEN]
    monkeypatch.setattr(main_module, "_scanner_observation_now", lambda: instant[0])
    async with fastapi_app.router.lifespan_context(fastapi_app):
        await until(lambda: timer.waiters)
        assert universe.reads == 0
        instant[0] = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        timer.advance(60)
        await until(lambda: workers[0].get_snapshot().last_success_at is not None)
        assert universe.reads == 1


async def test_busy_replay_connection_slot_skips_due_point_before_universe_read(monkeypatch):
    settings(monkeypatch)
    timer, universe, workers = controlled_worker(monkeypatch)
    seed_live_feature()
    async with fastapi_app.router.lifespan_context(fastapi_app):
        await until(lambda: workers[0].get_snapshot().last_success_at is not None)
        await until(lambda: timer.waiters)
        async with finnhub_connection_slot():
            timer.advance(60)
            await until(lambda: timer.waiters and timer.waiters[0][0] == 120)
            assert universe.reads == 1
        timer.advance(60)
        await until(lambda: universe.reads == 2)


@pytest.mark.parametrize("failure", ["blocked", "failed"])
async def test_execution_startup_unavailable_never_starts_observer(monkeypatch, failure):
    settings(monkeypatch)
    monkeypatch.setattr(main_module, "ScannerObservationWorker",
                        lambda **_: pytest.fail("blocked execution constructed observer"))
    if failure == "blocked":
        from app.portfolio_state.reconciliation import ReconciliationReport

        async def blocked(*_):
            return ReconciliationReport(discrepancies=["controlled discrepancy"])
        monkeypatch.setattr("app.portfolio_state.reconciliation.reconcile_with_venue", blocked)
    else:
        from app.broker_adapters.simulated_venue import SimulatedVenue

        async def failed(_):
            raise RuntimeError("controlled startup failure")
        monkeypatch.setattr(SimulatedVenue, "connect", failed)
    async with fastapi_app.router.lifespan_context(fastapi_app):
        expected = "reconciliation_blocked" if failure == "blocked" else "startup_failed"
        assert fastapi_app.state.execution_startup_status["status"] == expected
        assert fastapi_app.state.scanner_observation_reader is None
        assert (await status())["status"] == "unavailable"


async def test_worker_start_failure_rolls_back_reader_without_blocking_execution(monkeypatch):
    settings(monkeypatch)
    workers = []

    class FailingWorker(ScannerObservationWorker):
        async def start(self):
            await super().start()
            raise RuntimeError("controlled observer startup failure")

    def factory(*, eligible, settings):
        worker = FailingWorker(eligible=eligible, settings=settings, universe_provider=Universe())
        workers.append(worker)
        return worker

    monkeypatch.setattr(main_module, "ScannerObservationWorker", factory)
    async with fastapi_app.router.lifespan_context(fastapi_app):
        assert fastapi_app.state.execution_startup_status["status"] == "ready"
        assert fastapi_app.state.scanner_observation_reader is None
        assert (await status())["status"] == "unavailable"
    assert len(workers) == 1 and not workers[0].get_snapshot().running


async def test_shutdown_drains_blocked_read_before_feature_engine_stop(monkeypatch):
    settings(monkeypatch)
    universe = BlockingUniverse()
    _, _, workers = controlled_worker(monkeypatch, universe)
    seed_live_feature()
    context = fastapi_app.router.lifespan_context(fastapi_app)
    await context.__aenter__()
    exit_task = None
    try:
        await asyncio.to_thread(universe.entered.wait, 3)
        assert universe.entered.is_set()
        assert workers[0].get_snapshot().cycle_running
        exit_task = asyncio.create_task(context.__aexit__(None, None, None))
        await until(lambda: fastapi_app.state.scanner_observation_reader is None)
        assert not exit_task.done()
        universe.release.set()
        await asyncio.wait_for(exit_task, 4)
        assert workers[0].get_snapshot().last_success_at is None
        assert not workers[0].get_snapshot().running
    finally:
        universe.release.set()
        if exit_task is None:
            await context.__aexit__(None, None, None)
        elif not exit_task.done():
            await exit_task


async def test_replay_starting_during_universe_read_cannot_publish_replay_features(monkeypatch):
    settings(monkeypatch)
    universe = BlockingUniverse()
    timer, _, workers = controlled_worker(monkeypatch, universe)
    live = seed_live_feature()

    class ReplayFeature:
        calls = 0

        def get_snapshot(self, symbol):
            self.calls += 1
            return {symbol: {"1m": {"candle_ts": REPLAY_CANDLE.isoformat(),
                                    "close": 90.0, "features": {"rvol": 9.0}}}}

    replay = ReplayFeature()
    async with fastapi_app.router.lifespan_context(fastapi_app):
        try:
            await asyncio.to_thread(universe.entered.wait, 3)
            assert universe.entered.is_set()
            async with install_replay_engines(feature_engine=replay, level_interaction_engine=object(),
                                              market_state_engine=object(), context_engine=object()):
                universe.release.set()
                await until(lambda: not workers[0].get_snapshot().cycle_running)
                assert workers[0].get_snapshot().last_success_at is None
                assert replay.calls == 0
            assert get_feature_engine() is live
            await until(lambda: timer.waiters)
            timer.advance(60)
            await until(lambda: workers[0].get_snapshot().last_success_at is not None)
            body = await status()
            assert body["observation"]["results"][0]["source_candle_ts"] == "2026-10-08T14:29:00Z"
            assert body["observation"]["results"][0]["source_candle_ts"] != REPLAY_CANDLE.isoformat().replace("+00:00", "Z")
        finally:
            universe.release.set()


async def test_session_ending_during_universe_read_is_rechecked_before_scoring(monkeypatch):
    settings(monkeypatch)
    universe = BlockingUniverse()
    timer, _, workers = controlled_worker(monkeypatch, universe)
    seed_live_feature()
    instant = [OPEN]
    monkeypatch.setattr(main_module, "_scanner_observation_now", lambda: instant[0])
    async with fastapi_app.router.lifespan_context(fastapi_app):
        try:
            await asyncio.to_thread(universe.entered.wait, 3)
            assert universe.entered.is_set()
            instant[0] = datetime(2026, 10, 8, 20, 30, tzinfo=timezone.utc)  # after-hours
            universe.release.set()
            await until(lambda: not workers[0].get_snapshot().cycle_running)
            assert workers[0].get_snapshot().last_success_at is None
            await until(lambda: timer.waiters)
            instant[0] = OPEN
            timer.advance(60)
            await until(lambda: workers[0].get_snapshot().last_success_at is not None)
            assert (await status())["observation"]["results"][0]["source_candle_ts"] == "2026-10-08T14:29:00Z"
        finally:
            universe.release.set()


async def test_repeated_lifespan_entry_creates_no_stale_reader_or_worker(monkeypatch):
    settings(monkeypatch)
    _, _, workers = controlled_worker(monkeypatch)
    seed_live_feature()
    for attempt in range(2):
        async with fastapi_app.router.lifespan_context(fastapi_app):
            await until(lambda: len(workers) == attempt + 1)
            assert fastapi_app.state.scanner_observation_reader is workers[attempt]
            assert workers[attempt].get_snapshot().running
        assert fastapi_app.state.scanner_observation_reader is None
        assert not workers[attempt].get_snapshot().running
