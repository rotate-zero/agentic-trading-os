"""
Source feature-candle timestamps on the scheduled observation
(task `scanner-observation-source-timestamps`).

Everything here uses the REAL `run_scan`, the REAL `ScannerObservationWorker`
and the REAL `GET /scanner/observation` / `GET /scanner/state` routes. The
FeatureEngine is a real `FeatureEngine` instance whose `_latest` map (the
exact structure the engine itself replaces on every closed candle) is filled
with controlled `FeatureSet`s, so the timestamp travels through the genuine
`get_snapshot()` ISO round trip. Only the engine singleton accessor is
patched to hand that controlled instance to `run_scan`. No database, no
provider, no network.

What is proved:
- the candle_ts that supplied a row's score inputs reaches the row, from the
  same snapshot read (one `get_snapshot()` per symbol, asserted), even when
  the engine's state is replaced mid-acquisition;
- scores, ordering, zero-input rows and skipped symbols are unchanged;
- `ScanResult` stays constructible by older callers and `GET /scanner/state`
  keeps its exact response contract;
- a failed later cycle keeps the earlier successful rows (and their source
  timestamps) untouched; retained state is immutable;
- unknown / skipped / empty / UTC-converted / future timestamps behave as
  documented, with a server `read_at` and no freshness verdict anywhere.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

import app.api.routes.scanner as scanner_routes
import app.scanner.runner as runner_module
from app.api.routes.scanner import _project_observation
from app.feature_engine.engine import FeatureEngine
from app.main import app as fastapi_app
from app.scanner.runner import ScanResult, run_scan
from app.scanner.scanner import ObservationSnapshot, ObservedScanRow, ScannerObservationWorker
from app.scanner.scorer import score_symbol
from app.schemas.events.features import FeatureSet

WEIGHTS = dict(weight_rvol=1.0, weight_gap=1.0, weight_session_change=1.0)
T0 = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)  # the worker's (injected) clock origin
OLD = datetime(2026, 10, 5, 20, 59, tzinfo=timezone.utc)  # three days before T0


# ------------------------------------------------------------------ helpers


def make_engine() -> FeatureEngine:
    return FeatureEngine(
        MagicMock(),
        sma_periods=[], ema_periods=[], ema_seed_multiplier=3, aggregated_lookback_days=1,
        regression_configs=[], kama_configs=[], kama_seed_multiplier=3,
    )


def put(engine: FeatureEngine, symbol: str, candle_ts: datetime, features: dict[str, float], close: float = 100.0) -> None:
    engine._latest[(symbol, "1m")] = FeatureSet(timeframe="1m", candle_ts=candle_ts, close=close, features=features)


class CountingEngine:
    """Wraps a real FeatureEngine and records every get_snapshot() call."""

    def __init__(self, engine: FeatureEngine) -> None:
        self.engine = engine
        self.calls: list[str | None] = []
        self.after_read = None  # optional hook run right after a snapshot was handed out

    def get_snapshot(self, symbol=None):
        self.calls.append(symbol)
        snap = self.engine.get_snapshot(symbol)
        if self.after_read is not None:
            self.after_read(symbol)
        return snap


def use_engine(engine):
    return patch.object(runner_module, "get_feature_engine", return_value=engine)


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.waiters: list[tuple[float, asyncio.Future]] = []

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

    def get_core_universe(self) -> list[str]:
        if self.fail:
            raise RuntimeError("universe read failed")
        return list(self.symbols)


async def until(predicate) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=3)


def make_worker(clock: Clock, universe, scan=run_scan) -> ScannerObservationWorker:
    return ScannerObservationWorker(
        eligible=lambda: True,
        universe_provider=universe,
        scan=scan,
        settings=SimpleNamespace(
            scanner_weight_rvol=1.0, scanner_weight_gap=1.0,
            scanner_weight_session_change=1.0, scanner_weight_premarket_volume_ratio=0.0,
        ),
        monotonic=clock.monotonic,
        wait=clock.wait,
        now=clock.now,
    )


@pytest.fixture(autouse=True)
def _no_reader_left_behind():
    had = hasattr(fastapi_app.state, "scanner_observation_reader")
    previous = getattr(fastapi_app.state, "scanner_observation_reader", None)
    yield
    if had:
        fastapi_app.state.scanner_observation_reader = previous
    elif hasattr(fastapi_app.state, "scanner_observation_reader"):
        del fastapi_app.state.scanner_observation_reader


@pytest.fixture
def read_clock(monkeypatch):
    """Pins the route's server read clock; returns a mutable holder."""
    holder = SimpleNamespace(now=datetime(2026, 10, 8, 14, 31, 30, tzinfo=timezone.utc))
    monkeypatch.setattr(scanner_routes, "_utcnow", lambda: holder.now)
    return holder


async def _get(path: str = "/scanner/observation", **params) -> httpx.Response:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, params=params)


class FakeReader:
    def __init__(self, snapshot: ObservationSnapshot) -> None:
        self.snapshot = snapshot
        self.reads = 0

    def get_snapshot(self) -> ObservationSnapshot:
        self.reads += 1
        return self.snapshot


# ------------------------------------------------------------------ run_scan


def test_run_scan_carries_each_symbols_own_source_candle_ts():
    engine = make_engine()
    t_aaa = datetime(2026, 10, 8, 14, 29, tzinfo=timezone.utc)
    t_bbb = datetime(2026, 10, 8, 13, 45, tzinfo=timezone.utc)
    t_ccc = OLD
    put(engine, "AAA", t_aaa, {"rvol": 2.0, "gap_pct": 1.0, "session_pct_change": 1.0, "atr_14_pct": 2.0})
    put(engine, "BBB", t_bbb, {"rvol": 5.0})
    put(engine, "CCC", t_ccc, {"rvol": 1.0})

    with use_engine(engine):
        results, skipped = run_scan(["AAA", "BBB", "CCC", "ZZZ"], **WEIGHTS)

    assert skipped == ["ZZZ"]
    by_symbol = {r.symbol: r for r in results}
    assert by_symbol["AAA"].source_candle_ts == t_aaa
    assert by_symbol["BBB"].source_candle_ts == t_bbb
    assert by_symbol["CCC"].source_candle_ts == t_ccc
    assert len({r.source_candle_ts for r in results}) == 3  # different source times across symbols stay different
    assert all(r.source_candle_ts.tzinfo is not None for r in results)


def test_scores_ordering_zero_input_rows_and_skips_are_unchanged():
    engine = make_engine()
    data = {
        "AAA": (datetime(2026, 10, 8, 14, 29, tzinfo=timezone.utc), {"rvol": 2.3, "gap_pct": 1.8, "session_pct_change": -0.9, "atr_14_pct": 2.1}),
        "BBB": (OLD, {"rvol": 5.1}),
        "ZERO": (OLD, {"sma_9": 230.0}),  # no scoring input at all: still a retained row, score 0.0
    }
    for symbol, (ts, features) in data.items():
        put(engine, symbol, ts, features)

    with use_engine(engine):
        results, skipped = run_scan(["AAA", "BBB", "ZERO", "NONE"], **WEIGHTS)

    expected = {
        symbol: score_symbol(symbol, FeatureSet(timeframe="1m", candle_ts=ts, close=100.0, features=features), **WEIGHTS)
        for symbol, (ts, features) in data.items()
    }
    assert [r.symbol for r in results] == ["BBB", "AAA", "ZERO"]  # descending score, unchanged
    for r in results:
        assert r.score == expected[r.symbol].score
        assert r.inputs_available == expected[r.symbol].inputs_available
    zero = results[-1]
    assert (zero.score, zero.inputs_available, zero.features) == (0.0, 0, {})
    assert zero.source_candle_ts == OLD  # a zero-input row still states which candle it was read from
    assert skipped == ["NONE"]  # no snapshot row at all: skipped, no timestamp invented


def test_one_snapshot_read_per_symbol_and_none_for_skipped_extra_reads():
    engine = make_engine()
    put(engine, "AAA", OLD, {"rvol": 1.0})
    counting = CountingEngine(engine)

    with use_engine(counting):
        run_scan(["AAA", "MISSING"], **WEIGHTS)

    assert counting.calls == ["AAA", "MISSING"]  # exactly one read each; no second read to attach a timestamp


def test_timestamp_and_inputs_stay_paired_when_engine_state_is_replaced_during_acquisition():
    engine = make_engine()
    t_old = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)
    t_new = datetime(2026, 10, 8, 14, 1, tzinfo=timezone.utc)
    put(engine, "AAA", t_old, {"rvol": 2.0})
    put(engine, "BBB", t_old, {"rvol": 4.0})
    counting = CountingEngine(engine)

    def replace_everything(symbol):
        # A new candle closes right after each read: both the symbol just read
        # and the not-yet-read ones are replaced with different data and time.
        put(engine, "AAA", t_new, {"rvol": 99.0})
        put(engine, "BBB", t_new, {"rvol": 99.0})

    counting.after_read = replace_everything

    with use_engine(counting):
        results, _ = run_scan(["AAA", "BBB"], **WEIGHTS)

    by_symbol = {r.symbol: r for r in results}
    # AAA was read before any replacement: old time with old inputs.
    assert (by_symbol["AAA"].source_candle_ts, by_symbol["AAA"].features) == (t_old, {"rvol": 2.0})
    # BBB was read after the first replacement: new time with the replacement's inputs.
    assert (by_symbol["BBB"].source_candle_ts, by_symbol["BBB"].features) == (t_new, {"rvol": 99.0})
    assert by_symbol["AAA"].score == 2.0 and by_symbol["BBB"].score == 99.0
    assert counting.calls == ["AAA", "BBB"]


def test_replacement_between_read_and_scoring_cannot_unpair_the_row():
    engine = make_engine()
    t_old = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)
    t_new = datetime(2026, 10, 8, 14, 1, tzinfo=timezone.utc)
    put(engine, "AAA", t_old, {"rvol": 2.0})
    real_score = runner_module.score_symbol

    def replacing_score(symbol, feature_set, **kwargs):
        put(engine, "AAA", t_new, {"rvol": 99.0})  # engine replaced while scoring
        return real_score(symbol, feature_set, **kwargs)

    with use_engine(engine), patch.object(runner_module, "score_symbol", replacing_score):
        results, _ = run_scan(["AAA"], **WEIGHTS)

    assert results[0].source_candle_ts == t_old
    assert results[0].features == {"rvol": 2.0}
    assert results[0].score == 2.0


def test_a_recently_run_scan_can_carry_old_source_data():
    engine = make_engine()
    put(engine, "AAA", OLD, {"rvol": 3.0})

    with use_engine(engine):
        results, _ = run_scan(["AAA"], **WEIGHTS)

    # The scan happens "now" (2026-10-08) but the candle it scored is from 2026-10-05.
    assert results[0].source_candle_ts == OLD
    assert datetime(2026, 10, 8, tzinfo=timezone.utc) - results[0].source_candle_ts > timedelta(days=2)


def test_scanresult_remains_constructible_by_existing_callers():
    positional = ScanResult("AAA", 2.0, 3, {"rvol": 2.0})
    keyword = ScanResult(symbol="AAA", score=2.0, inputs_available=3, features={"rvol": 2.0})

    assert positional.source_candle_ts is None  # unknown, never guessed
    assert positional == keyword
    assert dataclasses.is_dataclass(positional) and positional.__dataclass_params__.frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        positional.source_candle_ts = OLD  # type: ignore[misc]


# ------------------------------------------------------------ GET /scanner/state


async def test_on_demand_state_response_contract_is_unchanged():
    engine = make_engine()
    put(engine, "AAA", OLD, {"rvol": 2.0, "gap_pct": 1.0, "session_pct_change": 1.0, "atr_14_pct": 2.0})
    put(engine, "BBB", T0, {"rvol": 5.0})

    with use_engine(engine):
        response = await _get("/scanner/state", symbols="AAA,BBB,CCC")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"universe", "results", "total_scored", "skipped"}  # no read_at, no new key
    assert body["universe"] == ["AAA", "BBB", "CCC"]
    assert body["skipped"] == ["CCC"]
    assert body["total_scored"] == 2
    for row in body["results"]:
        assert set(row) == {"symbol", "score", "inputs_available", "features"}  # no source_candle_ts here
    assert [r["symbol"] for r in body["results"]] == ["BBB", "AAA"]
    assert body["results"][1]["features"] == {"rvol": 2.0, "gap_pct": 1.0, "session_pct_change": 1.0, "atr_14_pct": 2.0}


# --------------------------------------------------- worker + observation route


async def test_worker_rows_carry_source_ts_and_route_serializes_it_utc_with_read_at(read_clock):
    engine = make_engine()
    t_aaa = datetime(2026, 10, 8, 14, 29, tzinfo=timezone.utc)
    put(engine, "AAA", t_aaa, {"rvol": 2.0})
    put(engine, "BBB", OLD, {"rvol": 1.0})
    clock = Clock()
    worker = make_worker(clock, Universe(["AAA", "BBB", "CCC"]))
    fastapi_app.state.scanner_observation_reader = worker

    with use_engine(engine):
        await worker.start()
        try:
            await until(lambda: worker.get_snapshot().last_success_at is not None)
        finally:
            await worker.stop()

    body = (await _get()).json()

    assert body["read_at"] == "2026-10-08T14:31:30Z"
    obs = body["observation"]
    assert obs["last_success_at"] == "2026-10-08T14:30:00Z"  # scan completion time...
    rows = {r["symbol"]: r for r in obs["results"]}
    assert rows["AAA"]["source_candle_ts"] == "2026-10-08T14:29:00Z"  # ...differs from data time per symbol
    assert rows["BBB"]["source_candle_ts"] == "2026-10-05T20:59:00Z"  # recent scan, three-day-old source data
    assert obs["skipped"] == ["CCC"]
    assert all(r["symbol"] != "CCC" for r in obs["results"])  # skipped symbol: no row, so no timestamp to show
    assert [r["symbol"] for r in obs["results"]] == ["AAA", "BBB"]  # ordering is the scorer's, unchanged


async def test_failed_later_cycle_retains_the_prior_successful_source_timestamps(read_clock):
    engine = make_engine()
    put(engine, "AAA", OLD, {"rvol": 2.0})
    put(engine, "BBB", T0, {"rvol": 1.0})
    clock = Clock()
    universe = Universe(["AAA", "BBB"])
    worker = make_worker(clock, universe)
    fastapi_app.state.scanner_observation_reader = worker

    with use_engine(engine):
        await worker.start()
        try:
            await until(lambda: worker.get_snapshot().last_success_at is not None)
            before = worker.get_snapshot()
            before_body = (await _get()).json()["observation"]
            await until(lambda: bool(clock.waiters))
            # The engine moves on, but the next cycle fails before it can rescan.
            put(engine, "AAA", T0 + timedelta(minutes=1), {"rvol": 50.0})
            universe.fail = True
            clock.advance(60)
            await until(lambda: worker.get_snapshot().last_error is not None)
            after = worker.get_snapshot()
            after_body = (await _get()).json()["observation"]
        finally:
            await worker.stop()

    assert after.last_error == "RuntimeError: universe read failed"
    assert after.results is before.results  # the very same retained tuple, not rebuilt
    assert [(r.symbol, r.source_candle_ts) for r in after.results] == [("AAA", OLD), ("BBB", T0)]
    assert after_body["latest_attempt"] == "failed"
    assert after_body["results"] == before_body["results"]  # includes source_candle_ts, byte-for-byte
    assert after_body["last_success_at"] == before_body["last_success_at"]
    assert {r["symbol"]: r["source_candle_ts"] for r in after_body["results"]} == {
        "AAA": "2026-10-05T20:59:00Z", "BBB": "2026-10-08T14:30:00Z",
    }


async def test_scan_failure_after_success_also_retains_source_timestamps():
    engine = make_engine()
    put(engine, "AAA", OLD, {"rvol": 2.0})
    clock = Clock()
    state = {"fail": False}

    def flaky_scan(universe, **weights):
        if state["fail"]:
            raise RuntimeError("scan blew up")
        return run_scan(universe, **weights)

    worker = make_worker(clock, Universe(["AAA"]), scan=flaky_scan)
    with use_engine(engine):
        await worker.start()
        try:
            await until(lambda: worker.get_snapshot().last_success_at is not None)
            await until(lambda: bool(clock.waiters))
            state["fail"] = True
            clock.advance(60)
            await until(lambda: worker.get_snapshot().last_error is not None)
            snapshot = worker.get_snapshot()
        finally:
            await worker.stop()

    assert snapshot.last_error == "RuntimeError: scan blew up"
    assert [(r.symbol, r.source_candle_ts) for r in snapshot.results] == [("AAA", OLD)]


async def test_a_later_successful_cycle_replaces_the_source_timestamps():
    engine = make_engine()
    put(engine, "AAA", OLD, {"rvol": 2.0})
    clock = Clock()
    worker = make_worker(clock, Universe(["AAA"]))
    with use_engine(engine):
        await worker.start()
        try:
            await until(lambda: worker.get_snapshot().last_success_at is not None)
            first = worker.get_snapshot()
            await until(lambda: bool(clock.waiters))
            put(engine, "AAA", T0, {"rvol": 3.0})
            clock.advance(60)
            await until(lambda: worker.get_snapshot().last_success_at != first.last_success_at)
            second = worker.get_snapshot()
        finally:
            await worker.stop()

    assert first.results[0].source_candle_ts == OLD  # the earlier snapshot object is untouched
    assert second.results[0].source_candle_ts == T0


async def test_successful_empty_result_has_no_rows_and_no_fabricated_timestamps(read_clock):
    engine = make_engine()  # nothing computed for any symbol yet
    clock = Clock()
    worker = make_worker(clock, Universe(["AAA", "BBB"]))
    fastapi_app.state.scanner_observation_reader = worker
    with use_engine(engine):
        await worker.start()
        try:
            await until(lambda: worker.get_snapshot().last_success_at is not None)
        finally:
            await worker.stop()

    body = (await _get()).json()

    obs = body["observation"]
    assert obs["retained"] == "empty" and obs["latest_attempt"] == "succeeded"
    assert obs["results"] == [] and obs["skipped"] == ["AAA", "BBB"]
    assert body["read_at"] == "2026-10-08T14:31:30Z"


async def test_missing_source_timestamps_are_null_never_filled_in(read_clock):
    class OldScanDouble:  # an injected scan that predates the field entirely
        row = SimpleNamespace(symbol="BBB", score=1.0, inputs_available=1, features={"rvol": 1.0})

    def scan_double(universe, **weights):
        return [
            ScanResult("AAA", 2.0, 3, {"rvol": 2.0}),  # constructed without a timestamp
            OldScanDouble.row,  # no attribute at all
            ScanResult("CCC", 0.5, 1, {"rvol": 0.5}, source_candle_ts=OLD),
        ], []

    clock = Clock()
    worker = make_worker(clock, Universe(["AAA", "BBB", "CCC"]), scan=scan_double)
    fastapi_app.state.scanner_observation_reader = worker
    await worker.start()
    try:
        await until(lambda: worker.get_snapshot().last_success_at is not None)
    finally:
        await worker.stop()

    rows = {r["symbol"]: r for r in (await _get()).json()["observation"]["results"]}

    assert rows["AAA"]["source_candle_ts"] is None
    assert rows["BBB"]["source_candle_ts"] is None  # the double had no attribute at all
    assert rows["CCC"]["source_candle_ts"] == "2026-10-05T20:59:00Z"
    assert "source_candle_ts" in rows["AAA"]  # the key is always present; null is the explicit "unknown"


def test_utc_serialization_of_offset_and_naive_timestamps():
    plus6 = datetime(2026, 10, 8, 20, 29, tzinfo=timezone(timedelta(hours=6)))  # == 14:29Z
    engine = make_engine()
    put(engine, "AAA", plus6, {"rvol": 1.0})
    with use_engine(engine):
        results, _ = run_scan(["AAA"], **WEIGHTS)
    snapshot = ObservationSnapshot(
        universe=("AAA", "NAIVE"),
        results=(
            ObservedScanRow("AAA", results[0].score, results[0].inputs_available, {}, results[0].source_candle_ts),
            ObservedScanRow("NAIVE", 0.0, 0, {}, datetime(2026, 10, 8, 14, 29)),  # naive: the codebase's UTC convention
        ),
        last_attempt_at=T0, last_success_at=T0, running=True,
    )

    rows = {r["symbol"]: r for r in _project_observation(snapshot)["observation"]["results"]}

    assert rows["AAA"]["source_candle_ts"] == "2026-10-08T14:29:00Z"
    assert rows["NAIVE"]["source_candle_ts"] == "2026-10-08T14:29:00Z"
    assert all(r["source_candle_ts"].endswith("Z") and "+" not in r["source_candle_ts"] for r in rows.values())


async def test_future_source_timestamps_are_reported_as_stored_not_clamped(read_clock):
    future = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)  # after read_at (14:31:30Z)
    install = FakeReader(ObservationSnapshot(
        universe=("AAA",),
        results=(ObservedScanRow("AAA", 1.0, 3, {}, future),),
        last_attempt_at=T0, last_success_at=T0, running=True,
    ))
    fastapi_app.state.scanner_observation_reader = install

    body = (await _get()).json()

    assert body["observation"]["results"][0]["source_candle_ts"] == "2026-10-08T15:00:00Z"  # not pulled back to read_at
    assert body["read_at"] == "2026-10-08T14:31:30Z"
    assert not any("stale" in key or "fresh" in key for key in body["observation"]["results"][0])  # no classification


async def test_read_at_is_one_server_timestamp_taken_with_the_single_snapshot_read(read_clock):
    reader = FakeReader(ObservationSnapshot(last_attempt_at=T0, last_success_at=T0, running=True))
    fastapi_app.state.scanner_observation_reader = reader

    first = (await _get()).json()
    read_clock.now += timedelta(seconds=45)
    second = (await _get()).json()

    assert reader.reads == 2  # exactly one snapshot read per request
    assert first["read_at"] == "2026-10-08T14:31:30Z"
    assert second["read_at"] == "2026-10-08T14:32:15Z"  # advances only with the server clock, per request


async def test_unavailable_response_keeps_its_shape_and_adds_read_at(read_clock):
    body = (await _get()).json()

    assert body["status"] == "unavailable"
    assert body["worker"] is None and body["observation"] is None
    assert body["read_at"] == "2026-10-08T14:31:30Z"


async def test_observation_route_never_reads_the_feature_engine(read_clock):
    fastapi_app.state.scanner_observation_reader = FakeReader(ObservationSnapshot(
        universe=("AAA",), results=(ObservedScanRow("AAA", 1.0, 3, {}, OLD),),
        last_attempt_at=T0, last_success_at=T0, running=True,
    ))
    boom = MagicMock(side_effect=AssertionError("the route must not touch the FeatureEngine"))

    with patch.object(runner_module, "get_feature_engine", boom):
        response = await _get()

    assert response.status_code == 200
    boom.assert_not_called()


async def test_retained_state_is_immutable_and_response_mutation_cannot_reach_it(read_clock):
    snapshot = ObservationSnapshot(
        universe=("AAA",),
        results=(ObservedScanRow("AAA", 1.0, 3, {"rvol": 1.0}, OLD),),
        last_attempt_at=T0, last_success_at=T0, running=True,
    )
    reader = FakeReader(snapshot)
    fastapi_app.state.scanner_observation_reader = reader
    pristine = copy.deepcopy(_project_observation(snapshot))

    projected = _project_observation(snapshot)
    projected["observation"]["results"][0]["source_candle_ts"] = "tampered"
    projected["observation"]["results"].clear()
    body = (await _get()).json()

    assert _project_observation(snapshot) == pristine  # projection is rebuilt from the untouched snapshot
    assert body["observation"]["results"][0]["source_candle_ts"] == "2026-10-05T20:59:00Z"
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.results[0].source_candle_ts = T0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.results = ()  # type: ignore[misc]
    assert snapshot.results[0].source_candle_ts == OLD
