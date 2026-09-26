"""
Route-level tests for the new `GET /intelligence/execution-orders` — the
first reader of the `orders` ledger (`models/execution_ledger.py`,
decision #172) anywhere in this codebase. Separate file, not an extension
of `test_backtest_runs_route.py` or `test_exit_intents_route.py`, matching
this suite's own established one-file-per-route-group precedent (that
file's own docstring makes the same call for `backtests` vs.
`strategy_outcomes`): this route reads a different table from either.

Hand-inserted rows only, no live authorizer-stub/Execution Engine run —
`test_execution_ledger.py` and `test_execution_engine.py` (sibling test
files) already cover that the real write path produces valid `orders`
rows; what's under test here is the route's own read shape: ordering,
filtering, bounds, empty-result, and UUID/timestamp serialization. Same
posture `test_backtest_runs_route.py`'s own hand-inserted-row tests take
for `backtests` rows it doesn't need a live run to produce.

Ordering/filtering assertions never assume the table is otherwise
empty — every hand-inserted row here carries a `_STRATEGY_NAME`-tagged
`trades` row (matching `test_execution_ledger.py`'s own `_make_trade`
marker convention) so cleanup is scoped and exact, and symbols are
synthetic (`ZZXO*`) so a filtered query can't accidentally pick up an
unrelated row from a different file or a real running system.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.api.routes.intelligence as intelligence_routes
from app.db.session import SessionLocal
from app.main import app
from app.models.execution_ledger import Order, Trade

_STRATEGY_NAME = "__TEST_ROUTE_EXECUTION_ORDERS__"
_BLOCK_TIMEOUT_SECONDS = 5


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
    """Mirrors test_execution_ledger.py's own _clean_test_rows() — orders
    and trades are cleaned in that order (orders carries the FK)."""
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM orders WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"),
            {"n": _STRATEGY_NAME},
        )
        session.execute(text("DELETE FROM trades WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_test_rows()
    yield
    _clean_test_rows()


def _make_trade(session, **overrides) -> Trade:
    defaults = dict(
        execution_mode="simulated",
        execution_venue="simulated",
        strategy_name=_STRATEGY_NAME,
        strategy_version="v1",
        direction="BUY",
        symbol="ZZXO1",
        thesis={},
        decision="approved",
        limits_snapshot={},
        status="open",
    )
    defaults.update(overrides)
    trade = Trade(**defaults)
    session.add(trade)
    session.flush()
    return trade


def _insert_order(**overrides) -> Order:
    """Inserts one real `orders` row (plus the `trades` row it requires
    via FK) and returns the refreshed ORM row, so callers can read back
    the server-generated `id`/`created_at`/`updated_at` when not
    overridden. `client_order_id` defaults to `f"{trade_id}:entry"`
    (the only shape the DB's own CHECK on `trade_reservations` cares
    about, but kept consistent here too) unless overridden — needed
    because a second order for the same trade (e.g. an exit) must use a
    different, unique `client_order_id`."""
    session = SessionLocal()
    try:
        trade_overrides = {k: v for k, v in overrides.items() if k in ("execution_mode", "execution_venue", "symbol")}
        trade = _make_trade(session, **trade_overrides)
        fields = dict(
            client_order_id=f"{trade.trade_id}:entry",
            trade_id=trade.trade_id,
            execution_mode="simulated",
            execution_venue="simulated",
            symbol=trade.symbol,
            side="BUY",
            position_effect="open",
            qty=10,
        )
        fields.update(overrides)
        row = Order(**fields)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    finally:
        session.close()


# --- Ordering, filtering, bounds, empty-result ---


def test_route_orders_newest_first_by_ledger_id():
    oldest = _insert_order(symbol="ZZXO1")
    middle = _insert_order(symbol="ZZXO1")
    newest = _insert_order(symbol="ZZXO1")
    assert oldest.id < middle.id < newest.id  # sanity: ledger id is the real insertion order

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO1"})

    assert resp.status_code == 200
    ids_in_order = [row["id"] for row in resp.json()["orders"]]
    assert ids_in_order == [newest.id, middle.id, oldest.id]


def test_route_filters_by_exact_symbol():
    matching = _insert_order(symbol="ZZXO2")
    _insert_order(symbol="ZZXO3")  # different symbol — must be excluded

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO2"})

    assert resp.status_code == 200
    body = resp.json()["orders"]
    assert len(body) == 1
    assert body[0]["id"] == matching.id
    assert body[0]["symbol"] == "ZZXO2"


def test_route_symbol_filter_is_exact_not_partial_or_case_insensitive():
    """'exact symbol filter' per this route's own approved scope — a
    lowercase or substring query must not match an uppercase/full
    symbol row."""
    _insert_order(symbol="ZZXO4")

    with TestClient(app) as client:
        lower = client.get("/intelligence/execution-orders", params={"symbol": "zzxo4"})
        partial = client.get("/intelligence/execution-orders", params={"symbol": "ZZX"})

    assert lower.status_code == 200
    assert lower.json() == {"orders": []}
    assert partial.status_code == 200
    assert partial.json() == {"orders": []}


def test_route_excludes_backtest_mode_orders():
    """Hard-scoped to execution_mode == 'simulated' (not a query
    parameter, per this route's own approved scope) — a 'backtest'-mode
    row (same 'simulated' venue, passing the DB's own mode/venue CHECK)
    must never appear, with no way for a caller to ask for it."""
    simulated = _insert_order(symbol="ZZXO5", execution_mode="simulated", execution_venue="simulated")
    _insert_order(symbol="ZZXO5", execution_mode="backtest", execution_venue="simulated")

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO5"})

    assert resp.status_code == 200
    body = resp.json()["orders"]
    assert len(body) == 1
    assert body[0]["id"] == simulated.id


def test_route_limit_caps_returned_rows_to_the_most_recent():
    _insert_order(symbol="ZZXO6")
    middle = _insert_order(symbol="ZZXO6")
    newest = _insert_order(symbol="ZZXO6")

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO6", "limit": 2})

    assert resp.status_code == 200
    body = resp.json()["orders"]
    assert len(body) == 2
    assert [row["id"] for row in body] == [newest.id, middle.id]


def test_route_default_limit_is_fifty():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO_NONE"})
    assert resp.status_code == 200  # no rows either way here; this just confirms no limit param is required


def test_route_rejects_limit_below_minimum():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"limit": 0})
    assert resp.status_code == 422


def test_route_rejects_limit_above_maximum():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"limit": 101})
    assert resp.status_code == 422


def test_route_accepts_limit_at_each_bound():
    _insert_order(symbol="ZZXO7")
    with TestClient(app) as client:
        low = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO7", "limit": 1})
        high = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO7", "limit": 100})
    assert low.status_code == 200
    assert high.status_code == 200
    assert len(low.json()["orders"]) == 1
    assert len(high.json()["orders"]) == 1


def test_route_returns_honest_empty_collection_for_unknown_symbol():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO_NEVER_INSERTED"})

    assert resp.status_code == 200
    assert resp.json() == {"orders": []}


# --- Response shape / serialization ---


def test_route_response_includes_curated_fields_with_safe_serialization():
    row = _insert_order(
        symbol="ZZXO8",
        side="SELL",
        position_effect="close",
        qty=25,
        status="filled",
        exit_reason="target",
        reject_reason=None,
    )

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO8"})

    assert resp.status_code == 200
    body = resp.json()["orders"]
    assert len(body) == 1
    order = body[0]

    # Identity + linkage
    assert order["id"] == row.id
    assert order["client_order_id"] == row.client_order_id
    assert order["trade_id"] == str(row.trade_id)  # UUID serialized as a plain string, not an object

    # Curated business fields
    assert order["symbol"] == "ZZXO8"
    assert order["side"] == "SELL"
    assert order["position_effect"] == "close"
    assert order["qty"] == 25
    assert order["status"] == "filled"
    assert order["execution_venue"] == "simulated"
    assert order["exit_reason"] == "target"
    assert order["reject_reason"] is None

    # Timestamps parse back as real ISO-8601 datetimes, not opaque strings
    created_at = datetime.fromisoformat(order["created_at"])
    updated_at = datetime.fromisoformat(order["updated_at"])
    assert created_at.tzinfo is not None
    assert updated_at.tzinfo is not None

    # Never a full-row dump — order_type/limit_price/venue_order_id/
    # execution_mode are real columns this route's own approved field
    # list doesn't ask for.
    assert "order_type" not in order
    assert "limit_price" not in order
    assert "venue_order_id" not in order
    assert "execution_mode" not in order


def test_route_rejection_reason_is_nullable_and_present_when_set():
    _insert_order(symbol="ZZXO9", status="rejected", reject_reason="risk_limit_exceeded", exit_reason=None)

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-orders", params={"symbol": "ZZXO9"})

    order = resp.json()["orders"][0]
    assert order["status"] == "rejected"
    assert order["reject_reason"] == "risk_limit_exceeded"
    assert order["exit_reason"] is None


# --- Off-the-event-loop regression (asyncio.to_thread offload) ---


@pytest.fixture
def _blocked_fetch_execution_orders(monkeypatch):
    """Same technique test_scanner_route_concurrency.py already
    established for scanner-route-db-offload: monkeypatch the
    module-level sync helper (as looked up in
    app.api.routes.intelligence's own namespace) with a fake that blocks
    on a threading.Event until released, so the test can deterministically
    prove the blocked call is running in a worker thread — not racing a
    sleep — before asserting an unrelated route stays responsive."""
    started = threading.Event()
    release = threading.Event()

    def _blocked(symbol, limit):
        started.set()
        if not release.wait(timeout=_BLOCK_TIMEOUT_SECONDS):
            raise TimeoutError("test never released the blocked execution-orders call")
        return []

    monkeypatch.setattr(intelligence_routes, "_fetch_execution_orders", _blocked)
    return started, release


async def test_blocked_execution_orders_read_does_not_block_an_unrelated_route(_blocked_fetch_execution_orders):
    started, release = _blocked_fetch_execution_orders

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        orders_task = asyncio.create_task(client.get("/intelligence/execution-orders"))

        started_in_time = await asyncio.to_thread(started.wait, _BLOCK_TIMEOUT_SECONDS)
        assert started_in_time, "blocked execution-orders helper never started"

        # The event loop must still be free to serve an unrelated,
        # lightweight route while that request is stuck in its worker
        # thread — the actual regression this test guards against.
        health_response = await asyncio.wait_for(client.get("/health"), timeout=2.0)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        release.set()
        orders_response = await asyncio.wait_for(orders_task, timeout=_BLOCK_TIMEOUT_SECONDS)

    assert orders_response.status_code == 200
    assert orders_response.json() == {"orders": []}
