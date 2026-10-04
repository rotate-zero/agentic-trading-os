"""``OutcomeRecorder`` recovery through the *real* FastAPI lifespan, on a real PostgreSQL database.

``test_outcome_recorder_restart_recovery.py`` proves that a freshly constructed recorder recovers
work from durable rows, but it builds the recorder by hand: ``main.py`` is not involved, so the
startup *ordering* of the real application is unproven.  ``test_execution_startup_status_route.py``
already covers the recorder-startup-failure and the blocked-state *responses*; this module does not
repeat them.  It covers the remaining gap: the production lifespan itself, in order,

    rebuild Portfolio State -> reconcile_with_venue -> (clean?) -> start workers
                                                           -> OutcomeRecorder.start()
                                                              -> real _startup_scan
                                                              -> worker -> record_trade
                                                              -> trades.outcome_status = 'recorded'

recovers a closed simulated trade left behind in the ledger, using only durable rows.

Scenarios
---------
1. A consistent, eligible closed trade is seeded *before* the lifespan.  Entering the real lifespan
   must (a) finish reconciliation before the recorder starts and before its startup scan, and
   (b) record exactly one linked outcome with the existing honest missing-snapshot contract
   (NULL plus a reason, never fabricated).  No ``PositionClosed``/``OrderFilled`` is published, and
   neither the recorder's scan nor ``record_trade`` is called by the test to obtain the result.
   Leaving the lifespan and entering a *fresh* one (loop-bound singletons reset) must leave the
   recorded outcome byte-for-byte unchanged and must not create a second one.
2. Reconciliation reports a discrepancy while an eligible trade is pending (the established
   discrepancy-injection pattern: ``app.portfolio_state.reconciliation.reconcile_with_venue`` is
   replaced).  The lifespan fails closed: the recorder is never constructed or started, a closure
   event published afterwards reaches no recorder, and the trade stays unrecorded and pending.

Observation, not substitution.  In scenario 1 every observed method (``reconcile_with_venue``,
``OutcomeRecorder.start/stop/_pending_rows/record_trade``, ``EventBus.publish``) is wrapped by a
function that delegates to the original and returns its real result; the wrappers only append to a
trace.  Scenario 2 replaces reconciliation by design (an injected discrepancy report) and observes
the rest the same way.

Synchronization is bounded and observable, never a fixed sleep: the app runs on the TestClient
portal's own event-loop thread, and the test thread polls with ``_wait_for`` (a deadline, with the
last observed state reported on timeout) for the worker's real verdict.  Absence claims are only
made after a positive barrier: scenario 1's fresh lifespan publishes a duplicate closure whose
``skipped`` verdict proves the single FIFO worker has drained anything the startup scan could have
queued; scenario 2's recorder start is synchronous inside the lifespan, so once the client has
entered, "never started" is final, and a published closure is followed by a bus-idle barrier.

Controlled inputs.  ``MarketClock.is_regular_session`` is pinned to True (as the other lifespan
tests do); the recorder's sweep interval is parked at 3600 s so only the startup scan can act; its
snapshot lag is 1 s so a closure recovered after the fact is deterministically "too old to
snapshot" (every snapshot key NULL with reason ``recorder_unavailable``).  Finnhub/Polygon are
blanked by ``conftest.py``, so no external provider is contacted.

Isolation.  The recorder's startup query is database-wide, so an autouse guard refuses to run unless
``trades`` is empty and the Portfolio State cursor is untouched (use a disposable database migrated
to Alembic head).  Fixture order is load-bearing: ``trace`` depends on ``seeded``, so ``trace``'s
teardown, which asserts that every recorder worker has finished, runs *before* ``seeded`` deletes
any row.  Cleanup removes only the seeded ``trade_id``s' rows (children first) and the simulated
Portfolio State cursor those rows required.

Not covered, deliberately: a process crash, kill -9, or more than one process.  The two "processes"
in scenario 1 are two sequential in-process lifespans over the same database; ``SimulatedVenue`` and
every cached singleton are rebuilt, nothing else is carried over.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

import app.core.config as config_module
import app.portfolio_state.reconciliation as reconciliation_module
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus, get_event_bus
from app.main import app as fastapi_app
from app.models.execution_ledger import (
    Fill, Order, PortfolioStateCursor, Position, PositionFillReceipt, Trade,
)
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.portfolio_state.reconciliation import ReconciliationReport
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import PositionClosed
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from tests.test_main_execution_pipeline import _reset_singletons, _wait_for
from tests.test_outcome_recorder import _seed

_SNAPSHOT_KEYS = ("market_state_at_entry", "context_at_entry", "market_state_at_exit", "context_at_exit")
_VERDICTS = {"recorded", "blocked", "pending_retry", "skipped"}
# Captured at import, before any test wraps the class: the real discovery query, for pre/post checks.
_REAL_PENDING_ROWS = OutcomeRecorder._pending_rows


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
        cursor = session.scalar(select(func.coalesce(func.max(PortfolioStateCursor.last_applied_ledger_seq), 0)))
    if trades or cursor:
        pytest.fail(
            f"database {database!r} already holds {trades} trade row(s) / Portfolio State cursor {cursor}. These "
            "tests run the real lifespan, whose OutcomeRecorder startup query is database-wide and would record "
            "unrelated trades. Run them against a disposable database migrated to Alembic head with no trades.")


@pytest.fixture(autouse=True)
def _lifespan_env(monkeypatch, _reset_app_singletons):
    """Controlled inputs. Depends on conftest's reset so the settings cache is cleared *after* the env is set."""
    monkeypatch.setenv("OUTCOME_SWEEP_INTERVAL_SECONDS", "3600")  # only the startup scan may act
    monkeypatch.setenv("OUTCOME_SNAPSHOT_MAX_LAG_SECONDS", "1")  # a recovered closure is always "too old"
    monkeypatch.setattr(MarketClock, "is_regular_session", lambda self, ts=None: True)
    config_module.get_settings.cache_clear()
    yield
    config_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Seeding + exact cleanup (keyed on seeded trade ids only)
# ---------------------------------------------------------------------------

def _make_checkpoint_consistent(trade_id: uuid.UUID) -> None:
    """Turn ``_seed``'s ledger rows into what a real Portfolio State would have left behind.

    ``_seed`` writes fills, applied-fill receipts and a closed position, which is enough for the recorder's
    own checks.  The real lifespan also rebuilds Portfolio State through ``PostgresPositionLedger``, which
    refuses a ledger whose applied-fill cursor does not equal the last receipt, or whose receipt trading day
    is not the ET day of the fill.  So: write the cursor, and use the clock's own trading day.
    """
    clock = MarketClock()
    with SessionLocal.begin() as session:
        receipts = session.scalars(
            select(PositionFillReceipt).join(Position, PositionFillReceipt.position_id == Position.position_id)
            .where(Position.trade_id == trade_id).order_by(PositionFillReceipt.ledger_seq)).all()
        assert receipts, "seed produced no applied-fill receipts"
        for receipt in receipts:
            receipt.trading_day = clock.trading_day(session.get(Fill, receipt.ledger_seq).venue_ts)
        cursor = session.get(PortfolioStateCursor, "simulated")  # a prior lifespan may have left a zero cursor
        if cursor is None:
            session.add(PortfolioStateCursor(execution_mode="simulated", last_applied_ledger_seq=receipts[-1].ledger_seq))
        else:
            cursor.last_applied_ledger_seq = receipts[-1].ledger_seq


@pytest.fixture
def seeded():
    created: list[uuid.UUID] = []
    with SessionLocal() as session:  # the guard allows a zero cursor left by an earlier lifespan; restore that state
        cursor_existed = session.get(PortfolioStateCursor, "simulated") is not None

    def seed() -> uuid.UUID:
        trade_id, _ = _seed()
        created.append(trade_id)
        _make_checkpoint_consistent(trade_id)
        return trade_id

    yield seed

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
        if created and cursor_existed:  # the guard proved it was 0 before this test: put it back
            session.execute(text(
                "UPDATE portfolio_state_cursor SET last_applied_ledger_seq = 0 WHERE execution_mode = 'simulated'"))
        elif created:  # no cursor existed before this test, so the one it needed is test-owned
            session.execute(text("DELETE FROM portfolio_state_cursor WHERE execution_mode = 'simulated'"))


# ---------------------------------------------------------------------------
# Observation: wrappers that delegate to the real methods and only append to a trace
# ---------------------------------------------------------------------------

class Trace:
    """What the real lifespan did, per lifespan generation (``new_generation`` starts a clean slate)."""

    def __init__(self) -> None:
        self.recorders: list[tuple[OutcomeRecorder, object]] = []  # (recorder, its worker task), all generations
        self.reports: list[ReconciliationReport] = []
        self.new_generation()

    def new_generation(self) -> None:
        self.events: list[str] = []      # ordered milestones
        self.scans: list[list[uuid.UUID]] = []  # trade ids each REAL _pending_rows page returned
        self.verdicts: list[tuple[uuid.UUID, str]] = []  # each REAL record_trade verdict
        self.published: list[EventType] = []  # every event type that went through EventBus.publish

    def index(self, event: str) -> int:
        assert event in self.events, f"{event!r} never happened; saw {self.events}"
        return self.events.index(event)

    def verdict_for(self, trade_id: uuid.UUID, verdict: str | None = None) -> bool:
        return any(t == trade_id and (verdict is None or v == verdict) for t, v in list(self.verdicts))

    def observe_reconciliation(self, monkeypatch, *, inject=None) -> None:
        """Wrap ``reconcile_with_venue``. By default delegates to the real one; ``inject`` (a factory returning
        a report) is the established discrepancy-injection pattern and replaces it."""
        real = reconciliation_module.reconcile_with_venue

        async def observed(*args):
            self.events.append("reconcile:begin")
            report = inject() if inject is not None else await real(*args)
            self.reports.append(report)
            self.events.append("reconcile:end")
            return report

        monkeypatch.setattr(reconciliation_module, "reconcile_with_venue", observed)


@pytest.fixture
def trace(monkeypatch, seeded):  # depends on `seeded`: this fixture's teardown runs BEFORE any row is deleted
    t = Trace()
    real_start, real_stop = OutcomeRecorder.start, OutcomeRecorder.stop
    real_record, real_publish = OutcomeRecorder.record_trade, EventBus.publish

    async def start(self):
        t.events.append("recorder.start:begin")
        await real_start(self)
        t.events.append("recorder.start:end")
        t.recorders.append((self, self._worker))

    async def stop(self):
        t.events.append("recorder.stop:begin")
        await real_stop(self)
        t.events.append("recorder.stop:end")

    def pending_rows(self, after):
        rows = _REAL_PENDING_ROWS(self, after)  # the real query, unmodified; runs in the scan's worker thread
        t.scans.append([trade_id for _, trade_id in rows])
        t.events.append("recorder.scan")
        return rows

    async def record_trade(self, trade_id):
        result = await real_record(self, trade_id)  # the real method; only its verdict is observed
        t.verdicts.append((trade_id, result))
        t.events.append(f"recorder.verdict:{result}")
        return result

    async def publish(self, envelope):
        t.published.append(envelope.event_type)
        await real_publish(self, envelope)

    monkeypatch.setattr(OutcomeRecorder, "start", start)
    monkeypatch.setattr(OutcomeRecorder, "stop", stop)
    monkeypatch.setattr(OutcomeRecorder, "_pending_rows", pending_rows)
    monkeypatch.setattr(OutcomeRecorder, "record_trade", record_trade)
    monkeypatch.setattr(EventBus, "publish", publish)

    yield t

    # Shutdown must have completed before the seeded rows are deleted (fixture order above).
    for recorder, worker in t.recorders:
        assert worker is not None and worker.done() and not worker.cancelled() and worker.exception() is None
        assert recorder._worker is None and recorder._sweeper is None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _position_closed(trade_id: uuid.UUID) -> EventEnvelope:
    """A valid ``PositionClosed`` envelope built from the durable position (used only as a barrier / probe)."""
    with SessionLocal() as session:
        position = session.scalar(select(Position).where(Position.trade_id == trade_id))
        payload = PositionClosed(
            position_id=str(position.position_id), exit_price=105.4, realized_pnl=float(position.realized_pnl),
            r_multiple_achieved=None, closed_ts=position.closed_at, trade_id=str(trade_id),
            execution_mode="simulated", execution_venue="simulated",
        )
        symbol = position.symbol
    return EventEnvelope(event_type=EventType.POSITION_CLOSED, symbol=symbol, payload=payload.model_dump(mode="json"))


def _state(trade_id: uuid.UUID):
    """(trades.outcome_status, trades.outcome_id, every column of each strategy_outcomes row)."""
    columns = [attr.key for attr in inspect(StrategyOutcomeRecord).mapper.column_attrs]
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        rows = session.scalars(select(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.opportunity_id == trade_id)).all()
        return trade.outcome_status, trade.outcome_id, [{c: getattr(row, c) for c in columns} for row in rows]


def _ledger(trade_id: uuid.UUID) -> tuple:
    """The durable execution ledger for the trade, excluding the outcome link: it must not move during recovery."""
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        position = session.scalar(select(Position).where(Position.trade_id == trade_id))
        orders = session.scalar(select(func.count()).select_from(Order).where(Order.trade_id == trade_id))
        fills = session.scalar(select(func.count()).select_from(Fill).join(
            Order, Fill.client_order_id == Order.client_order_id).where(Order.trade_id == trade_id))
        receipts = session.scalar(select(func.count()).select_from(PositionFillReceipt).where(
            PositionFillReceipt.position_id == position.position_id))
        cursor = session.get(PortfolioStateCursor, "simulated").last_applied_ledger_seq
        return (trade.status, position.status, position.qty, position.avg_price, position.realized_pnl,
                position.closed_at, orders, fills, receipts, cursor)


def _pending_ids() -> list[uuid.UUID]:
    """What the recorder's real discovery query lists right now (does not enqueue anything)."""
    return [trade_id for _, trade_id in _REAL_PENDING_ROWS(OutcomeRecorder(EventBus(), SessionLocal), None)]


def _outcome_links() -> tuple[int, int]:
    """(strategy_outcomes rows, trades linked to an outcome) over the whole, disposable, database."""
    with SessionLocal() as session:
        return (session.scalar(select(func.count()).select_from(StrategyOutcomeRecord)),
                session.scalar(select(func.count()).select_from(Trade).where(Trade.outcome_id.is_not(None))))


def _assert_recorded_once(trade_id: uuid.UUID) -> dict:
    status, outcome_id, rows = _state(trade_id)
    assert status == "recorded"
    assert outcome_id is not None
    assert len(rows) == 1  # exactly one outcome, and it is the one the trade links to
    assert rows[0]["outcome_id"] == outcome_id
    assert _outcome_links() == (1, 1)
    return rows[0]


def _assert_honest_snapshot_loss(outcome: dict) -> None:
    """Existing contract: no engine snapshot is invented; every key is NULL with a non-empty reason code."""
    for key in _SNAPSHOT_KEYS:
        assert outcome[key] is None
    assert outcome["snapshot_missing_reasons"] == {key: "recorder_unavailable" for key in _SNAPSHOT_KEYS}


def _assert_clean_recorder_shutdown(t: Trace) -> None:
    """The recorder's stop() began and finished, after its last verdict, with its worker task ended."""
    assert t.index("recorder.stop:begin") < t.index("recorder.stop:end")
    assert max(i for i, e in enumerate(t.events) if e.startswith("recorder.verdict:")) < t.index("recorder.stop:begin")
    recorder, worker = t.recorders[-1]
    assert worker.done() and not worker.cancelled() and worker.exception() is None
    assert recorder._worker is None and recorder._sweeper is None
    assert fastapi_app.state.execution_startup_status is None  # the lifespan is over


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def test_real_lifespan_recovers_closed_trade_once_and_fresh_lifespan_changes_nothing(seeded, trace, monkeypatch):
    trade_id = seeded()
    trace.observe_reconciliation(monkeypatch)  # delegates to the real reconcile_with_venue

    # Before any lifespan: durable, eligible, unrecorded, and nothing in this process has seen it.
    assert _state(trade_id) == (None, None, [])
    assert _pending_ids() == [trade_id]
    assert _outcome_links() == (0, 0)
    ledger_before = _ledger(trade_id)

    # --- lifespan #1: recovery is driven only by main.py's own startup sequence ---------------------------
    with TestClient(fastapi_app) as client:
        assert client.get("/health/execution-startup").json() == {
            "status": "ready", "reason_code": None, "discrepancy_count": None}

        _wait_for(lambda: trace.verdict_for(trade_id),
                  "OutcomeRecorder verdict for the pre-seeded closed trade",
                  describe=lambda: (trace.events, _state(trade_id)[:2]))
        assert trace.verdicts == [(trade_id, "recorded")]

        # Startup order: reconciliation (real, clean) finished before the recorder started, and before its scan.
        assert len(trace.reports) == 1 and not trace.reports[0].has_discrepancy
        assert trace.index("reconcile:begin") < trace.index("reconcile:end") < trace.index("recorder.start:begin")
        assert trace.index("recorder.start:begin") < trace.index("recorder.scan") < trace.index("recorder.start:end")
        assert trace.index("recorder.scan") < trace.index("recorder.verdict:recorded")
        assert trace.scans == [[trade_id]]  # found by the real startup query, not handed to the recorder
        # Recovered from durable rows alone: nothing replayed a closure or a fill through the bus.
        assert EventType.POSITION_CLOSED not in trace.published
        assert EventType.ORDER_FILLED not in trace.published

        outcome = _assert_recorded_once(trade_id)
        _assert_honest_snapshot_loss(outcome)
        assert outcome["opportunity_id"] == trade_id
        assert outcome["is_backtest"] is False and outcome["backtest_run_id"] is None
        assert (outcome["execution_mode"], outcome["execution_venue"], outcome["origin"]) == (
            "simulated", "simulated", "auto")
        assert (outcome["entry_qty"], outcome["exit_qty"], outcome["exit_reason"]) == (10, 10, "target")

    _assert_clean_recorder_shutdown(trace)
    recorded = _state(trade_id)
    assert recorded[0] == "recorded"
    _assert_recorded_once(trade_id)
    assert _ledger(trade_id) == ledger_before  # recovery touched only the outcome link, never the ledger
    assert _pending_ids() == []

    # --- lifespan #2: a fresh "process" over the same durable rows ----------------------------------------
    _reset_singletons()  # TestClient builds a new loop; every cached, loop-bound singleton must be rebuilt
    trace.new_generation()
    with TestClient(fastapi_app) as client:
        assert client.get("/health/execution-startup").json() == {
            "status": "ready", "reason_code": None, "discrepancy_count": None}
        assert trace.index("reconcile:begin") < trace.index("reconcile:end") < trace.index("recorder.start:begin")
        assert trace.index("recorder.scan") < trace.index("recorder.start:end")
        assert trace.scans == [[]]  # the real startup query no longer lists the recorded trade
        assert trace.verdicts == []  # so nothing was even queued for it
        assert EventType.POSITION_CLOSED not in trace.published

        # Barrier. One worker, FIFO queue: when this duplicate closure's verdict arrives, anything the startup
        # scan could have queued has been processed, so "unchanged" below cannot pass by looking too early.
        client.portal.call(get_event_bus().publish, _position_closed(trade_id))
        _wait_for(lambda: trace.verdict_for(trade_id),
                  "OutcomeRecorder verdict for the duplicate closure in the fresh lifespan",
                  describe=lambda: (trace.events, trace.verdicts))
        assert trace.verdicts == [(trade_id, "skipped")]
        assert _state(trade_id) == recorded

    _assert_clean_recorder_shutdown(trace)
    assert len(trace.recorders) == 2  # one recorder per lifespan, each stopped before the next began

    assert _state(trade_id) == recorded  # same status, same outcome_id, every outcome column identical
    _assert_recorded_once(trade_id)  # still exactly one outcome, still the linked one
    assert _ledger(trade_id) == ledger_before


def test_blocked_reconciliation_never_starts_recorder_and_leaves_pending_trade_unrecorded(seeded, trace, monkeypatch):
    trade_id = seeded()
    trace.observe_reconciliation(
        monkeypatch, inject=lambda: ReconciliationReport(discrepancies=["test mismatch A", "test mismatch B"]))

    assert _state(trade_id) == (None, None, [])
    assert _pending_ids() == [trade_id]  # eligible: the recorder would record it if it ran
    ledger_before = _ledger(trade_id)

    with TestClient(fastapi_app) as client:
        # Fail closed (decision #179): the discrepancy blocks entry acceptance, and with it the recorder.
        assert client.get("/health/execution-startup").json() == {
            "status": "reconciliation_blocked", "reason_code": "reconciliation_discrepancy", "discrepancy_count": 2}
        assert fastapi_app.state.world_view_portfolio_reader is None
        # Recorder start is synchronous inside the lifespan, so with the client entered "never started" is final.
        assert trace.events == ["reconcile:begin", "reconcile:end"]
        assert trace.recorders == [] and trace.scans == [] and trace.verdicts == []

        # Barrier: a closure event is published and the bus drains it. No recorder is subscribed to receive it.
        bus = get_event_bus()
        client.portal.call(bus.publish, _position_closed(trade_id))
        _wait_for(lambda: bus._critical_queue._unfinished_tasks == 0 and bus._normal_queue._unfinished_tasks == 0,
                  "bus dispatched the probe closure to every subscriber", describe=bus.queue_depths)
        assert trace.verdicts == [] and trace.scans == []
        assert _state(trade_id) == (None, None, [])
        assert _pending_ids() == [trade_id]  # still pending, not blocked and not lost

    assert fastapi_app.state.execution_startup_status is None
    assert trace.events == ["reconcile:begin", "reconcile:end"]  # no recorder start, scan, verdict or stop, ever
    assert trace.recorders == [] and trace.verdicts == []
    assert _state(trade_id) == (None, None, [])
    assert _outcome_links() == (0, 0)
    assert _ledger(trade_id) == ledger_before
    assert _pending_ids() == [trade_id]
