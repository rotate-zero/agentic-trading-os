"""
GET /scanner/observation (task `scanner-observation-status`).

Direct ASGI transport against the real, unstarted `app` (same technique as
test_scanner_state_route.py): no lifespan runs, so the only reader is the
one a test installs on `app.state.scanner_observation_reader` -- exactly the
slot a future, separately approved lifespan integration would own. Nothing
here needs PostgreSQL: the route must not touch the database, and the
side-effect tests below make any attempt to do so fail loudly.

Two kinds of reader are used:
- controlled fakes returning hand-built frozen `ObservationSnapshot`s, for
  exact state-by-state assertions (unavailable, initial, successful empty,
  populated, pending cycle, failed latest attempt with and without retained
  results, interrupted attempt);
- a real `ScannerObservationWorker` driven with an injected clock, universe
  and scan (no database), for the stopped-worker and failed-after-success
  states, so the mapping is proven against states the worker itself produces.
"""
from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace

import httpx
import pytest
from sqlalchemy.engine import Engine

import app.api.routes.scanner as scanner_routes
import app.scanner.scanner as scanner_module
from app.main import app as fastapi_app
from app.api.routes.scanner import _project_observation
from app.scanner.runner import ScanResult
from app.scanner.scanner import ObservationSnapshot, ObservedScanRow, ScannerObservationWorker

T0 = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_reader_left_behind():
    had = hasattr(fastapi_app.state, "scanner_observation_reader")
    previous = getattr(fastapi_app.state, "scanner_observation_reader", None)
    yield
    if had:
        fastapi_app.state.scanner_observation_reader = previous
    elif hasattr(fastapi_app.state, "scanner_observation_reader"):
        del fastapi_app.state.scanner_observation_reader


class FakeReader:
    def __init__(self, snapshot: ObservationSnapshot) -> None:
        self.snapshot = snapshot
        self.reads = 0

    def get_snapshot(self) -> ObservationSnapshot:
        self.reads += 1
        return self.snapshot


def install(reader) -> None:
    fastapi_app.state.scanner_observation_reader = reader


async def _get() -> httpx.Response:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/scanner/observation")


def row(symbol: str, score: float, inputs: int = 3, **features: float) -> ObservedScanRow:
    return ObservedScanRow(symbol, score, inputs, MappingProxyType(dict(features)))


# ---------------------------------------------------------------- unavailable


async def test_no_reader_installed_is_explicitly_unavailable():
    response = await _get()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert "No scheduled observation reader" in body["reason"]
    assert body["worker"] is None
    assert body["observation"] is None


async def test_reader_set_to_none_is_unavailable_too():
    install(None)

    assert (await _get()).json()["status"] == "unavailable"


# ------------------------------------------------------- controlled snapshots


async def test_initial_snapshot_is_never_attempted_not_empty_success():
    reader = FakeReader(ObservationSnapshot())
    install(reader)

    body = (await _get()).json()

    assert body["status"] == "available"
    assert body["reason"] is None
    assert body["worker"] == {"running": False, "cycle_running": False}
    obs = body["observation"]
    assert obs["retained"] == "none"
    assert obs["latest_attempt"] == "none"
    assert obs["universe"] == [] and obs["results"] == [] and obs["skipped"] == []
    assert obs["last_attempt_at"] is None and obs["last_success_at"] is None
    assert obs["last_error"] is None


async def test_successful_empty_is_distinct_from_never_succeeded():
    snapshot = ObservationSnapshot(
        universe=("AAA", "BBB"),
        results=(),
        skipped=("AAA", "BBB"),
        last_attempt_at=T0,
        last_success_at=T0 + timedelta(seconds=1),
        running=True,
    )
    install(FakeReader(snapshot))

    body = (await _get()).json()

    assert body["worker"] == {"running": True, "cycle_running": False}
    obs = body["observation"]
    assert obs["retained"] == "empty"
    assert obs["latest_attempt"] == "succeeded"
    assert obs["universe"] == ["AAA", "BBB"]
    assert obs["skipped"] == ["AAA", "BBB"]
    assert obs["results"] == []
    assert obs["last_success_at"] == "2026-10-08T14:30:01Z"
    assert obs["last_error"] is None


async def test_empty_universe_success_is_a_successful_empty_observation():
    install(FakeReader(ObservationSnapshot(last_attempt_at=T0, last_success_at=T0, running=True)))

    obs = (await _get()).json()["observation"]

    assert obs["retained"] == "empty"
    assert obs["latest_attempt"] == "succeeded"
    assert obs["universe"] == []


async def test_populated_success_returns_complete_results_in_order_with_utc_timestamps():
    snapshot = ObservationSnapshot(
        universe=("AAA", "BBB", "CCC"),
        results=(
            row("BBB", 2.5, 3, rvol=2.5, gap_pct=-1.25),
            row("AAA", 1.0, 2, rvol=1.0),
            row("CCC", 0.0, 0),  # zero-score / zero-input rows are retained, never filtered
        ),
        skipped=(),
        last_attempt_at=T0,
        last_success_at=T0 + timedelta(seconds=2),
        running=True,
    )
    install(FakeReader(snapshot))

    obs = (await _get()).json()["observation"]

    assert obs["retained"] == "populated"
    assert [r["symbol"] for r in obs["results"]] == ["BBB", "AAA", "CCC"]
    assert obs["results"][0] == {
        "symbol": "BBB", "score": 2.5, "inputs_available": 3, "features": {"rvol": 2.5, "gap_pct": -1.25},
    }
    assert obs["results"][2] == {"symbol": "CCC", "score": 0.0, "inputs_available": 0, "features": {}}
    assert obs["last_attempt_at"] == "2026-10-08T14:30:00Z"
    assert obs["last_success_at"] == "2026-10-08T14:30:02Z"
    assert obs["last_attempt_at"].endswith("Z") and obs["last_success_at"].endswith("Z")


async def test_pending_cycle_keeps_previous_results_and_reports_in_progress():
    previous = row("AAA", 1.5, 3, rvol=1.5)
    install(FakeReader(ObservationSnapshot(
        universe=("AAA",), results=(previous,), skipped=(),
        last_attempt_at=T0 + timedelta(seconds=60), last_success_at=T0, running=True, cycle_running=True,
    )))

    body = (await _get()).json()

    assert body["worker"] == {"running": True, "cycle_running": True}
    obs = body["observation"]
    assert obs["latest_attempt"] == "in_progress"
    assert obs["retained"] == "populated"
    assert [r["symbol"] for r in obs["results"]] == ["AAA"]
    assert obs["last_success_at"] == "2026-10-08T14:30:00Z"


async def test_pending_first_cycle_has_nothing_retained():
    install(FakeReader(ObservationSnapshot(last_attempt_at=T0, running=True, cycle_running=True)))

    obs = (await _get()).json()["observation"]

    assert obs["latest_attempt"] == "in_progress"
    assert obs["retained"] == "none"
    assert obs["last_success_at"] is None


async def test_failed_latest_attempt_retains_results_and_last_success():
    install(FakeReader(ObservationSnapshot(
        universe=("AAA",), results=(row("AAA", 1.5, 3, rvol=1.5),), skipped=("ZZZ",),
        last_attempt_at=T0 + timedelta(seconds=60), last_success_at=T0, last_error="RuntimeError: db down",
        running=True,
    )))

    body = (await _get()).json()
    obs = body["observation"]

    assert obs["latest_attempt"] == "failed"
    assert obs["last_error"] == "RuntimeError: db down"
    assert obs["retained"] == "populated"
    assert [r["symbol"] for r in obs["results"]] == ["AAA"]
    assert obs["skipped"] == ["ZZZ"]
    assert obs["last_attempt_at"] == "2026-10-08T14:31:00Z"
    assert obs["last_success_at"] == "2026-10-08T14:30:00Z"


async def test_failure_without_any_success_is_not_a_successful_empty_result():
    install(FakeReader(ObservationSnapshot(
        last_attempt_at=T0, last_error="OSError: boom", running=True,
    )))

    obs = (await _get()).json()["observation"]

    assert obs["latest_attempt"] == "failed"
    assert obs["retained"] == "none"
    assert obs["last_success_at"] is None


async def test_loop_level_error_without_an_attempt_timestamp_is_failed():
    install(FakeReader(ObservationSnapshot(last_error="RuntimeError: loop died")))

    obs = (await _get()).json()["observation"]

    assert obs["latest_attempt"] == "failed"
    assert obs["last_attempt_at"] is None


async def test_attempt_invalidated_by_stop_is_interrupted_not_failed_or_succeeded():
    install(FakeReader(ObservationSnapshot(
        universe=("AAA",), results=(row("AAA", 1.0, 3, rvol=1.0),),
        last_attempt_at=T0 + timedelta(seconds=60), last_success_at=T0, running=False,
    )))

    body = (await _get()).json()

    assert body["worker"] == {"running": False, "cycle_running": False}
    assert body["observation"]["latest_attempt"] == "interrupted"
    assert body["observation"]["last_error"] is None
    assert body["observation"]["retained"] == "populated"


async def test_naive_and_offset_timestamps_are_normalized_to_utc():
    eastern = timezone(timedelta(hours=-4))
    install(FakeReader(ObservationSnapshot(
        last_attempt_at=datetime(2026, 10, 8, 10, 30, tzinfo=eastern),  # 14:30Z
        last_success_at=datetime(2026, 10, 8, 14, 31),  # naive: the worker's clock is UTC
    )))

    obs = (await _get()).json()["observation"]

    assert obs["last_attempt_at"] == "2026-10-08T14:30:00Z"
    assert obs["last_success_at"] == "2026-10-08T14:31:00Z"


async def test_long_errors_are_bounded_and_non_finite_numbers_stay_valid_json():
    install(FakeReader(ObservationSnapshot(
        universe=("AAA",),
        results=(row("AAA", float("nan"), 3, rvol=float("inf"), gap_pct=1.0),),
        last_attempt_at=T0, last_success_at=T0, last_error="E" * 5000,
    )))

    response = await _get()

    assert response.status_code == 200
    obs = response.json()["observation"]
    assert len(obs["last_error"]) <= 501
    assert obs["results"][0]["score"] is None
    assert obs["results"][0]["features"] == {"rvol": None, "gap_pct": 1.0}


# ------------------------------------------------------ real worker, no DB


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.waiters: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.seconds)

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
        self.fail = False
        self.reads = 0

    def get_core_universe(self) -> list[str]:
        self.reads += 1
        if self.fail:
            raise RuntimeError("universe read failed")
        return list(self.symbols)


async def until(predicate) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=3)


def scan_two(symbols, **weights):
    return (
        [ScanResult("AAA", 2.0, 3, {"rvol": 2.0}), ScanResult("BBB", 1.0, 2, {"rvol": 1.0})],
        [s for s in symbols if s not in ("AAA", "BBB")],
    )


def make_worker(clock: Clock, universe, scan=scan_two) -> ScannerObservationWorker:
    return ScannerObservationWorker(
        eligible=lambda: True,
        universe_provider=universe,
        scan=scan,
        settings=SimpleNamespace(
            scanner_weight_rvol=1.0, scanner_weight_gap=0.0,
            scanner_weight_session_change=0.0, scanner_weight_premarket_volume_ratio=0.0,
        ),
        monotonic=clock.monotonic,
        wait=clock.wait,
        now=clock.now,
    )


async def test_stopped_real_worker_keeps_serving_its_retained_results():
    clock = Clock()
    universe = Universe(["AAA", "BBB", "CCC"])
    worker = make_worker(clock, universe)
    install(worker)

    await worker.start()
    try:
        await until(lambda: worker.get_snapshot().last_success_at is not None)
        running_body = (await _get()).json()
    finally:
        await worker.stop()
    assert running_body["worker"]["running"] is True
    assert running_body["observation"]["latest_attempt"] == "succeeded"

    body = (await _get()).json()

    assert body["status"] == "available"  # installed reader => retained view, even though stopped
    assert body["worker"] == {"running": False, "cycle_running": False}
    obs = body["observation"]
    assert obs["retained"] == "populated"
    assert [r["symbol"] for r in obs["results"]] == ["AAA", "BBB"]
    assert obs["skipped"] == ["CCC"]
    assert obs["universe"] == ["AAA", "BBB", "CCC"]
    assert obs["latest_attempt"] == "succeeded"
    assert obs["last_success_at"] == "2026-10-08T14:30:00Z"


async def test_real_worker_failed_cycle_after_success_retains_results():
    clock = Clock()
    universe = Universe(["AAA", "BBB"])
    worker = make_worker(clock, universe)
    install(worker)

    await worker.start()
    try:
        await until(lambda: worker.get_snapshot().last_success_at is not None)
        await until(lambda: bool(clock.waiters))  # the next 60 s timer exists before the clock moves
        universe.fail = True
        clock.advance(60)
        await until(lambda: worker.get_snapshot().last_error is not None)

        obs = (await _get()).json()["observation"]
    finally:
        await worker.stop()

    assert obs["latest_attempt"] == "failed"
    assert obs["last_error"] == "RuntimeError: universe read failed"
    assert obs["retained"] == "populated"
    assert [r["symbol"] for r in obs["results"]] == ["AAA", "BBB"]
    assert obs["last_success_at"] == "2026-10-08T14:30:00Z"
    assert obs["last_attempt_at"] == "2026-10-08T14:31:00Z"


async def test_real_worker_that_never_ran_reports_never_attempted():
    worker = make_worker(Clock(), Universe(["AAA"]))
    install(worker)

    body = (await _get()).json()

    assert body["worker"] == {"running": False, "cycle_running": False}
    assert body["observation"]["retained"] == "none"
    assert body["observation"]["latest_attempt"] == "none"


# ------------------------------------------------------ no side effects


def _forbid_everything(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("GET /scanner/observation must not reach the database, a scan or the network")

    monkeypatch.setattr(Engine, "connect", boom)
    monkeypatch.setattr(Engine, "begin", boom)
    monkeypatch.setattr(scanner_routes, "SessionLocal", boom)
    monkeypatch.setattr(scanner_module, "SessionLocal", boom)
    monkeypatch.setattr(scanner_routes, "run_scan", boom)
    monkeypatch.setattr(scanner_module, "run_scan", boom)
    monkeypatch.setattr(scanner_routes, "DbUniverseProvider", boom)
    monkeypatch.setattr(scanner_routes, "list_universe_symbols", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)


async def test_reads_do_not_scan_query_connect_or_start_anything(monkeypatch):
    class CountingUniverse:
        reads = 0

        def get_core_universe(self):
            CountingUniverse.reads += 1
            raise AssertionError("universe must not be read")

    def scan_must_not_run(symbols, **weights):
        raise AssertionError("scan must not run")

    worker = ScannerObservationWorker(
        eligible=lambda: (_ for _ in ()).throw(AssertionError("eligibility must not be consulted")),
        universe_provider=CountingUniverse(),
        scan=scan_must_not_run,
        settings=SimpleNamespace(
            scanner_weight_rvol=1.0, scanner_weight_gap=0.0,
            scanner_weight_session_change=0.0, scanner_weight_premarket_volume_ratio=0.0,
        ),
    )
    install(worker)
    _forbid_everything(monkeypatch)
    snapshot_before = worker.get_snapshot()
    tasks_before = asyncio.all_tasks()

    for _ in range(3):
        response = await _get()
        assert response.status_code == 200
        assert response.json()["status"] == "available"

    assert worker.get_snapshot() is snapshot_before  # state object untouched, not even replaced
    assert worker.get_snapshot().running is False
    assert CountingUniverse.reads == 0
    assert asyncio.all_tasks() == tasks_before  # no worker task or helper was started


async def test_unavailable_path_has_no_side_effects_either(monkeypatch):
    _forbid_everything(monkeypatch)

    assert (await _get()).json()["status"] == "unavailable"
    assert not hasattr(fastapi_app.state, "scanner_observation_reader") or fastapi_app.state.scanner_observation_reader is None


async def test_exactly_one_snapshot_read_per_request():
    reader = FakeReader(ObservationSnapshot(last_attempt_at=T0, last_success_at=T0))
    install(reader)

    await _get()
    assert reader.reads == 1
    await _get()
    assert reader.reads == 2


async def test_route_does_not_require_or_use_start_stop_on_the_reader():
    class SnapshotOnly:
        def get_snapshot(self):
            return ObservationSnapshot(last_attempt_at=T0, last_success_at=T0)

    install(SnapshotOnly())  # no start()/stop()/scan attributes at all

    assert (await _get()).json()["status"] == "available"


async def test_consumer_mutation_cannot_corrupt_retained_state():
    source_features = {"rvol": 2.0}
    source_row = ObservedScanRow("AAA", 2.0, 3, source_features)  # even a plain, mutable dict
    snapshot = ObservationSnapshot(
        universe=("AAA",), results=(source_row,), skipped=("ZZZ",),
        last_attempt_at=T0, last_success_at=T0, running=True,
    )

    projected = _project_observation(snapshot)
    projected["observation"]["results"][0]["features"]["rvol"] = 999.0
    projected["observation"]["results"].append({"symbol": "EVIL"})
    projected["observation"]["universe"].append("EVIL")
    projected["observation"]["skipped"].clear()
    projected["worker"]["running"] = False

    assert source_features == {"rvol": 2.0}
    assert snapshot.universe == ("AAA",) and snapshot.skipped == ("ZZZ",) and len(snapshot.results) == 1
    again = _project_observation(snapshot)
    assert again["observation"]["results"][0]["features"] == {"rvol": 2.0}
    assert again["observation"]["skipped"] == ["ZZZ"]
    assert again["worker"]["running"] is True


async def test_consumer_mutation_of_one_response_does_not_leak_into_the_next():
    reader = FakeReader(ObservationSnapshot(
        universe=("AAA",), results=(row("AAA", 2.0, 3, rvol=2.0),), last_attempt_at=T0, last_success_at=T0,
    ))
    install(reader)

    first = (await _get()).json()
    first["observation"]["results"][0]["features"]["rvol"] = -1
    first["observation"]["universe"].clear()
    second = (await _get()).json()

    assert second["observation"]["results"][0]["features"] == {"rvol": 2.0}
    assert second["observation"]["universe"] == ["AAA"]


# ---------------------------------------------------- neighbours unchanged


async def test_other_scanner_routes_are_still_registered_and_unchanged():
    paths = {(route.path, tuple(sorted(route.methods))) for route in fastapi_app.routes if hasattr(route, "methods")}

    assert ("/scanner/state", ("GET",)) in paths
    assert ("/scanner/universe", ("GET",)) in paths
    assert ("/scanner/universe", ("POST",)) in paths
    assert ("/scanner/universe/{symbol}", ("DELETE",)) in paths
    assert ("/scanner/observation", ("GET",)) in paths
    assert not any(p == "/scanner/observation" and "GET" not in m for p, m in paths)  # read-only


async def test_state_route_still_scans_on_demand_independent_of_the_observation_reader():
    install(FakeReader(ObservationSnapshot(universe=("ZZZ",), last_attempt_at=T0, last_success_at=T0)))
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/scanner/state", params={"symbols": "aapl"})

    assert response.status_code == 200
    assert response.json()["universe"] == ["AAPL"]
