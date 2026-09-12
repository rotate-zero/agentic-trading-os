"""
Route-level tests for decision #127's two new GET /intelligence routes:
`/win-rate-by-hour` and `/expectancy-by-session-type`. Both close the
gap decision #122's own "not built here" note named explicitly as
future work, deferred at the time specifically to avoid colliding with
the sibling parallel track that landed the previous batch of routes as
decision #123.

Deliberately does NOT re-test the grouping/arithmetic itself (ET-hour
bucketing, `win_count`/`total_trades` division, `AVG(realized_r)`,
live/backtest strict isolation, the honest-`None` session group) — that
is `test_performance_queries.py`'s job (8 tests, real Postgres, already
exhaustive) as of decision #122/#124. This file only proves each route
forwards to its underlying query function correctly (including real
filters) and shapes the result as documented, same "route proves
forwarding, not the algorithm" posture
`test_strategy_outcomes_and_opportunity_conflicts_routes.py` already
established for `/opportunity-conflicts` toward `opportunity_view.py`.

Same plain-synchronous-DB-read test style as `/strategy-outcomes` in
that same file (no background engine, no EventBus subscriber involved
in either of these two routes) — a sync `TestClient(app)` is enough.
Rows are inserted directly via the real `StrategyOutcomeRecord` ORM
model, the same "prove a row actually in the table round-trips through
the route" posture that file's own `_make_outcome_record()` uses, not
via `record_strategy_outcome()` (that write-path's own contract is
already covered by test_performance_intelligence.py/
test_performance_queries.py; re-exercising it here would just be
slower coverage of the same thing).

**Timezone ground truth, same as test_performance_queries.py.**
September 2026 is Eastern Daylight Time (UTC-4): `14:00Z` -> 10am ET,
`19:00Z` -> 3pm ET — verified directly against a real Postgres
`AT TIME ZONE 'America/New_York'` conversion in that file already, not
re-derived here.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.main import app
from app.models.trading_intelligence import StrategyOutcomeRecord

_STRATEGY_NAME = "__TEST_ROUTE_PERFORMANCE_ANALYTICS__"
_OTHER_STRATEGY_NAME = "__TEST_ROUTE_PERFORMANCE_ANALYTICS_OTHER__"


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


def _clean_own_rows() -> None:
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM strategy_outcomes WHERE strategy_name IN (:a, :b)"),
            {"a": _STRATEGY_NAME, "b": _OTHER_STRATEGY_NAME},
        )
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    if _db_available():
        _clean_own_rows()
    yield
    if _db_available():
        _clean_own_rows()


def _make_outcome_record(
    *,
    entry_filled_at: datetime,
    realized_r: float,
    strategy_name: str = _STRATEGY_NAME,
    strategy_version: str = "v1",
    is_backtest: bool = False,
    session_type: str | None = "open",
) -> StrategyOutcomeRecord:
    """Same field set as `test_strategy_outcomes_and_opportunity_
    conflicts_routes.py`'s own `_make_outcome_record()`. `context_at_
    entry` mirrors the REAL provider-keyed shape `ContextEngine` actually
    produces (`{"calendar": {"session": ...}}`), same as decision #124's
    correction and `test_performance_queries.py`'s own fixture —
    `session_type=None` simulates a row whose context never had a
    `calendar.session` value (the honest-`None` grouping path), not an
    omission. `entry_filled_at` drives the win-rate-by-hour bucket;
    `exit_filled_at` is set equal to it since these two routes never
    read `exit_filled_at`.
    """
    context_at_entry = {"calendar": {"session": session_type}} if session_type is not None else {}
    return StrategyOutcomeRecord(
        outcome_id=uuid.uuid4(),
        opportunity_id=uuid.uuid4(),
        schema_version=1,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        symbol="AAPL",
        origin="auto",
        is_backtest=is_backtest,
        backtest_run_id=None,
        trading_day=entry_filled_at.date(),
        setup_detected_at=entry_filled_at,
        signal_confirmed_at=entry_filled_at,
        decided_at=entry_filled_at,
        entry_filled_at=entry_filled_at,
        exit_filled_at=entry_filled_at,
        holding_seconds=60,
        direction="BUY",
        entry_price=100.0,
        entry_qty=1,
        exit_price=100.0 + realized_r,
        exit_qty=1,
        commission_total=0.0,
        slippage_entry=0.0,
        realized_pnl=realized_r,
        realized_r=realized_r,
        exit_reason="target",
        structural_invalidation=99.0,
        structural_target=102.0,
        final_stop=99.0,
        final_target=102.0,
        confidence_at_signal=0.5,
        evidence={"conditions": {}, "reason": "test fixture", "basis": "live"},
        market_state_at_entry={},
        context_at_entry=context_at_entry,
        market_state_at_exit={},
        context_at_exit={},
        feature_snapshot_id=None,
    )


def _insert(records: list[StrategyOutcomeRecord]) -> None:
    session = SessionLocal()
    try:
        for record in records:
            session.add(record)
        session.commit()
    finally:
        session.close()


# --- GET /intelligence/win-rate-by-hour -------------------------------------


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_win_rate_by_hour_route_empty_case_returns_honest_empty_collection():
    """No rows for this strategy_name — `{"hourly_win_rates": []}`,
    never a fabricated bucket. Scoped via the `strategy_name` filter
    rather than wiping the whole table (unlike `/strategy-outcomes`'
    own dedicated whole-table-empty test): this route always groups by
    hour, so an unscoped call could pick up unrelated real data if any
    ever exists, whereas an unscoped raw-rows read cannot "partially"
    reflect other content the same way.
    """
    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/win-rate-by-hour",
            params={"strategy_name": _STRATEGY_NAME},
        )

    assert resp.status_code == 200
    assert resp.json() == {"hourly_win_rates": []}


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_win_rate_by_hour_route_reflects_actual_query_layer_result():
    """Two rows in the 10am-ET bucket (one win, one loss), one row in
    the 3pm-ET bucket (a win) — asserts the exact `total_trades`/
    `win_count`/`win_rate` triple per hour, proving this route returns
    `get_win_rate_by_hour()`'s real result, not a route-level
    recomputation."""
    _insert([
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc), realized_r=1.5),
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc), realized_r=-0.5),
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 19, 0, tzinfo=timezone.utc), realized_r=2.0),
    ])

    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/win-rate-by-hour",
            params={"strategy_name": _STRATEGY_NAME},
        )

    assert resp.status_code == 200
    rows = {row["hour_et"]: row for row in resp.json()["hourly_win_rates"]}
    assert rows[10] == {"hour_et": 10, "total_trades": 2, "win_count": 1, "win_rate": 0.5}
    assert rows[15] == {"hour_et": 15, "total_trades": 1, "win_count": 1, "win_rate": 1.0}


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_win_rate_by_hour_route_strategy_name_filter_forwards_correctly():
    """A row under a different strategy_name in the same hour bucket
    must not leak into a `strategy_name`-filtered response."""
    _insert([
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc), realized_r=1.0),
        _make_outcome_record(
            entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc),
            realized_r=-1.0,
            strategy_name=_OTHER_STRATEGY_NAME,
        ),
    ])

    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/win-rate-by-hour",
            params={"strategy_name": _STRATEGY_NAME},
        )

    body = resp.json()
    assert len(body["hourly_win_rates"]) == 1
    assert body["hourly_win_rates"][0] == {"hour_et": 10, "total_trades": 1, "win_count": 1, "win_rate": 1.0}


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_win_rate_by_hour_route_is_backtest_forwards_correctly():
    """A live row and a backtest row in the same hour bucket, deliberately
    opposite outcomes so blending would be detectable — `is_backtest`
    is a real, already-existing parameter of `get_win_rate_by_hour()`
    (not invented at the route level), and this proves the route
    actually passes it through rather than defaulting/ignoring it."""
    _insert([
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc), realized_r=1.0, is_backtest=False),
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc), realized_r=-1.0, is_backtest=True),
    ])

    with TestClient(app) as client:
        live_resp = client.get(
            "/intelligence/win-rate-by-hour",
            params={"strategy_name": _STRATEGY_NAME, "is_backtest": False},
        )
        backtest_resp = client.get(
            "/intelligence/win-rate-by-hour",
            params={"strategy_name": _STRATEGY_NAME, "is_backtest": True},
        )

    assert live_resp.json()["hourly_win_rates"] == [{"hour_et": 10, "total_trades": 1, "win_count": 1, "win_rate": 1.0}]
    assert backtest_resp.json()["hourly_win_rates"] == [{"hour_et": 10, "total_trades": 1, "win_count": 0, "win_rate": 0.0}]


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_win_rate_by_hour_route_version_without_name_returns_400():
    """`strategy_version` without `strategy_name` is the one invalid
    filter combination `_validate_strategy_filters()` guards against —
    must surface as a clean 400 (same posture GET /series already uses
    for an unsupported timeframe), not a raw 500."""
    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/win-rate-by-hour",
            params={"strategy_version": "orb_v1"},
        )

    assert resp.status_code == 400
    assert "strategy_name" in resp.json()["detail"]


# --- GET /intelligence/expectancy-by-session-type ---------------------------


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_expectancy_by_session_type_route_empty_case_returns_honest_empty_collection():
    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/expectancy-by-session-type",
            params={"strategy_name": _STRATEGY_NAME},
        )

    assert resp.status_code == 200
    assert resp.json() == {"session_expectancy": []}


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_expectancy_by_session_type_route_reflects_actual_query_layer_result():
    """Two "open" rows (realized_r 1.0/3.0 -> expectancy 2.0), one
    "power_hour" row (realized_r -1.0), and one row with no
    `calendar.session` at all (the honest-`None` group) — asserts exact
    `trade_count`/`expectancy_r` per group, including that `None` passes
    through as JSON `null` rather than being dropped or relabeled."""
    _insert([
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc), realized_r=1.0, session_type="open"),
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc), realized_r=3.0, session_type="open"),
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 19, 0, tzinfo=timezone.utc), realized_r=-1.0, session_type="power_hour"),
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc), realized_r=0.5, session_type=None),
    ])

    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/expectancy-by-session-type",
            params={"strategy_name": _STRATEGY_NAME},
        )

    assert resp.status_code == 200
    rows = {row["session_type"]: row for row in resp.json()["session_expectancy"]}
    assert rows["open"] == {"session_type": "open", "trade_count": 2, "expectancy_r": 2.0}
    assert rows["power_hour"] == {"session_type": "power_hour", "trade_count": 1, "expectancy_r": -1.0}
    assert rows[None] == {"session_type": None, "trade_count": 1, "expectancy_r": 0.5}


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_expectancy_by_session_type_route_strategy_name_filter_forwards_correctly():
    _insert([
        _make_outcome_record(entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc), realized_r=1.0, session_type="open"),
        _make_outcome_record(
            entry_filled_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc),
            realized_r=-5.0,
            session_type="open",
            strategy_name=_OTHER_STRATEGY_NAME,
        ),
    ])

    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/expectancy-by-session-type",
            params={"strategy_name": _STRATEGY_NAME},
        )

    body = resp.json()
    assert len(body["session_expectancy"]) == 1
    assert body["session_expectancy"][0] == {"session_type": "open", "trade_count": 1, "expectancy_r": 1.0}


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_expectancy_by_session_type_route_version_without_name_returns_400():
    with TestClient(app) as client:
        resp = client.get(
            "/intelligence/expectancy-by-session-type",
            params={"strategy_version": "orb_v1"},
        )

    assert resp.status_code == 400
    assert "strategy_name" in resp.json()["detail"]
