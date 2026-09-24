"""Real PostgreSQL adapter, transaction, concurrency, and worker integration checks.

Run against an isolated migrated database. No skip-on-unavailable fallback:
this suite must exercise PostgreSQL to claim adapter coverage.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from threading import Barrier
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.models.execution_ledger import Fill, Order, PortfolioStateCursor, Position, PositionFillReceipt, Trade, TradeReservation
from app.portfolio_state.accounting import apply_fill
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.ports import FillApplication, PositionLedgerError, RealizedFill
from app.portfolio_state.postgres import PostgresPositionLedger
from app.schemas.events.envelope import EventType

TS = datetime(2026, 1, 5, 15, tzinfo=timezone.utc)
NAME = "TEST_POSITION_LEDGER_ADAPTER"


def clean():
    with SessionLocal.begin() as s:
        ids = select(Trade.trade_id).where(Trade.strategy_name == NAME)
        orders = select(Order.client_order_id).where(Order.trade_id.in_(ids))
        fills = select(Fill.ledger_seq).where(Fill.client_order_id.in_(orders))
        s.query(PositionFillReceipt).filter(PositionFillReceipt.ledger_seq.in_(fills)).delete(synchronize_session=False)
        s.query(Fill).filter(Fill.client_order_id.in_(orders)).delete(synchronize_session=False)
        s.query(Position).filter(Position.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Order).filter(Order.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(TradeReservation).filter(TradeReservation.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Trade).filter(Trade.strategy_name == NAME).delete(synchronize_session=False)
        s.query(PortfolioStateCursor).delete()


@pytest.fixture(autouse=True)
def database():
    clean()
    yield
    clean()


@pytest.fixture
def ledger():
    return PostgresPositionLedger(SessionLocal)


def order(*, mode="simulated", side="BUY", qty=10, trade_id=None, effect="open", status="submitted"):
    with SessionLocal.begin() as s:
        if trade_id is None:
            trade = Trade(execution_mode=mode, execution_venue="simulated" if mode in {"simulated", "backtest"} else "ibkr",
                strategy_name=NAME, strategy_version="1", direction=side, symbol="AAPL",
                thesis={"final_stop": 90, "final_target": 120}, decision="approved", status="open")
            s.add(trade)
            s.flush()
        else:
            trade = s.get(Trade, trade_id)
        row = Order(client_order_id=str(uuid4()), trade_id=trade.trade_id, execution_mode=trade.execution_mode,
            execution_venue=trade.execution_venue, symbol=trade.symbol, side=side, qty=qty,
            position_effect=effect, status=status)
        s.add(row)
        s.flush()
        return row.client_order_id, trade.trade_id


def insert_fill(s, order_id, *, qty=10, price=100, ts=TS, fee=None, anomaly=None):
    o = s.scalar(select(Order).where(Order.client_order_id == order_id))
    f = Fill(client_order_id=order_id, execution_venue=o.execution_venue, venue_fill_id=str(uuid4()),
        qty=qty, price=price, venue_ts=ts, commission=fee, anomaly=anomaly)
    s.add(f)
    s.flush()
    return f.ledger_seq


def source(order_id, **kw):
    with SessionLocal.begin() as s:
        return insert_fill(s, order_id, **kw)


def application(ledger, mode="simulated", *, fill=None):
    state = ledger.load_state(mode)
    fill = fill or ledger.pending_fills(mode, state.cursor)[0]
    prior = next((p for p in state.positions if p.symbol == fill.symbol), None)
    result = apply_fill(prior, fill, new_position_id=uuid4())
    p = result.position
    realized = RealizedFill(mode, fill.execution_venue, fill.venue_fill_id, fill.ledger_seq,
        p.position_id, fill.symbol, MarketClock().trading_day(fill.venue_ts), fill.venue_ts,
        result.realized_delta, fill.commission)
    return FillApplication(fill, p, realized, state.cursor)


@pytest.mark.parametrize("mode", ["simulated", "backtest", "paper", "live"])
def test_restore_modes_partial_reservations_and_terminal_status(ledger, mode):
    assert ledger.load_state(mode).positions == ()
    oid, _ = order(mode=mode)
    assert ledger.load_state(mode).orders[0].remaining_qty == 10
    assert ledger.get_order("missing") is None
    source(oid, qty=4, fee=D("0.25"))
    app = application(ledger, mode)
    result = ledger.commit_fill(app)
    assert result.applied
    restarted = PostgresPositionLedger(SessionLocal).load_state(mode)
    assert restarted.positions == (app.position,)
    assert restarted.orders[0].remaining_qty == 6
    assert ledger.get_order(oid).filled_qty == 4
    assert restarted.daily[0].fees == D("0.25")
    assert ledger.load_state("paper" if mode != "paper" else "live").positions == ()
    with SessionLocal.begin() as s:
        o = s.scalar(select(Order).where(Order.client_order_id == oid))
        assert o.status == "submitted"  # execution still owns status
        o.status = "cancelled"
    assert ledger.load_state(mode).orders == ()
    assert ledger.get_order(oid).status == "cancelled"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_multimonth_daily_profit_loss_fees_and_reopening_identity(ledger, side):
    oid, tid = order(side=side, qty=3)
    source(oid, qty=1, price=100, fee=1)
    first = application(ledger)
    ledger.commit_fill(first)
    source(oid, qty=2, price=101, fee=None)
    added = ledger.commit_fill(application(ledger)).state.positions[0]
    assert added.avg_price == D(302) / 3  # not the six-place projection
    exit_id, _ = order(trade_id=tid, side="SELL" if side == "BUY" else "BUY", effect="close", qty=3)
    source(exit_id, qty=1, price=110, ts=TS + timedelta(days=90), fee=D("-0.1"))
    partial = ledger.commit_fill(application(ledger)).state.positions[0]
    assert partial.position_id == first.position.position_id
    source(exit_id, qty=2, price=90, ts=TS + timedelta(days=240), fee=2)
    last = application(ledger)
    ledger.commit_fill(last)
    restored = PostgresPositionLedger(SessionLocal).load_state("simulated")
    assert restored.positions == ()
    assert len(restored.daily) == 3
    assert sum(d.profit for d in restored.daily) == last.position.realized_profit
    assert sum(d.loss for d in restored.daily) == last.position.realized_loss
    assert restored.daily[0].fees is None
    assert restored.daily[1].fees == D("-0.1")
    # Explicit receipt FK disambiguates even an identical opening timestamp.
    new_order, _ = order(trade_id=tid, side=side)
    source(new_order)
    reopened = ledger.commit_fill(application(ledger)).state.positions[0]
    assert reopened.position_id != first.position.position_id


def test_et_day_uses_fill_time(ledger):
    oid, _ = order()
    source(oid, ts=datetime(2026, 9, 23, 2, tzinfo=timezone.utc))
    state = ledger.commit_fill(application(ledger)).state
    assert str(state.daily[0].trading_day) == "2026-09-22"


def test_exact_duplicate_and_key_conflict_after_restart(ledger):
    oid, _ = order()
    source(oid)
    app = application(ledger)
    ledger.commit_fill(app)
    other = PostgresPositionLedger(SessionLocal)
    assert not other.commit_fill(app).applied
    with pytest.raises(PositionLedgerError, match="conflicting fill"):
        other.commit_fill(replace(app, fill=replace(app.fill, price=D(101))))
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(PositionFillReceipt)) == 1


@pytest.mark.parametrize("fault", ["cursor", "position", "realized", "source", "skip"])
def test_reject_invalid_application_without_partial_writes(ledger, fault):
    oid, _ = order(qty=20)
    source(oid)
    app = application(ledger)
    if fault == "cursor":
        app = replace(app, expected_cursor=123456)
    elif fault == "position":
        app = replace(app, position=replace(app.position, qty=9))
    elif fault == "realized":
        app = replace(app, realized=replace(app.realized, gross_pnl=D(5)))
    elif fault == "source":
        app = replace(app, fill=replace(app.fill, price=D(5)))
    else:
        source(oid)
        app = application(ledger, fill=ledger.pending_fills("simulated", 0)[1])
    with pytest.raises(PositionLedgerError):
        ledger.commit_fill(app)
    assert ledger.load_state("simulated").cursor == 0
    assert ledger.load_state("simulated").positions == ()


def test_real_rollback_after_all_writes_and_lost_commit_acknowledgement(ledger):
    oid, _ = order()
    source(oid)
    app = application(ledger)
    class FaultSession(Session):
        pass
    factory = sessionmaker(bind=SessionLocal.kw["bind"], class_=FaultSession, autoflush=False)
    def fail_commit(s):
        raise PositionLedgerError("injected before commit")
    event.listen(FaultSession, "before_commit", fail_commit)
    with pytest.raises(PositionLedgerError, match="injected"):
        PostgresPositionLedger(factory).commit_fill(app)
    assert ledger.load_state("simulated").cursor == 0
    assert ledger.pending_fills("simulated", 0) == (app.fill,)
    event.remove(FaultSession, "before_commit", fail_commit)
    def lost_ack(s):
        raise PositionLedgerError("injected acknowledgement loss")
    event.listen(FaultSession, "after_commit", lost_ack)
    with pytest.raises(PositionLedgerError, match="acknowledgement"):
        PostgresPositionLedger(factory).commit_fill(app)
    assert ledger.load_state("simulated").positions == (app.position,)
    assert not ledger.commit_fill(app).applied


def wait_for_lock_waiter():
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with SessionLocal() as s:
            if s.scalar(text("SELECT count(*) FROM pg_locks WHERE relation = 'fills'::regclass "
                             "AND mode = 'ShareRowExclusiveLock' AND NOT granted")):
                return
        sleep(0.01)
    pytest.fail("reader did not reach the fill lock barrier")


@pytest.mark.parametrize("finish", ["commit", "rollback", "disconnect"])
def test_safe_prefix_waits_for_lower_sequence_transaction(ledger, finish):
    oid, _ = order(qty=20)
    writer = SessionLocal()
    try:
        lower = insert_fill(writer, oid)
        higher = source(oid)
        assert higher > lower
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ledger.pending_fills, "simulated", 0)
            try:
                wait_for_lock_waiter()
                assert not future.done()
            finally:
                if finish == "disconnect":
                    writer.close()
                else:
                    getattr(writer, finish)()
            rows = future.result(timeout=5)
        assert [f.ledger_seq for f in rows] == ([lower, higher] if finish == "commit" else [higher])
        for fill in rows:
            ledger.commit_fill(application(ledger, fill=fill))
        assert ledger.load_state("simulated").cursor == higher
    finally:
        writer.close()


def test_competing_consumers_apply_exactly_once(ledger):
    oid, _ = order()
    source(oid)
    app = application(ledger)
    barrier = Barrier(2)
    def consume():
        barrier.wait(timeout=5)
        return PostgresPositionLedger(SessionLocal).commit_fill(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert sorted(r.applied for r in results) == [False, True]
    assert ledger.load_state("simulated").positions[0].qty == 10


@pytest.mark.parametrize("fault", ["anomaly", "overfill", "projection", "missing_receipt", "missing_cursor", "source_mutation", "reservation", "metadata"])
def test_incomplete_or_corrupt_checkpoint_fails_closed(ledger, fault):
    oid, tid = order()
    source(oid, qty=11 if fault == "overfill" else 10, anomaly="overfill" if fault == "anomaly" else None)
    if fault in {"anomaly", "overfill"}:
        with pytest.raises(PositionLedgerError):
            ledger.commit_fill(application(ledger))
        return
    ledger.commit_fill(application(ledger))
    with SessionLocal.begin() as s:
        if fault == "projection":
            s.scalar(select(Position)).qty = 1
        elif fault == "missing_receipt":
            s.query(PositionFillReceipt).delete()
        elif fault == "missing_cursor":
            s.query(PortfolioStateCursor).delete()
        elif fault == "source_mutation":
            s.scalar(select(Fill)).price = 300
        elif fault == "reservation":
            # Existing approved trade before Execution inserts an order.
            s.add(Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=NAME,
                strategy_version="1", direction="BUY", symbol="MSFT", thesis={}, decision="approved"))
        else:
            s.get(Trade, tid).symbol = "MSFT"
    with pytest.raises(PositionLedgerError):
        ledger.load_state("simulated")


def test_legacy_checkpoint_is_not_silently_zeroed(ledger):
    from app.portfolio_state.engine import PortfolioState
    oid, _ = order()
    seq = source(oid)
    with SessionLocal() as s:
        PortfolioState("simulated").apply_fill(s, s.get(Fill, seq))
        s.commit()
    with pytest.raises(PositionLedgerError, match="legacy checkpoint"):
        ledger.load_state("simulated")


def test_sequence_policy_rejects_explicit_ids_and_unsafe_cache(ledger):
    oid, _ = order()
    with SessionLocal() as s:
        with pytest.raises(DBAPIError):
            s.execute(text("INSERT INTO fills (ledger_seq, client_order_id, execution_venue, venue_fill_id, qty, price, venue_ts) "
                           "VALUES (123456, :oid, 'simulated', 'explicit', 1, 1, now())"), {"oid": oid})
        s.rollback()
    try:
        with SessionLocal.begin() as s:
            s.execute(text("ALTER TABLE fills ALTER COLUMN ledger_seq SET CACHE 2"))
        with pytest.raises(PositionLedgerError, match="unsafe fill sequence"):
            ledger.load_state("simulated")
    finally:
        with SessionLocal.begin() as s:
            s.execute(text("ALTER TABLE fills ALTER COLUMN ledger_seq SET CACHE 1"))


@pytest.mark.asyncio
async def test_worker_commits_before_closure_and_restart_does_not_republish(ledger):
    oid, tid = order()
    source(oid)
    exit_id, _ = order(trade_id=tid, effect="close", side="SELL")
    closed_seq = source(exit_id, price=105, ts=TS + timedelta(days=200))
    bus = EventBus()
    seen = []
    async def closure(envelope):
        state = await asyncio.to_thread(ledger.load_state, "simulated")
        assert state.cursor == closed_seq
        assert state.positions == ()
        seen.append(envelope)
    bus.subscribe(EventType.POSITION_CLOSED, closure)
    await bus.start()
    worker = PortfolioState("simulated", ledger=ledger, bus=bus)
    try:
        await worker.start()
        await worker._queue.join()
        await bus._critical_queue.join()
        assert len(seen) == 1
        assert worker.get_snapshot() is not None
        await worker.stop()
        worker = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=bus)
        await worker.start()
        await worker._queue.join()
        await bus._critical_queue.join()
        assert len(seen) == 1
    finally:
        await worker.stop()
        await bus.stop()


def test_projection_rounding_does_not_change_exact_average(ledger):
    oid, _ = order(qty=2)
    source(oid, qty=1, price=D("1.000000"))
    ledger.commit_fill(application(ledger))
    source(oid, qty=1, price=D("1.000001"))
    result = ledger.commit_fill(application(ledger))
    assert result.state.positions[0].avg_price == D("1.0000005")
    with SessionLocal() as s:
        assert s.scalar(select(Position)).avg_price == D("1.000001")


def test_interleaved_modes_do_not_require_contiguous_sequences(ledger):
    simulated, _ = order(qty=20)
    paper, _ = order(mode="paper")
    first = source(simulated)
    middle = source(paper)
    last = source(simulated)
    assert first < middle < last
    for fill in ledger.pending_fills("simulated", 0):
        ledger.commit_fill(application(ledger, fill=fill))
    assert ledger.load_state("simulated").cursor == last
    assert ledger.load_state("paper").cursor == 0
    assert ledger.pending_fills("paper", 0)[0].ledger_seq == middle
    ledger.commit_fill(application(ledger, "paper"))
    assert ledger.load_state("paper").positions[0].qty == 10


def test_cursor_conflict_after_another_application(ledger):
    oid, _ = order(qty=20)
    source(oid)
    first = application(ledger)
    source(oid)
    stale = application(ledger, fill=ledger.pending_fills("simulated", 0)[1])
    ledger.commit_fill(first)
    with pytest.raises(PositionLedgerError, match="cursor conflict"):
        ledger.commit_fill(stale)
    assert ledger.load_state("simulated").positions[0].qty == 10


def test_thesis_changes_do_not_rewrite_historical_inputs(ledger):
    oid, tid = order(qty=20)
    source(oid)
    first = application(ledger)
    ledger.commit_fill(first)
    with SessionLocal.begin() as s:
        s.get(Trade, tid).thesis = {"final_stop": 80}
    assert ledger.load_state("simulated").positions[0].stop == D(90)
    assert not ledger.commit_fill(first).applied
    source(oid)
    assert ledger.commit_fill(application(ledger)).state.positions[0].qty == 20


def test_migration_roundtrip_preserves_old_rows_and_advances_past_explicit_ids(ledger):
    from alembic import command
    from alembic.config import Config
    config = Config()
    config.set_main_option("script_location", "alembic")
    oid, _ = order(qty=20)
    source(oid)
    command.downgrade(config, "0012")
    try:
        with SessionLocal.begin() as s:
            high = s.scalar(text("SELECT last_value + 100 FROM fills_ledger_seq_seq"))
            s.execute(text("INSERT INTO fills (ledger_seq, client_order_id, execution_venue, venue_fill_id, qty, price, venue_ts) "
                           "VALUES (:seq, :oid, 'simulated', 'pre-migration-explicit', 1, 1, now())"), {"seq": high, "oid": oid})
    finally:
        command.upgrade(config, "head")
    new_seq = source(oid, qty=1)
    assert new_seq > high
    assert high in [f.ledger_seq for f in ledger.pending_fills("simulated", 0)]
    assert ledger.load_state("simulated").cursor == 0


def test_migration_downgrade_refuses_to_destroy_applied_receipts(ledger):
    from alembic import command
    from alembic.config import Config
    oid, _ = order()
    source(oid)
    app = application(ledger)
    ledger.commit_fill(app)
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        config = Config()
        config.set_main_option("script_location", "alembic")
        command.downgrade(config, "0012")
    assert ledger.load_state("simulated").positions == (app.position,)
    with SessionLocal() as s:
        assert s.scalar(text("SELECT version_num FROM alembic_version")) == "0014"
