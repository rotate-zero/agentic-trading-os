"""Protocol contract tests with a real bus and an in-memory ledger.

These are NOT PostgreSQL, crash recovery, transaction isolation, or adapter
proofs. The separate Session compatibility tests exercise real PostgreSQL.
"""
import asyncio
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.core.market_clock import MarketClock
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.portfolio_state.accounting import LedgerFill
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.ports import CommitResult, DailyAmounts, InFlightOrder, LedgerState, PositionLedgerError, TERMINAL_STATUSES
from app.schemas.events.envelope import EventType
from app.schemas.events.execution import OrderApproved, OrderFilled, OrderStatusChanged
from app.schemas.events.market_data import PriceUpdated

TS = datetime(2026, 1, 5, 15, tzinfo=timezone.utc)
TRADE = UUID("00000000-0000-0000-0000-000000000001")


class Clock(MarketClock):
    now = TS

    def trading_day(self, ts=None):
        return super().trading_day(self.now if ts is None else ts)


def fill(seq=1, **changes):
    values = dict(ledger_seq=seq, venue_fill_id=f"f{seq}", client_order_id="entry", trade_id=TRADE,
                  execution_mode="simulated", execution_venue="simulated", symbol="AAPL", side="BUY",
                  position_effect="open", qty=10, price=D("100"), venue_ts=TS, stop=D("90"))
    values.update(changes)
    return LedgerFill(**values)


class FakeLedger:
    def __init__(self):
        self.states = {}
        self.orders = {}
        self.fills = []
        self.applied = {}
        self.closed = {}
        self.commits = []
        self.fail = False
        self.uncertain = False
        self.block = None
        self.entered = threading.Event()
        self.duplicate_response = False

    def order(self, order_id="entry", *, qty=10, effect="open", side="BUY", mode="simulated", symbol="AAPL", status="approved"):
        self.orders[order_id] = InFlightOrder(order_id, symbol, side, qty, effect, mode, status,
                                            reference_price=D("100"), stop=D("90"))

    def load_state(self, mode):
        state = self.states.get(mode, LedgerState(mode, 0, TS))
        return replace(state, orders=tuple(o for o in self.orders.values()
                                          if o.execution_mode == mode and o.status not in TERMINAL_STATUSES))

    def pending_fills(self, mode, after_cursor):
        return tuple(f for f in self.fills if f.execution_mode == mode and f.ledger_seq > after_cursor)

    def get_order(self, order_id):
        return self.orders.get(order_id)

    def commit_fill(self, app):
        self.entered.set()
        if self.block is not None:
            assert self.block.wait(5), "test failed to release persistence"
        if self.fail:
            raise PositionLedgerError("injected commit failure")
        f = app.fill
        if f.key in self.applied:
            if self.applied[f.key].fill != f:
                raise PositionLedgerError("conflicting fill identity")
            return CommitResult(False, self.load_state(f.execution_mode))
        state = self.load_state(f.execution_mode)
        if state.cursor != app.expected_cursor:
            raise PositionLedgerError("cursor conflict")
        positions = {p.symbol: p for p in state.positions}
        if app.position.qty:
            positions[f.symbol] = app.position
        else:
            positions.pop(f.symbol, None)
            self.closed[app.position.position_id] = app.position
        amounts = {(r.symbol, r.trading_day): r for r in state.daily}
        key = (f.symbol, app.realized.trading_day)
        row = amounts.get(key, DailyAmounts(*key))
        delta = app.realized.gross_pnl
        amounts[key] = replace(row, profit=row.profit + max(delta, D(0)), loss=row.loss + max(-delta, D(0)),
                               reported_fees=row.reported_fees + (f.commission if f.commission is not None else D(0)),
                               unknown_fee_count=row.unknown_fee_count + (f.commission is None))
        order = self.orders[f.client_order_id]
        filled = order.filled_qty + f.qty
        status = order.status if order.status in TERMINAL_STATUSES else ("filled" if filled == order.qty else "partially_filled")
        self.orders[f.client_order_id] = replace(order, filled_qty=filled, status=status)
        self.applied[f.key] = app
        self.states[f.execution_mode] = replace(state, cursor=f.ledger_seq, positions=tuple(positions.values()),
                                                daily=tuple(amounts.values()), as_of=f.venue_ts)
        self.commits.append(f.key)
        if self.uncertain:
            raise PositionLedgerError("commit succeeded but acknowledgement was lost")
        return CommitResult(not self.duplicate_response, self.load_state(f.execution_mode))


async def drain(bus, engine):
    await bus._critical_queue.join()
    await bus._normal_queue.join()
    await engine._queue.join()
    await bus._critical_queue.join()


async def notify(bus, engine, event_type=EventType.ORDER_FILLED, order_id="entry", **changes):
    if event_type == EventType.ORDER_APPROVED:
        payload = OrderApproved(order_id=order_id, symbol="AAPL", side="BUY", qty=10, position_effect="open")
    elif event_type == EventType.ORDER_STATUS_CHANGED:
        payload = OrderStatusChanged(order_id=order_id, status="rejected", reason="test")
    else:
        # Deliberately not trusted for arithmetic: no stable per-fill ID here.
        payload = OrderFilled(order_id=order_id, side="BUY", qty=999, fill_price=1, fill_ts=TS)
    await bus.publish(make_envelope(event_type, payload, symbol="AAPL"))
    await drain(bus, engine)


@pytest.fixture
async def setup():
    ledger, clock, bus = FakeLedger(), Clock(), EventBus()
    engine = PortfolioState("simulated", ledger=ledger, bus=bus, clock=clock)
    events = []
    bus.subscribe(EventType.POSITION_CLOSED, events.append)
    await bus.start()
    try:
        yield ledger, clock, bus, engine, events
    finally:
        if ledger.block is not None:
            ledger.block.set()
        await engine.stop()
        await bus.stop()


async def test_unknown_then_known_flat_snapshot_is_detached_and_io_free(setup):
    ledger, clock, bus, engine, _ = setup
    assert engine.get_snapshot() is None
    await engine.start()
    assert engine.get_snapshot().realized_pnl_today == 0
    assert engine.get_snapshot("UNKNOWN") is None
    def no_io(*args):
        raise AssertionError("read-side performed I/O")
    ledger.load_state = no_io
    assert engine.get_snapshot().buying_power is None
    clock.now += timedelta(days=30)
    assert engine.get_snapshot().trading_day == clock.trading_day()


async def test_partial_entry_duplicate_notifications_marks_and_completion(setup):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    await engine.start()
    await notify(bus, engine, EventType.ORDER_APPROVED)
    assert engine.get_snapshot().in_flight_count == 1
    ledger.fills.append(fill(qty=4))
    await notify(bus, engine)
    snap = engine.get_snapshot("AAPL")
    identity = snap.positions["AAPL"].position_id
    assert [e.qty for e in snap.exposures] == [4, 6]
    assert snap.unrealized_pnl is None and snap.open_risk is None
    await notify(bus, engine)
    assert len(ledger.commits) == 1
    ledger.fills.append(fill(2, qty=6, price=D("110")))
    await notify(bus, engine)
    snap = engine.get_snapshot()
    assert snap.in_flight_count == 0 and snap.positions["AAPL"].avg_price == 106
    assert snap.positions["AAPL"].position_id == identity
    snap.positions.clear()
    assert engine.get_snapshot().open_position_count == 1
    for symbol, price, ts in [("UNHELD", 1, TS), ("AAPL", 116, TS + timedelta(seconds=2)), ("AAPL", 1, TS)]:
        await bus.publish(make_envelope(EventType.PRICE_UPDATED, PriceUpdated(price=price, size=1, exchange_ts=ts), symbol=symbol))
    await drain(bus, engine)
    assert engine.get_snapshot().unrealized_pnl == 100
    assert set(engine.get_snapshot().marks) == {"AAPL"}
    assert events == []


@pytest.mark.parametrize("side,exit_side,first,last", [("BUY", "SELL", "110", "95"), ("SELL", "BUY", "90", "105")])
async def test_months_of_holding_partial_close_dates_fees_closure_order_and_restore(setup, side, exit_side, first, last):
    ledger, clock, bus, engine, events = setup
    ledger.order(side=side)
    ledger.fills.append(fill(side=side, commission=D("1")))
    await engine.start()
    identity = engine.get_snapshot().positions["AAPL"].position_id
    clock.now = datetime(2026, 4, 6, 15, tzinfo=timezone.utc)
    assert engine.get_snapshot().positions["AAPL"].position_id == identity
    assert engine.get_snapshot().realized_pnl_today == 0
    ledger.order("exit", effect="close", side=exit_side)
    ledger.fills.append(fill(2, client_order_id="exit", side=exit_side, position_effect="close", qty=4,
                             price=D(first), venue_ts=clock.now, commission=D("0.4")))
    await notify(bus, engine, order_id="exit")
    assert engine.get_snapshot().realized_profit_today == 40
    assert engine.get_snapshot().fees_today == D("0.4")
    april = clock.trading_day()
    assert events == []
    clock.now = datetime(2026, 9, 22, 15, tzinfo=timezone.utc)
    ledger.fills.append(fill(3, client_order_id="exit", side=exit_side, position_effect="close", qty=6,
                             price=D(last), venue_ts=clock.now, commission=D("0.6")))
    observed_commits = []
    bus.subscribe(EventType.POSITION_CLOSED, lambda event: observed_commits.append(tuple(ledger.commits)))
    await notify(bus, engine, order_id="exit")
    assert len(events) == 1 and len(observed_commits[0]) == 3
    event = events[0]
    assert event.is_critical and event.payload["position_id"] == str(identity)
    assert event.payload["realized_pnl"] == 10 and event.payload["fees"] == 2
    assert event.payload["r_multiple_achieved"] is None
    assert event.payload["r_multiple_missing_reason"] == "immutable_risk_basis_unavailable"
    assert engine.get_snapshot().realized_loss_today == 30
    assert engine.get_snapshot(trading_day=april).realized_profit_today == 40
    assert engine.get_snapshot(trading_day=april).fees_today == D("0.4")
    await notify(bus, engine, order_id="exit")
    assert len(events) == 1
    fresh = PortfolioState("simulated", ledger=ledger, bus=bus, clock=clock)
    await fresh.start()
    assert fresh.get_snapshot().realized_loss_today == 30
    assert fresh.get_snapshot(trading_day=april).realized_profit_today == 40
    assert fresh.get_snapshot().open_position_count == 0
    assert len(events) == 1
    await fresh.stop()


async def test_each_mode_and_et_trading_day_are_isolated(setup):
    ledger, clock, bus, engine, events = setup
    clock.now = datetime(2026, 1, 6, 1, tzinfo=timezone.utc)  # Jan 5 ET
    for index, mode in enumerate(("simulated", "backtest", "paper", "live")):
        venue = "simulated" if mode in {"simulated", "backtest"} else "ibkr"
        ledger.order(f"entry-{mode}", mode=mode)
        ledger.order(f"exit-{mode}", mode=mode, effect="close", side="SELL")
        ledger.fills.extend([
            fill(2 * index + 1, client_order_id=f"entry-{mode}", execution_mode=mode, execution_venue=venue),
            fill(2 * index + 2, client_order_id=f"exit-{mode}", execution_mode=mode, execution_venue=venue,
                 position_effect="close", side="SELL", price=D(101 + index), venue_ts=clock.now),
        ])
    for index, mode in enumerate(("simulated", "backtest", "paper", "live")):
        other = PortfolioState(mode, ledger=ledger, bus=bus, clock=clock)
        await other.start()
        assert other.get_snapshot().realized_profit_today == (index + 1) * 10
        assert other.get_snapshot().trading_day.isoformat() == "2026-01-05"
        await other.stop()


async def test_commit_failure_publishes_nothing_and_retry_does_not_double_apply(setup):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    ledger.fills.append(fill())
    await engine.start()
    ledger.order("exit", side="SELL", effect="close")
    ledger.fills.append(fill(2, client_order_id="exit", side="SELL", position_effect="close", price=D("105")))
    ledger.fail = True
    await notify(bus, engine, order_id="exit")
    assert events == [] and ledger.states["simulated"].cursor == 1
    assert engine.get_snapshot() is None
    ledger.fail = False
    await engine.refresh()
    await drain(bus, engine)
    assert len(events) == 1 and len(ledger.commits) == 2


async def test_commit_then_lost_ack_recovers_without_claiming_event_delivery(setup):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    ledger.fills.append(fill())
    await engine.start()
    ledger.order("exit", side="SELL", effect="close")
    ledger.fills.append(fill(2, client_order_id="exit", side="SELL", position_effect="close", price=D("105")))
    ledger.uncertain = True
    await notify(bus, engine, order_id="exit")
    assert ledger.states["simulated"].cursor == 2 and events == []
    ledger.uncertain = False
    await engine.refresh()
    assert engine.get_snapshot().realized_profit_today == 50
    assert events == []  # no outbox, so no guaranteed replay of notification


async def test_duplicate_commit_result_uses_stored_identity_and_publishes_nothing(setup):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    ledger.fills.append(fill())
    ledger.duplicate_response = True
    await engine.start()
    assert len(ledger.commits) == 1 and engine.get_snapshot().open_position_count == 1
    assert events == []


async def test_order_rejection_and_cancel_refresh_and_stale_approval_do_not_reopen(setup):
    ledger, clock, bus, engine, _ = setup
    ledger.order()
    await engine.start()
    ledger.fills.append(fill(qty=4))
    await notify(bus, engine)
    ledger.orders["entry"] = replace(ledger.orders["entry"], status="cancelled")
    await engine.refresh()  # no cancellation event exists on current main
    assert engine.get_snapshot().in_flight_count == 0
    assert engine.get_snapshot().positions["AAPL"].qty == 4
    await notify(bus, engine, EventType.ORDER_APPROVED)
    assert engine.get_snapshot().in_flight_count == 0
    ledger.order("rejected")
    await notify(bus, engine, EventType.ORDER_APPROVED, "rejected")
    assert engine.get_snapshot().in_flight_count == 1
    ledger.orders["rejected"] = replace(ledger.orders["rejected"], status="rejected")
    await notify(bus, engine, EventType.ORDER_STATUS_CHANGED, "rejected")
    assert engine.get_snapshot().in_flight_count == 0


async def test_unresolved_approval_blocks_reads_until_durable_metadata_exists(setup):
    ledger, clock, bus, engine, _ = setup
    await engine.start()
    await notify(bus, engine, EventType.ORDER_APPROVED)
    assert engine.get_snapshot() is None
    ledger.order()
    await engine.refresh()
    assert engine.get_snapshot().in_flight_count == 1


async def test_unknown_history_and_unknown_fees_are_not_zero(setup):
    ledger, clock, bus, engine, _ = setup
    ledger.states["simulated"] = LedgerState("simulated", 0, TS, history_complete=False)
    await engine.start()
    assert engine.get_snapshot().realized_pnl_today is None
    assert engine.get_snapshot().fees_today is None
    ledger.states["simulated"] = replace(ledger.states["simulated"], history_complete=True)
    ledger.order()
    ledger.fills.append(fill())
    await engine.refresh()
    assert engine.get_snapshot().reported_fees_today == 0
    assert engine.get_snapshot().fees_today is None
    assert engine.get_snapshot().unknown_fee_count_today == 1


async def test_reopening_same_symbol_gets_new_id_and_discards_old_mark(setup):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    ledger.fills.append(fill())
    await engine.start()
    identity = engine.get_snapshot().positions["AAPL"].position_id
    engine.update_mark("AAPL", 101, TS)
    ledger.order("exit", side="SELL", effect="close")
    ledger.order("entry2")
    ledger.fills.extend([
        fill(2, client_order_id="exit", position_effect="close", side="SELL"),
        fill(3, client_order_id="entry2", venue_ts=TS + timedelta(days=90)),
    ])
    await engine.refresh()
    p = engine.get_snapshot().positions["AAPL"]
    assert p.position_id != identity and p.opened_at == TS + timedelta(days=90)
    assert engine.get_snapshot().marks == {}
    engine.update_mark("AAPL", 1, TS)
    assert engine.get_snapshot().marks == {}


async def test_slow_commit_does_not_block_critical_bus_and_stop_drains(setup):
    ledger, clock, bus, engine, _ = setup
    ledger.order()
    await engine.start()
    ledger.block = threading.Event()
    ledger.fills.append(fill())
    envelope = make_envelope(EventType.ORDER_FILLED, OrderFilled(order_id="entry", side="BUY", qty=10, fill_price=100, fill_ts=TS))
    await bus.publish(envelope)
    assert await asyncio.to_thread(ledger.entered.wait, 2)
    unrelated = asyncio.Event()
    bus.subscribe(EventType.ORDER_STATUS_CHANGED, lambda e: unrelated.set())
    await bus.publish(make_envelope(EventType.ORDER_STATUS_CHANGED, OrderStatusChanged(order_id="entry", status="rejected", reason="test")))
    await asyncio.wait_for(unrelated.wait(), 1)
    assert engine.get_snapshot() is None
    stop = asyncio.create_task(engine.stop())
    await asyncio.sleep(0)
    assert not stop.done()
    ledger.block.set()
    await asyncio.wait_for(stop, 3)
    assert len(ledger.commits) == 1


@pytest.mark.parametrize("mutation", ["wrong_mode", "out_of_order", "invalid_close", "conflicting_key"])
async def test_invalid_ledger_input_never_changes_accounting_or_publishes(setup, mutation):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    ledger.fills.append(fill())
    await engine.start()
    if mutation == "wrong_mode":
        ledger.pending_fills = lambda *args: (fill(2, execution_mode="backtest"),)
    elif mutation == "out_of_order":
        ledger.pending_fills = lambda *args: (fill(),)
    elif mutation == "invalid_close":
        ledger.order("exit", side="SELL", effect="close", qty=11)
        ledger.fills.append(fill(2, client_order_id="exit", side="SELL", position_effect="close", qty=11))
    else:
        ledger.order("entry2")
        ledger.fills.append(fill(2, venue_fill_id="f1", client_order_id="entry2"))
    await engine.refresh()
    assert engine.get_snapshot() is None
    assert ledger.states["simulated"].cursor == 1 and events == []


async def test_publish_failure_after_commit_has_documented_recovery_gap(setup):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    ledger.fills.append(fill())
    await engine.start()
    ledger.order("exit", effect="close", side="SELL")
    ledger.fills.append(fill(2, client_order_id="exit", side="SELL", position_effect="close", price=D("105")))
    original = bus.publish
    async def fail_closure(envelope):
        if envelope.event_type == EventType.POSITION_CLOSED:
            assert ledger.states["simulated"].cursor == 2
            raise RuntimeError("publication unavailable")
        await original(envelope)
    bus.publish = fail_closure
    await engine.refresh()
    assert engine.get_snapshot() is None and events == []
    bus.publish = original
    await engine.refresh()
    assert engine.get_snapshot().realized_profit_today == 50
    assert events == []  # stable committed close, no invented delivery guarantee


async def test_unheld_price_rejected_before_queueing_and_restart_has_unknown_marks(setup):
    ledger, clock, bus, engine, events = setup
    ledger.order()
    ledger.fills.append(fill())
    await engine.start()
    engine._on_event(make_envelope(EventType.PRICE_UPDATED, PriceUpdated(price=1, size=1, exchange_ts=TS), symbol="MSFT"))
    assert engine._queue.empty()
    engine.update_mark("AAPL", 105, TS)
    assert engine.get_snapshot().unrealized_pnl == 50
    await engine.stop()
    await engine.start()
    assert engine.get_snapshot().unrealized_pnl is None
    assert engine.get_snapshot().marks == {}
    assert len(bus._subscribers[EventType.ORDER_FILLED]) == 1


async def test_new_worker_does_not_silently_adopt_legacy_session_calls(setup):
    ledger, clock, bus, engine, _ = setup
    with pytest.raises(RuntimeError, match="cannot both own"):
        engine.apply_fill(None, None)
