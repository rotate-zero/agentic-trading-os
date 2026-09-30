"""
Route-level tests for `GET /intelligence/execution-outcome-status` — a
read-only view of the OutcomeRecorder's progress (`trades.outcome_status`,
EX-12 option (a), `execution-engine-design.md` §6.7.1) over approved, closed,
simulated, auto trades.

Real PostgreSQL, hand-inserted `trades` rows only: no recorder, Execution
Engine or Portfolio State runs. What is under test is the route's own read
shape — population isolation, every status bucket, SQL NULL, `counts`
independent of `limit`, `updated_at`/`trade_id` ordering with ties, the empty
result, serialization, read-only behaviour and event-loop offload.

**No app lifespan is booted**: requests use `httpx.ASGITransport(app=app)`,
which does not run `main.py`'s `lifespan()` (same reasoning and precedent as
`test_execution_positions_route.py`).

Isolation. The shared database may hold unrelated qualifying trades, so no
test assumes the population is otherwise empty: count assertions are
*deltas* against a baseline read through the route before inserting, and
every inserted trade carries `_STRATEGY_NAME` so cleanup is exact. Ordering
tests use far-future `updated_at` values so unrelated rows cannot displace
ours from the bounded list. The one test that needs a genuinely empty
population points the route at a scratch schema holding an empty `trades`
table (`_empty_schema_session_factory`) rather than deleting anything shared.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import app.api.routes.intelligence as intelligence_routes
import app.db.session as db_session_module
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.execution_ledger import Trade
from app.models.trading_intelligence import StrategyOutcomeRecord

_STRATEGY_NAME = "__TEST_ROUTE_EXECUTION_OUTCOME_STATUS__"
_BLOCK_TIMEOUT_SECONDS = 5
_FUTURE = datetime(2099, 1, 5, 14, 30, tzinfo=timezone.utc)
_EMPTY_SCHEMA = "zz_outcome_status_empty"
_COUNT_KEYS = ["pending", "pending_retry", "blocked", "recorded", "other"]
_TRADE_FIELDS = {"trade_id", "symbol", "strategy_name", "outcome_status", "outcome_id", "updated_at"}


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


def _clean_test_rows() -> None:
    """`trades.outcome_id` FKs `strategy_outcomes`, so trades go first."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM trades WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_test_rows()
    yield
    _clean_test_rows()


def _insert_trade(**overrides) -> Trade:
    """One `trades` row that is IN the route's population by default
    (approved, closed, simulated/simulated, auto, NULL outcome_status).
    Overrides steer a single dimension out of (or around) it. `updated_at`
    is written explicitly on INSERT, so the ORM's `onupdate` never rewrites it."""
    session = SessionLocal()
    try:
        fields = dict(
            execution_mode="simulated",
            execution_venue="simulated",
            origin="auto",
            strategy_name=_STRATEGY_NAME,
            strategy_version="v1",
            direction="BUY",
            symbol="ZZOS1",
            thesis={},
            decision="approved",
            limits_snapshot={},
            status="closed",
            outcome_status=None,
            updated_at=_FUTURE,
        )
        fields.update(overrides)
        trade = Trade(**fields)
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade
    finally:
        session.close()


def _insert_outcome() -> uuid.UUID:
    """A real `strategy_outcomes` row so `trades.outcome_id` (an enforced FK)
    can be set. Field set mirrors the strategy-outcomes route tests."""
    now = datetime(2026, 9, 10, 14, 35, tzinfo=timezone.utc)
    outcome_id = uuid.uuid4()
    session = SessionLocal()
    try:
        session.add(
            StrategyOutcomeRecord(
                outcome_id=outcome_id,
                opportunity_id=uuid.uuid4(),
                schema_version=2,
                strategy_name=_STRATEGY_NAME,
                strategy_version="v1",
                symbol="ZZOS1",
                origin="auto",
                is_backtest=False,
                backtest_run_id=None,
                execution_mode="simulated",
                execution_venue="simulated",
                trading_day=now.date(),
                setup_detected_at=now,
                signal_confirmed_at=None,
                decided_at=now,
                entry_filled_at=now,
                exit_filled_at=now + timedelta(minutes=5),
                holding_seconds=300,
                direction="BUY",
                entry_price=10.0,
                entry_qty=1,
                exit_price=11.0,
                exit_qty=1,
                commission_total=None,
                slippage_entry=0.0,
                realized_pnl=1.0,
                realized_r=1.0,
                exit_reason="target",
                structural_invalidation=9.0,
                structural_target=11.0,
                final_stop=9.0,
                final_target=11.0,
                confidence_at_signal=0.5,
                evidence={"basis": "live"},
                market_state_at_entry=None,
                context_at_entry=None,
                market_state_at_exit=None,
                context_at_exit=None,
                snapshot_missing_reasons={
                    "market_state_at_entry": "recorder_unavailable",
                    "context_at_entry": "recorder_unavailable",
                    "market_state_at_exit": "recorder_unavailable",
                    "context_at_exit": "recorder_unavailable",
                },
                feature_snapshot_id=None,
            )
        )
        session.commit()
        return outcome_id
    finally:
        session.close()


async def _get(**params) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/intelligence/execution-outcome-status", params=params)


async def _baseline() -> dict[str, int]:
    resp = await _get()
    assert resp.status_code == 200
    return resp.json()["counts"]


def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {key: after[key] - before[key] for key in _COUNT_KEYS}


def _ids(resp: httpx.Response) -> list[str]:
    return [row["trade_id"] for row in resp.json()["trades"]]


# --- Population isolation ---


async def test_route_counts_and_lists_only_approved_closed_simulated_auto_trades():
    """Every excluded trade carries `recorded`, which would visibly move the
    `recorded` count (and appear in the list) if any filter leaked. `backtest`
    shares the `simulated` venue (passing the mode/venue CHECK); `paper` and
    `live` need a real-venue name. A rejected trade with status `closed` is
    schema-legal and must still be excluded by `decision`."""
    before = await _baseline()
    included = _insert_trade(outcome_status="recorded")
    excluded = [
        _insert_trade(outcome_status="recorded", decision="rejected"),
        _insert_trade(outcome_status="recorded", status="open"),
        _insert_trade(outcome_status="recorded", status="closing"),
        _insert_trade(outcome_status="recorded", status=None),
        _insert_trade(outcome_status="recorded", execution_mode="backtest", execution_venue="simulated"),
        _insert_trade(outcome_status="recorded", execution_mode="paper", execution_venue="ibkr"),
        _insert_trade(outcome_status="recorded", execution_mode="live", execution_venue="ibkr"),
        _insert_trade(outcome_status="recorded", origin="manual"),
    ]

    resp = await _get(limit=100)

    assert resp.status_code == 200
    assert _delta(resp.json()["counts"], before) == {
        "pending": 0, "pending_retry": 0, "blocked": 0, "recorded": 1, "other": 0,
    }
    ids = _ids(resp)
    assert str(included.trade_id) in ids
    for trade in excluded:
        assert str(trade.trade_id) not in ids


async def test_route_ignores_mode_and_origin_query_parameters():
    """Population is not a parameter: undeclared params are dropped by FastAPI
    and cannot widen the scope."""
    before = await _baseline()
    _insert_trade(outcome_status="recorded", execution_mode="backtest", execution_venue="simulated")
    _insert_trade(outcome_status="recorded", origin="manual")

    resp = await _get(execution_mode="backtest", origin="manual", decision="rejected", status="open")

    assert resp.status_code == 200
    assert _delta(resp.json()["counts"], before) == {k: 0 for k in _COUNT_KEYS}


# --- Status buckets ---


async def test_route_counts_every_status_bucket_exactly():
    before = await _baseline()
    outcome_id = _insert_outcome()
    for _ in range(2):
        _insert_trade(outcome_status=None)  # SQL NULL -> pending
    _insert_trade(outcome_status="pending_retry")
    for _ in range(3):
        _insert_trade(outcome_status="blocked")
    _insert_trade(outcome_status="recorded", outcome_id=outcome_id)
    _insert_trade(outcome_status="recorded")
    # Unexpected values: the literal "pending" is never written by the
    # recorder, so it is `other` (not `pending`); so is an empty string and any
    # unknown label; case differences are not folded.
    for unexpected in ("pending", "", "weird", "BLOCKED"):
        _insert_trade(outcome_status=unexpected)

    resp = await _get(limit=1)  # tiny limit: counts must not depend on it

    assert resp.status_code == 200
    body = resp.json()
    assert list(body["counts"]) == _COUNT_KEYS
    assert _delta(body["counts"], before) == {
        "pending": 2, "pending_retry": 1, "blocked": 3, "recorded": 2, "other": 4,
    }
    assert len(body["trades"]) == 1


async def test_route_counts_are_independent_of_limit():
    before = await _baseline()
    for i in range(5):
        _insert_trade(outcome_status="blocked", updated_at=_FUTURE + timedelta(minutes=i))

    small = await _get(limit=2)
    large = await _get(limit=100)

    assert len(small.json()["trades"]) == 2
    assert small.json()["counts"] == large.json()["counts"]
    assert _delta(small.json()["counts"], before)["blocked"] == 5


async def test_route_buckets_are_exclusive_and_sum_to_the_population():
    before = await _baseline()
    for value in (None, "pending_retry", "blocked", "recorded", "other-thing"):
        _insert_trade(outcome_status=value)

    counts = (await _get()).json()["counts"]

    assert sum(_delta(counts, before).values()) == 5


async def test_route_null_status_is_returned_as_null_and_counted_pending():
    before = await _baseline()
    trade = _insert_trade(symbol="ZZOS2", outcome_status=None)

    resp = await _get()

    row = next(r for r in resp.json()["trades"] if r["trade_id"] == str(trade.trade_id))
    assert row["outcome_status"] is None  # never rewritten to "pending"
    assert row["outcome_id"] is None
    assert _delta(resp.json()["counts"], before) == {
        "pending": 1, "pending_retry": 0, "blocked": 0, "recorded": 0, "other": 0,
    }


async def test_route_returns_stored_status_verbatim_including_unexpected_values():
    _insert_trade(symbol="ZZOS3A", outcome_status="pending_retry", updated_at=_FUTURE + timedelta(minutes=3))
    _insert_trade(symbol="ZZOS3B", outcome_status="blocked", updated_at=_FUTURE + timedelta(minutes=2))
    _insert_trade(symbol="ZZOS3C", outcome_status="recorded", updated_at=_FUTURE + timedelta(minutes=1))
    _insert_trade(symbol="ZZOS3D", outcome_status="pending", updated_at=_FUTURE)

    rows = (await _get(limit=4)).json()["trades"]

    assert [(r["symbol"], r["outcome_status"]) for r in rows] == [
        ("ZZOS3A", "pending_retry"), ("ZZOS3B", "blocked"), ("ZZOS3C", "recorded"), ("ZZOS3D", "pending"),
    ]


# --- Ordering, ties, limit ---


async def test_route_orders_newest_updated_at_first_not_insertion_order():
    middle = _insert_trade(updated_at=_FUTURE + timedelta(minutes=5))
    oldest = _insert_trade(updated_at=_FUTURE)
    newest = _insert_trade(updated_at=_FUTURE + timedelta(minutes=10))

    resp = await _get(limit=3)

    assert _ids(resp) == [str(newest.trade_id), str(middle.trade_id), str(oldest.trade_id)]


async def test_route_breaks_updated_at_ties_by_trade_id_descending_and_is_stable():
    tied_at = _FUTURE + timedelta(minutes=3)
    # Inserted in strictly ASCENDING id order while the route must return them
    # DESCENDING, so a route without its tie-break fails deterministically.
    tied_ids = sorted(uuid.uuid4() for _ in range(4))
    for tied_id in tied_ids:
        _insert_trade(updated_at=tied_at, trade_id=tied_id)
    newer = _insert_trade(updated_at=tied_at + timedelta(minutes=1))
    older = _insert_trade(updated_at=tied_at - timedelta(minutes=1))
    expected = [str(newer.trade_id), *(str(i) for i in reversed(tied_ids)), str(older.trade_id)]

    first = await _get(limit=6)
    second = await _get(limit=6)

    assert _ids(first) == expected
    assert _ids(second) == expected


async def test_route_limit_cuts_through_a_tie_deterministically():
    tied_at = _FUTURE + timedelta(minutes=7)
    tied_ids = sorted(uuid.uuid4() for _ in range(3))
    for tied_id in tied_ids:
        _insert_trade(updated_at=tied_at, trade_id=tied_id)
    expected = [str(i) for i in reversed(tied_ids)][:2]

    first = await _get(limit=2)
    second = await _get(limit=2)

    assert _ids(first) == expected
    assert _ids(second) == expected


async def test_route_default_limit_is_fifty_and_keeps_the_newest_fifty():
    inserted = [_insert_trade(updated_at=_FUTURE + timedelta(minutes=i)) for i in range(51)]
    newest_first = [str(t.trade_id) for t in reversed(inserted)]

    resp = await _get()

    assert resp.status_code == 200
    ids = _ids(resp)
    assert len(ids) == 50
    assert ids == newest_first[:50]
    assert str(inserted[0].trade_id) not in ids


@pytest.mark.parametrize("bad_limit", [0, -1, 101, "abc"])
async def test_route_rejects_limit_outside_bounds(bad_limit):
    resp = await _get(limit=bad_limit)
    assert resp.status_code == 422


async def test_route_accepts_limit_at_each_bound():
    _insert_trade()

    low = await _get(limit=1)
    high = await _get(limit=100)

    assert low.status_code == 200 and high.status_code == 200
    assert len(low.json()["trades"]) == 1
    assert 1 <= len(high.json()["trades"]) <= 100


# --- Empty result ---


@pytest.fixture
def _empty_schema_session_factory(monkeypatch):
    """Points the helper's `SessionLocal` at a scratch schema whose `trades`
    table is empty (same columns), via a per-connection `search_path`. The
    shared `public.trades` is never touched."""
    settings = get_settings()
    admin = SessionLocal()
    try:
        admin.execute(text(f"DROP SCHEMA IF EXISTS {_EMPTY_SCHEMA} CASCADE"))
        admin.execute(text(f"CREATE SCHEMA {_EMPTY_SCHEMA}"))
        admin.execute(text(f"CREATE TABLE {_EMPTY_SCHEMA}.trades (LIKE public.trades INCLUDING ALL)"))
        admin.commit()
    finally:
        admin.close()
    engine = create_engine(settings.database_url, connect_args={"options": f"-c search_path={_EMPTY_SCHEMA}"})
    monkeypatch.setattr(db_session_module, "SessionLocal", sessionmaker(bind=engine))
    yield
    engine.dispose()
    cleanup = SessionLocal()
    try:
        cleanup.execute(text(f"DROP SCHEMA IF EXISTS {_EMPTY_SCHEMA} CASCADE"))
        cleanup.commit()
    finally:
        cleanup.close()


async def test_route_returns_zero_counts_and_empty_list_for_an_empty_population(_empty_schema_session_factory):
    _insert_trade(outcome_status="recorded")  # lives in public.trades; must not be seen

    resp = await _get()

    assert resp.status_code == 200
    assert resp.json() == {
        "counts": {"pending": 0, "pending_retry": 0, "blocked": 0, "recorded": 0, "other": 0},
        "trades": [],
    }


async def test_route_returns_empty_list_when_only_excluded_trades_exist():
    before = await _baseline()
    _insert_trade(outcome_status="blocked", origin="manual")
    _insert_trade(outcome_status="blocked", status="open")

    resp = await _get(limit=100)

    assert resp.status_code == 200
    assert _delta(resp.json()["counts"], before) == {k: 0 for k in _COUNT_KEYS}
    assert all(row["strategy_name"] != _STRATEGY_NAME for row in resp.json()["trades"])


# --- Serialization ---


async def test_route_serializes_exact_response_shape_and_types():
    outcome_id = _insert_outcome()
    updated_at = datetime(2099, 3, 4, 15, 16, 17, 123456, tzinfo=timezone.utc)
    trade = _insert_trade(
        symbol="ZZOS4", outcome_status="recorded", outcome_id=outcome_id, updated_at=updated_at,
    )

    resp = await _get(limit=1)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"counts", "trades"}
    assert list(body["counts"]) == _COUNT_KEYS
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in body["counts"].values())
    row = body["trades"][0]
    # Exactly the contract's field set: no blocked reason, mode, venue, status leakage.
    assert set(row) == _TRADE_FIELDS
    assert row["trade_id"] == str(trade.trade_id)
    assert row["symbol"] == "ZZOS4"
    assert row["strategy_name"] == _STRATEGY_NAME
    assert row["outcome_status"] == "recorded"
    assert row["outcome_id"] == str(outcome_id)
    parsed = datetime.fromisoformat(row["updated_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)  # normalised to UTC
    assert parsed == updated_at  # microseconds preserved


async def test_route_normalises_updated_at_to_utc_regardless_of_session_timezone(monkeypatch):
    """The route's own connection may run in a non-UTC server/session zone
    (psycopg2 then returns that offset); the response must still be UTC."""
    _insert_trade(symbol="ZZOS5", updated_at=datetime(2099, 6, 1, 6, 0, tzinfo=timezone.utc))
    engine = create_engine(get_settings().database_url, connect_args={"options": "-c timezone=Asia/Dhaka"})
    monkeypatch.setattr(db_session_module, "SessionLocal", sessionmaker(bind=engine))
    try:
        row = (await _get(limit=1)).json()["trades"][0]
    finally:
        engine.dispose()

    assert row["symbol"] == "ZZOS5"
    assert datetime.fromisoformat(row["updated_at"]).utcoffset() == timedelta(0)
    assert datetime.fromisoformat(row["updated_at"]) == datetime(2099, 6, 1, 6, 0, tzinfo=timezone.utc)


# --- Read-only ---


async def test_route_is_read_only():
    """A GET leaves the trade (status, link and `updated_at`) untouched, and
    the path accepts no write verbs."""
    trade = _insert_trade(symbol="ZZOS6", outcome_status="blocked")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/intelligence/execution-outcome-status")).status_code == 200
        for method in ("post", "put", "patch", "delete"):
            resp = await getattr(client, method)("/intelligence/execution-outcome-status")
            assert resp.status_code == 405, method

    session = SessionLocal()
    try:
        stored = session.get(Trade, trade.trade_id)
        assert stored is not None
        assert (stored.status, stored.outcome_status, stored.outcome_id) == ("closed", "blocked", None)
        assert stored.updated_at == trade.updated_at
    finally:
        session.close()


# --- Off-the-event-loop regression (asyncio.to_thread offload) ---


@pytest.fixture
def _blocked_fetch_execution_outcome_status(monkeypatch):
    """Same technique as the sibling route tests: replace the module-level sync
    helper with one that blocks on a `threading.Event`, proving
    deterministically that the call runs in a worker thread."""
    started = threading.Event()
    release = threading.Event()
    empty = {"counts": {k: 0 for k in _COUNT_KEYS}, "trades": []}

    def _blocked(limit):
        started.set()
        if not release.wait(timeout=_BLOCK_TIMEOUT_SECONDS):
            raise TimeoutError("test never released the blocked execution-outcome-status call")
        return empty

    monkeypatch.setattr(intelligence_routes, "_fetch_execution_outcome_status", _blocked)
    return started, release


async def test_blocked_outcome_status_read_does_not_block_an_unrelated_route(_blocked_fetch_execution_outcome_status):
    started, release = _blocked_fetch_execution_outcome_status

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task = asyncio.create_task(client.get("/intelligence/execution-outcome-status"))

        assert await asyncio.to_thread(started.wait, _BLOCK_TIMEOUT_SECONDS), "blocked helper never started"

        health = await asyncio.wait_for(client.get("/health"), timeout=2.0)
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        release.set()
        response = await asyncio.wait_for(task, timeout=_BLOCK_TIMEOUT_SECONDS)

    assert response.status_code == 200
    assert response.json() == {"counts": {k: 0 for k in _COUNT_KEYS}, "trades": []}
