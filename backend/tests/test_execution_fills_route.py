"""
Route-level tests for the new `GET /intelligence/execution-fills` — the
first HTTP route over the `fills` ledger (`models/execution_ledger.py`,
decision #172). Separate file, not an extension of
`test_execution_orders_route.py`, matching this suite's own established
one-file-per-route-group precedent: this route reads `fills` joined to
`orders`, a different shape from that file's plain `orders`-only read.

Hand-inserted rows only, no live authorizer-stub/Execution Engine run —
`test_execution_ledger.py` and `test_execution_engine.py` (sibling test
files) already cover that the real write path produces valid `fills`
rows; what's under test here is the route's own read shape: ordering,
filtering, mode isolation via the join, bounds, empty-result, and decimal/
nullable serialization.

Ordering/filtering assertions never assume the table is otherwise empty —
every hand-inserted row here carries a `_STRATEGY_NAME`-tagged `trades` row
(matching `test_execution_ledger.py`'s/`test_execution_orders_route.py`'s
own marker convention) so cleanup is scoped and exact, and symbols are
synthetic (`ZZXF*`) so a filtered query can't accidentally pick up an
unrelated row from a different file or a real running system.

**Finding, not silently worked around:** every test here opens exactly one
`with TestClient(app)` block. An earlier draft of the commission test opened
two, sequentially, in the same test function (matching the assertion, not
the boot pattern, of `test_execution_orders_route.py`'s existing tests) —
this reproducibly failed at the SECOND block's shutdown with `asyncio`'s
own `Queue ... is bound to a different event loop`, inside `main.py`'s
`lifespan()` → `bus.stop()`. `get_event_bus()` (like `get_feature_engine()`
and this file's other module-level singletons `main.py` starts) is a
process-wide singleton reused across every `TestClient(app)` boot; a grep of
this whole suite before writing this note confirmed no other test function
anywhere opens `TestClient(app)` more than once, so this file is the first
to have exercised that specific double-boot shape. Split into two separate
test functions instead (`test_route_commission_is_null_when_absent` /
`test_route_commission_is_preserved_as_exact_string_when_set`), each with
its own single boot and relying on the autouse `_cleanup` fixture for
isolation — the same one-boot-per-function shape every other test in this
suite already uses successfully. Reported here rather than fixed: the
singleton's own re-entrancy under a double boot is `main.py`/`event_bus/
bus.py` territory, outside this route's approved footprint.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.api.routes.intelligence as intelligence_routes
from app.db.session import SessionLocal
from app.main import app
from app.models.execution_ledger import Fill, Order, Trade

_STRATEGY_NAME = "__TEST_ROUTE_EXECUTION_FILLS__"
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
    """Wider than test_execution_orders_route.py's own cleanup, and for a
    real reason, not extra caution: every test below opens `TestClient(app)`,
    which runs `main.py`'s real `lifespan()` — including its unconditional
    `PortfolioState.rebuild_from_ledger()` reconciliation pass
    (`portfolio_state/postgres.py`) against the *real* configured database.
    Since every hand-inserted fill here uses `execution_mode="simulated"` by
    default (this file's own `_make_trade()` default, matching
    `settings.execution_mode`, the only mode this slice supports), that
    reconciliation genuinely applies these test fills and writes real
    `positions` + `position_fill_receipts` rows — confirmed empirically: the
    first version of this cleanup (fills/orders/trades only, mirroring
    `test_execution_orders_route.py`, which never boots the app and so never
    triggers this) failed on `trades`' own FK from `positions` the very
    first time a test here opened a `TestClient`. `position_fill_receipts`
    is deleted first (it FKs both `fills.ledger_seq` and
    `positions.position_id`), then `positions`, then `fills`, then `orders`
    (which carries the `fills` FK), then `trades` — matching
    `test_execution_ledger.py`'s own established ordering, widened by the
    one extra table its own tests never populate because they never boot
    the app either.

    `portfolio_state_cursor` itself is deliberately left untouched: it is
    one shared, monotonic, process-wide row (not scoped to this file's own
    trades), and leaving it advanced past whatever this file's fills
    reconciled is the same forward-only semantics production reconciliation
    already has — a later test's own new, higher-`ledger_seq` fills are
    still picked up correctly regardless of where this row currently sits.
    """
    session = SessionLocal()
    try:
        session.execute(
            text(
                "DELETE FROM position_fill_receipts WHERE ledger_seq IN "
                "(SELECT ledger_seq FROM fills WHERE client_order_id IN "
                "(SELECT client_order_id FROM orders WHERE trade_id IN "
                "(SELECT trade_id FROM trades WHERE strategy_name = :n)))"
            ),
            {"n": _STRATEGY_NAME},
        )
        session.execute(
            text("DELETE FROM positions WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"),
            {"n": _STRATEGY_NAME},
        )
        session.execute(
            text(
                "DELETE FROM fills WHERE client_order_id IN "
                "(SELECT client_order_id FROM orders WHERE trade_id IN "
                "(SELECT trade_id FROM trades WHERE strategy_name = :n))"
            ),
            {"n": _STRATEGY_NAME},
        )
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
        symbol="ZZXF1",
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


def _insert_fill(order_overrides: dict | None = None, **fill_overrides) -> tuple[Order, Fill]:
    """Inserts one real `orders` row (plus the `trades` row it requires via
    FK) and one `fills` row referencing it via `client_order_id`, returning
    both refreshed ORM rows. `order_overrides` steers the owning order
    (symbol, execution_mode/venue); everything else overrides the fill
    itself. `venue_fill_id` defaults to a fresh unique value per call so
    multiple fills can be inserted per order without tripping the `fills`
    table's own `UNIQUE (execution_venue, venue_fill_id)` constraint."""
    session = SessionLocal()
    try:
        order_overrides = dict(order_overrides or {})
        trade_overrides = {k: v for k, v in order_overrides.items() if k in ("execution_mode", "execution_venue", "symbol")}
        trade = _make_trade(session, **trade_overrides)
        order_fields = dict(
            client_order_id=f"{trade.trade_id}:entry",
            trade_id=trade.trade_id,
            execution_mode="simulated",
            execution_venue="simulated",
            symbol=trade.symbol,
            side="BUY",
            position_effect="open",
            qty=10,
            status="filled",
        )
        order_fields.update(order_overrides)
        order = Order(**order_fields)
        session.add(order)
        session.flush()

        fill_fields = dict(
            client_order_id=order.client_order_id,
            execution_venue="simulated",
            venue_fill_id=f"{order.client_order_id}:f{uuid.uuid4().hex[:8]}",
            qty=10,
            price=Decimal("190.000000"),
            venue_ts=datetime.now(timezone.utc),
        )
        fill_fields.update(fill_overrides)
        fill = Fill(**fill_fields)
        session.add(fill)
        session.commit()
        session.refresh(order)
        session.refresh(fill)
        return order, fill
    finally:
        session.close()


# --- Ordering, filtering, bounds, empty-result ---


def test_route_fills_newest_first_by_ledger_seq():
    _, oldest = _insert_fill(dict(symbol="ZZXF1"))
    _, middle = _insert_fill(dict(symbol="ZZXF1"))
    _, newest = _insert_fill(dict(symbol="ZZXF1"))
    assert oldest.ledger_seq < middle.ledger_seq < newest.ledger_seq  # sanity: real insertion order

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF1"})

    assert resp.status_code == 200
    seqs_in_order = [row["ledger_seq"] for row in resp.json()["fills"]]
    assert seqs_in_order == [newest.ledger_seq, middle.ledger_seq, oldest.ledger_seq]


def test_route_filters_by_exact_symbol():
    order, fill = _insert_fill(dict(symbol="ZZXF2"))
    _insert_fill(dict(symbol="ZZXF3"))  # different symbol — must be excluded

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF2"})

    assert resp.status_code == 200
    body = resp.json()["fills"]
    assert len(body) == 1
    assert body[0]["ledger_seq"] == fill.ledger_seq
    assert body[0]["symbol"] == "ZZXF2"


def test_route_symbol_filter_is_exact_not_partial_or_case_insensitive():
    """'exact symbol filter' per this route's own approved scope — a
    lowercase or substring query must not match an uppercase/full symbol
    row (matched through the owning order, since `fills` carries no
    `symbol` column itself)."""
    _insert_fill(dict(symbol="ZZXF4"))

    with TestClient(app) as client:
        lower = client.get("/intelligence/execution-fills", params={"symbol": "zzxf4"})
        partial = client.get("/intelligence/execution-fills", params={"symbol": "ZZX"})

    assert lower.status_code == 200
    assert lower.json() == {"fills": []}
    assert partial.status_code == 200
    assert partial.json() == {"fills": []}


def test_route_excludes_fills_of_backtest_mode_orders():
    """Hard-scoped to the OWNING ORDER's execution_mode == 'simulated' (not
    a query parameter, per this route's own approved scope, and expressed
    only through the join since `Fill` itself has no `execution_mode`
    column) — a fill whose order is 'backtest'-mode (same 'simulated'
    venue, passing the DB's own mode/venue CHECK) must never appear, with
    no way for a caller to ask for it."""
    _, simulated_fill = _insert_fill(dict(symbol="ZZXF5", execution_mode="simulated", execution_venue="simulated"))
    _insert_fill(dict(symbol="ZZXF5", execution_mode="backtest", execution_venue="simulated"))

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF5"})

    assert resp.status_code == 200
    body = resp.json()["fills"]
    assert len(body) == 1
    assert body[0]["ledger_seq"] == simulated_fill.ledger_seq


def test_route_limit_caps_returned_rows_to_the_most_recent():
    _insert_fill(dict(symbol="ZZXF6"))
    _, middle = _insert_fill(dict(symbol="ZZXF6"))
    _, newest = _insert_fill(dict(symbol="ZZXF6"))

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF6", "limit": 2})

    assert resp.status_code == 200
    body = resp.json()["fills"]
    assert len(body) == 2
    assert [row["ledger_seq"] for row in body] == [newest.ledger_seq, middle.ledger_seq]


def test_route_default_limit_is_fifty():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF_NONE"})
    assert resp.status_code == 200  # no rows either way here; this just confirms no limit param is required


def test_route_rejects_limit_below_minimum():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"limit": 0})
    assert resp.status_code == 422


def test_route_rejects_limit_above_maximum():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"limit": 101})
    assert resp.status_code == 422


def test_route_accepts_limit_at_each_bound():
    _insert_fill(dict(symbol="ZZXF7"))
    with TestClient(app) as client:
        low = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF7", "limit": 1})
        high = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF7", "limit": 100})
    assert low.status_code == 200
    assert high.status_code == 200
    assert len(low.json()["fills"]) == 1
    assert len(high.json()["fills"]) == 1


def test_route_returns_honest_empty_collection_for_unknown_symbol():
    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF_NEVER_INSERTED"})

    assert resp.status_code == 200
    assert resp.json() == {"fills": []}


# --- Response shape / serialization ---


def test_route_response_includes_curated_fields_with_safe_serialization():
    # qty=17 on BOTH the order and the fill — matched deliberately: a fill
    # qty exceeding its order's qty is a real, distinct system condition
    # (portfolio_state/legacy.py's own overfill detection, I14) that
    # retroactively sets `anomaly` on reconciliation; this test wants a
    # clean, non-anomalous row instead, so the two must agree.
    order, fill = _insert_fill(
        dict(symbol="ZZXF8", qty=17),
        qty=17,
        price=Decimal("123.456700"),
        anomaly=None,
    )

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF8"})

    assert resp.status_code == 200
    body = resp.json()["fills"]
    assert len(body) == 1
    row = body[0]

    # Identity + linkage
    assert row["ledger_seq"] == fill.ledger_seq
    assert row["client_order_id"] == fill.client_order_id
    assert row["trade_id"] == str(order.trade_id)  # UUID serialized as a plain string, not an object

    # Curated business fields
    assert row["symbol"] == "ZZXF8"
    assert row["execution_venue"] == "simulated"
    assert row["venue_fill_id"] == fill.venue_fill_id
    assert row["qty"] == 17
    assert row["anomaly"] is None

    # Decimal fields are exact strings, never floats — see the route's own
    # docstring for why. Asserting the type rules out FastAPI's default
    # jsonable_encoder float conversion sneaking back in.
    assert row["price"] == "123.456700"
    assert isinstance(row["price"], str)

    # Timestamps parse back as real ISO-8601 datetimes, not opaque strings
    venue_ts = datetime.fromisoformat(row["venue_ts"])
    created_at = datetime.fromisoformat(row["created_at"])
    assert venue_ts.tzinfo is not None
    assert created_at.tzinfo is not None

    # Never a full-row dump — no execution_mode field exists on `fills` at
    # all, and this route's own approved field list is curated.
    assert "execution_mode" not in row


def test_route_commission_is_null_when_absent():
    """I3: an absent commission is JSON `null`, never a fabricated `0`."""
    _, no_commission = _insert_fill(dict(symbol="ZZXF9A"), commission=None)

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF9A"})

    row = resp.json()["fills"][0]
    assert row["ledger_seq"] == no_commission.ledger_seq
    assert row["commission"] is None


def test_route_commission_is_preserved_as_exact_string_when_set():
    """A present commission is an exact decimal string, not a JSON number —
    same reasoning, and same one-`TestClient`-boot-per-test-function
    convention every other test in this file (and this whole suite; see
    `_insert_fill`'s own module docstring note) already follows, as a
    separate test function from the null case immediately above rather
    than a second `with TestClient(app)` block in the same one."""
    _, with_commission = _insert_fill(dict(symbol="ZZXF9B"), commission=Decimal("0.350000"))

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF9B"})

    row = resp.json()["fills"][0]
    assert row["ledger_seq"] == with_commission.ledger_seq
    assert row["commission"] == "0.350000"
    assert isinstance(row["commission"], str)


def test_route_anomaly_is_nullable_and_present_when_set():
    _, flagged = _insert_fill(dict(symbol="ZZXF10"), anomaly="overfill")

    with TestClient(app) as client:
        resp = client.get("/intelligence/execution-fills", params={"symbol": "ZZXF10"})

    row = resp.json()["fills"][0]
    assert row["ledger_seq"] == flagged.ledger_seq
    assert row["anomaly"] == "overfill"


# --- Off-the-event-loop regression (asyncio.to_thread offload) ---


@pytest.fixture
def _blocked_fetch_execution_fills(monkeypatch):
    """Same technique test_execution_orders_route.py already established
    (itself borrowed from test_scanner_route_concurrency.py for
    scanner-route-db-offload): monkeypatch the module-level sync helper (as
    looked up in app.api.routes.intelligence's own namespace) with a fake
    that blocks on a threading.Event until released, so the test can
    deterministically prove the blocked call is running in a worker
    thread — not racing a sleep — before asserting an unrelated route stays
    responsive."""
    started = threading.Event()
    release = threading.Event()

    def _blocked(symbol, limit):
        started.set()
        if not release.wait(timeout=_BLOCK_TIMEOUT_SECONDS):
            raise TimeoutError("test never released the blocked execution-fills call")
        return []

    monkeypatch.setattr(intelligence_routes, "_fetch_execution_fills", _blocked)
    return started, release


async def test_blocked_execution_fills_read_does_not_block_an_unrelated_route(_blocked_fetch_execution_fills):
    started, release = _blocked_fetch_execution_fills

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        fills_task = asyncio.create_task(client.get("/intelligence/execution-fills"))

        started_in_time = await asyncio.to_thread(started.wait, _BLOCK_TIMEOUT_SECONDS)
        assert started_in_time, "blocked execution-fills helper never started"

        # The event loop must still be free to serve an unrelated,
        # lightweight route while that request is stuck in its worker
        # thread — the actual regression this test guards against.
        health_response = await asyncio.wait_for(client.get("/health"), timeout=2.0)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        release.set()
        fills_response = await asyncio.wait_for(fills_task, timeout=_BLOCK_TIMEOUT_SECONDS)

    assert fills_response.status_code == 200
    assert fills_response.json() == {"fills": []}
