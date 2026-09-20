"""HTTP-level tests for POST /backtest/sweep (decision #159). Real
Postgres, DB-gated, same convention as `test_backtest_routes.py`.

Deliberately does NOT re-test `BacktestRunner`'s own orchestration or
`sweep_id` constructor threading — those are covered by
`test_backtest_runner.py`. What's under test here is the route itself:
pre-execution validation (strategy/scenario/batch-size), the real
sequential-execution/shared-sweep_id contract proven against the actual
lock (not mocked), deterministic response ordering, and partial-failure
handling.

One real, load-bearing finding this suite deliberately does NOT paper
over: `level_interaction_state`/`level_interaction_events` carry no
`backtest_run_id` (unlike `daily_levels_state`, decision #141/D19), so a
second real run against the *same symbol* can legitimately produce a
different `outcomes_recorded` than the first — confirmed reproducible
via the existing, unmodified `/backtest/run` route called twice, so this
is a pre-existing `BacktestRunner` characteristic, not a sweep defect.
See this delivery's decision-log entry. No test here asserts that
repeat-symbol runs produce identical outcome counts.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.api.routes import backtest as backtest_route
from app.api.routes import finnhub_data, market_data
from app.backtest_runner import runner as runner_module
from app.backtest_runner.engine_singleton_guard import (
    install_replay_engines as real_install_replay_engines,
)
from app.db.session import SessionLocal
from app.main import app

# Distinct prefix from test_backtest_routes.py's ZBTR* symbols — no collision risk.
SWEEP_A = "ZSWA"
SWEEP_B = "ZSWB"
SWEEP_DUP = "ZSWDUP"
SWEEP_PF_A = "ZSWPFA"
SWEEP_PF_B = "ZSWPFB"
BATCH20_SYMBOLS = [f"ZSWBAT{i}" for i in range(5)]
BATCH21_SYMBOLS = [f"ZSWREJ{i}" for i in range(21)]

ALL_TEST_SYMBOLS = [SWEEP_A, SWEEP_B, SWEEP_DUP, SWEEP_PF_A, SWEEP_PF_B, *BATCH20_SYMBOLS, *BATCH21_SYMBOLS]


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


def _clean_test_symbol(ticker: str) -> None:
    """Copied verbatim from test_backtest_routes.py's own helper — these
    tables key off `symbol_id` (FK to `symbols`), not a bare `symbol`/
    `ticker` text column, so the subquery form is required."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes WHERE symbol = :t"), {"t": ticker})
        session.execute(text("DELETE FROM backtests WHERE :t = ANY(symbol_universe)"), {"t": ticker})
        for table in (
            "level_interaction_events",
            "level_interaction_state",
            "daily_levels_state",
            "market_state_history",
            "symbol_fundamentals",
            "scanner_universe_symbols",
            "candles",
        ):
            session.execute(
                text(f"DELETE FROM {table} WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
                {"t": ticker},
            )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_symbols():
    for symbol in ALL_TEST_SYMBOLS:
        _clean_test_symbol(symbol)
    yield
    for symbol in ALL_TEST_SYMBOLS:
        _clean_test_symbol(symbol)


def _sweep_rows(sweep_id: str) -> list:
    session = SessionLocal()
    try:
        return session.execute(
            text("SELECT run_id, sweep_id, symbol_universe FROM backtests WHERE sweep_id = :s"),
            {"s": sweep_id},
        ).fetchall()
    finally:
        session.close()


# --- Happy path: real multi-run sweep, sequential, shared sweep_id ---------


def test_sweep_valid_cross_product_shares_sweep_id_and_preserves_order():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[
                ("strategy_name", "FirstPullback"),
                ("symbols", SWEEP_A),
                ("symbols", SWEEP_B),
                ("scenarios", "first_pullback_vwap_dip"),
                ("scenarios", "vwap_neutral_conquest"),
            ],
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_name"] == "FirstPullback"
    assert body["pairs_requested"] == 4
    assert body["pairs_succeeded"] == 4
    assert body["pairs_failed"] == 0

    runs = body["runs"]
    assert len(runs) == 4
    # Deterministic caller ordering: symbols outer, scenarios inner —
    # never database/collection order.
    expected_pairs = [
        (SWEEP_A, "first_pullback_vwap_dip"),
        (SWEEP_A, "vwap_neutral_conquest"),
        (SWEEP_B, "first_pullback_vwap_dip"),
        (SWEEP_B, "vwap_neutral_conquest"),
    ]
    assert [(r["symbol"], r["scenario"]) for r in runs] == expected_pairs

    for r in runs:
        assert r["error"] is None
        assert r["run_id"] is not None
        assert r["outcomes_recorded"] is not None

    run_ids = {r["run_id"] for r in runs}
    assert len(run_ids) == 4  # every run is real and distinct

    rows = _sweep_rows(body["sweep_id"])
    assert len(rows) == 4
    assert all(str(row.sweep_id) == body["sweep_id"] for row in rows)
    assert {str(row.run_id) for row in rows} == run_ids


def test_sweep_runs_strictly_sequentially_through_the_real_lock():
    """Proves sequential execution against the REAL `_RUN_LOCK` path —
    not a timing floor. Spies on the actual `install_replay_engines`
    context manager (still delegating to the real implementation, real
    lock, real engines) and asserts the recorded [enter, exit] intervals
    never overlap."""
    intervals: list[tuple[float, float]] = []

    @asynccontextmanager
    async def spy(**kwargs):
        t_enter = time.monotonic()
        async with real_install_replay_engines(**kwargs):
            yield
        t_exit = time.monotonic()
        intervals.append((t_enter, t_exit))

    original = runner_module.install_replay_engines
    runner_module.install_replay_engines = spy
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/backtest/sweep",
                params=[
                    ("strategy_name", "FirstPullback"),
                    ("symbols", SWEEP_A),
                    ("symbols", SWEEP_B),
                    ("scenarios", "volume_gated_baseline"),
                ],
            )
    finally:
        runner_module.install_replay_engines = original

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs_requested"] == 2
    assert body["pairs_succeeded"] == 2

    assert len(intervals) == 2
    intervals.sort()
    (a_start, a_end), (b_start, b_end) = intervals
    assert a_end <= b_start, "second run's lock window started before the first run's ended — not sequential"


def test_sweep_rejects_when_live_data_connected_before_any_run(monkeypatch):
    """Same guard `/backtest/run` already applies (decision #132),
    checked once before the sweep starts — see the route's own
    docstring for why per-pair re-checking isn't warranted here."""
    monkeypatch.setattr(finnhub_data, "is_connected", lambda: True)
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[
                ("strategy_name", "FirstPullback"),
                ("symbols", SWEEP_A),
                ("symbols", SWEEP_B),
                ("scenarios", "first_pullback_vwap_dip"),
            ],
        )

    assert resp.status_code == 409
    assert "Finnhub" in resp.json()["detail"]
    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT 1 FROM strategy_outcomes WHERE symbol = ANY(:t)"), {"t": [SWEEP_A, SWEEP_B]}
        ).fetchone()
    finally:
        session.close()
    assert row is None, "the guard must fire before any run starts, not after"


# --- Pre-execution validation ------------------------------------------------


def test_sweep_rejects_unknown_strategy_name():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[
                ("strategy_name", "NotARealStrategy"),
                ("symbols", SWEEP_A),
                ("scenarios", "first_pullback_vwap_dip"),
            ],
        )
    assert resp.status_code == 400


def test_sweep_rejects_unknown_scenario_before_any_run():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[
                ("strategy_name", "FirstPullback"),
                ("symbols", SWEEP_A),
                ("scenarios", "first_pullback_vwap_dip"),
                ("scenarios", "not_a_real_scenario"),
            ],
        )
    assert resp.status_code == 400
    session = SessionLocal()
    try:
        count = session.execute(
            text("SELECT count(*) FROM backtests WHERE :t = ANY(symbol_universe)"), {"t": SWEEP_A}
        ).scalar()
    finally:
        session.close()
    assert count == 0, "an unknown scenario must reject the whole request before any run starts"


def test_sweep_requires_symbols_param():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[("strategy_name", "FirstPullback"), ("scenarios", "first_pullback_vwap_dip")],
        )
    assert resp.status_code == 422


def test_sweep_requires_scenarios_param():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[("strategy_name", "FirstPullback"), ("symbols", SWEEP_A)],
        )
    assert resp.status_code == 422


def test_sweep_rejects_empty_symbol_value():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[
                ("strategy_name", "FirstPullback"),
                ("symbols", "   "),
                ("scenarios", "first_pullback_vwap_dip"),
            ],
        )
    assert resp.status_code == 422


def test_sweep_accepts_exactly_the_max_batch_size():
    """5 symbols × 4 scenarios = 20 — right at `_MAX_SWEEP_PAIRS`. Runs
    for real (not just validated) to prove the bound is on requested
    pairs, not an arbitrary smaller cap."""
    params = [("strategy_name", "ORB")]
    params += [("symbols", s) for s in BATCH20_SYMBOLS]
    params += [
        ("scenarios", s)
        for s in (
            "first_pullback_vwap_dip",
            "reversal_vwap_break",
            "vwap_neutral_conquest",
            "volume_gated_baseline",
        )
    ]
    with TestClient(app) as client:
        resp = client.post("/backtest/sweep", params=params)

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs_requested"] == 20
    assert body["pairs_succeeded"] == 20
    assert body["pairs_failed"] == 0
    rows = _sweep_rows(body["sweep_id"])
    assert len(rows) == 20


def test_sweep_rejects_21_pairs_before_any_run():
    params = [("strategy_name", "ORB")]
    params += [("symbols", s) for s in BATCH21_SYMBOLS]
    params += [("scenarios", "volume_gated_baseline")]
    with TestClient(app) as client:
        resp = client.post("/backtest/sweep", params=params)

    assert resp.status_code == 400
    session = SessionLocal()
    try:
        count = sum(
            session.execute(
                text("SELECT count(*) FROM backtests WHERE :t = ANY(symbol_universe)"), {"t": s}
            ).scalar()
            for s in BATCH21_SYMBOLS
        )
    finally:
        session.close()
    assert count == 0, "21 requested pairs must be rejected before any run starts"


# --- Partial-failure handling -----------------------------------------------


def test_sweep_partial_failure_continues_and_preserves_earlier_successes(monkeypatch):
    """Forces a genuine, real per-pair error (not an honest zero-outcome
    run) for one scenario, deterministically, via a narrow monkeypatch —
    everything else in the request still runs through the real
    BacktestRunner/lock path. Proves: (a) a failed pair doesn't abort
    pairs requested after it, (b) already-succeeded pairs' real DB rows
    are preserved, (c) the failure is reported per-pair, not hidden."""
    real_load = backtest_route.load_scenario_candles
    FAIL_SCENARIO = "vwap_neutral_conquest"

    def flaky_load(name: str):
        if name == FAIL_SCENARIO:
            raise RuntimeError("synthetic failure injected for sweep partial-failure test")
        return real_load(name)

    monkeypatch.setattr(backtest_route, "load_scenario_candles", flaky_load)

    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[
                ("strategy_name", "FirstPullback"),
                ("symbols", SWEEP_PF_A),
                ("symbols", SWEEP_PF_B),
                ("scenarios", "first_pullback_vwap_dip"),
                ("scenarios", FAIL_SCENARIO),
            ],
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs_requested"] == 4
    assert body["pairs_succeeded"] == 2
    assert body["pairs_failed"] == 2

    runs = body["runs"]
    assert (runs[0]["symbol"], runs[0]["scenario"]) == (SWEEP_PF_A, "first_pullback_vwap_dip")
    assert runs[0]["error"] is None and runs[0]["run_id"] is not None

    assert (runs[1]["symbol"], runs[1]["scenario"]) == (SWEEP_PF_A, FAIL_SCENARIO)
    assert runs[1]["error"] is not None and "RuntimeError" in runs[1]["error"]
    assert runs[1]["run_id"] is None

    # Crucially: the pair requested AFTER the failure still ran for real.
    assert (runs[2]["symbol"], runs[2]["scenario"]) == (SWEEP_PF_B, "first_pullback_vwap_dip")
    assert runs[2]["error"] is None and runs[2]["run_id"] is not None

    assert (runs[3]["symbol"], runs[3]["scenario"]) == (SWEEP_PF_B, FAIL_SCENARIO)
    assert runs[3]["error"] is not None

    rows = _sweep_rows(body["sweep_id"])
    assert len(rows) == 2  # only the two real successes persisted
    assert {str(r.run_id) for r in rows} == {runs[0]["run_id"], runs[2]["run_id"]}


def test_sweep_processes_duplicate_requested_pairs_as_independent_real_runs():
    """Same (symbol, scenario) pair listed twice: both are executed as
    real, independent runs — no crash, no dedup, matching the confirmed
    design (the caller says exactly what to sweep). Deliberately does
    NOT assert both produce identical outcomes_recorded — see this
    file's module docstring for why that assumption would be false."""
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/sweep",
            params=[
                ("strategy_name", "FirstPullback"),
                ("symbols", SWEEP_DUP),
                ("scenarios", "first_pullback_vwap_dip"),
                ("scenarios", "first_pullback_vwap_dip"),
            ],
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs_requested"] == 2
    assert body["pairs_succeeded"] == 2
    runs = body["runs"]
    assert runs[0]["run_id"] != runs[1]["run_id"]
    assert runs[0]["error"] is None and runs[1]["error"] is None

    rows = _sweep_rows(body["sweep_id"])
    assert len(rows) == 2


# --- Existing single-run route stays intact ---------------------------------


def test_run_route_unaffected_by_sweep_route_presence():
    """Lightweight smoke check alongside the sweep tests above — the
    full existing regression suite (`test_backtest_routes.py`) is the
    real proof and is re-run as part of this delivery's verification."""
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={"strategy_name": "FirstPullback", "symbol": SWEEP_A, "scenario": "first_pullback_vwap_dip"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcomes_recorded"] == 1
