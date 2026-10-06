"""PostgreSQL tests for complete selected-run/sweep performance summaries."""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.trading_intelligence.backtest_selection_summary as summary_query
from app.db.session import SessionLocal
from app.main import app
from app.models.trading_intelligence import BacktestRunRecord, StrategyOutcomeRecord

NAME = "__TEST_BACKTEST_SELECTION_SUMMARY__"
T0 = datetime(2026, 2, 2, 14, 30, tzinfo=timezone.utc)


def _db_available():
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="PostgreSQL unavailable")


@pytest.fixture(autouse=True)
def _isolated():
    def clean():
        with SessionLocal() as session:
            session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :name"), {"name": NAME})
            session.execute(text("DELETE FROM backtests WHERE strategy_name = :name"), {"name": NAME})
            session.commit()
    clean()
    yield
    clean()


def _run(session, sweep_id, *, version="v1", config="config-a", data="fixture:a", feature="feature-v1"):
    row = BacktestRunRecord(
        run_id=uuid.uuid4(), sweep_id=sweep_id, strategy_name=NAME,
        strategy_version=version, config_hash=config, symbol_universe=["AAPL"],
        date_range_start=date(2026, 2, 2), date_range_end=date(2026, 2, 2),
        data_version=data, feature_version=feature, walk_forward_fold=None, is_holdout=False,
    )
    session.add(row)
    return row


def _outcome(session, run, r, index, *, is_backtest=True, execution_mode="backtest"):
    ts = T0 + timedelta(minutes=index)
    session.add(StrategyOutcomeRecord(
        outcome_id=uuid.uuid4(), opportunity_id=uuid.uuid4(), schema_version=1,
        strategy_name=NAME, strategy_version=run.strategy_version, symbol="AAPL", origin="auto",
        is_backtest=is_backtest, backtest_run_id=run.run_id,
        execution_mode=execution_mode, execution_venue="simulated",
        trading_day=ts.date(), setup_detected_at=ts, signal_confirmed_at=ts, decided_at=ts,
        entry_filled_at=ts, exit_filled_at=ts, holding_seconds=60,
        direction="BUY", entry_price=100, entry_qty=1, exit_price=100 + r, exit_qty=1,
        commission_total=0, slippage_entry=0, realized_pnl=r, realized_r=r,
        exit_reason="target", structural_invalidation=99, structural_target=102,
        final_stop=99, final_target=102, confidence_at_signal=0.5,
        evidence={}, market_state_at_entry={}, context_at_entry={},
        market_state_at_exit={}, context_at_exit={}, feature_snapshot_id=None,
    ))


def _get(**params):
    return TestClient(app).get("/intelligence/backtest-selection-summary", params=params)


def test_complete_sweep_groups_metrics_zero_runs_and_isolation(monkeypatch):
    sweep = uuid.uuid4()
    with SessionLocal() as session:
        first = _run(session, sweep)
        second = _run(session, sweep)
        other_source = _run(session, sweep, data="stored:postgres:candles:1m-1d")
        other_version = _run(session, sweep, version="v2")
        other_feature = _run(session, sweep, feature="feature-v2")
        zero = _run(session, sweep, config="config-empty")
        outsider = _run(session, uuid.uuid4())
        for index, r in enumerate([1, -0.5, 0, 2, -1, 0]):
            _outcome(session, first if index < 4 else second, r, index)
        _outcome(session, other_source, 3, 7)
        _outcome(session, other_version, -2, 10)
        _outcome(session, other_feature, 0, 11)
        _outcome(session, outsider, 90, 8)
        _outcome(session, first, 80, 9, is_backtest=False, execution_mode="simulated")
        session.commit()
        first_id = first.run_id

    # The same selection's raw list is deliberately capped below its six
    # eligible outcomes; the aggregate must still include all six.
    limited = TestClient(app).get("/intelligence/strategy-outcomes", params={
        "limit": 2, "is_backtest": "true", "sweep_id": str(sweep),
    })
    assert limited.status_code == 200
    assert len(limited.json()["outcomes"]) == 2

    real_factory = summary_query.SessionLocal
    observed = []

    def tracking_factory():
        session = real_factory()
        original_execute = session.execute

        def execute(*args, **kwargs):
            connection = session.connection()
            observed.append((threading.current_thread().name,
                             connection.exec_driver_sql("SHOW transaction_read_only").scalar_one(),
                             connection.exec_driver_sql("SHOW transaction_isolation").scalar_one()))
            return original_execute(*args, **kwargs)

        session.execute = execute
        return session

    monkeypatch.setattr(summary_query, "SessionLocal", tracking_factory)
    response = _get(sweep_id=str(sweep))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selection_found"] is True
    assert len(body["groups"]) == 5
    groups = {(g["strategy_version"], g["config_hash"], g["data_version"], g["feature_version"]): g
              for g in body["groups"]}
    main = groups[("v1", "config-a", "fixture:a", "feature-v1")]
    assert main == {
        "strategy_name": NAME, "strategy_version": "v1", "config_hash": "config-a",
        "data_version": "fixture:a", "feature_version": "feature-v1", "run_count": 2,
        "total_outcomes": 6, "wins": 2, "losses": 2, "breakevens": 2,
        "win_rate": pytest.approx(2 / 6), "mean_realized_r": pytest.approx(0.25),
    }
    assert groups[("v1", "config-a", "stored:postgres:candles:1m-1d", "feature-v1")]["total_outcomes"] == 1
    assert groups[("v2", "config-a", "fixture:a", "feature-v1")]["losses"] == 1
    assert groups[("v1", "config-a", "fixture:a", "feature-v2")]["breakevens"] == 1
    empty = groups[("v1", "config-empty", "fixture:a", "feature-v1")]
    assert (empty["run_count"], empty["total_outcomes"], empty["win_rate"], empty["mean_realized_r"]) == (1, 0, None, None)
    assert observed and all(name != "MainThread" and readonly == "on" and isolation == "repeatable read"
                            for name, readonly, isolation in observed)

    single = _get(run_id=str(first_id)).json()
    assert single["selection_found"] is True
    assert single["groups"][0]["total_outcomes"] == 4


def test_unknown_and_known_zero_are_distinct():
    sweep = uuid.uuid4()
    with SessionLocal() as session:
        run = _run(session, sweep)
        session.commit()
        run_id = run.run_id
    unknown = _get(run_id=str(uuid.uuid4()))
    assert unknown.status_code == 200
    assert unknown.json() == {"selection_found": False, "groups": []}
    known = _get(run_id=str(run_id))
    assert known.status_code == 200
    assert known.json()["selection_found"] is True
    assert known.json()["groups"][0]["total_outcomes"] == 0
    assert known.json()["groups"][0]["win_rate"] is None
    assert _get(sweep_id=str(sweep)).json()["groups"][0]["run_count"] == 1


@pytest.mark.parametrize("params", [
    {}, {"run_id": str(uuid.uuid4()), "sweep_id": str(uuid.uuid4())},
    {"run_id": "bad"}, {"sweep_id": "bad"},
])
def test_requires_exactly_one_valid_uuid(params):
    response = _get(**params)
    assert response.status_code == 400
