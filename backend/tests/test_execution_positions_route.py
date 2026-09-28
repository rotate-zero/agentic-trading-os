"""
Route-level tests for the new `GET /intelligence/execution-positions` — a
read-only view of persisted simulated `positions` rows (`models/
execution_ledger.py`, decision #172; owner: Portfolio State). Separate file,
matching this suite's one-file-per-route-group precedent
(`test_execution_orders_route.py`, `test_execution_fills_route.py`).

Real PostgreSQL, hand-inserted rows only: no Execution Engine or Portfolio
State run is involved, because what is under test is the route's own read
shape — mode isolation, exact-symbol filtering, newest-first ordering with a
deterministic tie-break, limit bounds, the empty result, exact-decimal/null
serialization, and event-loop responsiveness.

**No app lifespan is booted here, on purpose.** Every request goes through
`httpx.ASGITransport(app=app)`, which does not run `main.py`'s `lifespan()`.
The sibling fills-route tests use `TestClient(app)`, whose lifespan runs
Portfolio State's startup restore/reconciliation against the shared database.
This route reads `positions` directly and its tests insert `positions` rows
with no fills behind them. Checked empirically before choosing this: booting
the lifespan with such a row logs a `PositionLedgerError: positions do not
match durable fill history` traceback from `portfolio_state/postgres.py`'s
`load_state()` and leaves the row unmodified (the route still returns it), so a
lifespan boot here would add error noise and exercise Portfolio State's startup
behavior rather than this route. Skipping it also sidesteps the
double-`TestClient`-boot shutdown failure the fills-route file documents. The
route itself needs no `app.state`.

Isolation: every inserted `positions` row belongs to a `trades` row tagged
with `_STRATEGY_NAME`, so cleanup is scoped and exact; symbols are synthetic
(`ZZXP*`) so a filtered query cannot pick up a row from another test file or
a real running system. No assertion assumes the table is otherwise empty.
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
from app.models.execution_ledger import Position, Trade

_STRATEGY_NAME = "__TEST_ROUTE_EXECUTION_POSITIONS__"
_BLOCK_TIMEOUT_SECONDS = 5
_BASE_TS = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
_EXPECTED_FIELDS = {
    "position_id",
    "trade_id",
    "symbol",
    "side",
    "qty",
    "status",
    "avg_price",
    "stop",
    "target",
    "opened_at",
    "closed_at",
    "realized_pnl",
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
    """`positions` FKs `trades`, so positions go first. Nothing else can
    reference these positions: no fills, receipts, orders or exit requests
    are ever created here, and no lifespan runs to create them."""
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM positions WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"),
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


def _insert_position(**overrides) -> Position:
    """Inserts one `trades` row (required by the FK) and one `positions` row,
    returning the refreshed ORM row. Mode/venue/symbol overrides steer both
    rows together, since the trade and its position must agree. The mode/venue
    pairing CHECK (`simulated` venue iff `backtest`/`simulated` mode) applies
    to both tables."""
    session = SessionLocal()
    try:
        mode = overrides.pop("execution_mode", "simulated")
        venue = overrides.pop("execution_venue", "simulated")
        symbol = overrides.pop("symbol", "ZZXP1")
        side = overrides.get("side", "BUY")
        trade = Trade(
            execution_mode=mode,
            execution_venue=venue,
            strategy_name=_STRATEGY_NAME,
            strategy_version="v1",
            direction=side,
            symbol=symbol,
            thesis={},
            decision="approved",
            limits_snapshot={},
            status="open",
        )
        session.add(trade)
        session.flush()

        fields = dict(
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
        fields.update(overrides)
        position = Position(**fields)
        session.add(position)
        session.commit()
        session.refresh(position)
        return position
    finally:
        session.close()


async def _get(**params) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/intelligence/execution-positions", params=params)


def _ids(response: httpx.Response) -> list[str]:
    return [row["position_id"] for row in response.json()["positions"]]


# --- Mode isolation ---


async def test_route_excludes_positions_of_every_other_execution_mode():
    """Hard-scoped to `execution_mode == 'simulated'`. `backtest` shares the
    `simulated` venue (passing the mode/venue CHECK); `paper` and `live` need
    a real-venue name. None may ever appear, whatever the caller passes."""
    simulated = _insert_position(symbol="ZZXP2")
    _insert_position(symbol="ZZXP2", execution_mode="backtest", execution_venue="simulated")
    _insert_position(symbol="ZZXP2", execution_mode="paper", execution_venue="ibkr")
    _insert_position(symbol="ZZXP2", execution_mode="live", execution_venue="ibkr")

    resp = await _get(symbol="ZZXP2")

    assert resp.status_code == 200
    assert _ids(resp) == [str(simulated.position_id)]


async def test_route_ignores_an_execution_mode_query_parameter():
    """Mode is not a parameter: an unknown `execution_mode` query value is
    ignored (FastAPI drops undeclared params) and cannot widen the scope."""
    simulated = _insert_position(symbol="ZZXP3")
    _insert_position(symbol="ZZXP3", execution_mode="backtest", execution_venue="simulated")

    resp = await _get(symbol="ZZXP3", execution_mode="backtest")

    assert resp.status_code == 200
    assert _ids(resp) == [str(simulated.position_id)]


# --- Symbol filter ---


async def test_route_filters_by_exact_symbol():
    wanted = _insert_position(symbol="ZZXP4")
    _insert_position(symbol="ZZXP5")  # different symbol — must be excluded

    resp = await _get(symbol="ZZXP4")

    assert resp.status_code == 200
    body = resp.json()["positions"]
    assert [row["position_id"] for row in body] == [str(wanted.position_id)]
    assert body[0]["symbol"] == "ZZXP4"


async def test_route_symbol_filter_is_exact_not_partial_or_case_insensitive():
    _insert_position(symbol="ZZXP6")

    lower = await _get(symbol="zzxp6")
    partial = await _get(symbol="ZZXP")

    assert lower.status_code == 200
    assert lower.json() == {"positions": []}
    assert partial.status_code == 200
    assert partial.json() == {"positions": []}


async def test_route_without_symbol_returns_positions_across_symbols():
    # Far-future timestamps make these the newest rows in the table, so
    # unrelated simulated rows can never push them out of the 100-row window.
    future = datetime(2099, 1, 5, 14, 30, tzinfo=timezone.utc)
    a = _insert_position(symbol="ZZXP7A", opened_at=future)
    b = _insert_position(symbol="ZZXP7B", opened_at=future + timedelta(minutes=1))

    resp = await _get(limit=100)

    assert resp.status_code == 200
    ids = _ids(resp)
    # The table may hold unrelated simulated rows; only relative order of ours matters.
    assert str(a.position_id) in ids and str(b.position_id) in ids
    assert ids.index(str(b.position_id)) < ids.index(str(a.position_id))


# --- Ordering and ties ---


async def test_route_orders_newest_first_by_opened_at_not_insertion_order():
    """Rows are inserted out of time order so insertion order (and any
    accidental primary-key ordering) cannot satisfy the assertion."""
    middle = _insert_position(symbol="ZZXP8", opened_at=_BASE_TS + timedelta(minutes=5))
    oldest = _insert_position(symbol="ZZXP8", opened_at=_BASE_TS)
    newest = _insert_position(symbol="ZZXP8", opened_at=_BASE_TS + timedelta(minutes=10))

    resp = await _get(symbol="ZZXP8")

    assert resp.status_code == 200
    assert _ids(resp) == [str(newest.position_id), str(middle.position_id), str(oldest.position_id)]


async def test_route_breaks_opened_at_ties_by_position_id_descending_and_is_stable():
    tied_at = _BASE_TS + timedelta(minutes=3)
    # Tied rows are inserted in strictly ASCENDING id order while the route
    # must return them DESCENDING, so a route that lost its tie-break (and
    # fell back to insertion/heap order) fails deterministically instead of
    # matching the expected order by chance, as random uuid4 ids sometimes would.
    tied_ids = sorted(uuid.uuid4() for _ in range(4))
    for tied_id in tied_ids:
        _insert_position(symbol="ZZXP9", opened_at=tied_at, position_id=tied_id)
    newer = _insert_position(symbol="ZZXP9", opened_at=tied_at + timedelta(minutes=1))
    older = _insert_position(symbol="ZZXP9", opened_at=tied_at - timedelta(minutes=1))

    expected = [str(newer.position_id), *(str(i) for i in reversed(tied_ids)), str(older.position_id)]

    first = await _get(symbol="ZZXP9")
    second = await _get(symbol="ZZXP9")

    assert first.status_code == 200
    assert _ids(first) == expected
    assert _ids(second) == expected  # repeated reads never reshuffle tied rows


async def test_route_limit_cuts_through_a_tie_deterministically():
    """A limit that lands inside a run of tied rows must keep the same rows
    every time — the tie-break decides which tied rows survive the cap."""
    tied_at = _BASE_TS + timedelta(minutes=7)
    tied_ids = sorted(uuid.uuid4() for _ in range(3))  # inserted ascending; must come back descending
    for tied_id in tied_ids:
        _insert_position(symbol="ZZXP10", opened_at=tied_at, position_id=tied_id)
    expected = [str(i) for i in reversed(tied_ids)][:2]

    first = await _get(symbol="ZZXP10", limit=2)
    second = await _get(symbol="ZZXP10", limit=2)

    assert _ids(first) == expected
    assert _ids(second) == expected


# --- Limit bounds ---


async def test_route_limit_caps_returned_rows_to_the_newest():
    _insert_position(symbol="ZZXP11", opened_at=_BASE_TS)
    middle = _insert_position(symbol="ZZXP11", opened_at=_BASE_TS + timedelta(minutes=1))
    newest = _insert_position(symbol="ZZXP11", opened_at=_BASE_TS + timedelta(minutes=2))

    resp = await _get(symbol="ZZXP11", limit=2)

    assert resp.status_code == 200
    assert _ids(resp) == [str(newest.position_id), str(middle.position_id)]


async def test_route_default_limit_is_fifty_and_keeps_the_newest_fifty():
    inserted = [
        _insert_position(symbol="ZZXP12", opened_at=_BASE_TS + timedelta(minutes=i)) for i in range(51)
    ]
    newest_first = [str(p.position_id) for p in reversed(inserted)]

    resp = await _get(symbol="ZZXP12")

    assert resp.status_code == 200
    ids = _ids(resp)
    assert len(ids) == 50
    assert ids == newest_first[:50]
    assert str(inserted[0].position_id) not in ids  # the oldest one is the row cut


@pytest.mark.parametrize("bad_limit", [0, -1, 101, "abc"])
async def test_route_rejects_limit_outside_bounds(bad_limit):
    resp = await _get(limit=bad_limit)
    assert resp.status_code == 422


async def test_route_accepts_limit_at_each_bound():
    _insert_position(symbol="ZZXP13")

    low = await _get(symbol="ZZXP13", limit=1)
    high = await _get(symbol="ZZXP13", limit=100)

    assert low.status_code == 200
    assert high.status_code == 200
    assert len(low.json()["positions"]) == 1
    assert len(high.json()["positions"]) == 1


# --- Empty result ---


async def test_route_returns_honest_empty_collection_for_unknown_symbol():
    resp = await _get(symbol="ZZXP_NEVER_INSERTED")

    assert resp.status_code == 200
    assert resp.json() == {"positions": []}


async def test_route_returns_empty_collection_when_only_other_modes_exist_for_the_symbol():
    """No simulated rows for the symbol, but a `backtest` one exists: the
    answer is the honest empty collection, not an error and not that row."""
    _insert_position(symbol="ZZXP14", execution_mode="backtest", execution_venue="simulated")

    resp = await _get(symbol="ZZXP14")

    assert resp.status_code == 200
    assert resp.json() == {"positions": []}


# --- Serialization ---


async def test_route_open_position_serializes_curated_fields_with_null_for_unset_values():
    position = _insert_position(
        symbol="ZZXP15",
        side="BUY",
        qty=17,
        avg_price=Decimal("123.456700"),
        stop=None,
        target=None,
        closed_at=None,
        realized_pnl=None,
        status="open",
    )

    resp = await _get(symbol="ZZXP15")

    assert resp.status_code == 200
    body = resp.json()["positions"]
    assert len(body) == 1
    row = body[0]

    # Exactly the curated field set — no execution_mode/venue/exit_attempt leakage.
    assert set(row) == _EXPECTED_FIELDS

    assert row["position_id"] == str(position.position_id)
    assert row["trade_id"] == str(position.trade_id)  # UUIDs are plain strings
    assert row["symbol"] == "ZZXP15"
    assert row["side"] == "BUY"
    assert row["qty"] == 17
    assert row["status"] == "open"

    assert row["avg_price"] == "123.456700"
    assert isinstance(row["avg_price"], str)

    # Unset nullable values are JSON null — never omitted, never "0"/0.
    assert row["stop"] is None
    assert row["target"] is None
    assert row["closed_at"] is None
    assert row["realized_pnl"] is None

    opened_at = datetime.fromisoformat(row["opened_at"])
    assert opened_at.tzinfo is not None
    assert opened_at == _BASE_TS


async def test_route_closed_position_serializes_exact_money_strings_and_close_time():
    closed_at = _BASE_TS + timedelta(hours=2)
    position = _insert_position(
        symbol="ZZXP16",
        side="SELL",
        qty=0,
        avg_price=Decimal("50.250000"),
        stop=Decimal("51.100000"),
        target=Decimal("48.000000"),
        closed_at=closed_at,
        realized_pnl=Decimal("-7.250000"),
        status="closed",
    )

    resp = await _get(symbol="ZZXP16")

    row = resp.json()["positions"][0]
    assert row["position_id"] == str(position.position_id)
    assert row["side"] == "SELL"
    assert row["qty"] == 0
    assert row["status"] == "closed"
    assert row["avg_price"] == "50.250000"
    assert row["stop"] == "51.100000"
    assert row["target"] == "48.000000"
    assert row["realized_pnl"] == "-7.250000"
    for name in ("avg_price", "stop", "target", "realized_pnl"):
        assert isinstance(row[name], str), name
    assert datetime.fromisoformat(row["closed_at"]) == closed_at


async def test_route_decimal_strings_are_exact_where_a_float_would_not_be():
    """`Decimal('12345678901.123456')` needs 17 significant digits; a float
    round-trip cannot be relied on to reproduce it. A zero P&L must also stay
    a string ('0.000000'), distinct from a null (nothing realized)."""
    _insert_position(
        symbol="ZZXP17A",
        avg_price=Decimal("12345678901.123456"),
        stop=Decimal("0.100000"),
        target=Decimal("0.300000"),
        realized_pnl=Decimal("0.000000"),
    )

    resp = await _get(symbol="ZZXP17A")

    row = resp.json()["positions"][0]
    assert row["avg_price"] == "12345678901.123456"
    assert row["stop"] == "0.100000"
    assert row["target"] == "0.300000"
    assert row["realized_pnl"] == "0.000000"


async def test_route_reports_partially_reduced_position_status_and_remaining_qty():
    _insert_position(symbol="ZZXP18", qty=4, status="closing", realized_pnl=Decimal("12.500000"))

    row = (await _get(symbol="ZZXP18")).json()["positions"][0]

    assert row["status"] == "closing"
    assert row["qty"] == 4
    assert row["closed_at"] is None
    assert row["realized_pnl"] == "12.500000"


# --- Read-only ---


async def test_route_is_read_only():
    """A GET leaves the row untouched, and the path accepts no write verbs."""
    position = _insert_position(symbol="ZZXP19", status="open", qty=10)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/intelligence/execution-positions", params={"symbol": "ZZXP19"})).status_code == 200
        for method in ("post", "put", "patch", "delete"):
            resp = await getattr(client, method)("/intelligence/execution-positions")
            assert resp.status_code == 405, method

    session = SessionLocal()
    try:
        stored = session.get(Position, position.position_id)
        assert stored is not None
        assert (stored.status, stored.qty, stored.exit_attempt) == ("open", 10, 0)
    finally:
        session.close()


# --- Off-the-event-loop regression (asyncio.to_thread offload) ---


@pytest.fixture
def _blocked_fetch_execution_positions(monkeypatch):
    """Same technique the sibling route tests use: monkeypatch the
    module-level sync helper (looked up in `app.api.routes.intelligence`'s
    namespace) with a fake that blocks on a `threading.Event` until released,
    so the test can prove deterministically — not by racing a sleep — that
    the blocked call runs in a worker thread before asserting an unrelated
    route stays responsive."""
    started = threading.Event()
    release = threading.Event()

    def _blocked(symbol, limit):
        started.set()
        if not release.wait(timeout=_BLOCK_TIMEOUT_SECONDS):
            raise TimeoutError("test never released the blocked execution-positions call")
        return []

    monkeypatch.setattr(intelligence_routes, "_fetch_execution_positions", _blocked)
    return started, release


async def test_blocked_execution_positions_read_does_not_block_an_unrelated_route(_blocked_fetch_execution_positions):
    started, release = _blocked_fetch_execution_positions

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        positions_task = asyncio.create_task(client.get("/intelligence/execution-positions"))

        started_in_time = await asyncio.to_thread(started.wait, _BLOCK_TIMEOUT_SECONDS)
        assert started_in_time, "blocked execution-positions helper never started"

        # The event loop must still be free to serve an unrelated,
        # lightweight route while that request is stuck in its worker thread.
        health_response = await asyncio.wait_for(client.get("/health"), timeout=2.0)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        release.set()
        positions_response = await asyncio.wait_for(positions_task, timeout=_BLOCK_TIMEOUT_SECONDS)

    assert positions_response.status_code == 200
    assert positions_response.json() == {"positions": []}
