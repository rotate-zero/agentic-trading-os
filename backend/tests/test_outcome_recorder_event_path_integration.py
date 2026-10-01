"""Event-driven ``OutcomeRecorder`` path, observed through ``GET /intelligence/execution-outcome-status``.

``test_execution_outcome_status_recorder_integration.py`` proves the route reflects the
real recorder, but it drives the recorder by calling ``record_trade()`` directly.  This
module proves the *wake-up path* on a real PostgreSQL database:

    running EventBus --PositionClosed--> started OutcomeRecorder --queue--> worker
        --record_trade()--> trades.outcome_status / strategy_outcomes
        --> real GET /intelligence/execution-outcome-status

Order of events (the order is the point of the test):

1. a real ``EventBus`` is started and a real ``OutcomeRecorder`` is started on it, while
   no test trade exists, so the recorder's startup scan cannot be what records it;
2. the closed eligible trade is seeded (``tests.test_outcome_recorder._seed``, reused);
3. a valid ``PositionClosed`` envelope is published on the bus; the bus's critical-lane
   consumer calls the recorder's subscription, which enqueues the trade, and the
   recorder's own worker records it;
4. the route reports ``recorded`` with the persisted ``strategy_outcomes`` link;
5. a duplicate ``PositionClosed`` is published; the worker sees the trade already
   linked, returns ``skipped``, and no second outcome exists.

Synchronization is bounded, never a fixed sleep: ``record_trade`` on the recorder
*instance* is wrapped so each worker result is put on an ``asyncio.Queue`` that the test
awaits with ``asyncio.wait_for(..., timeout=_TIMEOUT)``.  The wrapper calls the real
method and returns its real result; it only observes.

Isolation.  The recorder's startup scan is a real, database-wide query and would record
*any* unrelated qualifying trade a shared development database happens to hold.  To keep
the test from mutating rows it did not create, the instance's ``_pending_rows`` is wrapped
so the startup scan still runs (and is asserted to have run) but yields nothing; the
sweeper interval is set far above the test's lifetime.  Neither the scan nor the sweep is
under test here.  Cleanup deletes only the seeded ``trade_id``s' rows, children first.

Out of scope: the authorizer -> venue -> portfolio execution path; no app lifespan is
booted (``httpx.ASGITransport`` does not run it).
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.main import app
from app.models.execution_ledger import Position, Trade
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import PositionClosed
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from tests.test_outcome_recorder import _seed

_TIMEOUT = 10.0  # upper bound for any single wait; the happy path takes milliseconds
_COUNT_KEYS = ["pending", "pending_retry", "blocked", "recorded", "other"]


def _db_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="real PostgreSQL unavailable")


# ---------------------------------------------------------------------------
# Seeding + exact cleanup (keyed on seeded trade ids only)
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded():
    created: list[uuid.UUID] = []

    def seed(**kwargs) -> uuid.UUID:
        trade_id, _ = _seed(**kwargs)
        created.append(trade_id)
        return trade_id

    yield seed

    if created:
        params = {"ids": created}
        with SessionLocal.begin() as session:
            session.execute(text("UPDATE trades SET outcome_id = NULL WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM strategy_outcomes WHERE opportunity_id = ANY(:ids)"), params)
            session.execute(text(
                "DELETE FROM position_fill_receipts WHERE position_id IN "
                "(SELECT position_id FROM positions WHERE trade_id = ANY(:ids))"), params)
            session.execute(text(
                "DELETE FROM fills WHERE client_order_id IN "
                "(SELECT client_order_id FROM orders WHERE trade_id = ANY(:ids))"), params)
            session.execute(text("DELETE FROM orders WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM trade_reservations WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM positions WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM trades WHERE trade_id = ANY(:ids)"), params)


# ---------------------------------------------------------------------------
# A running bus + a started recorder, stopped reliably
# ---------------------------------------------------------------------------

class Harness:
    def __init__(self, bus: EventBus, recorder: OutcomeRecorder, results: asyncio.Queue, scans: list):
        self.bus, self.recorder, self.results, self.scans = bus, recorder, results, scans

    async def next_result(self):
        """The next ``(trade_id, record_trade result)`` from the recorder's worker, bounded."""
        return await asyncio.wait_for(self.results.get(), timeout=_TIMEOUT)


@pytest.fixture
async def harness(seeded):  # depends on `seeded` so its cleanup runs after the stop below
    bus = EventBus()
    results: asyncio.Queue = asyncio.Queue()
    scans: list = []
    await bus.start()
    recorder = OutcomeRecorder(bus, SessionLocal, sweep_interval_seconds=3600)

    real_pending = recorder._pending_rows
    real_record = recorder.record_trade

    def startup_scan_sees_nothing(after):
        scans.append(real_pending(after))  # the real query runs; its rows are not acted on
        return []

    async def observed_record_trade(trade_id):
        result = await real_record(trade_id)
        results.put_nowait((trade_id, result))
        return result

    recorder._pending_rows = startup_scan_sees_nothing
    recorder.record_trade = observed_record_trade  # the worker calls self.record_trade(...)
    try:
        await recorder.start()
        yield Harness(bus, recorder, results, scans)
    finally:
        try:
            await asyncio.wait_for(recorder.stop(), timeout=_TIMEOUT)
        finally:
            await bus.stop()


# ---------------------------------------------------------------------------
# Route + DB helpers
# ---------------------------------------------------------------------------

async def _read() -> dict:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/intelligence/execution-outcome-status", params={"limit": 100})
    assert resp.status_code == 200
    return resp.json()


def _delta(body: dict, baseline: dict[str, int]) -> dict[str, int]:
    return {key: body["counts"][key] - baseline[key] for key in _COUNT_KEYS}


def _expect(**nonzero: int) -> dict[str, int]:
    return {key: nonzero.get(key, 0) for key in _COUNT_KEYS}


def _row(body: dict, trade_id: uuid.UUID) -> dict:
    rows = [r for r in body["trades"] if r["trade_id"] == str(trade_id)]
    assert len(rows) == 1, f"trade {trade_id} not listed exactly once (limit=100)"
    return rows[0]


def _db_state(trade_id: uuid.UUID):
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        rows = session.scalars(select(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.opportunity_id == trade_id)).all()
        return trade.outcome_status, trade.outcome_id, rows


def _position_closed(trade_id: uuid.UUID) -> EventEnvelope:
    """A valid ``PositionClosed`` envelope built from the seeded durable position."""
    with SessionLocal() as session:
        position = session.scalar(select(Position).where(Position.trade_id == trade_id))
        payload = PositionClosed(
            position_id=str(position.position_id), exit_price=105.4, realized_pnl=float(position.realized_pnl),
            r_multiple_achieved=None, closed_ts=position.closed_at, trade_id=str(trade_id),
            execution_mode="simulated", execution_venue="simulated",
        )
        symbol = position.symbol
    return EventEnvelope(event_type=EventType.POSITION_CLOSED, symbol=symbol,
                         payload=payload.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

async def test_bus_delivered_position_closed_is_recorded_by_worker_and_duplicate_is_inert(seeded, harness):
    baseline = (await _read())["counts"]
    # The recorder is already started and its startup scan has already run: no test trade existed.
    assert len(harness.scans) == 1
    trade_id = seeded()
    assert trade_id not in {row[1] for row in harness.scans[0]}  # the scan could not have seen it

    # Closed + eligible, but nothing has told the recorder: still NULL / pending, worker idle.
    before = await _read()
    assert _delta(before, baseline) == _expect(pending=1)
    assert _row(before, trade_id)["outcome_status"] is None
    assert harness.results.empty()
    assert _db_state(trade_id)[:2] == (None, None)

    # The event under test.
    await harness.bus.publish(_position_closed(trade_id))
    assert await harness.next_result() == (trade_id, "recorded")

    after = await _read()
    assert _delta(after, baseline) == _expect(recorded=1)
    status, outcome_id, outcomes = _db_state(trade_id)
    assert (status, len(outcomes)) == ("recorded", 1)
    assert outcomes[0].outcome_id == outcome_id
    row = _row(after, trade_id)
    assert row["outcome_status"] == "recorded"
    assert row["outcome_id"] == str(outcome_id)

    # Duplicate close notification: delivered, picked up by the worker, but a no-op.
    await harness.bus.publish(_position_closed(trade_id))
    assert await harness.next_result() == (trade_id, "skipped")

    status, same_outcome_id, outcomes = _db_state(trade_id)
    assert (status, same_outcome_id, len(outcomes)) == ("recorded", outcome_id, 1)
    final = await _read()
    assert _delta(final, baseline) == _expect(recorded=1)
    assert _row(final, trade_id)["outcome_id"] == str(outcome_id)
    assert harness.results.empty()  # the worker did no further work for this trade
