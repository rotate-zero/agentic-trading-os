"""
Route-level tests for the new `GET /intelligence/execution-exit-requests` — a
read-only view of the persisted `exit_requests` rows (`models/
execution_ledger.py`, decision #184) joined to their positions. Separate file,
matching this suite's one-file-per-route-group precedent
(`test_execution_positions_route.py`, `test_execution_fills_route.py`).

Real PostgreSQL, hand-inserted rows only: no Execution Engine, Position
Monitor or Portfolio State run is involved, because what is under test is the
route's own read shape — mode isolation through the joined position, exact
symbol filtering, newest-first ordering with a deterministic tie-break, limit
bounds, the empty result, exact-decimal/null serialization, the distinction
from the in-memory `/exit-intents` route, and event-loop responsiveness.

**No app lifespan is booted, on purpose** (same reasoning as the positions
route tests): every request goes through `httpx.ASGITransport(app=app)`, which
does not run `main.py`'s `lifespan()`. Booting it would run Portfolio State's
startup restore against positions that have no fills behind them and log a
`PositionLedgerError`, exercising startup behavior rather than this route.
Consequently `app.state.position_monitor` is unset, which the
`/exit-intents` comparison test relies on.

Isolation: every inserted `positions` row belongs to a `trades` row tagged with
`_STRATEGY_NAME`, so cleanup is scoped and exact; symbols are synthetic
(`ZZXR*`) so a filtered query cannot pick up a row from another test file. No
assertion assumes the table is otherwise empty.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

import app.api.routes.intelligence as intelligence_routes
from app.db.session import SessionLocal
from app.main import app
from app.models.execution_ledger import ExitRequest, Position, Trade

_STRATEGY_NAME = "__TEST_ROUTE_EXECUTION_EXIT_REQUESTS__"
_BLOCK_TIMEOUT_SECONDS = 5
_BASE_TS = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
_EXPECTED_FIELDS = {
    "position_id",
    "symbol",
    "exit_reason",
    "trigger_price",
    "trigger_ts",
    "retry_after",
    "created_at",
    "position_status",
    "remaining_qty",
}


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
    """`exit_requests` FKs `positions` and `positions` FKs `trades`, so delete
    in that order. Nothing else references these rows: no orders, fills or
    receipts are created here, and no lifespan runs to create them."""
    session = SessionLocal()
    try:
        scoped = "SELECT trade_id FROM trades WHERE strategy_name = :n"
        session.execute(
            text(f"DELETE FROM exit_requests WHERE position_id IN (SELECT position_id FROM positions WHERE trade_id IN ({scoped}))"),
            {"n": _STRATEGY_NAME},
        )
        session.execute(text(f"DELETE FROM positions WHERE trade_id IN ({scoped})"), {"n": _STRATEGY_NAME})
        session.execute(text("DELETE FROM trades WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_test_rows()
    yield
    _clean_test_rows()


def _insert_exit_request(*, position_overrides: dict | None = None, **request_overrides) -> tuple[Position, ExitRequest]:
    """Inserts a `trades` row (FK), a `positions` row and its one
    `exit_requests` row, returning both refreshed ORM rows. `position_overrides`
    steer the position (mode/venue/symbol/qty/status/position_id); everything
    else steers the request. The mode/venue pairing CHECK applies to both
    `trades` and `positions`."""
    position_overrides = dict(position_overrides or {})
    session = SessionLocal()
    try:
        mode = position_overrides.pop("execution_mode", "simulated")
        venue = position_overrides.pop("execution_venue", "simulated")
        symbol = position_overrides.pop("symbol", "ZZXR1")
        trade = Trade(
            execution_mode=mode,
            execution_venue=venue,
            strategy_name=_STRATEGY_NAME,
            strategy_version="v1",
            direction="BUY",
            symbol=symbol,
            thesis={},
            decision="approved",
            limits_snapshot={},
            status="open",
        )
        session.add(trade)
        session.flush()

        position_fields = dict(
            trade_id=trade.trade_id,
            execution_mode=mode,
            execution_venue=venue,
            symbol=symbol,
            side="BUY",
            qty=10,
            avg_price=Decimal("190.000000"),
            opened_at=_BASE_TS,
            status="open",
        )
        position_fields.update(position_overrides)
        position = Position(**position_fields)
        session.add(position)
        session.flush()

        request_fields = dict(
            position_id=position.position_id,
            exit_reason="stop",
            trigger_price=Decimal("185.500000"),
            trigger_ts=_BASE_TS + timedelta(minutes=30),
        )
        request_fields.update(request_overrides)
        request = ExitRequest(**request_fields)
        session.add(request)
        session.commit()
        session.refresh(position)
        session.refresh(request)
        return position, request
    finally:
        session.close()


async def _get(**params) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/intelligence/execution-exit-requests", params=params)


def _ids(response: httpx.Response) -> list[str]:
    return [row["position_id"] for row in response.json()["exit_requests"]]


# --- Mode isolation (through the joined position) ---


async def test_route_excludes_requests_of_every_other_position_mode():
    """Hard-scoped to `Position.execution_mode == 'simulated'`. `backtest`
    shares the `simulated` venue (passing the mode/venue CHECK); `paper` and
    `live` need a real-venue name. None may ever appear."""
    _, simulated = _insert_exit_request(position_overrides={"symbol": "ZZXR2"})
    _insert_exit_request(position_overrides={"symbol": "ZZXR2", "execution_mode": "backtest", "execution_venue": "simulated"})
    _insert_exit_request(position_overrides={"symbol": "ZZXR2", "execution_mode": "paper", "execution_venue": "ibkr"})
    _insert_exit_request(position_overrides={"symbol": "ZZXR2", "execution_mode": "live", "execution_venue": "ibkr"})

    resp = await _get(symbol="ZZXR2")

    assert resp.status_code == 200
    assert _ids(resp) == [str(simulated.position_id)]


async def test_route_ignores_an_execution_mode_query_parameter():
    _, simulated = _insert_exit_request(position_overrides={"symbol": "ZZXR3"})
    _insert_exit_request(position_overrides={"symbol": "ZZXR3", "execution_mode": "backtest", "execution_venue": "simulated"})

    resp = await _get(symbol="ZZXR3", execution_mode="backtest")

    assert resp.status_code == 200
    assert _ids(resp) == [str(simulated.position_id)]


# --- Symbol filter ---


async def test_route_filters_by_exact_symbol():
    wanted, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR4"})
    _insert_exit_request(position_overrides={"symbol": "ZZXR5"})

    resp = await _get(symbol="ZZXR4")

    assert resp.status_code == 200
    body = resp.json()["exit_requests"]
    assert [row["position_id"] for row in body] == [str(wanted.position_id)]
    assert body[0]["symbol"] == "ZZXR4"


async def test_route_symbol_filter_is_exact_not_partial_or_case_insensitive():
    _insert_exit_request(position_overrides={"symbol": "ZZXR6"})

    lower = await _get(symbol="zzxr6")
    partial = await _get(symbol="ZZXR")

    assert lower.status_code == 200 and lower.json() == {"exit_requests": []}
    assert partial.status_code == 200 and partial.json() == {"exit_requests": []}


async def test_route_without_symbol_returns_requests_across_symbols():
    # Far-future trigger times make these the newest rows in the table, so
    # unrelated simulated rows can never push them out of the 100-row window.
    future = datetime(2099, 1, 5, 14, 30, tzinfo=timezone.utc)
    a, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR7A"}, trigger_ts=future)
    b, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR7B"}, trigger_ts=future + timedelta(minutes=1))

    resp = await _get(limit=100)

    assert resp.status_code == 200
    ids = _ids(resp)
    assert str(a.position_id) in ids and str(b.position_id) in ids
    assert ids.index(str(b.position_id)) < ids.index(str(a.position_id))


# --- Ordering and ties ---


async def test_route_orders_newest_trigger_first_not_insertion_or_creation_order():
    """Inserted out of time order, so insertion order, `created_at` and any
    accidental key ordering cannot satisfy the assertion."""
    middle, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR8"}, trigger_ts=_BASE_TS + timedelta(minutes=5))
    oldest, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR8"}, trigger_ts=_BASE_TS)
    newest, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR8"}, trigger_ts=_BASE_TS + timedelta(minutes=10))

    resp = await _get(symbol="ZZXR8")

    assert resp.status_code == 200
    assert _ids(resp) == [str(newest.position_id), str(middle.position_id), str(oldest.position_id)]


async def test_route_breaks_trigger_ts_ties_by_position_id_descending_and_is_stable():
    tied_at = _BASE_TS + timedelta(minutes=3)
    # Inserted ascending, must come back descending: a route that lost its
    # tie-break fails deterministically instead of matching by chance.
    tied_ids = sorted(uuid.uuid4() for _ in range(4))
    for tied_id in tied_ids:
        _insert_exit_request(position_overrides={"symbol": "ZZXR9", "position_id": tied_id}, trigger_ts=tied_at)
    newer, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR9"}, trigger_ts=tied_at + timedelta(minutes=1))
    older, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR9"}, trigger_ts=tied_at - timedelta(minutes=1))

    expected = [str(newer.position_id), *(str(i) for i in reversed(tied_ids)), str(older.position_id)]

    first = await _get(symbol="ZZXR9")
    second = await _get(symbol="ZZXR9")

    assert first.status_code == 200
    assert _ids(first) == expected
    assert _ids(second) == expected


async def test_route_limit_cuts_through_a_tie_deterministically():
    tied_at = _BASE_TS + timedelta(minutes=7)
    tied_ids = sorted(uuid.uuid4() for _ in range(3))
    for tied_id in tied_ids:
        _insert_exit_request(position_overrides={"symbol": "ZZXR10", "position_id": tied_id}, trigger_ts=tied_at)
    expected = [str(i) for i in reversed(tied_ids)][:2]

    first = await _get(symbol="ZZXR10", limit=2)
    second = await _get(symbol="ZZXR10", limit=2)

    assert _ids(first) == expected
    assert _ids(second) == expected


# --- Limit bounds ---


async def test_route_limit_caps_returned_rows_to_the_newest():
    _insert_exit_request(position_overrides={"symbol": "ZZXR11"}, trigger_ts=_BASE_TS)
    middle, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR11"}, trigger_ts=_BASE_TS + timedelta(minutes=1))
    newest, _ = _insert_exit_request(position_overrides={"symbol": "ZZXR11"}, trigger_ts=_BASE_TS + timedelta(minutes=2))

    resp = await _get(symbol="ZZXR11", limit=2)

    assert resp.status_code == 200
    assert _ids(resp) == [str(newest.position_id), str(middle.position_id)]


async def test_route_default_limit_is_fifty_and_keeps_the_newest_fifty():
    inserted = [
        _insert_exit_request(position_overrides={"symbol": "ZZXR12"}, trigger_ts=_BASE_TS + timedelta(minutes=i))[0]
        for i in range(51)
    ]
    newest_first = [str(p.position_id) for p in reversed(inserted)]

    resp = await _get(symbol="ZZXR12")

    assert resp.status_code == 200
    ids = _ids(resp)
    assert len(ids) == 50
    assert ids == newest_first[:50]
    assert str(inserted[0].position_id) not in ids


@pytest.mark.parametrize("bad_limit", [0, -1, 101, "abc"])
async def test_route_rejects_limit_outside_bounds(bad_limit):
    resp = await _get(limit=bad_limit)
    assert resp.status_code == 422


async def test_route_accepts_limit_at_each_bound():
    _insert_exit_request(position_overrides={"symbol": "ZZXR13"})

    low = await _get(symbol="ZZXR13", limit=1)
    high = await _get(symbol="ZZXR13", limit=100)

    assert low.status_code == 200 and high.status_code == 200
    assert len(low.json()["exit_requests"]) == 1
    assert len(high.json()["exit_requests"]) == 1


# --- Empty result ---


async def test_route_returns_honest_empty_collection_for_unknown_symbol():
    resp = await _get(symbol="ZZXR_NEVER_INSERTED")

    assert resp.status_code == 200
    assert resp.json() == {"exit_requests": []}


async def test_route_returns_empty_collection_when_only_other_modes_exist_for_the_symbol():
    _insert_exit_request(position_overrides={"symbol": "ZZXR14", "execution_mode": "backtest", "execution_venue": "simulated"})

    resp = await _get(symbol="ZZXR14")

    assert resp.status_code == 200
    assert resp.json() == {"exit_requests": []}


async def test_route_returns_empty_collection_for_a_position_without_a_request():
    """A position with no `exit_requests` row is not an exit request: the
    inner join returns nothing rather than a placeholder."""
    session = SessionLocal()
    try:
        trade = Trade(
            execution_mode="simulated", execution_venue="simulated", strategy_name=_STRATEGY_NAME,
            strategy_version="v1", direction="BUY", symbol="ZZXR15", thesis={}, decision="approved",
            limits_snapshot={}, status="open",
        )
        session.add(trade)
        session.flush()
        session.add(Position(
            trade_id=trade.trade_id, execution_mode="simulated", execution_venue="simulated", symbol="ZZXR15",
            side="BUY", qty=10, avg_price=Decimal("190.000000"), opened_at=_BASE_TS, status="open",
        ))
        session.commit()
    finally:
        session.close()

    resp = await _get(symbol="ZZXR15")

    assert resp.status_code == 200
    assert resp.json() == {"exit_requests": []}


# --- Serialization ---


async def test_route_unretried_request_serializes_curated_fields_with_null_retry_after():
    position, request = _insert_exit_request(
        position_overrides={"symbol": "ZZXR16", "qty": 17, "status": "open"},
        exit_reason="target",
        trigger_price=Decimal("201.250000"),
        retry_after=None,
    )

    resp = await _get(symbol="ZZXR16")

    assert resp.status_code == 200
    body = resp.json()["exit_requests"]
    assert len(body) == 1
    row = body[0]

    # Exactly the curated set — no order status, protection flag or retry outcome.
    assert set(row) == _EXPECTED_FIELDS

    assert row["position_id"] == str(position.position_id)
    assert row["symbol"] == "ZZXR16"
    assert row["exit_reason"] == "target"
    assert row["trigger_price"] == "201.250000"
    assert isinstance(row["trigger_price"], str)
    assert row["position_status"] == "open"
    assert row["remaining_qty"] == 17

    # Unset retry_after is JSON null — never omitted and never a zero/epoch value.
    assert row["retry_after"] is None

    trigger_ts = datetime.fromisoformat(row["trigger_ts"])
    assert trigger_ts.tzinfo is not None and trigger_ts == request.trigger_ts
    created_at = datetime.fromisoformat(row["created_at"])
    assert created_at.tzinfo is not None and created_at == request.created_at


async def test_route_serializes_retry_after_as_the_stored_timestamp_only():
    retry_after = _BASE_TS + timedelta(hours=1)
    _insert_exit_request(position_overrides={"symbol": "ZZXR17"}, retry_after=retry_after)

    row = (await _get(symbol="ZZXR17")).json()["exit_requests"][0]

    assert datetime.fromisoformat(row["retry_after"]) == retry_after
    # A stored timestamp is not an outcome: nothing about attempts or results is reported.
    assert set(row) == _EXPECTED_FIELDS


async def test_route_trigger_price_is_an_exact_decimal_string():
    """`Decimal('12345678901.123456')` needs 17 significant digits; a float
    cannot be relied on to reproduce it. A small price keeps trailing zeros."""
    _insert_exit_request(position_overrides={"symbol": "ZZXR18A"}, trigger_price=Decimal("12345678901.123456"))
    _insert_exit_request(position_overrides={"symbol": "ZZXR18B"}, trigger_price=Decimal("0.100000"))

    big = (await _get(symbol="ZZXR18A")).json()["exit_requests"][0]
    small = (await _get(symbol="ZZXR18B")).json()["exit_requests"][0]

    assert big["trigger_price"] == "12345678901.123456"
    assert small["trigger_price"] == "0.100000"
    assert isinstance(big["trigger_price"], str) and isinstance(small["trigger_price"], str)


async def test_route_reports_the_positions_current_status_and_remaining_quantity():
    """Status and quantity are the position's state now, not a copy taken at
    trigger time: a partly reduced position reports `closing` and what is
    left, a fully closed one reports `closed` and 0. The request row is
    unchanged by either."""
    _insert_exit_request(position_overrides={"symbol": "ZZXR19A", "qty": 4, "status": "closing"})
    _insert_exit_request(
        position_overrides={"symbol": "ZZXR19B", "qty": 0, "status": "closed", "closed_at": _BASE_TS + timedelta(hours=2)},
    )

    closing = (await _get(symbol="ZZXR19A")).json()["exit_requests"][0]
    closed = (await _get(symbol="ZZXR19B")).json()["exit_requests"][0]

    assert (closing["position_status"], closing["remaining_qty"]) == ("closing", 4)
    assert (closed["position_status"], closed["remaining_qty"]) == ("closed", 0)


# --- Distinct from /intelligence/exit-intents ---


async def test_route_serves_persisted_rows_when_the_in_memory_monitor_is_absent():
    """`/exit-intents` reports the running monitor's memory. With no monitor
    (no lifespan here) it says `unavailable` and lists nothing; this route
    still returns the durable row, proving it does not read the monitor."""
    _, request = _insert_exit_request(position_overrides={"symbol": "ZZXR20"})
    assert getattr(app.state, "position_monitor", None) is None

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        intents = await client.get("/intelligence/exit-intents", params={"symbol": "ZZXR20"})
        persisted = await client.get("/intelligence/execution-exit-requests", params={"symbol": "ZZXR20"})

    assert intents.status_code == 200
    assert intents.json()["monitor_status"] == "unavailable"
    assert intents.json()["exit_intents"] == []
    assert persisted.status_code == 200
    assert "monitor_status" not in persisted.json() and "intent_status" not in persisted.json()
    assert [r["position_id"] for r in persisted.json()["exit_requests"]] == [str(request.position_id)]


# --- Read-only ---


async def test_route_is_read_only():
    position, request = _insert_exit_request(position_overrides={"symbol": "ZZXR21"}, retry_after=None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/intelligence/execution-exit-requests", params={"symbol": "ZZXR21"})).status_code == 200
        for method in ("post", "put", "patch", "delete"):
            resp = await getattr(client, method)("/intelligence/execution-exit-requests")
            assert resp.status_code == 405, method

    session = SessionLocal()
    try:
        stored = session.get(ExitRequest, request.position_id)
        assert stored is not None
        assert (stored.exit_reason, stored.trigger_price, stored.retry_after) == ("stop", Decimal("185.500000"), None)
        stored_position = session.get(Position, position.position_id)
        assert (stored_position.status, stored_position.qty, stored_position.exit_attempt) == ("open", 10, 0)
    finally:
        session.close()


# --- Off-the-event-loop regression (asyncio.to_thread offload) ---


@pytest.fixture
def _blocked_fetch_execution_exit_requests(monkeypatch):
    """Monkeypatch the module-level sync helper with a fake that blocks on a
    `threading.Event` until released, so the test proves deterministically —
    not by racing a sleep — that the call runs in a worker thread."""
    started = threading.Event()
    release = threading.Event()

    def _blocked(symbol, limit):
        started.set()
        if not release.wait(timeout=_BLOCK_TIMEOUT_SECONDS):
            raise TimeoutError("test never released the blocked execution-exit-requests call")
        return []

    monkeypatch.setattr(intelligence_routes, "_fetch_execution_exit_requests", _blocked)
    return started, release


async def test_blocked_exit_requests_read_does_not_block_an_unrelated_route(_blocked_fetch_execution_exit_requests):
    started, release = _blocked_fetch_execution_exit_requests

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task = asyncio.create_task(client.get("/intelligence/execution-exit-requests"))

        started_in_time = await asyncio.to_thread(started.wait, _BLOCK_TIMEOUT_SECONDS)
        assert started_in_time, "blocked execution-exit-requests helper never started"

        health_response = await asyncio.wait_for(client.get("/health"), timeout=2.0)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        release.set()
        response = await asyncio.wait_for(task, timeout=_BLOCK_TIMEOUT_SECONDS)

    assert response.status_code == 200
    assert response.json() == {"exit_requests": []}
