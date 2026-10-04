"""``OutcomeRecorder`` recovery across a *recorder-object* restart, on a real PostgreSQL database.

Existing real-database coverage proves the recorder's startup scan, paginated scan, periodic
sweep, direct-call retry and event-driven write paths (``test_outcome_recorder.py``,
``test_outcome_recorder_event_path_integration.py``,
``test_execution_outcome_status_recorder_integration.py``).  Each of those exercises *one*
recorder object.  This module covers the remaining gap: a **freshly constructed**
``OutcomeRecorder`` must recover work left behind by a previous, cleanly stopped recorder,
using only durable rows.  Nothing in memory (queue, ``_queued`` set, scan cursor, entry/exit
snapshots) survives a recorder; the ledger rows are the only carrier.

    recorder A (start .. stop)            durable ledger rows            recorder B (new object)
    --------------------------            -------------------            -----------------------
                                          closed trade, no outcome  -->  start(): real _startup_scan
                                          (or status pending_retry)        -> real _pending_rows
                                                                           -> worker -> record_trade
                                                                           -> trades.outcome_status
                                                                              = 'recorded' + outcome

Scope: this is a restart of the recorder *object*.  It is not a process crash and it does not
boot the app lifespan or ``main.py``'s wiring; ``httpx``/lifespan are not involved at all.

What drives recovery.  Only the recorder's own ``start()`` -> ``_startup_scan`` -> ``_pending_rows``
-> queue -> worker -> ``record_trade`` chain.  The tests never call ``record_trade()``, ``scan()``
or ``_startup_scan()`` to produce a result, never publish a closure event for the trade being
recovered, and park the periodic sweep (``sweep_interval_seconds=3600``).  ``_pending_rows`` is
**not** replaced: its real query is the thing under test (it is wrapped only to observe the
rows it returned).

Synchronization is bounded and observable, never a fixed sleep: ``record_trade`` on each
recorder *instance* is wrapped so the worker's real verdict is put on an ``asyncio.Queue``
awaited with ``asyncio.wait_for(..., timeout=_TIMEOUT)``.  The wrapper calls the real method
and returns its real result.

"Already recorded stays unchanged" is asserted for both recovery scenarios by a further fresh
recorder over the recovered trade.  Its barrier: ``start()`` awaits the startup scan, then a
duplicate ``PositionClosed`` is published on that recorder's bus.  The queue is FIFO with one
worker, so when the duplicate's ``skipped`` verdict arrives, everything the startup scan could
have enqueued has already been processed; asserting the verdict, an empty result queue, and a
scan page that did not list the trade means checking too early cannot produce a false pass.

Lost snapshots keep the existing honest contract (NULL plus a reason code, never fabricated);
this module asserts that and does not re-test price / P&L formulas.

Isolation.  The recorder's startup query is database-wide, so it would record any unrelated
qualifying trade.  An autouse guard therefore refuses to run unless ``trades`` is empty (use a
disposable database migrated to Alembic head).  Cleanup deletes only the seeded ``trade_id``s'
rows, children first, after every recorder worker has been stopped.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.models.execution_ledger import Position, Trade
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import PositionClosed
from app.trading_intelligence import outcome_recorder as recorder_module
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from tests.test_outcome_recorder import _seed

_TIMEOUT = 10.0  # upper bound for any single wait; the happy path takes milliseconds
_SNAPSHOT_KEYS = ("market_state_at_entry", "context_at_entry", "market_state_at_exit", "context_at_exit")


def _db_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="real PostgreSQL unavailable")


# ---------------------------------------------------------------------------
# Guard: the real startup query is database-wide
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _database_holds_only_test_data():
    with SessionLocal() as session:
        database = session.scalar(text("SELECT current_database()"))
        trades = session.scalar(select(func.count()).select_from(Trade))
    if trades:
        pytest.fail(
            f"database {database!r} already holds {trades} trade row(s). These tests let the recorder's real, "
            "database-wide startup query run, which would record unrelated trades. Run them against a "
            "disposable database migrated to Alembic head that contains no trades.")


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
            # trades.outcome_id FKs strategy_outcomes: unlink, then delete children first.
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
# One recorder object (with its own bus) over the shared database
# ---------------------------------------------------------------------------

class Generation:
    """A recorder generation: a bus, a real ``OutcomeRecorder`` and observation hooks."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.recorder = OutcomeRecorder(self.bus, SessionLocal, sweep_interval_seconds=3600)
        self.results: asyncio.Queue = asyncio.Queue()
        self.scans: list[list[uuid.UUID]] = []  # trade ids returned by each REAL _pending_rows page
        self.worker: asyncio.Task | None = None
        self._bus_started = False
        self._recorder_started = False
        self._recorder_stopped = False

        self._real_pending = self.recorder._pending_rows
        real_record = self.recorder.record_trade

        def observed_pending_rows(after):
            rows = self._real_pending(after)  # the real query, unmodified
            self.scans.append([trade_id for _, trade_id in rows])
            return rows

        async def observed_record_trade(trade_id):
            result = await real_record(trade_id)  # the real method; only its verdict is observed
            self.results.put_nowait((trade_id, result))
            return result

        self.recorder._pending_rows = observed_pending_rows
        self.recorder.record_trade = observed_record_trade  # the worker calls self.record_trade(...)

    def pending_now(self) -> list[uuid.UUID]:
        """What the recorder's real discovery query lists right now (does not enqueue anything)."""
        return [trade_id for _, trade_id in self._real_pending(None)]

    async def start(self) -> None:
        await self.bus.start()
        self._bus_started = True
        self._recorder_started = True
        await self.recorder.start()  # returns once the real startup scan has enqueued its pages
        self.worker = self.recorder._worker
        assert self.worker is not None and not self.worker.done()

    async def stop(self) -> None:
        """Stop the recorder (drains its queue, ends its worker) and then its bus. Idempotent."""
        try:
            if self._recorder_started and not self._recorder_stopped:
                self._recorder_stopped = True
                await asyncio.wait_for(self.recorder.stop(), timeout=_TIMEOUT)
        finally:
            if self._bus_started:
                self._bus_started = False
                await self.bus.stop()

    def assert_worker_finished(self) -> None:
        """The restart boundary: the previous worker task has ended cleanly, not been abandoned."""
        assert self.worker is not None
        assert self.worker.done() and not self.worker.cancelled() and self.worker.exception() is None
        assert self.recorder._worker is None and self.recorder._sweeper is None

    async def next_result(self):
        """The next ``(trade_id, record_trade verdict)`` from this recorder's worker, bounded."""
        return await asyncio.wait_for(self.results.get(), timeout=_TIMEOUT)

    async def publish_closed(self, trade_id: uuid.UUID) -> None:
        await self.bus.publish(_position_closed(trade_id))


@pytest.fixture
async def make_generation(seeded):  # depends on `seeded`, so its teardown (stop everything) runs first
    created: list[Generation] = []

    def make() -> Generation:
        generation = Generation()
        created.append(generation)
        return generation

    try:
        yield make
    finally:
        for generation in reversed(created):  # workers are stopped before any row is deleted
            await generation.stop()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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


def _state(trade_id: uuid.UUID):
    """(trades.outcome_status, trades.outcome_id, every column of each strategy_outcomes row)."""
    columns = [attr.key for attr in inspect(StrategyOutcomeRecord).mapper.column_attrs]
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        rows = session.scalars(select(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.opportunity_id == trade_id)).all()
        return trade.outcome_status, trade.outcome_id, [{c: getattr(row, c) for c in columns} for row in rows]


def _assert_recorded_once(trade_id: uuid.UUID) -> dict:
    status, outcome_id, rows = _state(trade_id)
    assert status == "recorded"
    assert outcome_id is not None
    assert len(rows) == 1  # exactly one outcome, and it is the one the trade links to
    assert rows[0]["outcome_id"] == outcome_id
    return rows[0]


def _assert_honest_snapshot_loss(outcome: dict) -> None:
    """In-memory snapshots do not survive a recorder: NULL + reason, never fabricated state."""
    for key in _SNAPSHOT_KEYS:
        assert outcome[key] is None
    reasons = outcome["snapshot_missing_reasons"]
    assert set(reasons) == set(_SNAPSHOT_KEYS)
    assert all(isinstance(reason, str) and reason for reason in reasons.values())
    assert reasons["market_state_at_entry"] == reasons["context_at_entry"] == "recorder_unavailable"


async def _assert_fresh_recorder_leaves_recorded_outcome_unchanged(trade_id: uuid.UUID, make_generation) -> None:
    """Scenario 3: yet another recorder object over an already-recorded trade changes nothing."""
    before = _state(trade_id)
    assert before[0] == "recorded" and len(before[2]) == 1

    third = make_generation()
    assert third.pending_now() == []  # the real discovery query no longer lists the trade
    await third.start()
    assert third.scans == [[]]  # the real startup scan ran to completion and found nothing to enqueue

    # Barrier. One worker, FIFO queue: once the duplicate's verdict arrives, anything the startup
    # scan could have enqueued has been processed, so the checks below cannot pass merely by
    # looking before the worker acted.
    await third.publish_closed(trade_id)
    assert await third.next_result() == (trade_id, "skipped")
    assert third.results.empty()  # no earlier verdict from the startup scan, no further work

    assert _state(trade_id) == before  # same status, same outcome_id, every outcome column identical
    await third.stop()
    third.assert_worker_finished()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

async def test_closure_missed_while_no_recorder_ran_is_recovered_by_fresh_recorder_startup(seeded, make_generation):
    first = make_generation()
    await first.start()
    assert first.scans == [[]]  # empty database: the first recorder had nothing to do
    await first.stop()
    first.assert_worker_finished()  # recorder A is gone, with its worker ended
    assert first.results.empty()

    # The closure happens while no recorder runs: the durable rows exist, no event reaches anyone.
    trade_id = seeded()
    assert _state(trade_id)[:2] == (None, None)  # no verdict yet, no link
    second = make_generation()  # a NEW recorder object, not started yet
    assert second.pending_now() == [trade_id]  # pending before it starts, and the only pending trade
    assert second.results.empty()

    # Recovery is driven only by the new recorder's own start(): startup scan -> queue -> worker.
    await second.start()
    assert await second.next_result() == (trade_id, "recorded")
    assert second.scans == [[trade_id]]  # found by its real startup query, not handed to it

    outcome = _assert_recorded_once(trade_id)
    _assert_honest_snapshot_loss(outcome)
    await second.stop()
    second.assert_worker_finished()

    await _assert_fresh_recorder_leaves_recorded_outcome_unchanged(trade_id, make_generation)


async def test_durable_pending_retry_survives_recorder_restart_and_is_recovered(seeded, make_generation):
    real_writer = recorder_module.record_strategy_outcome_in_session
    injected: list[uuid.UUID] = []

    def fail_once_after_insert(session, outcome):
        if injected:  # a single fault only; anything later is the real writer
            return real_writer(session, outcome)
        injected.append(outcome.outcome_id)
        real_writer(session, outcome)  # the insert really happens inside the unit of work ...
        session.flush()
        raise SQLAlchemyError("injected transient fault after outcome insert, before the trade link")

    first = make_generation()
    await first.start()
    assert first.scans == [[]]
    trade_id = seeded()  # closed after A's startup scan: only the bus event can reach A

    recorder_module.record_strategy_outcome_in_session = fail_once_after_insert
    try:
        await first.publish_closed(trade_id)
        assert await first.next_result() == (trade_id, "pending_retry")
        await first.stop()
        first.assert_worker_finished()
    finally:
        recorder_module.record_strategy_outcome_in_session = real_writer  # always restored
    assert recorder_module.record_strategy_outcome_in_session is real_writer
    assert len(injected) == 1  # the fault really fired, once

    # Durable result of the failure: retry marker only; the inserted outcome rolled back with the unit.
    assert _state(trade_id) == ("pending_retry", None, [])

    # Recorder A is gone; nothing replays the event and nothing calls the recorder directly.
    second = make_generation()
    assert second.pending_now() == [trade_id]  # pending_retry stays discoverable by the real query
    assert second.results.empty()
    await second.start()
    assert await second.next_result() == (trade_id, "recorded")
    assert second.scans == [[trade_id]]
    assert len(injected) == 1  # recovery used the real writer, not another injected fault

    outcome = _assert_recorded_once(trade_id)
    assert outcome["outcome_id"] != injected[0]  # the rolled-back insert left no trace; this is a new row
    _assert_honest_snapshot_loss(outcome)
    await second.stop()
    second.assert_worker_finished()

    await _assert_fresh_recorder_leaves_recorded_outcome_unchanged(trade_id, make_generation)
