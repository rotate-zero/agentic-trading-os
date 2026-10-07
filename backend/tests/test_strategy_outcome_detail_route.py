"""PostgreSQL-backed tests for GET /intelligence/strategy-outcomes/{outcome_id}
(task `recorded-outcome-evidence-detail`).

Every row is inserted directly through the real `StrategyOutcomeRecord` /
`BacktestRunRecord` ORM models under one marker strategy name and removed
before and after each test. The strongest faithfulness check is equality with
the SAME row as returned by the existing list route: the detail route must add,
drop and relabel nothing relative to the contract that route already serves.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

import app.api.routes.intelligence as route
from app.db.session import SessionLocal
from app.main import app
from app.models.trading_intelligence import BacktestRunRecord, StrategyOutcomeRecord

MARKER = "__OUTCOME_DETAIL_TEST__"
WHEN = datetime(2099, 3, 4, 15, 0, tzinfo=timezone.utc)  # far future: newest rows in any shared table
SNAPSHOT_FIELDS = ("market_state_at_entry", "context_at_entry", "market_state_at_exit", "context_at_exit")


def _clean() -> None:
    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), {"n": MARKER})
        session.execute(text("DELETE FROM backtests WHERE strategy_name = :n"), {"n": MARKER})


@pytest.fixture(autouse=True)
def clean_rows():
    _clean()
    yield
    _clean()


def backtest_run() -> uuid.UUID:
    with SessionLocal.begin() as session:
        row = BacktestRunRecord(
            sweep_id=uuid.uuid4(), strategy_name=MARKER, strategy_version="orb_v7",
            config_hash=f"cfg_{uuid.uuid4().hex[:8]}", symbol_universe=["ZZOD1"],
            date_range_start=date(2026, 1, 1), date_range_end=date(2026, 6, 30),
            data_version="polygon_2026_08", feature_version="feature_engine_v1",
            walk_forward_fold=None, is_holdout=False,
        )
        session.add(row)
        session.flush()
        return row.run_id


def insert(**changes) -> uuid.UUID:
    """A simulated, recorder-style row with NULL snapshots by default."""
    values = dict(
        outcome_id=uuid.uuid4(), opportunity_id=uuid.uuid4(), schema_version=2, strategy_name=MARKER,
        strategy_version="gap_v3", symbol="ZZOD1", origin="auto", is_backtest=False, backtest_run_id=None,
        execution_mode="simulated", execution_venue="simulated", trading_day=WHEN.date(),
        setup_detected_at=WHEN - timedelta(minutes=9), signal_confirmed_at=None, decided_at=WHEN - timedelta(minutes=8),
        entry_filled_at=WHEN, exit_filled_at=WHEN + timedelta(minutes=5), holding_seconds=300, direction="BUY",
        entry_price=Decimal("10.123457"), entry_qty=Decimal("100"), exit_price=Decimal("10.500000"),
        exit_qty=Decimal("100"), commission_total=None, slippage_entry=None,
        realized_pnl=Decimal("37.654300"), realized_r=Decimal("1.2346"), exit_reason="target",
        structural_invalidation=Decimal("9.5"), structural_target=Decimal("11.25"), final_stop=Decimal("9.75"),
        final_target=Decimal("11"), confidence_at_signal=Decimal("0.5"), evidence={"basis": "live"},
        market_state_at_entry=None, context_at_entry=None, market_state_at_exit=None, context_at_exit=None,
        snapshot_missing_reasons={field: "recorder_unavailable" for field in SNAPSHOT_FIELDS},
    )
    values.update(changes)
    with SessionLocal.begin() as session:
        session.add(StrategyOutcomeRecord(**values))
    return values["outcome_id"]


def complete_snapshots() -> dict:
    return dict(
        market_state_at_entry={"trend_score": 62.5, "volatility_regime_score": 41.0},
        context_at_entry={"gap_day": False, "session_type": "regular", "vix_regime": "normal"},
        market_state_at_exit={"trend_score": 48.0, "volatility_regime_score": 39.5},
        context_at_exit={"gap_day": False, "session_type": "regular", "vix_regime": None},
        snapshot_missing_reasons=None,
    )


async def get(outcome_id) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/intelligence/strategy-outcomes/{outcome_id}")


async def list_rows(**params) -> list[dict]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/intelligence/strategy-outcomes", params={"limit": 500, **params})
    assert response.status_code == 200
    return [row for row in response.json()["outcomes"] if row["strategy_name"] == MARKER]


@pytest.mark.asyncio
async def test_simulated_row_with_missing_snapshots_and_commission_is_returned_exactly_as_stored():
    outcome_id = insert()
    response = await get(outcome_id)
    assert response.status_code == 200
    body = response.json()
    assert body["outcome_id"] == str(outcome_id)
    assert (body["is_backtest"], body["execution_mode"], body["execution_venue"]) == (False, "simulated", "simulated")
    assert body["backtest_run_id"] is None and body["origin"] == "auto"
    assert (body["strategy_name"], body["strategy_version"], body["schema_version"]) == (MARKER, "gap_v3", 2)
    # Missing values stay null — never zero, never a fabricated {}.
    assert body["commission_total"] is None and body["slippage_entry"] is None and body["signal_confirmed_at"] is None
    for field in SNAPSHOT_FIELDS:
        assert body[field] is None
    assert body["snapshot_missing_reasons"] == {field: "recorder_unavailable" for field in SNAPSHOT_FIELDS}
    # Recorded numbers, levels and timestamps are the stored values (existing float/ISO conventions).
    assert (body["entry_price"], body["entry_qty"], body["exit_price"], body["exit_qty"]) == (10.123457, 100.0, 10.5, 100.0)
    assert (body["realized_pnl"], body["realized_r"], body["exit_reason"]) == (37.6543, 1.2346, "target")
    assert (body["structural_invalidation"], body["structural_target"], body["final_stop"], body["final_target"]) == (9.5, 11.25, 9.75, 11.0)
    assert body["decided_at"].startswith("2099-03-04T14:52:00") and body["exit_filled_at"].startswith("2099-03-04T15:05:00")
    assert body["evidence"] == {"basis": "live"} and body["feature_snapshot_id"] is None


@pytest.mark.asyncio
async def test_detail_equals_the_same_row_from_the_existing_list_route():
    run_id = backtest_run()
    simulated = insert(commission_total=Decimal("1.25"), slippage_entry=Decimal("0.02"), **complete_snapshots())
    unavailable = insert(entry_price=Decimal("20.5"))
    backtest = insert(
        is_backtest=True, backtest_run_id=run_id, execution_mode="backtest", execution_venue="simulated",
        commission_total=Decimal("0.5"), **complete_snapshots(),
    )
    listed = {row["outcome_id"]: row for row in await list_rows()} | {row["outcome_id"]: row for row in await list_rows(is_backtest="true")}
    assert set(listed) == {str(simulated), str(unavailable), str(backtest)}
    for outcome_id in (simulated, unavailable, backtest):
        assert (await get(outcome_id)).json() == listed[str(outcome_id)]


@pytest.mark.asyncio
async def test_nested_evidence_is_returned_verbatim_without_interpretation():
    evidence = {
        "conditions": {"or_break": True, "gap_pct": -0.0125, "levels": [{"price": 10.5, "tags": ["pdh", None]}, []]},
        "reason": "<img src=x onerror=alert(1)> & <b>bold</b>",
        "unicode": "naïve — 日本語",
        "empty": {}, "null_value": None, "zero": 0, "flag": False,
        "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": [1, [2, [3]]]}}}}}}},
    }
    outcome_id = insert(evidence=evidence)
    assert (await get(outcome_id)).json()["evidence"] == evidence


@pytest.mark.asyncio
async def test_partial_snapshots_keep_present_ones_and_only_the_recorded_reasons():
    reasons = {"market_state_at_exit": "snapshot_capture_error", "context_at_exit": "engine_state_lost_on_restart"}
    snapshots = complete_snapshots() | {"market_state_at_exit": None, "context_at_exit": None, "snapshot_missing_reasons": reasons}
    body = (await get(insert(**snapshots))).json()
    assert body["market_state_at_entry"] == snapshots["market_state_at_entry"]
    assert body["context_at_entry"] == snapshots["context_at_entry"]
    assert body["market_state_at_exit"] is None and body["context_at_exit"] is None
    assert body["snapshot_missing_reasons"] == reasons  # exactly the recorded codes, nothing added


@pytest.mark.asyncio
async def test_backtest_row_stays_backtest_and_a_simulated_row_stays_simulated():
    run_id = backtest_run()
    backtest = insert(is_backtest=True, backtest_run_id=run_id, execution_mode="backtest", **complete_snapshots())
    simulated = insert(**complete_snapshots())
    backtest_body = (await get(backtest)).json()
    assert (backtest_body["is_backtest"], backtest_body["execution_mode"], backtest_body["backtest_run_id"]) == (True, "backtest", str(run_id))
    assert backtest_body["execution_venue"] == "simulated"  # a backtest's venue is the simulated one; mode carries the population
    simulated_body = (await get(simulated)).json()
    assert (simulated_body["is_backtest"], simulated_body["execution_mode"], simulated_body["backtest_run_id"]) == (False, "simulated", None)


@pytest.mark.asyncio
async def test_only_the_requested_row_is_returned_even_with_a_shared_opportunity():
    opportunity = uuid.uuid4()
    first = insert(opportunity_id=opportunity, symbol="ZZOD1", realized_r=Decimal("1"))
    run_id = backtest_run()
    second = insert(
        opportunity_id=opportunity, is_backtest=True, backtest_run_id=run_id, execution_mode="backtest",
        symbol="ZZOD2", realized_r=Decimal("-2"), **complete_snapshots(),
    )
    for wanted, other in ((first, second), (second, first)):
        body = (await get(wanted)).json()
        assert body["outcome_id"] == str(wanted) and body["outcome_id"] != str(other)
    assert (await get(first)).json()["symbol"] == "ZZOD1" and (await get(second)).json()["symbol"] == "ZZOD2"


@pytest.mark.asyncio
async def test_unknown_id_is_404_and_malformed_uuid_is_422_without_touching_the_helper(monkeypatch):
    insert()
    unknown = await get(uuid.uuid4())
    assert unknown.status_code == 404 and "Unknown strategy outcome" in unknown.json()["detail"]

    def forbidden(*args, **kwargs):
        raise AssertionError("helper called for a malformed id")

    monkeypatch.setattr(route, "_fetch_strategy_outcome", forbidden)
    for bad in ("not-a-uuid", "123", "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"):
        assert (await get(bad)).status_code == 422


def _fingerprint() -> list:
    with SessionLocal() as session:
        return [
            session.execute(text("SELECT * FROM strategy_outcomes WHERE strategy_name = :n ORDER BY outcome_id"), {"n": MARKER}).all(),
            session.execute(text("SELECT * FROM backtests WHERE strategy_name = :n ORDER BY run_id"), {"n": MARKER}).all(),
        ]


@pytest.mark.asyncio
async def test_read_runs_in_a_worker_thread_in_a_read_only_repeatable_read_snapshot_and_writes_nothing(monkeypatch):
    run_id = backtest_run()
    target = insert(is_backtest=True, backtest_run_id=run_id, execution_mode="backtest", **complete_snapshots())
    insert()
    before = _fingerprint()
    observed: list[tuple[int, str, str]] = []
    write_error: list[BaseException] = []
    loop_thread = threading.get_ident()

    def factory():
        session = SessionLocal()
        execute, rollback = session.execute, session.rollback

        def checked(statement, *args, **kwargs):
            observed.append((
                threading.get_ident(),
                execute(text("SHOW transaction_read_only")).scalar_one(),
                execute(text("SHOW transaction_isolation")).scalar_one(),
            ))
            return execute(statement, *args, **kwargs)

        def probing_rollback():
            try:  # the server must refuse a write inside the helper's own transaction
                execute(text("UPDATE strategy_outcomes SET symbol = 'WRITTEN' WHERE outcome_id = :i"), {"i": target})
            except Exception as exc:  # noqa: BLE001
                write_error.append(exc)
            rollback()

        session.execute, session.rollback = checked, probing_rollback
        return session

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", factory)  # the helper imports SessionLocal at call time
    assert (await get(target)).status_code == 200
    assert observed and all(t != loop_thread and ro == "on" and iso == "repeatable read" for t, ro, iso in observed)
    assert len(write_error) == 1 and "read-only" in str(write_error[0]).lower()
    monkeypatch.undo()
    assert _fingerprint() == before


@pytest.mark.asyncio
async def test_blocked_query_does_not_block_health(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def blocked(outcome_id):
        started.set()
        if not release.wait(5):
            raise TimeoutError("blocked outcome read was never released")
        return None

    monkeypatch.setattr(route, "_fetch_strategy_outcome", blocked)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        task = asyncio.create_task(client.get(f"/intelligence/strategy-outcomes/{uuid.uuid4()}"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            health = await asyncio.wait_for(client.get("/health"), 2)
            assert health.status_code == 200 and health.json()["status"] == "ok"
        finally:
            release.set()
        assert (await asyncio.wait_for(task, 5)).status_code == 404
