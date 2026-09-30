"""OutcomeRecorder integration checks against the real execution ledger schema."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.models.execution_ledger import Fill, Order, Position, PositionFillReceipt, Trade, TradeReservation
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.portfolio_state.accounting import LedgerFill, apply_fill
from app.portfolio_state.postgres import _encode
from app.trading_intelligence import outcome_recorder as module
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots

NAME = "TEST_OUTCOME_RECORDER"


def _db_available():
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="real PostgreSQL unavailable")


@pytest.fixture(autouse=True)
def _cleanup():
    def clean():
        with SessionLocal.begin() as session:
            session.execute(text("UPDATE trades SET outcome_id = NULL WHERE strategy_name = :n"), {"n": NAME})
            session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), {"n": NAME})
            session.execute(text("DELETE FROM position_fill_receipts WHERE position_id IN "
                                 "(SELECT position_id FROM positions WHERE trade_id IN "
                                 "(SELECT trade_id FROM trades WHERE strategy_name = :n))"), {"n": NAME})
            session.execute(text("DELETE FROM fills WHERE client_order_id IN "
                                 "(SELECT client_order_id FROM orders WHERE trade_id IN "
                                 "(SELECT trade_id FROM trades WHERE strategy_name = :n))"), {"n": NAME})
            session.execute(text("DELETE FROM orders WHERE trade_id IN "
                                 "(SELECT trade_id FROM trades WHERE strategy_name = :n)"), {"n": NAME})
            session.execute(text("DELETE FROM trade_reservations WHERE trade_id IN "
                                 "(SELECT trade_id FROM trades WHERE strategy_name = :n)"), {"n": NAME})
            session.execute(text("DELETE FROM positions WHERE trade_id IN "
                                 "(SELECT trade_id FROM trades WHERE strategy_name = :n)"), {"n": NAME})
            session.execute(text("DELETE FROM trades WHERE strategy_name = :n"), {"n": NAME})
    clean()
    yield
    clean()


def _seed(*, evidence=True, basis=98, fees=(None, None, None, None), reason="target"):
    trade_id, position_id = uuid4(), uuid4()
    ts = datetime.now(timezone.utc) - timedelta(seconds=20)
    thesis = {"structural_invalidation": basis, "structural_target": 110,
              "final_stop": basis, "final_target": 110, "confidence": 0.7}
    if evidence:
        thesis["evidence"] = {"signal": "observed"}
    with SessionLocal.begin() as session:
        trade = Trade(trade_id=trade_id, execution_mode="simulated", execution_venue="simulated",
                      origin="auto", strategy_name=NAME, strategy_version="v1", direction="BUY",
                      symbol="AAPL", thesis=thesis, decision="approved", limits_snapshot={},
                      decision_record={"structural_invalidation": basis,
                                       "setup_detected_at": ts.isoformat(), "decided_at": ts.isoformat()},
                      status="closed")
        session.add(trade)
        session.flush()
        session.add(TradeReservation(trade_id=trade_id, client_order_id=f"{trade_id}:entry",
                                     qty=10, reference_price=Decimal("100")))
        entry_id = f"{trade_id}:entry"
        first_exit_id = f"{trade_id}:exit:1"
        last_exit_id = f"{trade_id}:exit:2"
        for order_id, effect, qty, exit_reason in (
            (entry_id, "open", 10, None), (first_exit_id, "close", 3, "stop"),
            (last_exit_id, "close", 7, reason),
        ):
            session.add(Order(client_order_id=order_id, trade_id=trade_id,
                              execution_mode="simulated", execution_venue="simulated", symbol="AAPL",
                              side="BUY" if effect == "open" else "SELL", position_effect=effect,
                              qty=qty, status="filled", exit_reason=exit_reason))
        session.flush()
        state = None
        receipts = []
        for index, (order_id, effect, qty, price) in enumerate((
            (entry_id, "open", 4, 100), (entry_id, "open", 6, 102),
            (first_exit_id, "close", 3, 104), (last_exit_id, "close", 7, 106),
        )):
            stamp = ts + timedelta(seconds=index)
            db_fill = Fill(client_order_id=order_id, execution_venue="simulated",
                           venue_fill_id=f"{trade_id}:f{index}", qty=qty, price=price,
                           venue_ts=stamp, commission=fees[index])
            session.add(db_fill)
            session.flush()
            fill = LedgerFill(db_fill.ledger_seq, db_fill.venue_fill_id, order_id, trade_id,
                              "simulated", "simulated", "AAPL", "BUY" if effect == "open" else "SELL",
                              effect, qty, Decimal(price), stamp,
                              None if fees[index] is None else Decimal(str(fees[index])))
            result = apply_fill(state, fill, new_position_id=position_id if state is None else None)
            state = result.position
            receipts.append((db_fill.ledger_seq, fill, result.realized_delta))
        session.add(Position(position_id=position_id, trade_id=trade_id, execution_mode="simulated",
                             execution_venue="simulated", symbol="AAPL", side="BUY", qty=0,
                             avg_price=state.avg_price, opened_at=state.opened_at, closed_at=state.closed_at,
                             status="closed", realized_pnl=state.realized_pnl))
        session.flush()
        for seq, fill, delta in receipts:
            session.add(PositionFillReceipt(ledger_seq=seq, execution_mode="simulated",
                                            execution_venue="simulated", venue_fill_id=fill.venue_fill_id,
                                            position_id=position_id, fill_data=_encode(fill),
                                            trading_day=ts.date(), gross_pnl=delta))
    return trade_id, entry_id


def _result(trade_id):
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        rows = session.scalars(select(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.opportunity_id == trade_id)).all()
        return trade.outcome_status, trade.outcome_id, rows


def _seed_first_entry():
    trade_id = uuid4()
    stamp = datetime.now(timezone.utc)
    order_id = f"{trade_id}:entry"
    with SessionLocal.begin() as session:
        session.add(Trade(trade_id=trade_id, execution_mode="simulated", execution_venue="simulated",
                          origin="auto", strategy_name=NAME, strategy_version="v1", direction="BUY",
                          symbol="AAPL", thesis={"evidence": {"signal": "observed"}}, decision="approved",
                          limits_snapshot={}, status="open"))
        session.flush()
        session.add(Order(client_order_id=order_id, trade_id=trade_id, execution_mode="simulated",
                          execution_venue="simulated", symbol="AAPL", side="BUY", position_effect="open",
                          qty=10, status="partially_filled"))
        session.flush()
        session.add(Fill(client_order_id=order_id, execution_venue="simulated",
                         venue_fill_id=f"{trade_id}:first", qty=4, price=100, venue_ts=stamp))
    return trade_id, order_id


@pytest.mark.asyncio
async def test_partial_fills_and_reductions_record_one_outcome_with_missing_snapshots(monkeypatch):
    trade_id, _ = _seed()
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots(None, None))
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert await recorder.record_trade(trade_id) == "recorded"
    assert await recorder.record_trade(trade_id) == "skipped"
    status, linked, rows = _result(trade_id)
    assert status == "recorded" and linked == rows[0].outcome_id and len(rows) == 1
    row = rows[0]
    assert row.entry_qty == row.exit_qty == 10
    assert row.entry_price == Decimal("101.200000")
    assert row.exit_price == Decimal("105.400000")
    assert row.realized_pnl == Decimal("42.000000")
    assert row.realized_r == Decimal("1.3125")
    assert row.commission_total is None
    assert row.exit_reason == "target"
    assert row.market_state_at_entry is None and row.market_state_at_exit is None
    assert row.snapshot_missing_reasons == {
        "market_state_at_entry": "recorder_unavailable", "context_at_entry": "recorder_unavailable",
        "market_state_at_exit": "engine_cold_start", "context_at_exit": "engine_cold_start",
    }
    assert row.schema_version == 2 and row.execution_mode == row.execution_venue == "simulated"
    assert row.signal_confirmed_at is None


@pytest.mark.asyncio
async def test_known_fees_and_concurrent_wakeups(monkeypatch):
    trade_id, _ = _seed(fees=(1, 2, 3, -1))
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots({"m": 1}, {"c": 1}))
    a = OutcomeRecorder(EventBus(), SessionLocal)
    b = OutcomeRecorder(EventBus(), SessionLocal)
    assert sorted(await asyncio.gather(a.record_trade(trade_id), b.record_trade(trade_id))) == ["recorded", "skipped"]
    status, linked, rows = _result(trade_id)
    assert status == "recorded" and linked == rows[0].outcome_id and len(rows) == 1
    assert rows[0].commission_total == Decimal("5.000000")
    assert rows[0].realized_pnl == Decimal("37.000000")


@pytest.mark.asyncio
async def test_partial_reduction_waits_for_closed_trade_marker():
    trade_id, _ = _seed()
    with SessionLocal.begin() as session:
        session.get(Trade, trade_id).status = "closing"
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert await recorder.record_trade(trade_id) == "skipped"
    assert _result(trade_id) == (None, None, [])
    with SessionLocal.begin() as session:
        session.get(Trade, trade_id).status = "closed"
    assert await recorder.record_trade(trade_id) == "recorded"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,reason", [
    ({"evidence": False}, "evidence_unavailable"),
    ({"basis": 101.2}, "r_basis_unavailable"),
    ({"reason": None}, "exit_reason_unavailable"),
])
async def test_missing_attribution_blocks_without_outcome(kwargs, reason, caplog):
    trade_id, _ = _seed(**kwargs)
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert await recorder.record_trade(trade_id) == "blocked"
    assert reason in caplog.text
    assert _result(trade_id) == ("blocked", None, [])
    assert await recorder.record_trade(trade_id) == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,venue", [("paper", "ibkr"), ("live", "ibkr")])
async def test_unsupported_mode_is_blocked_without_outcome(mode, venue, caplog):
    trade_id, _ = _seed()
    with SessionLocal.begin() as session:
        trade = session.get(Trade, trade_id)
        trade.execution_mode = mode
        trade.execution_venue = venue
    assert await OutcomeRecorder(EventBus(), SessionLocal).record_trade(trade_id) == "blocked"
    assert "unsupported_mode" in caplog.text
    assert _result(trade_id) == ("blocked", None, [])


@pytest.mark.asyncio
async def test_multi_day_receipt_blocks_without_outcome(caplog):
    trade_id, _ = _seed()
    with SessionLocal.begin() as session:
        receipt = session.scalar(select(PositionFillReceipt).join(
            Position, PositionFillReceipt.position_id == Position.position_id)
            .where(Position.trade_id == trade_id).order_by(PositionFillReceipt.ledger_seq.desc()))
        receipt.trading_day += timedelta(days=1)
    assert await OutcomeRecorder(EventBus(), SessionLocal).record_trade(trade_id) == "blocked"
    assert "multi_day_position" in caplog.text
    assert _result(trade_id) == ("blocked", None, [])


@pytest.mark.asyncio
async def test_failed_link_rolls_back_insert_and_retries(monkeypatch):
    trade_id, _ = _seed()
    real_writer = module.record_strategy_outcome_in_session
    def fail_after_insert(session, outcome):
        real_writer(session, outcome)
        session.flush()
        raise SQLAlchemyError("injected fault before trade link")
    monkeypatch.setattr(module, "record_strategy_outcome_in_session", fail_after_insert)
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert await recorder.record_trade(trade_id) == "pending_retry"
    assert _result(trade_id) == ("pending_retry", None, [])
    monkeypatch.setattr(module, "record_strategy_outcome_in_session", real_writer)
    assert await recorder.record_trade(trade_id) == "recorded"


@pytest.mark.asyncio
async def test_rejected_insert_blocks_without_orphan_row(monkeypatch, caplog):
    trade_id, _ = _seed()
    def reject(session, outcome):
        raise IntegrityError("INSERT strategy_outcomes", {}, Exception("rejected"))
    monkeypatch.setattr(module, "record_strategy_outcome_in_session", reject)
    assert await OutcomeRecorder(EventBus(), SessionLocal).record_trade(trade_id) == "blocked"
    assert "write_rejected" in caplog.text
    assert _result(trade_id) == ("blocked", None, [])


@pytest.mark.asyncio
async def test_startup_scan_and_sweep_recover_lost_events(monkeypatch):
    first, _ = _seed()
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots(None, None))
    recorder = OutcomeRecorder(EventBus(), SessionLocal, sweep_interval_seconds=60)
    await recorder.start()
    await recorder._queue.join()
    assert _result(first)[0] == "recorded"
    second, _ = _seed()
    await recorder.scan()  # the bounded sweep uses this same path
    await recorder._queue.join()
    assert _result(second)[0] == "recorded"
    await recorder.stop()


@pytest.mark.asyncio
async def test_startup_scan_pages_through_more_than_one_bounded_batch(monkeypatch):
    first, _ = _seed()
    second, _ = _seed()
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots(None, None))
    recorder = OutcomeRecorder(EventBus(), SessionLocal, batch_size=1)
    await recorder.start()
    await recorder._queue.join()
    assert _result(first)[0] == _result(second)[0] == "recorded"
    await recorder.stop()


@pytest.mark.asyncio
async def test_entry_hook_captures_only_first_fill_and_preserves_missing_reason(monkeypatch):
    trade_id, order_id = _seed_first_entry()
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots",
                        lambda symbol: StrategyOutcomeSnapshots({"market": 1}, None))
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    await recorder.capture_entry(order_id)
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        assert trade.entry_market_state == {"market": 1}
        assert trade.entry_context is None
        assert trade.entry_snapshot_missing_reasons == {"context_at_entry": "engine_state_lost_on_restart"}
        captured_at = trade.entry_snapshot_captured_at
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots",
                        lambda symbol: StrategyOutcomeSnapshots({"market": 2}, {"context": 2}))
    await recorder.capture_entry(order_id)
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        assert trade.entry_snapshot_captured_at == captured_at
        assert trade.entry_market_state == {"market": 1}


@pytest.mark.asyncio
async def test_entry_snapshot_error_does_not_discard_fill(monkeypatch):
    trade_id, order_id = _seed_first_entry()
    def fail(symbol):
        raise RuntimeError("capture failed")
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", fail)
    await OutcomeRecorder(EventBus(), SessionLocal).capture_entry(order_id)
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        assert trade.entry_snapshot_missing_reasons == {
            "market_state_at_entry": "snapshot_capture_error", "context_at_entry": "snapshot_capture_error"}
        assert session.scalar(select(Fill).where(Fill.client_order_id == order_id)) is not None


@pytest.mark.asyncio
async def test_late_exit_snapshot_is_null_with_reason(monkeypatch):
    trade_id, _ = _seed()
    def should_not_capture(symbol):
        raise AssertionError("late snapshot must not be relabelled as exit")
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", should_not_capture)
    recorder = OutcomeRecorder(EventBus(), SessionLocal, snapshot_max_lag_seconds=1)
    assert await recorder.record_trade(trade_id) == "recorded"
    row = _result(trade_id)[2][0]
    assert row.market_state_at_exit is None and row.context_at_exit is None
    assert row.snapshot_missing_reasons["market_state_at_exit"] == "recorder_unavailable"


@pytest.mark.asyncio
async def test_periodic_sweep_recovers_closure_without_event(monkeypatch):
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots(None, None))
    recorder = OutcomeRecorder(EventBus(), SessionLocal, sweep_interval_seconds=0.02)
    await recorder.start()
    trade_id, _ = _seed()
    for _ in range(100):
        if _result(trade_id)[0] == "recorded":
            break
        await asyncio.sleep(0.02)
    assert _result(trade_id)[0] == "recorded"
    await recorder.stop()


# --- Lost-PositionClosed recovery: trades the positions inner join could not see (§6.7.1 C8) ---

def _drop_positions(trade_id, *, keep=0):
    """Model a ledger whose position rows are missing (zero) without touching the schema."""
    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM position_fill_receipts WHERE position_id IN "
                             "(SELECT position_id FROM positions WHERE trade_id = :t)"), {"t": trade_id})
        session.execute(text("DELETE FROM positions WHERE trade_id = :t"), {"t": trade_id})


def _seed_zero_position():
    """Closed approved simulated trade with a reservation but no positions row."""
    trade_id, _ = _seed()
    _drop_positions(trade_id)
    return trade_id


def _add_extra_position(trade_id, *, closed_at):
    with SessionLocal.begin() as session:
        session.add(Position(position_id=uuid4(), trade_id=trade_id, execution_mode="simulated",
                             execution_venue="simulated", symbol="AAPL", side="BUY", qty=0, avg_price=100,
                             opened_at=datetime.now(timezone.utc) - timedelta(minutes=5), closed_at=closed_at,
                             status="closed", realized_pnl=0))


def _position_count(trade_id):
    with SessionLocal() as session:
        return session.scalar(text("SELECT count(*) FROM positions WHERE trade_id = :t"), {"t": trade_id})


def _outcome_count(trade_id):
    return len(_result(trade_id)[2])


def _page_all(recorder, limit=50):
    """Walk _pending_rows exactly the way the startup scan does; return every row seen."""
    seen, cursor = [], None
    for _ in range(limit):
        rows = recorder._pending_rows(cursor)
        seen.extend(rows)
        if len(rows) < recorder._batch_size:
            return seen
        cursor = rows[-1]
    raise AssertionError("pagination did not terminate")


def _quiet(monkeypatch):
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots", lambda symbol: StrategyOutcomeSnapshots(None, None))


@pytest.mark.asyncio
async def test_startup_scan_blocks_zero_position_trade_with_no_event(monkeypatch, caplog):
    _quiet(monkeypatch)
    trade_id = _seed_zero_position()
    assert _position_count(trade_id) == 0
    recorder = OutcomeRecorder(EventBus(), SessionLocal)  # no PositionClosed is ever published
    await recorder.start()
    await recorder._queue.join()
    status, outcome_id, rows = _result(trade_id)
    assert (status, outcome_id, rows) == ("blocked", None, [])
    assert "reason=multi_position_trade" in caplog.text
    assert _position_count(trade_id) == 0  # nothing fabricated
    await recorder.stop()


@pytest.mark.asyncio
async def test_sweep_blocks_zero_position_trade_that_appears_after_start(monkeypatch):
    _quiet(monkeypatch)
    recorder = OutcomeRecorder(EventBus(), SessionLocal, sweep_interval_seconds=60)
    await recorder.start()
    await recorder._queue.join()
    trade_id = _seed_zero_position()
    await recorder.scan()
    await recorder._queue.join()
    assert _result(trade_id)[0] == "blocked"
    assert _outcome_count(trade_id) == 0 and _position_count(trade_id) == 0
    await recorder.scan()  # a blocked trade is never rediscovered
    assert not [row for row in recorder._pending_rows(None) if row[1] == trade_id]
    await recorder.stop()


@pytest.mark.asyncio
async def test_multi_position_trade_is_one_bounded_candidate_and_blocked(monkeypatch):
    _quiet(monkeypatch)
    trade_id, _ = _seed()
    now = datetime.now(timezone.utc)
    _add_extra_position(trade_id, closed_at=now)
    _add_extra_position(trade_id, closed_at=None)
    assert _position_count(trade_id) == 3
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert [row for row in recorder._pending_rows(None) if row[1] == trade_id].__len__() == 1
    await recorder.start()
    await recorder._queue.join()
    assert _result(trade_id)[0] == "blocked" and _outcome_count(trade_id) == 0
    await recorder.stop()


@pytest.mark.asyncio
async def test_null_closed_at_position_is_discovered_and_blocked(monkeypatch):
    _quiet(monkeypatch)
    trade_id = _seed_zero_position()
    _add_extra_position(trade_id, closed_at=None)
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert [row for row in recorder._pending_rows(None) if row[1] == trade_id].__len__() == 1
    await recorder.start()
    await recorder._queue.join()
    assert _result(trade_id)[0] == "blocked" and _outcome_count(trade_id) == 0
    await recorder.stop()


@pytest.mark.parametrize("batch_size", [1, 2, 3])
def test_pagination_returns_each_candidate_exactly_once_across_page_boundaries(batch_size):
    ordinary = [_seed()[0] for _ in range(2)]
    zero = [_seed_zero_position() for _ in range(2)]
    multi = _seed()[0]
    _add_extra_position(multi, closed_at=datetime.now(timezone.utc))
    _add_extra_position(multi, closed_at=None)
    null_only = _seed_zero_position()
    _add_extra_position(null_only, closed_at=None)
    mine = {*ordinary, *zero, multi, null_only}
    recorder = OutcomeRecorder(EventBus(), SessionLocal, batch_size=batch_size)
    rows = _page_all(recorder)
    ids = [trade_id for _, trade_id in rows]
    assert sorted(t for t in ids if t in mine) == sorted(mine)
    assert len(ids) == len(set(ids))  # no trade repeats across pages
    assert rows == sorted(rows, key=lambda row: (row[0], row[1]))  # stable keyset order
    assert all(len(rows[i:i + batch_size]) <= batch_size for i in range(0, len(rows), batch_size))


@pytest.mark.asyncio
async def test_batch_size_one_startup_scan_records_ordinary_once_and_blocks_the_rest(monkeypatch):
    _quiet(monkeypatch)
    ordinary = _seed()[0]
    zero = _seed_zero_position()
    multi = _seed()[0]
    _add_extra_position(multi, closed_at=datetime.now(timezone.utc))
    null_only = _seed_zero_position()
    _add_extra_position(null_only, closed_at=None)
    recorder = OutcomeRecorder(EventBus(), SessionLocal, batch_size=1)
    await recorder.start()
    await recorder._queue.join()
    assert _result(ordinary)[0] == "recorded" and _outcome_count(ordinary) == 1
    for trade_id in (zero, multi, null_only):
        assert _result(trade_id)[0] == "blocked" and _outcome_count(trade_id) == 0
    await recorder.scan()
    await recorder._queue.join()
    assert _outcome_count(ordinary) == 1  # a later sweep never records it twice
    await recorder.stop()


@pytest.mark.asyncio
async def test_sweep_rotates_through_pages_and_wraps(monkeypatch):
    _quiet(monkeypatch)
    recorder = OutcomeRecorder(EventBus(), SessionLocal, batch_size=1)
    recorder._started_at = datetime.now(timezone.utc)
    zero, null_only = _seed_zero_position(), _seed_zero_position()
    _add_extra_position(null_only, closed_at=None)
    ordinary = _seed()[0]
    for _ in range(60):  # bounded: one row per scan, cursor rotates and wraps
        if all(("close", t) in recorder._queued for t in (zero, null_only, ordinary)):
            break
        await recorder.scan()
    assert all(("close", t) in recorder._queued for t in (zero, null_only, ordinary))
