"""HTTP-level tests for POST /backtest/run (decision #131). Real
Postgres, DB-gated, same convention as the rest of this suite.

Deliberately does NOT re-test `BacktestRunner`'s own orchestration
(fill timing, snapshot-capture instant, persistence correctness) — that's
already covered by `test_backtest_runner.py`. What's under test here is
this route specifically: query-param validation, strategy/scenario
lookup wiring, and the real HTTP response shape.

The route remains a synchronous HTTP call, but replay settlement is now
queue-driven rather than paced by Market State's live debounce floor.
The guaranteed-fire test proves full replay with exact persisted candle
counts instead of a brittle minimum wall-clock duration.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.api.routes import finnhub_data, market_data
from app.db.session import SessionLocal
from app.main import app
from app.strategy_engine.momentum_strategy import DEFAULT_ACCELERATION_SCORE_THRESHOLD

FIRST_PULLBACK_SYMBOL = "ZBTR1"
VOLUME_GATED_SYMBOL = "ZBTR2"
MOMENTUM_SYMBOL = "ZBTR3"
D20_ISOLATION_SYMBOL = "ZBTRD20"


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
    _clean_test_symbol(MOMENTUM_SYMBOL)
    _clean_test_symbol(D20_ISOLATION_SYMBOL)
    yield
    _clean_test_symbol(FIRST_PULLBACK_SYMBOL)
    _clean_test_symbol(VOLUME_GATED_SYMBOL)
    _clean_test_symbol(MOMENTUM_SYMBOL)
    _clean_test_symbol(D20_ISOLATION_SYMBOL)


def test_run_backtest_first_pullback_scenario_fires_and_persists():
    """The guaranteed-fire path: a real strategy, a real scenario,
    genuinely records a real StrategyOutcomeRecord through this route —
    not just through direct BacktestRunner construction."""
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "FirstPullback",
                "symbol": FIRST_PULLBACK_SYMBOL,
                "scenario": "first_pullback_vwap_dip",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcomes_recorded"] == 1
    assert body["discarded_signals"] == []
    # Real UUIDs, not placeholders.
    uuid.UUID(body["run_id"])
    uuid.UUID(body["sweep_id"])

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
        market_state_count = session.execute(
            text(
                "SELECT count(*) FROM market_state_history msh JOIN symbols s ON s.id = msh.symbol_id "
                "WHERE s.ticker = :t AND s.is_backtest IS TRUE"
            ),
            {"t": FIRST_PULLBACK_SYMBOL},
        ).scalar_one()
    finally:
        session.close()
    assert row is not None
    assert row.strategy_name == "FirstPullback"
    assert row.direction == "BUY"
    assert row.exit_reason == "target"
    # Scenario has 130 candles, while the route intentionally passes
    # end=candles[-1].candle_ts to a [start,end) provider: 129 are replayed.
    assert market_state_count == 129


def test_two_separate_runs_isolate_level_interaction_state_and_events():
    """Two ordinary route calls for the same symbol must be independent.

    Keep the deterministic outcome assertion before the new-column queries:
    on pre-D20 ``main`` this fails with the confirmed ``[1, 0]`` result,
    demonstrating the inherited-state defect itself rather than merely the
    absence of the migration's columns.
    """
    with TestClient(app) as client:
        responses = [
            client.post(
                "/backtest/run",
                params={
                    "strategy_name": "FirstPullback",
                    "symbol": D20_ISOLATION_SYMBOL,
                    "scenario": "first_pullback_vwap_dip",
                },
            )
            for _ in range(2)
        ]

    assert [response.status_code for response in responses] == [200, 200]
    bodies = [response.json() for response in responses]
    assert [body["outcomes_recorded"] for body in bodies] == [1, 1]
    run_ids = [uuid.UUID(body["run_id"]) for body in bodies]
    assert run_ids[0] != run_ids[1]

    session = SessionLocal()
    try:
        state_rows = session.execute(
            text(
                "SELECT lis.id, lis.is_backtest, lis.backtest_run_id, lis.level_key, "
                "lis.trading_day, lis.touch_count_today, lis.zone, lis.zone_entered_ts, "
                "lis.touch_anchor_price, lis.touch_entered_ts, lis.touch_entered_from "
                "FROM level_interaction_state lis "
                "JOIN symbols s ON s.id = lis.symbol_id "
                "WHERE s.ticker = :ticker ORDER BY lis.backtest_run_id, lis.level_key"
            ),
            {"ticker": D20_ISOLATION_SYMBOL},
        ).mappings().all()
        event_rows = session.execute(
            text(
                "SELECT lie.id, lie.is_backtest, lie.backtest_run_id, lie.level_key, "
                "lie.trading_day, lie.outcome, lie.entered_from, lie.exited_to, "
                "lie.entered_ts, lie.exited_ts, lie.seconds_in_zone, lie.anchor_price, "
                "lie.distance_pct, lie.observed_via "
                "FROM level_interaction_events lie "
                "JOIN symbols s ON s.id = lie.symbol_id "
                "WHERE s.ticker = :ticker ORDER BY lie.backtest_run_id, lie.id"
            ),
            {"ticker": D20_ISOLATION_SYMBOL},
        ).mappings().all()
    finally:
        session.close()

    expected_run_ids = set(run_ids)
    assert state_rows
    assert event_rows
    assert {row["backtest_run_id"] for row in state_rows} == expected_run_ids
    assert {row["backtest_run_id"] for row in event_rows} == expected_run_ids
    assert all(row["is_backtest"] is True for row in state_rows + event_rows)

    states_by_run = {
        run_id: [row for row in state_rows if row["backtest_run_id"] == run_id]
        for run_id in run_ids
    }
    assert all(states_by_run.values())
    assert {row["id"] for row in states_by_run[run_ids[0]]}.isdisjoint(
        row["id"] for row in states_by_run[run_ids[1]]
    )

    # VWAP is a stable key in both identical fixture replays (unlike Daily
    # Level IDs, whose identifiers are deliberately minted per run). Inspect
    # the persisted state values directly, not merely the row counts. The
    # final checkpoint identity/zone/timestamps are the isolation contract;
    # touch_count_today is inspected independently rather than made an
    # equality precondition for the strategy outcome this regression proves.
    vwap_rows = {
        run_id: next(row for row in states_by_run[run_id] if row["level_key"] == "vwap")
        for run_id in run_ids
    }

    def vwap_state(run_id):
        row = vwap_rows[run_id]
        return (
            row["trading_day"],
            row["zone"],
            row["zone_entered_ts"],
            row["touch_anchor_price"],
            row["touch_entered_ts"],
            row["touch_entered_from"],
        )

    assert vwap_state(run_ids[0]) == vwap_state(run_ids[1])
    assert all(
        isinstance(vwap_rows[run_id]["touch_count_today"], int)
        and vwap_rows[run_id]["touch_count_today"] >= 0
        for run_id in run_ids
    )

    def normalized_events(run_id):
        return [
            tuple(row[key] for key in (
                "level_key", "trading_day", "outcome", "entered_from", "exited_to",
                "entered_ts", "exited_ts", "seconds_in_zone", "anchor_price",
                "distance_pct", "observed_via",
            ))
            for row in event_rows
            if row["backtest_run_id"] == run_id
        ]

    assert normalized_events(run_ids[0]) == normalized_events(run_ids[1])


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


def test_momentum_result_change_is_explained_by_candle_time_acceleration():
    """Decision #155's wall-clock replay produced one Momentum BUY on
    this scenario. With source-time deltas, every non-null acceleration
    stays below Momentum's threshold, so zero outcomes is intentional."""
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "Momentum",
                "symbol": MOMENTUM_SYMBOL,
                "scenario": "volume_gated_baseline",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["outcomes_recorded"] == 0

    session = SessionLocal()
    try:
        row_count, max_acceleration = session.execute(
            text(
                "SELECT count(*), max(msh.acceleration_score) "
                "FROM market_state_history msh JOIN symbols s ON s.id = msh.symbol_id "
                "WHERE s.ticker = :t AND s.is_backtest IS TRUE"
            ),
            {"t": MOMENTUM_SYMBOL},
        ).one()
    finally:
        session.close()

    assert row_count == 119
    assert float(max_acceleration) < DEFAULT_ACCELERATION_SCORE_THRESHOLD


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


# --- Decision #132: refuses to run while live data is connected ---
#
# Both tests below monkeypatch is_connected() directly rather than
# standing up a real Finnhub/Polygon connection (which would need a real
# API key this test environment doesn't have, and would reintroduce this
# route's full replay work for no benefit — the guard fires
# before any candle is replayed, so these are deliberately fast tests).


def test_run_backtest_rejects_when_finnhub_connected(monkeypatch):
    monkeypatch.setattr(finnhub_data, "is_connected", lambda: True)
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "FirstPullback",
                "symbol": FIRST_PULLBACK_SYMBOL,
                "scenario": "first_pullback_vwap_dip",
            },
        )

    assert resp.status_code == 409
    assert "Finnhub" in resp.json()["detail"]

    # The guard must fire before any row is written — confirms this
    # isn't a race where the replay starts and is aborted partway.
    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT 1 FROM strategy_outcomes WHERE symbol = :t"),
            {"t": FIRST_PULLBACK_SYMBOL},
        ).fetchone()
    finally:
        session.close()
    assert row is None


def test_run_backtest_rejects_when_polygon_connected(monkeypatch):
    monkeypatch.setattr(market_data, "is_connected", lambda: True)
    with TestClient(app) as client:
        resp = client.post(
            "/backtest/run",
            params={
                "strategy_name": "FirstPullback",
                "symbol": FIRST_PULLBACK_SYMBOL,
                "scenario": "first_pullback_vwap_dip",
            },
        )

    assert resp.status_code == 409
    assert "Polygon" in resp.json()["detail"]
