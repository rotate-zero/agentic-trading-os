"""HTTP-level tests for POST /backtest/run (decision #130). Real
Postgres, DB-gated, same convention as the rest of this suite.

Deliberately does NOT re-test `BacktestRunner`'s own orchestration
(fill timing, snapshot-capture instant, persistence correctness) — that's
already covered by `test_backtest_runner.py`. What's under test here is
this route specifically: query-param validation, strategy/scenario
lookup wiring, and the real HTTP response shape.

**Why two of these tests are genuinely slow (~130-140s each), and why
that's deliberate rather than something to work around.** `POST
/backtest/run` is a fully synchronous call — `EngineBackedReplayStateProducer`
costs a measured, real ~1 second of engine-settle time per replayed
candle, and this route's own docstring is explicit that a caller should
expect the HTTP response itself to take that long. A test using a short
timeout, or one that only checks the fast validation-error paths, would
quietly hide that reality rather than prove it — so
`test_run_backtest_first_pullback_scenario_fires_and_persists` below
deliberately measures its own wall-clock elapsed time and asserts a
generous floor, specifically so a future change that accidentally
short-circuits the replay (e.g. a bug that returns before really
replaying every candle) would fail this test, not just silently ship a
faster-but-wrong response. `TestClient`'s in-process ASGI transport does
NOT enforce httpx's normal 5-second default client timeout (confirmed
directly: a throwaway `asyncio.sleep(7)` endpoint returns successfully
through `TestClient` well past 5 seconds) — so these tests would pass
even if this route somehow took much longer than documented in a real
deployment; they prove correctness and genuine non-trivial elapsed time,
not an upper bound on latency. Whether a real deployment's own
infrastructure (a reverse proxy or load balancer in front of this
service, if one is ever added) would truncate a multi-minute request is
outside anything a test against this app alone can determine — see this
route's own docstring for what was checked about this codebase's actual
deployment (no such component exists in it today).
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app

FIRST_PULLBACK_SYMBOL = "ZBTR1"
VOLUME_GATED_SYMBOL = "ZBTR2"


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
def _clean_before_and_after():
    _clean_test_symbol(FIRST_PULLBACK_SYMBOL)
    _clean_test_symbol(VOLUME_GATED_SYMBOL)
    yield
    _clean_test_symbol(FIRST_PULLBACK_SYMBOL)
    _clean_test_symbol(VOLUME_GATED_SYMBOL)


def test_run_backtest_first_pullback_scenario_fires_and_persists():
    """The guaranteed-fire path: a real strategy, a real scenario,
    genuinely records a real StrategyOutcomeRecord through this route —
    not just through direct BacktestRunner construction."""
    with TestClient(app) as client:
        t0 = time.monotonic()
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "FirstPullback",
                "symbol": FIRST_PULLBACK_SYMBOL,
                "scenario": "first_pullback_vwap_dip",
            },
        )
        elapsed = time.monotonic() - t0

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcomes_recorded"] == 1
    assert body["discarded_signals"] == []
    # Real UUIDs, not placeholders.
    uuid.UUID(body["run_id"])
    uuid.UUID(body["sweep_id"])

    # This scenario is 130 candles; ~1s/candle is a measured, real cost
    # (see this module's own docstring), not an incidental slowdown —
    # a response coming back near-instantly would mean the replay didn't
    # actually happen. Floor is deliberately well under the ~130s this
    # should really take, to avoid flaking on a slower CI machine, while
    # still being far enough above zero to catch a short-circuit.
    assert elapsed > 60, (
        f"expected a ~130s synchronous replay (130 candles @ ~1s/candle), "
        f"got {elapsed:.1f}s — did the replay actually run?"
    )

    # Verify against the DB directly, same posture test_backtest_runner.py
    # already takes — don't just trust the response body.
    session = SessionLocal()
    try:
        row = session.execute(
            text(
                "SELECT strategy_name, direction, exit_reason FROM strategy_outcomes "
                "WHERE symbol = :t"
            ),
            {"t": FIRST_PULLBACK_SYMBOL},
        ).fetchone()
    finally:
        session.close()
    assert row is not None
    assert row.strategy_name == "FirstPullback"
    assert row.direction == "BUY"
    assert row.exit_reason == "target"


def test_run_backtest_volume_gated_strategy_returns_honest_zero():
    """ORB against the baseline scenario is expected, documented,
    correct behavior to return 200 with zero recorded outcomes — not an
    error. Exercises the route's real response shape for that path too,
    not just the guaranteed-fire path above."""
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "ORB",
                "symbol": VOLUME_GATED_SYMBOL,
                "scenario": "volume_gated_baseline",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcomes_recorded"] == 0
    assert body["discarded_signals"] == []


def test_run_backtest_rejects_unknown_strategy_name():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "NotARealStrategy",
                "symbol": FIRST_PULLBACK_SYMBOL,
                "scenario": "first_pullback_vwap_dip",
            },
        )

    assert resp.status_code == 400
    assert "NotARealStrategy" in resp.json()["detail"]
    assert "FirstPullback" in resp.json()["detail"]


def test_run_backtest_rejects_unknown_scenario():
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "FirstPullback",
                "symbol": FIRST_PULLBACK_SYMBOL,
                "scenario": "not_a_real_scenario",
            },
        )

    assert resp.status_code == 400
    assert "not_a_real_scenario" in resp.json()["detail"]
    assert "first_pullback_vwap_dip" in resp.json()["detail"]


def test_run_backtest_requires_all_three_query_params():
    with TestClient(app) as client:
        resp = client.post("/backtest/run", params={"strategy_name": "FirstPullback"})

    # FastAPI's own required-query-param validation (422), not this
    # route's own 400s -- confirms no silent defaults were introduced.
    assert resp.status_code == 422
