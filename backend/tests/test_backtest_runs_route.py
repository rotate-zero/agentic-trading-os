"""
Route-level tests for decision #136's new `GET /intelligence/backtest-runs`
— the first reader of the `backtests` table (decision #89's shape,
decision #120's migration, decision #128's real writer).

Separate file, not an extension of
`test_strategy_outcomes_and_opportunity_conflicts_routes.py`, matching
this suite's own established one-file-per-route-group precedent:
`test_performance_analytics_routes.py` already sits separately from that
file despite both testing routes in `app/api/routes/intelligence.py`.
This route reads a different table (`backtests`, not `strategy_outcomes`
or the `OpportunityCache`), so it gets its own file rather than growing
either existing one past what its own name promises.

Two test styles, deliberately:

- `test_route_reflects_a_real_backtest_runner_write` proves the primary
  claim — a row produced by the REAL write path (`BacktestRunner.run()`
  → `_write_backtest_run_record()`, decision #128) round-trips through
  this route correctly. Same fast fixture-strategy/4-candle pattern
  `test_backtest_runner.py` already established (a stub `Strategy`, not
  one of the 7 real ones — nothing here re-tests GATE/MATCH/SCORE logic
  or the runner's own fill-timing behavior, both already covered there).
- Every other test (filtering, ordering, limit, empty-result, malformed
  UUID) seeds rows directly via the real `BacktestRunRecord` ORM model —
  same posture `test_strategy_outcomes_and_opportunity_conflicts_routes.
  py`'s own `_insert_backtest_run()` already takes for `backtests` rows
  it doesn't need a live write-path run to produce. Ordering/limit
  assertions use explicit far-future (year 2099) `created_at` timestamps
  for our own synthetic rows so they're deterministically the newest
  regardless of any other content in the table — no assumption that the
  table is otherwise empty.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.backtest_runner.context_provider import FixtureBacktestContextProvider
from app.backtest_runner.fixture_provider import FixtureCandleProvider
from app.backtest_runner.runner import BacktestRunner
from app.broker_adapters.base import Candle
from app.db.session import SessionLocal
from app.main import app
from app.models.trading_intelligence import BacktestRunRecord
from app.strategy_engine.base_strategy import Opportunity, Strategy, StrategyConfig, every_candle

# Hand-inserted rows are tagged with this strategy_name for scoped cleanup —
# same "__TEST_ROUTE_..._" marker convention
# test_strategy_outcomes_and_opportunity_conflicts_routes.py's own
# _STRATEGY_NAME already uses.
_STRATEGY_NAME = "__TEST_ROUTE_BACKTEST_RUNS__"

# Real-runner test gets its own synthetic ticker, distinct from
# test_backtest_runner.py's ZZUNIT4, so a full run of both files never
# touches the same symbols/candles rows.
_REAL_RUN_SYMBOL = "ZZBTR5"


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


def _clean_hand_inserted_rows() -> None:
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM backtests WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.commit()
    finally:
        session.close()


def _clean_real_run_symbol() -> None:
    """Mirrors test_backtest_runner.py's own `_clean_test_symbol()` —
    a real run through the full engine pipeline touches these same
    tables, not just `backtests`."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes WHERE symbol = :t"), {"t": _REAL_RUN_SYMBOL})
        session.execute(text("DELETE FROM backtests WHERE :t = ANY(symbol_universe)"), {"t": _REAL_RUN_SYMBOL})
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
                {"t": _REAL_RUN_SYMBOL},
            )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": _REAL_RUN_SYMBOL})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_hand_inserted_rows()
    _clean_real_run_symbol()
    yield
    _clean_hand_inserted_rows()
    _clean_real_run_symbol()


def _insert_backtest_run(**overrides) -> BacktestRunRecord:
    """Inserts one real `backtests` row, tagged `_STRATEGY_NAME` for this
    file's own cleanup, and returns the refreshed ORM row (so callers can
    read back the server-generated `run_id`/`created_at` when not
    overridden). Field set matches
    `test_strategy_outcomes_and_opportunity_conflicts_routes.py`'s own
    `_insert_backtest_run()`."""
    session = SessionLocal()
    try:
        fields = dict(
            sweep_id=uuid.uuid4(),
            strategy_name=_STRATEGY_NAME,
            strategy_version="orb_v1",
            config_hash=f"cfg_{uuid.uuid4().hex[:8]}",
            symbol_universe=["AAPL"],
            date_range_start=date(2026, 1, 1),
            date_range_end=date(2026, 6, 30),
            data_version="polygon_2026_08",
            feature_version="feature_engine_v1",
            walk_forward_fold=1,
            is_holdout=False,
        )
        fields.update(overrides)
        row = BacktestRunRecord(**fields)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    finally:
        session.close()


# --- Primary claim: a real BacktestRunner write round-trips through the route ---


class _OneShotStubStrategy(Strategy):
    """Same deterministic single-fire stub `test_backtest_runner.py`
    already established — this test isn't re-proving fill-timing or
    GATE/MATCH/SCORE logic, just that a real run's `backtests` row can be
    read back by run_id."""

    name = "STUB_ROUTE_TEST"
    trigger = every_candle(timeframe="1m")

    def __init__(self, config: StrategyConfig, *, fire_on_call_index: int, opportunity: Opportunity) -> None:
        super().__init__(config)
        self._fire_on_call_index = fire_on_call_index
        self._opportunity = opportunity
        self.calls = 0

    async def evaluate(self, symbol, market_state, features, context) -> Opportunity | None:  # noqa: ANN001
        idx = self.calls
        self.calls += 1
        return self._opportunity if idx == self._fire_on_call_index else None


def _fixture_candles() -> list[Candle]:
    """Same 4-candle shape as test_backtest_runner.py's own fixture —
    what's under test here is the route reading the run's own metadata
    back, not the runner's fill-timing behavior."""
    start = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)
    return [
        Candle(timeframe="1m", open=100.0, high=100.15, low=99.95, close=100.10, volume=1000, candle_ts=start),
        Candle(timeframe="1m", open=100.10, high=100.25, low=100.05, close=100.20, volume=1000, candle_ts=start + timedelta(minutes=1)),
        Candle(timeframe="1m", open=100.20, high=100.30, low=100.15, close=100.25, volume=1000, candle_ts=start + timedelta(minutes=2)),
        Candle(timeframe="1m", open=100.25, high=102.00, low=100.20, close=101.80, volume=1000, candle_ts=start + timedelta(minutes=3)),
    ]


async def test_route_reflects_a_real_backtest_runner_write():
    candles = _fixture_candles()
    opportunity = Opportunity(
        strategy="STUB_ROUTE_TEST",
        version="stub_v1",
        direction="BUY",
        confidence=0.9,
        structural_invalidation=98.0,
        structural_target=101.0,
        evidence={"conditions": {}, "reason": "stub fires deterministically for decision #136's route test", "basis": "closed"},
        setup_detected_at=candles[1].candle_ts,
    )
    strategy = _OneShotStubStrategy(
        StrategyConfig(
            strategy_name="STUB_ROUTE_TEST",
            version="stub_v1",
            gate_conditions={},
            params={},
            active_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        fire_on_call_index=1,
        opportunity=opportunity,
    )
    provider = FixtureCandleProvider.single(_REAL_RUN_SYMBOL, "1m", candles)

    runner = BacktestRunner(
        strategy=strategy,
        symbol=_REAL_RUN_SYMBOL,
        market_data_provider=provider,
        start=candles[0].candle_ts,
        end=candles[-1].candle_ts + timedelta(minutes=1),
        context_provider=FixtureBacktestContextProvider(),
        data_version="fixture-v1",
        feature_version="feature_engine_v1",
    )

    result = await runner.run()
    assert result.outcomes_recorded == 1  # confirms the write path actually ran, not just that a row happens to exist

    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"run_id": str(result.run_id)})

    assert resp.status_code == 200
    body = resp.json()["backtest_runs"]
    assert len(body) == 1
    run = body[0]
    assert run["run_id"] == str(result.run_id)
    assert run["strategy_name"] == "STUB_ROUTE_TEST"
    assert run["strategy_version"] == "stub_v1"
    assert run["symbol_universe"] == [_REAL_RUN_SYMBOL]
    assert run["data_version"] == "fixture-v1"
    assert run["feature_version"] == "feature_engine_v1"
    assert run["is_holdout"] is False
    assert run["walk_forward_fold"] is None  # not set by BacktestRunner's constructor call above
    assert run["created_at"] is not None
    assert run["sweep_id"] is not None


# --- Filtering, ordering, limit, empty-result, malformed input — hand-inserted rows ---


def test_route_filters_by_strategy_name():
    matching = _insert_backtest_run(strategy_name=_STRATEGY_NAME)
    other_name = f"{_STRATEGY_NAME}_OTHER"
    _insert_backtest_run(strategy_name=other_name)
    try:
        with TestClient(app) as client:
            resp = client.get("/intelligence/backtest-runs", params={"strategy_name": _STRATEGY_NAME, "limit": 500})
        assert resp.status_code == 200
        run_ids = {row["run_id"] for row in resp.json()["backtest_runs"]}
        assert str(matching.run_id) in run_ids
        assert all(row["strategy_name"] == _STRATEGY_NAME for row in resp.json()["backtest_runs"])
    finally:
        session = SessionLocal()
        try:
            session.execute(text("DELETE FROM backtests WHERE strategy_name = :n"), {"n": other_name})
            session.commit()
        finally:
            session.close()


def test_route_filters_by_sweep_id():
    target_sweep = uuid.uuid4()
    matching = _insert_backtest_run(sweep_id=target_sweep)
    _insert_backtest_run(sweep_id=uuid.uuid4())  # different sweep, same strategy_name — must be excluded

    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"sweep_id": str(target_sweep), "limit": 500})

    assert resp.status_code == 200
    body = resp.json()["backtest_runs"]
    assert len(body) == 1
    assert body[0]["run_id"] == str(matching.run_id)
    assert body[0]["sweep_id"] == str(target_sweep)


def test_route_filters_by_run_id():
    matching = _insert_backtest_run()
    _insert_backtest_run()  # a second row that must NOT be returned

    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"run_id": str(matching.run_id)})

    assert resp.status_code == 200
    body = resp.json()["backtest_runs"]
    assert len(body) == 1
    assert body[0]["run_id"] == str(matching.run_id)


def test_route_orders_newest_first_by_created_at():
    oldest = _insert_backtest_run(created_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    middle = _insert_backtest_run(created_at=datetime(2099, 1, 2, tzinfo=timezone.utc))
    newest = _insert_backtest_run(created_at=datetime(2099, 1, 3, tzinfo=timezone.utc))

    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"strategy_name": _STRATEGY_NAME, "limit": 500})

    assert resp.status_code == 200
    run_ids_in_order = [row["run_id"] for row in resp.json()["backtest_runs"]]
    assert run_ids_in_order == [str(newest.run_id), str(middle.run_id), str(oldest.run_id)]


def test_route_limit_caps_returned_rows():
    _insert_backtest_run(created_at=datetime(2099, 2, 1, tzinfo=timezone.utc))
    middle = _insert_backtest_run(created_at=datetime(2099, 2, 2, tzinfo=timezone.utc))
    newest = _insert_backtest_run(created_at=datetime(2099, 2, 3, tzinfo=timezone.utc))

    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"strategy_name": _STRATEGY_NAME, "limit": 2})

    assert resp.status_code == 200
    body = resp.json()["backtest_runs"]
    assert len(body) == 2
    assert [row["run_id"] for row in body] == [str(newest.run_id), str(middle.run_id)]


def test_route_returns_honest_empty_collection_for_unknown_run_id():
    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"run_id": str(uuid.uuid4())})

    assert resp.status_code == 200
    assert resp.json() == {"backtest_runs": []}


def test_route_rejects_malformed_run_id():
    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"run_id": "not-a-uuid"})

    assert resp.status_code == 400


def test_route_rejects_malformed_sweep_id():
    with TestClient(app) as client:
        resp = client.get("/intelligence/backtest-runs", params={"sweep_id": "not-a-uuid"})

    assert resp.status_code == 400
