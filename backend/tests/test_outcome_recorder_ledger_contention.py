"""``OutcomeRecorder`` atomicity and idempotence under *real* database lock contention.

``test_known_fees_and_concurrent_wakeups`` runs two recorders under ``asyncio.gather`` but never
forces or observes a database wait: the two writers can simply run one after the other.  This
module makes two independent database transactions genuinely contend for the *same* closed trade
and proves the contention from PostgreSQL's own lock tables, not from elapsed time.

    recorder A (engine A)                         PostgreSQL                     recorder B (engine B)
    ---------------------                         ----------                     ---------------------
    ledger_transaction: BEGIN
      LOCK TABLE trades, orders,   -------->  A holds ShareRowExclusive
           trade_reservations                 on all three tables
      SELECT trade FOR UPDATE
      _build()  (real)
      writer stages + flushes INSERT
      [test hook: HOLD here]                                        <------  record_trade(): LOCK TABLE trades ...
                                              B waits (pg_locks: relation
                                              'trades', granted = false,
                                              pg_blocking_pids = [A])
      [test releases A]
      scenario 1: COMMIT           -------->  A's locks released, B granted
      scenario 2: raise -> ROLLBACK                                         SELECT FOR UPDATE, sees committed/clean row
                                                                            scenario 1: outcome_id set -> "skipped"
                                                                            scenario 2: no outcome -> real _build + write

What is observed (and what is not).  The production transaction takes its table locks first
(``LOCK TABLE trades, orders, trade_reservations IN SHARE ROW EXCLUSIVE MODE``) and only then
``SELECT ... FOR UPDATE`` on the trade row, so a competitor queues at the **table** lock and never
reaches the row lock while another writer holds the tables.  The tests therefore assert a
blocked, ungranted ``ShareRowExclusiveLock`` request on a ledger *relation* whose
``pg_blocking_pids`` contains recorder A's backend, and they do not require a row-lock wait.  The
lock actually observed is stored on the test (``record_property``) and printed with ``-s``.

Scenario 1 - successful writer, then a waiting competitor.  A is held inside its real transaction
after the production locks, ``_build`` and the staged outcome INSERT.  B is started for the same
trade and is shown blocked on A.  After A commits: A ``recorded``, B ``skipped``, one linked
outcome row, A built and wrote once, B never built or wrote.

Scenario 2 - first writer rolls back.  Same hold, but a narrow test-only injection raises
``SQLAlchemyError`` after the outcome INSERT is staged and flushed and before commit.  A's
transaction rolls back (its outcome never becomes visible), B is granted the locks and records
the trade with its own real build and write, and A's follow-up ``_mark_retry`` queues behind B
and finds the trade already linked, so it cannot downgrade ``recorded`` to ``pending_retry``.  A
later attempt is inert.

What is exercised for real: ``ledger_transaction`` (isolation, ``SET LOCAL``, ``LOCK TABLE``),
``SELECT ... FOR UPDATE``, ``OutcomeRecorder._build``, the real outcome writer and the
``trades.outcome_id`` link.  The only test-only code is (a) a wrapper that calls the real
writer and then holds/raises, (b) counters around ``_build`` / ``_mark_retry`` that call the real
methods, and (c) the snapshot capture stub (snapshots are not under test).

Scope: two independent SQLAlchemy engines (distinct pools, distinct PostgreSQL backends) in one
test process.  This is **not** multi-process coverage and **not** crash recovery.

Synchronization is bounded: every wait has a timeout, PostgreSQL ``lock_timeout`` /
``statement_timeout`` / ``idle_in_transaction_session_timeout`` are set on the contenders'
connections, polling for lock evidence is deadline-bounded (the poll interval is not evidence),
and the release barrier is set and both tasks are joined in ``finally``.

Isolation.  Run against a disposable database migrated to Alembic head.  The tests look at
their own seeded ``trade_id`` only and cleanup deletes only those rows, children first, after
both contender engines are disposed (open transactions would otherwise block the deletes).
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.models.execution_ledger import Trade
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.trading_intelligence import outcome_recorder as module
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots
from tests.test_outcome_recorder import _seed

_TIMEOUT = 15.0  # upper bound for any single wait; the happy path takes milliseconds
_HOLD_TIMEOUT = 20.0  # A never holds its transaction longer than this
_LEDGER_TABLES = {"trades", "orders", "trade_reservations"}
_SHARE_ROW_EXCLUSIVE = "ShareRowExclusiveLock"
_PG_OPTIONS = ("-c lock_timeout=20000 -c statement_timeout=30000 "
               "-c idle_in_transaction_session_timeout=60000")


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
            session.execute(text("SET LOCAL lock_timeout = '10s'"))  # fail loudly, never hang
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
# Two independent contenders: own engine (own pool / backends), own recorder
# ---------------------------------------------------------------------------

@dataclass
class Contender:
    name: str
    engine: object
    recorder: OutcomeRecorder
    pids: set = field(default_factory=set)  # every backend pid this engine ever checked out
    build_calls: int = 0
    write_calls: int = 0
    retry_calls: int = 0
    states_after_retry: list = field(default_factory=list)


def _make_contender(name: str) -> Contender:
    engine = create_engine(get_settings().database_url, future=True, pool_size=2, max_overflow=0,
                           connect_args={"options": _PG_OPTIONS})
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    contender = Contender(name, engine, OutcomeRecorder(EventBus(), sessions, snapshot_max_lag_seconds=3600))

    @event.listens_for(engine, "checkout")
    def _remember_backend(dbapi_connection, _record, _proxy):
        contender.pids.add(dbapi_connection.info.backend_pid)

    real_build = contender.recorder._build
    real_retry = contender.recorder._mark_retry

    def counted_build(*args, **kwargs):  # the real _build; only the call is counted
        contender.build_calls += 1
        return real_build(*args, **kwargs)

    def counted_retry(trade_id):  # the real _mark_retry; its outcome is read back afterwards
        contender.retry_calls += 1
        real_retry(trade_id)
        contender.states_after_retry.append(_trade_state(trade_id))

    contender.recorder._build = counted_build
    contender.recorder._mark_retry = counted_retry
    return contender


@dataclass
class Gate:
    """Holds recorder A inside its real transaction; optionally makes it fail before commit."""
    fail_before_commit: bool
    staged: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    staged_outcome_id: object = None
    a_pid: int | None = None


@pytest.fixture
def contenders(monkeypatch, seeded):
    """Depends on ``seeded`` so engines are disposed *before* the cleanup deletes run."""
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots",
                        lambda symbol: StrategyOutcomeSnapshots(None, None))
    a, b = _make_contender("A"), _make_contender("B")
    try:
        yield a, b
    finally:
        a.engine.dispose()
        b.engine.dispose()


def _install_writer_hook(monkeypatch, gate: Gate, a: Contender, b: Contender) -> None:
    """Wrap the real outcome writer.  A stages + flushes, signals, holds, then commits or fails."""
    real_writer = module.record_strategy_outcome_in_session

    def hooked_writer(session, outcome):
        bind = session.get_bind()
        who = a if bind is a.engine else b if bind is b.engine else None
        real_writer(session, outcome)  # the real writer stages the row
        if who is not None:
            who.write_calls += 1
        if who is not a:
            return
        session.flush()  # the INSERT is now pending inside A's open transaction
        gate.a_pid = session.connection().connection.dbapi_connection.info.backend_pid
        gate.staged_outcome_id = outcome.outcome_id
        gate.staged.set()
        if not gate.release.wait(_HOLD_TIMEOUT):
            raise AssertionError("test never released recorder A")
        if gate.fail_before_commit:
            raise SQLAlchemyError("injected failure after staged outcome write, before commit")

    monkeypatch.setattr(module, "record_strategy_outcome_in_session", hooked_writer)


# ---------------------------------------------------------------------------
# Observation helpers
# ---------------------------------------------------------------------------

_LOCKS_SQL = text("""
SELECT l.pid, c.relname, l.mode, l.granted, l.locktype,
       pg_blocking_pids(l.pid) AS blockers, a.wait_event_type, a.wait_event
FROM pg_locks l
JOIN pg_class c ON c.oid = l.relation
JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'relation'
  AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND c.relname = ANY(:tables)
""")


def _observe_locks() -> list[dict]:
    with SessionLocal() as session:
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        rows = session.execute(_LOCKS_SQL, {"tables": sorted(_LEDGER_TABLES)}).mappings().all()
        return [dict(row) for row in rows]


def _trade_state(trade_id):
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        return trade.outcome_status, trade.outcome_id


def _outcome_rows(trade_id):
    with SessionLocal() as session:
        return session.scalars(select(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.opportunity_id == trade_id)).all()


async def _wait_set(event_: threading.Event, what: str) -> None:
    assert await asyncio.to_thread(event_.wait, _TIMEOUT), f"timed out waiting for {what}"


async def _await_a_holds_locks(gate: Gate, a: Contender) -> None:
    await _wait_set(gate.staged, "recorder A to stage its outcome inside the transaction")
    assert gate.a_pid in a.pids
    rows = await asyncio.to_thread(_observe_locks)
    held = {r["relname"] for r in rows
            if r["pid"] == gate.a_pid and r["granted"] and r["mode"] == _SHARE_ROW_EXCLUSIVE}
    assert held == _LEDGER_TABLES, f"A should hold ShareRowExclusive on the ledger tables, holds {held}"


async def _await_b_blocked_on_a(task_b: asyncio.Task, gate: Gate, a: Contender, b: Contender) -> dict:
    """Return the ungranted lock request proving B waits on A.  Deadline-bounded poll; never a sleep-as-proof."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _TIMEOUT
    while True:
        assert not task_b.done(), f"B finished instead of blocking behind A: {task_b.result()!r}"
        rows = await asyncio.to_thread(_observe_locks)
        waiting = [r for r in rows
                   if not r["granted"] and r["pid"] in b.pids and gate.a_pid in r["blockers"]]
        if waiting:
            request = waiting[0]
            assert request["pid"] not in a.pids and request["pid"] != gate.a_pid
            assert request["locktype"] == "relation"
            assert request["relname"] in _LEDGER_TABLES
            assert request["mode"] == _SHARE_ROW_EXCLUSIVE
            assert request["wait_event_type"] == "Lock"
            return request
        assert loop.time() < deadline, f"B never showed up as blocked by A in pg_locks; saw {rows}"
        await asyncio.sleep(0.02)  # poll interval only; the evidence is the pg_locks row above


async def _settle(gate: Gate, tasks: list[asyncio.Task]) -> None:
    """finally-path: release the barrier and join everything, bounded."""
    gate.release.set()
    pending = [t for t in tasks if not t.done()]
    if pending:
        _, still = await asyncio.wait(pending, timeout=_TIMEOUT)
        for task in still:
            task.cancel()


# ---------------------------------------------------------------------------
# Scenario 1: successful writer, then a waiting competitor
# ---------------------------------------------------------------------------

async def test_waiting_competitor_skips_after_first_writer_commits(
        monkeypatch, seeded, contenders, record_property):
    trade_id = seeded()
    a, b = contenders
    gate = Gate(fail_before_commit=False)
    _install_writer_hook(monkeypatch, gate, a, b)
    task_a = task_b = None
    try:
        task_a = asyncio.create_task(a.recorder.record_trade(trade_id))
        await _await_a_holds_locks(gate, a)

        task_b = asyncio.create_task(b.recorder.record_trade(trade_id))
        request = await _await_b_blocked_on_a(task_b, gate, a, b)
        record_property("observed_blocked_lock", f"{request['locktype']}:{request['relname']}:{request['mode']}")
        print(f"\nobserved lock wait: B (pid {request['pid']}) waits for {request['mode']} on relation "
              f"{request['relname']!r}, blocked by A (pid {gate.a_pid}); wait_event="
              f"{request['wait_event_type']}/{request['wait_event']}")

        # While A holds the transaction open: B has not built or written and nothing is committed.
        assert not task_b.done() and not task_a.done()
        assert (b.build_calls, b.write_calls) == (0, 0) and (a.build_calls, a.write_calls) == (1, 1)
        assert _trade_state(trade_id) == (None, None)
        assert _outcome_rows(trade_id) == []

        gate.release.set()
        result_a, result_b = await asyncio.wait_for(asyncio.gather(task_a, task_b), _TIMEOUT)
    finally:
        await _settle(gate, [t for t in (task_a, task_b) if t is not None])

    assert (result_a, result_b) == ("recorded", "skipped")
    rows = _outcome_rows(trade_id)
    status, linked = _trade_state(trade_id)
    assert len(rows) == 1 and status == "recorded" and linked == rows[0].outcome_id
    assert rows[0].outcome_id == gate.staged_outcome_id  # the one outcome is A's staged write
    assert (a.build_calls, a.write_calls) == (1, 1)
    assert (b.build_calls, b.write_calls) == (0, 0)  # no second outcome build or write
    assert (a.retry_calls, b.retry_calls) == (0, 0)


# ---------------------------------------------------------------------------
# Scenario 2: first writer rolls back after staging its outcome
# ---------------------------------------------------------------------------

async def test_waiting_competitor_records_after_first_writer_rolls_back(
        monkeypatch, seeded, contenders, record_property):
    trade_id = seeded()
    a, b = contenders
    gate = Gate(fail_before_commit=True)
    _install_writer_hook(monkeypatch, gate, a, b)
    task_a = task_b = None
    try:
        task_a = asyncio.create_task(a.recorder.record_trade(trade_id))
        await _await_a_holds_locks(gate, a)

        task_b = asyncio.create_task(b.recorder.record_trade(trade_id))
        request = await _await_b_blocked_on_a(task_b, gate, a, b)
        record_property("observed_blocked_lock", f"{request['locktype']}:{request['relname']}:{request['mode']}")
        print(f"\nobserved lock wait: B (pid {request['pid']}) waits for {request['mode']} on relation "
              f"{request['relname']!r}, blocked by A (pid {gate.a_pid}); wait_event="
              f"{request['wait_event_type']}/{request['wait_event']}")

        assert not task_b.done() and not task_a.done()
        assert (b.build_calls, b.write_calls) == (0, 0)
        assert _trade_state(trade_id) == (None, None)

        gate.release.set()  # A now raises SQLAlchemyError before commit -> rollback
        result_a, result_b = await asyncio.wait_for(asyncio.gather(task_a, task_b), _TIMEOUT)
    finally:
        await _settle(gate, [t for t in (task_a, task_b) if t is not None])

    # A failed transiently (record_trade reports pending_retry); B finished the job.
    assert (result_a, result_b) == ("pending_retry", "recorded")
    rows = _outcome_rows(trade_id)
    status, linked = _trade_state(trade_id)
    assert len(rows) == 1 and status == "recorded" and linked == rows[0].outcome_id

    # A's staged outcome was rolled back: it is not the committed row and exists nowhere.
    assert gate.staged_outcome_id is not None and rows[0].outcome_id != gate.staged_outcome_id
    with SessionLocal() as session:
        assert session.get(StrategyOutcomeRecord, gate.staged_outcome_id) is None
        assert session.scalar(select(func.count()).select_from(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.opportunity_id == trade_id)) == 1  # no orphan outcome

    # A really ran the retry marker, but only after B had committed, so it could not downgrade.
    assert a.retry_calls == 1 and b.retry_calls == 0
    assert a.states_after_retry == [("recorded", rows[0].outcome_id)]
    assert (a.build_calls, a.write_calls) == (1, 1)
    assert (b.build_calls, b.write_calls) == (1, 1)  # B did its own real build and write

    # Another attempt (from either recorder) is inert.
    assert await asyncio.wait_for(a.recorder.record_trade(trade_id), _TIMEOUT) == "skipped"
    assert await asyncio.wait_for(b.recorder.record_trade(trade_id), _TIMEOUT) == "skipped"
    assert (a.build_calls, a.write_calls, b.build_calls, b.write_calls) == (1, 1, 1, 1)
    assert a.retry_calls == 1
    assert _trade_state(trade_id) == ("recorded", rows[0].outcome_id)
    assert [r.outcome_id for r in _outcome_rows(trade_id)] == [rows[0].outcome_id]
