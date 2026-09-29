"""EOD request / fallback / reservation / expiry / dispatch-claim state machine.

Real PostgreSQL, injected clock. `EodIntent` structurally matches the ExitIntent
the Position Monitor sibling will emit (optional `eod_flatten_at`/`eod_close_at`);
the monitor itself is not imported or edited.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.session import SessionLocal
from app.execution_engine.exit_ledger import (
    ClaimDisposition as C, EodExpiryDisposition as X, ExitLedgerError, ObserveDisposition as O,
    PostgresExitLedger, PrepareDisposition as P,
)
from app.models.execution_ledger import ExitRequest, Fill, Order, Position, PositionFillReceipt, Trade

NAME = "TEST_EOD_EXIT_LEDGER"
UTC = timezone.utc
# Wed 2026-09-16 (regular day, EDT): flatten 19:59:00Z, close 20:00:00Z.
DAY_OPEN = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
FLATTEN = datetime(2026, 9, 16, 19, 59, tzinfo=UTC)
CLOSE = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
IN_WINDOW = FLATTEN + timedelta(seconds=10)
AFTER = CLOSE + timedelta(seconds=30)


class Clock:
    def __init__(self, now=IN_WINDOW):
        self.now = now

    def __call__(self):
        return self.now


@dataclass(frozen=True)
class EodIntent:
    position_id: UUID
    symbol: str = "ZZEOD"
    side: str = "BUY"
    qty: int = 999  # never authority
    exit_reason: str = "eod_flatten"
    trigger_price: float = 101.0
    trigger_ts: datetime = DAY_OPEN + timedelta(hours=5)
    eod_flatten_at: datetime | None = None
    eod_close_at: datetime | None = None


def protective(position_id, reason="stop", price=89.0, ts=IN_WINDOW):
    return EodIntent(position_id, exit_reason=reason, trigger_price=price, trigger_ts=ts)


@pytest.fixture(autouse=True)
def clean():
    def remove():
        with SessionLocal.begin() as session:
            ids = select(Trade.trade_id).where(Trade.strategy_name == NAME)
            orders = select(Order.client_order_id).where(Order.trade_id.in_(ids))
            positions = select(Position.position_id).where(Position.trade_id.in_(ids))
            session.query(PositionFillReceipt).filter(PositionFillReceipt.position_id.in_(positions)).delete(synchronize_session=False)
            session.query(ExitRequest).filter(ExitRequest.position_id.in_(positions)).delete(synchronize_session=False)
            session.query(Fill).filter(Fill.client_order_id.in_(orders)).delete(synchronize_session=False)
            session.query(Order).filter(Order.trade_id.in_(ids)).delete(synchronize_session=False)
            session.query(Position).filter(Position.trade_id.in_(ids)).delete(synchronize_session=False)
            session.query(Trade).filter(Trade.strategy_name == NAME).delete(synchronize_session=False)

    remove()
    yield
    remove()


def seeded(*, qty=5, entry_status="filled", opened_at=DAY_OPEN):
    with SessionLocal.begin() as session:
        trade = Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=NAME,
                      strategy_version="v1", direction="BUY", symbol="ZZEOD", decision="approved", status="open")
        session.add(trade)
        session.flush()
        position = Position(trade_id=trade.trade_id, execution_mode="simulated", execution_venue="simulated",
                            symbol="ZZEOD", side="BUY", qty=qty, avg_price=100, stop=90, target=120,
                            opened_at=opened_at, status="open")
        session.add(position)
        session.flush()
        entry = Order(client_order_id=f"{trade.trade_id}:entry", trade_id=trade.trade_id,
                      execution_mode="simulated", execution_venue="simulated", symbol="ZZEOD",
                      side="BUY", position_effect="open", qty=qty, order_type="market", status=entry_status)
        session.add(entry)
        return trade.trade_id, position.position_id, entry.client_order_id


def make(clock=None, **kw):
    clock = clock or Clock()
    return clock, PostgresExitLedger(SessionLocal, clock=clock, eod_lead_seconds=kw.pop("lead", 60), **kw)


def row(position_id):
    with SessionLocal() as session:
        session.expire_on_commit = False
        return session.get(ExitRequest, position_id)


def orders_of(position_id):
    with SessionLocal() as session:
        return session.scalars(select(Order).where(Order.position_id == position_id).order_by(Order.id)).all()


def reserve_eod(ledger, position_id):
    assert ledger.observe_exit(EodIntent(position_id)).disposition is O.STORED
    result = ledger.prepare_exit(position_id)
    assert result.disposition is P.SUBMIT
    return result.action


# ---------------------------------------------------------------- observe

def test_eod_observation_stores_one_row_with_ledger_derived_bounds_and_ignores_quantity():
    _, pid, _ = seeded()
    _, ledger = make()
    first = ledger.observe_exit(EodIntent(pid))
    assert first.disposition is O.STORED and first.acknowledged and not first.retry
    again = ledger.observe_exit(EodIntent(pid, trigger_price=5.0))
    assert again.disposition is O.ALREADY_STORED
    stored = row(pid)
    assert (stored.exit_reason, stored.trigger_price) == ("eod_flatten", Decimal("101.000000"))
    assert (stored.eod_flatten_at, stored.eod_close_at) == (FLATTEN, CLOSE)
    assert stored.eod_expired_at is None and stored.fallback_reason is None
    # supplied bounds that equal the derived window are accepted
    _, pid2, _ = seeded()
    ok = ledger.observe_exit(EodIntent(pid2, eod_flatten_at=FLATTEN, eod_close_at=CLOSE))
    assert ok.disposition is O.STORED


def test_window_not_open_and_closed_write_nothing_and_boundaries_are_exact():
    _, pid, _ = seeded()
    clock, ledger = make(Clock(FLATTEN - timedelta(microseconds=1)))
    early = ledger.observe_exit(EodIntent(pid))
    assert early.disposition is O.WINDOW_NOT_OPEN and early.retry and not early.acknowledged
    assert row(pid) is None
    clock.now = CLOSE
    late = ledger.observe_exit(EodIntent(pid))
    assert late.disposition is O.WINDOW_CLOSED and not late.retry
    assert row(pid) is None and orders_of(pid) == []
    clock.now = FLATTEN
    assert ledger.observe_exit(EodIntent(pid)).disposition is O.STORED  # inclusive lower bound
    # a closed window does not block a later ordinary protective request (A2)
    _, pid2, _ = seeded()
    clock.now = AFTER
    assert ledger.observe_exit(EodIntent(pid2)).disposition is O.WINDOW_CLOSED
    assert ledger.observe_exit(protective(pid2, "target", 121.0)).disposition is O.STORED
    assert row(pid2).exit_reason == "target" and row(pid2).eod_flatten_at is None


@pytest.mark.parametrize("kwargs,reason", [
    (dict(trigger_ts=DAY_OPEN - timedelta(seconds=1)), "label_before_position_opened"),
    (dict(trigger_ts=IN_WINDOW + timedelta(hours=1)), "label_in_future"),
    (dict(trigger_price=float("nan")), "invalid_trigger_price"),
    (dict(trigger_price=0.0), "invalid_trigger_price"),
    (dict(trigger_price=float("inf")), "invalid_trigger_price"),
    (dict(trigger_ts=datetime(2026, 9, 16, 19, 59)), "naive_trigger_ts"),
    (dict(eod_flatten_at=FLATTEN + timedelta(seconds=30), eod_close_at=CLOSE), "eod_bounds_mismatch"),
    (dict(eod_flatten_at=FLATTEN, eod_close_at=CLOSE + timedelta(hours=2)), "eod_bounds_mismatch"),
    (dict(eod_flatten_at=FLATTEN), "eod_bounds_incomplete"),
    (dict(eod_flatten_at=datetime(2026, 9, 16, 19, 59), eod_close_at=datetime(2026, 9, 16, 20, 0)), "eod_bounds_naive"),
    (dict(exit_reason="liquidate"), "unsupported_exit_reason"),
])
def test_invalid_eod_observations_are_rejected_and_never_stored(kwargs, reason):
    _, pid, _ = seeded()
    _, ledger = make()
    result = ledger.observe_exit(EodIntent(pid, **kwargs))
    assert (result.disposition, result.reason) == (O.INVALID, reason)
    assert row(pid) is None


def test_supplied_bounds_are_not_trusted_and_lead_is_the_ledgers_own():
    _, pid, _ = seeded()
    _, sixty = make(lead=60)
    _, thirty = make(lead=30)
    # the monitor computed a 30 s window; the ledger's configured 60 s window disagrees
    stale = EodIntent(pid, eod_flatten_at=CLOSE - timedelta(seconds=30), eod_close_at=CLOSE)
    assert sixty.observe_exit(stale).reason == "eod_bounds_mismatch"
    assert row(pid) is None
    # the same window is valid for a ledger configured with 30 s, and is then stored as such
    clock = Clock(CLOSE - timedelta(seconds=10))
    thirty._clock = clock
    assert thirty.observe_exit(stale).disposition is O.STORED
    assert row(pid).eod_flatten_at == CLOSE - timedelta(seconds=30)


def test_entry_day_label_holiday_and_unsupported_calendar_are_invalid():
    clock, ledger = make()
    # label from the next ET day for a position opened after the prior close
    _, pid, _ = seeded(opened_at=datetime(2026, 9, 15, 20, 30, tzinfo=UTC))
    r = ledger.observe_exit(EodIntent(pid, trigger_ts=datetime(2026, 9, 16, 15, 0, tzinfo=UTC)))
    assert r.reason == "label_not_entry_day"
    # covered holiday: Labor Day
    opened = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)
    _, pid2, _ = seeded(opened_at=opened)
    clock.now = datetime(2026, 9, 7, 19, 59, 30, tzinfo=UTC)
    r = ledger.observe_exit(EodIntent(pid2, trigger_ts=opened))
    assert r.reason == "no_eod_session_on_entry_day"
    # uncovered years
    for year in (2025, 2029):
        opened = datetime(year, 3, 3, 15, 0, tzinfo=UTC)
        _, p, _ = seeded(opened_at=opened)
        clock.now = datetime(year, 3, 3, 20, 59, 30, tzinfo=UTC)
        assert ledger.observe_exit(EodIntent(p, trigger_ts=opened)).reason == "unsupported_eod_calendar"


def test_half_day_and_standard_time_windows_come_from_the_shared_helper():
    clock, ledger = make()
    opened = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)  # half-day, EST: 13:00 ET = 18:00Z
    _, pid, _ = seeded(opened_at=opened)
    clock.now = datetime(2026, 11, 27, 17, 59, 30, tzinfo=UTC)
    assert ledger.observe_exit(EodIntent(pid, trigger_ts=opened)).disposition is O.STORED
    assert row(pid).eod_close_at == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    opened = datetime(2026, 1, 12, 15, 0, tzinfo=UTC)  # EST regular day: 16:00 ET = 21:00Z
    _, pid2, _ = seeded(opened_at=opened)
    clock.now = datetime(2026, 1, 12, 20, 59, 30, tzinfo=UTC)
    assert ledger.observe_exit(EodIntent(pid2, trigger_ts=opened)).disposition is O.STORED
    assert row(pid2).eod_close_at == datetime(2026, 1, 12, 21, 0, tzinfo=UTC)


def test_identity_mismatch_raises_unknown_and_flat_positions_are_dispositions():
    _, pid, _ = seeded()
    _, ledger = make()
    with pytest.raises(ExitLedgerError):
        ledger.observe_exit(EodIntent(pid, symbol="OTHER"))
    with pytest.raises(ExitLedgerError):
        ledger.observe_exit(EodIntent(pid, side="SELL"))
    assert row(pid) is None
    import uuid
    assert ledger.observe_exit(EodIntent(uuid.uuid4())).reason == "unknown_position"
    with SessionLocal.begin() as session:
        p = session.get(Position, pid)
        p.qty, p.status = 0, "closed"
    assert ledger.observe_exit(EodIntent(pid)).disposition is O.POSITION_CLOSED
    assert ledger.observe_exit(protective(pid)).disposition is O.POSITION_CLOSED
    assert ledger.prepare_exit(pid).disposition is P.NO_REQUEST


def test_concurrent_observers_create_one_row_and_one_first_fallback():
    _, pid, _ = seeded()
    _, ledger = make()
    results = []

    def go(intent):
        results.append(ledger.observe_exit(intent).disposition)

    threads = [threading.Thread(target=go, args=(EodIntent(pid),)) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert results.count(O.STORED) == 1 and results.count(O.ALREADY_STORED) == 5
    results.clear()
    threads = [threading.Thread(target=go, args=(protective(pid, "stop" if i % 2 else "target", 80 + i),))
               for i in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert results.count(O.FALLBACK_STORED) == 1 and results.count(O.FALLBACK_ALREADY_STORED) == 5
    assert row(pid).fallback_reason in {"stop", "target"}


# ---------------------------------------------------------------- fallback & precedence

def test_first_protective_observation_is_the_immutable_fallback_and_eod_never_displaces_protective():
    _, pid, _ = seeded()
    _, ledger = make()
    assert ledger.observe_exit(EodIntent(pid)).disposition is O.STORED
    first = ledger.observe_exit(protective(pid, "target", 121.0))
    assert first.disposition is O.FALLBACK_STORED and first.slot.fallback_reason == "target"
    second = ledger.observe_exit(protective(pid, "stop", 80.0))
    assert second.disposition is O.FALLBACK_ALREADY_STORED and second.slot.fallback_reason == "target"
    stored = row(pid)
    assert (stored.exit_reason, stored.fallback_reason, stored.fallback_trigger_price) == (
        "eod_flatten", "target", Decimal("121.000000"))
    with pytest.raises(DBAPIError):  # database refuses to rewrite the fallback
        with SessionLocal.begin() as session:
            session.get(ExitRequest, pid).fallback_reason = "stop"
    # protective first: EOD adds nothing, original stop stands
    _, pid2, _ = seeded()
    assert ledger.observe_exit(protective(pid2)).disposition is O.STORED
    assert ledger.observe_exit(EodIntent(pid2)).disposition is O.SUPERSEDED
    assert row(pid2).exit_reason == "stop" and row(pid2).eod_flatten_at is None
    assert ledger.observe_exit(protective(pid2, "target", 121)).disposition is O.ALREADY_STORED


def test_database_enforces_field_groups_and_original_immutability():
    _, pid, _ = seeded()
    _, ledger = make()
    ledger.observe_exit(EodIntent(pid))
    for column, value in (("exit_reason", "stop"), ("trigger_price", 7), ("eod_close_at", CLOSE + timedelta(hours=1)),
                          ("eod_flatten_at", FLATTEN - timedelta(minutes=5))):
        with pytest.raises(DBAPIError):
            with SessionLocal.begin() as session:
                setattr(session.get(ExitRequest, pid), column, value)
    _, pid2, _ = seeded()
    for bad in (
        dict(exit_reason="eod_flatten"),                                            # no bounds
        dict(exit_reason="stop", eod_flatten_at=FLATTEN, eod_close_at=CLOSE),       # bounds on a stop
        dict(exit_reason="eod_flatten", eod_flatten_at=CLOSE, eod_close_at=FLATTEN),  # inverted
        dict(exit_reason="eod_flatten", eod_flatten_at=FLATTEN, eod_close_at=CLOSE,
             eod_expired_at=FLATTEN),                                               # expiry before close
        dict(exit_reason="stop", fallback_reason="stop", fallback_trigger_price=1, fallback_trigger_ts=IN_WINDOW),
        dict(exit_reason="eod_flatten", eod_flatten_at=FLATTEN, eod_close_at=CLOSE, fallback_reason="stop"),
        dict(exit_reason="eod_flatten", eod_flatten_at=FLATTEN, eod_close_at=CLOSE, fallback_reason="stop",
             fallback_trigger_price=Decimal("NaN"), fallback_trigger_ts=IN_WINDOW),
        dict(exit_reason="eod_flatten", eod_flatten_at=FLATTEN, eod_close_at=CLOSE, fallback_reason="eod_flatten",
             fallback_trigger_price=1, fallback_trigger_ts=IN_WINDOW),
    ):
        with pytest.raises(IntegrityError):
            with SessionLocal.begin() as session:
                session.add(ExitRequest(position_id=pid2, trigger_price=1, trigger_ts=IN_WINDOW, **bad))


# ---------------------------------------------------------------- reserve / claim

def test_reservation_uses_committed_quantity_reuses_unsent_and_claims_exactly_once():
    trade_id, pid, _ = seeded(qty=5)
    _, ledger = make()
    action = reserve_eod(ledger, pid)
    assert (action.kind, action.qty, action.side, action.exit_reason) == ("submit", 5, "SELL", "eod_flatten")
    assert action.client_order_id == f"{trade_id}:exit:1"
    (order,) = orders_of(pid)
    assert order.exit_dispatch_started_at is None and order.exit_reason == "eod_flatten"
    again = ledger.prepare_exit(pid)  # proven-unsent reservation is reused, not duplicated
    assert again.disposition is P.SUBMIT and again.action == action and len(orders_of(pid)) == 1

    claim = ledger.claim_dispatch(action.client_order_id)
    assert claim.send and claim.disposition is C.CLAIMED and claim.action == action
    assert orders_of(pid)[0].exit_dispatch_started_at == IN_WINDOW  # durable before any venue call
    stale = ledger.claim_dispatch(action.client_order_id)
    assert stale.disposition is C.ALREADY_CLAIMED and not stale.send
    assert ledger.prepare_exit(pid).disposition is P.WAIT_UNCERTAIN_DISPATCH
    assert ledger.set_status(action.client_order_id, "submitted", venue_order_id="v1")
    assert ledger.prepare_exit(pid).disposition is P.WAIT_ACTIVE_ORDER
    assert len(orders_of(pid)) == 1


def test_concurrent_claims_have_a_single_claimant_and_concurrent_prepares_a_single_order():
    _, pid, _ = seeded()
    _, ledger = make()
    ledger.observe_exit(EodIntent(pid))
    prepared = []
    threads = [threading.Thread(target=lambda: prepared.append(ledger.prepare_exit(pid))) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert {p.action.client_order_id for p in prepared} == {orders_of(pid)[0].client_order_id}
    assert len(orders_of(pid)) == 1
    oid = orders_of(pid)[0].client_order_id
    claims = []
    threads = [threading.Thread(target=lambda: claims.append(ledger.claim_dispatch(oid).disposition)) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert claims.count(C.CLAIMED) == 1 and claims.count(C.ALREADY_CLAIMED) == 7


def test_unclaimed_eod_close_cannot_be_transitioned_and_reserved_reason_is_refused():
    _, pid, _ = seeded()
    _, ledger = make()
    action = reserve_eod(ledger, pid)
    for status in ("submitted", "rejected", "cancelled"):
        with pytest.raises(ExitLedgerError):
            ledger.set_status(action.client_order_id, status)
    assert orders_of(pid)[0].status == "approved"
    ledger.claim_dispatch(action.client_order_id)
    with pytest.raises(ExitLedgerError):
        ledger.set_status(action.client_order_id, "cancelled", reason="eod_window_closed")
    with pytest.raises(IntegrityError):  # DB: a marked order can never carry the proven-unsent reason
        with SessionLocal.begin() as session:
            o = session.scalar(select(Order).where(Order.client_order_id == action.client_order_id))
            o.status, o.reject_reason = "cancelled", "eod_window_closed"
    with pytest.raises(DBAPIError):  # marker immutable
        with SessionLocal.begin() as session:
            session.scalar(select(Order).where(Order.client_order_id == action.client_order_id)
                           ).exit_dispatch_started_at = None


def test_claim_guards_return_waits_and_unsafe_not_benign_false():
    trade_id, pid, entry_id = seeded()
    _, ledger = make()
    action = reserve_eod(ledger, pid)
    oid = action.client_order_id
    assert ledger.claim_dispatch("nope").disposition is C.UNSAFE
    assert ledger.claim_dispatch(entry_id).disposition is C.UNSAFE  # an entry is not a position close

    with SessionLocal.begin() as session:
        session.scalar(select(Order).where(Order.client_order_id == entry_id)).status = "submitted"
    assert ledger.claim_dispatch(oid).disposition is C.WAIT_ENTRY_ACTIVITY
    with SessionLocal.begin() as session:
        session.scalar(select(Order).where(Order.client_order_id == entry_id)).status = "filled"
        session.add(Fill(client_order_id=entry_id, execution_venue="simulated", venue_fill_id="late-1",
                         qty=1, price=100, venue_ts=DAY_OPEN))
    assert ledger.claim_dispatch(oid).disposition is C.WAIT_PENDING_FILL
    assert ledger.prepare_exit(pid).disposition is P.WAIT_PENDING_FILL
    with SessionLocal.begin() as session:
        session.query(Fill).filter(Fill.venue_fill_id == "late-1").delete()
        session.get(Position, pid).qty = 4
    r = ledger.claim_dispatch(oid)
    assert (r.disposition, r.reason) == (C.UNSAFE, "reservation_quantity_differs")
    with SessionLocal.begin() as session:
        session.get(Position, pid).qty = 5
        session.get(Position, pid).symbol = "TAMPER"
    assert ledger.claim_dispatch(oid).reason == "reservation_differs_from_position"
    with SessionLocal.begin() as session:
        session.get(Position, pid).symbol = "ZZEOD"
    assert ledger.claim_dispatch(oid).send  # everything settled: now it may be claimed
    with SessionLocal.begin() as session:
        session.get(Position, pid).status = "closed"
    assert ledger.claim_dispatch(oid).disposition is C.STALE


def test_prepare_identity_failures_raise_as_unsafe_errors():
    trade_id, pid, _ = seeded()
    _, ledger = make()
    ledger.observe_exit(EodIntent(pid))
    with SessionLocal.begin() as session:
        session.get(Trade, trade_id).decision = "rejected"
    with pytest.raises(ExitLedgerError):
        ledger.prepare_exit(pid)
    with SessionLocal.begin() as session:
        session.get(Trade, trade_id).decision = "approved"
        session.get(Position, pid).symbol = "TAMPER"
    with pytest.raises(ExitLedgerError):
        ledger.prepare_exit(pid)


def test_entry_is_cancelled_before_close_and_receipts_gate_the_reservation():
    _, pid, entry_id = seeded(entry_status="partially_filled")
    _, ledger = make()
    ledger.observe_exit(EodIntent(pid))
    r = ledger.prepare_exit(pid)
    assert (r.disposition, r.action.kind, r.action.client_order_id) == (P.CANCEL_ENTRY, "cancel_entry", entry_id)
    assert ledger.set_status(entry_id, "cancelled")
    assert ledger.prepare_exit(pid).disposition is P.SUBMIT


# ---------------------------------------------------------------- expiry

def test_unsent_approved_reservation_is_cancelled_atomically_at_the_deadline_and_never_reused():
    trade_id, pid, _ = seeded()
    clock, ledger = make()
    action = reserve_eod(ledger, pid)
    clock.now = CLOSE
    r = ledger.prepare_exit(pid)
    assert r.disposition is P.DORMANT and r.expired_now and r.eod_expired
    assert r.cancelled_order_id == action.client_order_id and r.action is None
    (order,) = orders_of(pid)
    assert (order.status, order.reject_reason, order.exit_dispatch_started_at) == ("cancelled", "eod_window_closed", None)
    assert row(pid).eod_expired_at == CLOSE
    assert ledger.claim_dispatch(action.client_order_id).disposition is C.STALE  # nothing left to send
    assert ledger.prepare_exit(pid).disposition is P.DORMANT  # dormant without fallback
    assert ledger.pending_exit_position_ids() == ()
    # a later protective observation wakes it: new ID, reason stop, committed qty
    assert ledger.observe_exit(protective(pid, ts=AFTER)).disposition is O.FALLBACK_STORED
    assert ledger.pending_exit_position_ids() == (pid,)
    clock.now = AFTER
    nxt = ledger.prepare_exit(pid)
    assert nxt.disposition is P.SUBMIT
    assert (nxt.action.client_order_id, nxt.action.exit_reason, nxt.action.qty) == (f"{trade_id}:exit:2", "stop", 5)
    assert nxt.action.client_order_id != action.client_order_id
    assert [o.exit_reason for o in orders_of(pid)] == ["eod_flatten", "stop"]


def test_claim_after_deadline_never_sends_and_cancels_the_unsent_reservation():
    _, pid, _ = seeded()
    clock, ledger = make()
    action = reserve_eod(ledger, pid)
    clock.now = CLOSE  # between prepare and the final guard
    r = ledger.claim_dispatch(action.client_order_id)
    assert (r.disposition, r.send) == (C.WINDOW_EXPIRED, False)
    (order,) = orders_of(pid)
    assert (order.status, order.reject_reason, order.exit_dispatch_started_at) == ("cancelled", "eod_window_closed", None)
    assert row(pid).eod_expired_at == CLOSE
    assert ledger.claim_dispatch(action.client_order_id).disposition is C.STALE


def test_expiry_persists_even_when_entry_or_receipt_delays_the_reservation():
    _, pid, entry_id = seeded(entry_status="submitted")
    clock, ledger = make()
    ledger.observe_exit(EodIntent(pid))
    assert ledger.prepare_exit(pid).disposition is P.CANCEL_ENTRY  # delayed by a working entry
    clock.now = AFTER
    r = ledger.prepare_exit(pid)
    assert (r.disposition, r.expired_now) == (P.DORMANT, True)
    assert orders_of(pid) == [] and row(pid).eod_expired_at == AFTER  # no EOD reservation after close
    # with a stored fallback the same guards (entry first) now govern protective work
    ledger.observe_exit(protective(pid, ts=AFTER))
    assert ledger.prepare_exit(pid).disposition is P.CANCEL_ENTRY
    ledger.set_status(entry_id, "cancelled")
    with SessionLocal.begin() as session:
        session.add(Fill(client_order_id=entry_id, execution_venue="simulated", venue_fill_id="f1",
                         qty=1, price=100, venue_ts=DAY_OPEN))
    assert ledger.prepare_exit(pid).disposition is P.WAIT_PENDING_FILL  # receipts gate the reservation


def test_advance_expiry_recovers_from_stored_bounds_and_config_or_clock_cannot_reopen_it():
    _, pid, _ = seeded()
    clock, ledger = make()
    assert ledger.advance_eod_expiry(pid).disposition is X.NO_REQUEST
    ledger.observe_exit(EodIntent(pid))
    assert ledger.advance_eod_expiry(pid).disposition is X.NOT_DUE
    clock.now = AFTER
    # a fresh ledger instance (restart) with a different configured lead recovers the same deadline
    _, restarted = make(Clock(AFTER), lead=300)
    assert restarted.advance_eod_expiry(pid).disposition is X.EXPIRED
    assert restarted.advance_eod_expiry(pid).disposition is X.ALREADY_EXPIRED
    clock.now = FLATTEN + timedelta(seconds=5)  # backward clock jump
    assert ledger.prepare_exit(pid).disposition is P.DORMANT
    assert (row(pid).eod_flatten_at, row(pid).eod_close_at) == (FLATTEN, CLOSE)
    # config change cannot move a stored deadline: a 300 s ledger stores its own window ...
    _, other, _ = seeded()
    wide_clock, wide = make(Clock(CLOSE - timedelta(seconds=200)), lead=300)
    stored = wide.observe_exit(EodIntent(other, eod_flatten_at=CLOSE - timedelta(seconds=300), eod_close_at=CLOSE))
    assert stored.disposition is O.STORED
    # ... and a 60 s ledger (whose own window would still be closed) follows the STORED bounds
    _, narrow = make(Clock(CLOSE - timedelta(seconds=200)), lead=60)
    assert narrow.prepare_exit(other).disposition is P.SUBMIT
    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM orders WHERE position_id = :p"), {"p": other})
        session.get(Position, other).exit_attempt = 0
    narrow._clock = Clock(CLOSE - timedelta(seconds=301))  # before the stored flatten_at
    assert narrow.prepare_exit(other).disposition is P.WAIT_WINDOW_NOT_OPEN


def test_flat_position_makes_expiry_and_fallback_inert():
    _, pid, _ = seeded()
    clock, ledger = make()
    ledger.observe_exit(EodIntent(pid))
    with SessionLocal.begin() as session:
        p = session.get(Position, pid)
        p.qty, p.status = 0, "closed"
    clock.now = AFTER
    assert ledger.advance_eod_expiry(pid).disposition is X.POSITION_CLOSED
    assert ledger.prepare_exit(pid).disposition is P.POSITION_CLOSED
    assert row(pid).eod_expired_at is None and ledger.pending_exit_position_ids() == ()


# ---------------------------------------------------------------- terminal outcomes

@pytest.mark.parametrize("terminal", ["rejected", "cancelled"])
def test_terminal_eod_inside_window_retries_eod_on_a_new_id_and_duplicates_add_nothing(terminal):
    trade_id, pid, _ = seeded()
    clock, ledger = make()
    action = reserve_eod(ledger, pid)
    assert ledger.claim_dispatch(action.client_order_id).send
    assert ledger.set_status(action.client_order_id, terminal, reason="outside_regular_session")
    assert not ledger.set_status(action.client_order_id, terminal)  # duplicate update
    assert row(pid).retry_after == IN_WINDOW + timedelta(seconds=5)
    assert ledger.prepare_exit(pid).disposition is P.WAIT_RETRY_DELAY
    clock.now = IN_WINDOW + timedelta(seconds=6)
    retry = ledger.prepare_exit(pid)
    assert retry.action.client_order_id == f"{trade_id}:exit:2" and retry.action.exit_reason == "eod_flatten"
    assert retry.action.qty == 5
    assert len(orders_of(pid)) == 2 and row(pid).eod_expired_at is None


@pytest.mark.parametrize("terminal,fallback", [("rejected", "stop"), ("cancelled", "target")])
def test_terminal_eod_at_or_after_close_expires_and_only_a_fallback_can_place(terminal, fallback):
    trade_id, pid, _ = seeded()
    clock, ledger = make(Clock(CLOSE - timedelta(seconds=1)))
    action = reserve_eod(ledger, pid)
    assert ledger.claim_dispatch(action.client_order_id).send
    clock.now = CLOSE  # venue answers at the bell
    assert ledger.set_status(action.client_order_id, terminal, reason="outside_regular_session")
    assert row(pid).eod_expired_at == CLOSE  # set_status advanced the expiry
    clock.now = CLOSE + timedelta(seconds=2)  # still inside the 5 s delay the rejection set
    assert ledger.prepare_exit(pid).disposition is P.DORMANT  # no fallback: dormant, no new EOD
    assert len(orders_of(pid)) == 1
    assert ledger.observe_exit(protective(pid, fallback, ts=clock.now)).disposition is O.FALLBACK_STORED
    assert ledger.observe_exit(protective(pid, "stop" if fallback == "target" else "target", ts=clock.now)
                               ).disposition is O.FALLBACK_ALREADY_STORED
    assert ledger.prepare_exit(pid).disposition is P.WAIT_RETRY_DELAY  # 5 s bound not reset
    clock.now = CLOSE + timedelta(seconds=6)
    nxt = ledger.prepare_exit(pid)
    assert (nxt.action.client_order_id, nxt.action.exit_reason, nxt.action.qty) == (f"{trade_id}:exit:2", fallback, 5)
    assert [o.exit_reason for o in orders_of(pid)] == ["eod_flatten", fallback]
    # the fallback attempt uses the same durable claim protocol
    assert ledger.claim_dispatch(nxt.action.client_order_id).send
    assert orders_of(pid)[1].exit_dispatch_started_at is not None


def test_submitted_order_stays_working_past_close_and_fallback_cannot_place():
    _, pid, _ = seeded()
    clock, ledger = make()
    action = reserve_eod(ledger, pid)
    ledger.claim_dispatch(action.client_order_id)
    ledger.set_status(action.client_order_id, "submitted", venue_order_id="v1")
    clock.now = AFTER
    assert ledger.observe_exit(protective(pid, ts=AFTER)).disposition is O.FALLBACK_STORED
    r = ledger.prepare_exit(pid)
    assert (r.disposition, r.eod_expired, r.cancelled_order_id) == (P.WAIT_ACTIVE_ORDER, True, None)
    (order,) = orders_of(pid)
    assert order.status == "submitted" and order.reject_reason is None  # not cancelled or replaced
    assert row(pid).eod_expired_at == AFTER
    assert ledger.claim_dispatch(action.client_order_id).disposition is C.STALE


def test_dispatch_marked_approved_close_at_close_is_uncertain_not_unsent():
    _, pid, _ = seeded()
    clock, ledger = make()
    action = reserve_eod(ledger, pid)
    assert ledger.claim_dispatch(action.client_order_id).send  # ... then the process "crashes"
    clock.now = AFTER
    assert ledger.advance_eod_expiry(pid).cancelled_order_id is None
    r = ledger.prepare_exit(pid)
    assert r.disposition is P.DORMANT and r.cancelled_order_id is None
    ledger.observe_exit(protective(pid, ts=AFTER))
    assert ledger.prepare_exit(pid).disposition is P.WAIT_UNCERTAIN_DISPATCH
    (order,) = orders_of(pid)
    assert (order.status, order.reject_reason) == ("approved", None)
    assert ledger.claim_dispatch(action.client_order_id).disposition is C.ALREADY_CLAIMED  # never resend
    # a real venue report resolves it, and only then may the fallback place
    assert ledger.set_status(action.client_order_id, "rejected", reason="venue_report")
    clock.now = AFTER + timedelta(seconds=6)
    assert ledger.prepare_exit(pid).action.exit_reason == "stop"


def _apply_fill(pid, trade_id, order_id, qty, seq_name, remaining):
    with SessionLocal.begin() as session:
        fill = Fill(client_order_id=order_id, execution_venue="simulated", venue_fill_id=seq_name,
                    qty=qty, price=101, venue_ts=IN_WINDOW)
        session.add(fill)
        session.flush()
        session.add(PositionFillReceipt(ledger_seq=fill.ledger_seq, execution_mode="simulated",
                                        execution_venue="simulated", venue_fill_id=seq_name, position_id=pid,
                                        fill_data={"qty": str(qty)}, trading_day=date(2026, 9, 16), gross_pnl=0))
        position = session.get(Position, pid)
        position.qty = remaining
        if remaining == 0:
            position.status = "closed"
        return fill.ledger_seq


def test_partial_fill_keeps_one_active_order_then_fallback_sizes_the_settled_remainder():
    trade_id, pid, _ = seeded(qty=10)
    clock, ledger = make()
    action = reserve_eod(ledger, pid)
    assert action.qty == 10
    ledger.claim_dispatch(action.client_order_id)
    ledger.set_status(action.client_order_id, "submitted", venue_order_id="v1")
    _apply_fill(pid, trade_id, action.client_order_id, 3, "p1", 7)
    with SessionLocal.begin() as session:
        session.scalar(select(Order).where(Order.client_order_id == action.client_order_id)).status = "partially_filled"
    clock.now = AFTER
    ledger.observe_exit(protective(pid, "target", 121.0, AFTER))
    r = ledger.prepare_exit(pid)
    assert r.disposition is P.WAIT_ACTIVE_ORDER and len(orders_of(pid)) == 1  # no second close
    assert ledger.set_status(action.client_order_id, "cancelled")
    clock.now = AFTER + timedelta(seconds=6)
    with SessionLocal.begin() as session:  # a fill still awaiting its receipt blocks sizing
        session.add(Fill(client_order_id=action.client_order_id, execution_venue="simulated",
                         venue_fill_id="p2", qty=1, price=101, venue_ts=AFTER))
    assert ledger.prepare_exit(pid).disposition is P.WAIT_PENDING_FILL
    with SessionLocal.begin() as session:
        session.query(Fill).filter(Fill.venue_fill_id == "p2").delete()
    nxt = ledger.prepare_exit(pid)
    assert (nxt.action.qty, nxt.action.exit_reason, nxt.action.client_order_id) == (7, "target", f"{trade_id}:exit:2")


def test_partial_cancellation_without_fallback_is_dormant_and_full_closure_makes_fallback_inert():
    trade_id, pid, _ = seeded(qty=10)
    clock, ledger = make()
    action = reserve_eod(ledger, pid)
    ledger.claim_dispatch(action.client_order_id)
    ledger.set_status(action.client_order_id, "submitted", venue_order_id="v1")
    _apply_fill(pid, trade_id, action.client_order_id, 3, "q1", 7)
    with SessionLocal.begin() as session:
        session.scalar(select(Order).where(Order.client_order_id == action.client_order_id)).status = "partially_filled"
    clock.now = AFTER
    ledger.set_status(action.client_order_id, "cancelled")
    assert ledger.prepare_exit(pid).disposition is P.DORMANT
    ledger.observe_exit(protective(pid, ts=AFTER))
    _apply_fill(pid, trade_id, action.client_order_id, 7, "q2", 0)  # a late fill flattens it
    assert ledger.prepare_exit(pid).disposition is P.POSITION_CLOSED
    assert ledger.pending_exit_position_ids() == ()
    assert len(orders_of(pid)) == 1  # no over-close, no reversal order


# ---------------------------------------------------------------- legacy surface

def test_legacy_surface_serves_stop_target_rows_and_never_surfaces_eod():
    trade_id, pid, _ = seeded()
    _, ledger = make()
    assert ledger.observe(EodIntent(pid)) is False and row(pid) is None  # worker cannot create EOD
    assert ledger.observe(protective(pid, "target", 121.0)) is True
    assert ledger.observe(protective(pid, "stop", 80.0)) is True and row(pid).exit_reason == "target"
    assert ledger.pending_position_ids() == (pid,)
    action = ledger.prepare(pid)
    assert (action.kind, action.qty, action.exit_reason) == ("submit", 5, "target")
    assert ledger.confirm_recovery_exit(action.client_order_id)
    assert ledger.confirm_recovery_exit(action.client_order_id)  # #184: no marker, idempotent
    assert orders_of(pid)[0].exit_dispatch_started_at is None
    with pytest.raises(ExitLedgerError):
        ledger.observe(protective(pid, price=float("nan")))

    _, eod_pid, _ = seeded()
    ledger.observe_exit(EodIntent(eod_pid))
    assert ledger.prepare(eod_pid) is None and eod_pid not in ledger.pending_position_ids()
    assert eod_pid in ledger.pending_exit_position_ids() and orders_of(eod_pid) == []
    state = ledger.slot_state(eod_pid)
    assert state.is_eod and not state.eod_expired and not state.dormant
    assert ledger.slot_state(pid).original_reason == "target" and ledger.slot_state(pid).eod_flatten_at is None


def test_ledger_accepts_the_real_position_monitor_exit_intent_unchanged():
    """The merged monitor's ExitIntent (5fc4dfb) is read structurally; nothing is imported by app code."""
    from app.position_monitor.engine import ExitIntent

    _, pid, _ = seeded()
    _, ledger = make()
    eod = ExitIntent(pid, "ZZEOD", "BUY", 999, "eod_flatten", 101.0, DAY_OPEN + timedelta(hours=5),
                     eod_flatten_at=FLATTEN, eod_close_at=CLOSE)
    assert ledger.observe_exit(eod).disposition is O.STORED
    stop = ExitIntent(pid, "ZZEOD", "BUY", 999, "stop", 89.0, IN_WINDOW)
    assert ledger.observe_exit(stop).disposition is O.FALLBACK_STORED
    assert ledger.observe(stop) is True  # legacy surface takes the same object
    _, pid2, _ = seeded()
    bad = ExitIntent(pid2, "ZZEOD", "BUY", 999, "eod_flatten", 101.0, DAY_OPEN + timedelta(hours=5),
                     eod_flatten_at=FLATTEN + timedelta(seconds=30), eod_close_at=CLOSE)
    assert ledger.observe_exit(bad).reason == "eod_bounds_mismatch"
