"""Stop/target closes are not reserved or dispatched outside regular hours.

Real PostgreSQL, the ledger's injectable clock and (engine tests) the real
SimulatedVenue. Documented defect reproduced first: a stop/target close rejected
by the venue after the bell got `retry_after = now + 5 s`, and every later service
pass then reserved a NEW attempt (`<trade>:exit:N`) and called the venue again.

Calendar (America/New_York): 2026-09-16 is a regular EDT day (close 20:00Z, next
open 2026-09-17 13:30Z); 2026-11-27 is an EST half-day (close 13:00 ET = 18:00Z,
next open Mon 2026-11-30 14:30Z).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from app.broker_adapters.simulated_venue import SimulatedVenue
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.execution_engine.engine import ExecutionEngine
from app.execution_engine.exit_ledger import (
    ClaimDisposition as C, ObserveDisposition as O, PostgresExitLedger, PrepareDisposition as P,
)
from app.execution_engine.fill_ledger import PostgresFillLedger
from app.execution_engine.postgres import PostgresOrderLedger
from app.models.execution_ledger import (
    ExitRequest, Fill, Order, PortfolioStateCursor, Position, PositionFillReceipt, Trade,
)
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.postgres import PostgresPositionLedger
from app.position_monitor.engine import ExitIntent

NAME = "TEST_PROTECTIVE_SESSION_RETRY"
UTC = timezone.utc
MICRO = timedelta(microseconds=1)

DAY_OPEN = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
CLOSE = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)               # 16:00 ET
NEXT_OPEN = datetime(2026, 9, 17, 13, 30, tzinfo=UTC)          # 09:30 ET

HALF_OPENED = datetime(2026, 11, 27, 14, 30, tzinfo=UTC)       # 09:30 ET
HALF_CLOSE = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)         # 13:00 ET
HALF_NEXT_OPEN = datetime(2026, 11, 30, 14, 30, tzinfo=UTC)    # Monday 09:30 ET


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@dataclass(frozen=True)
class Obs:
    position_id: UUID
    exit_reason: str = "stop"
    symbol: str = "ZZSESS"
    side: str = "BUY"
    qty: int = 999  # never authority
    trigger_price: float = 89.0
    trigger_ts: datetime = DAY_OPEN + timedelta(hours=5)
    eod_flatten_at: datetime | None = None
    eod_close_at: datetime | None = None


def _remove():
    with SessionLocal.begin() as s:
        ids = select(Trade.trade_id).where(Trade.strategy_name == NAME)
        orders = select(Order.client_order_id).where(Order.trade_id.in_(ids))
        positions = select(Position.position_id).where(Position.trade_id.in_(ids))
        s.query(PositionFillReceipt).filter(PositionFillReceipt.position_id.in_(positions)).delete(synchronize_session=False)
        s.query(ExitRequest).filter(ExitRequest.position_id.in_(positions)).delete(synchronize_session=False)
        s.query(Fill).filter(Fill.client_order_id.in_(orders)).delete(synchronize_session=False)
        s.query(Order).filter(Order.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Position).filter(Position.trade_id.in_(ids)).delete(synchronize_session=False)
        s.query(Trade).filter(Trade.strategy_name == NAME).delete(synchronize_session=False)
        s.query(PortfolioStateCursor).filter(PortfolioStateCursor.execution_mode == "simulated").delete()


@pytest.fixture(autouse=True)
def clean():
    _remove()
    yield
    _remove()


def seeded(*, qty=5, opened_at=DAY_OPEN, symbol="ZZSESS"):
    with SessionLocal.begin() as s:
        trade = Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=NAME,
                      strategy_version="v1", direction="BUY", symbol=symbol, decision="approved", status="open")
        s.add(trade)
        s.flush()
        position = Position(trade_id=trade.trade_id, execution_mode="simulated", execution_venue="simulated",
                            symbol=symbol, side="BUY", qty=qty, avg_price=100, stop=90, target=120,
                            opened_at=opened_at, status="open")
        s.add(position)
        s.flush()
        s.add(Order(client_order_id=f"{trade.trade_id}:entry", trade_id=trade.trade_id,
                    execution_mode="simulated", execution_venue="simulated", symbol=symbol,
                    side="BUY", position_effect="open", qty=qty, order_type="market", status="filled"))
        return trade.trade_id, position.position_id


def make(now):
    clock = Clock(now)
    return clock, PostgresExitLedger(SessionLocal, clock=clock, eod_lead_seconds=60)


def orders_of(position_id):
    with SessionLocal() as s:
        return s.scalars(select(Order).where(Order.position_id == position_id, Order.position_effect == "close")
                         .order_by(Order.id)).all()


def attempt_of(position_id):
    with SessionLocal() as s:
        return s.get(Position, position_id).exit_attempt


def reserve_and_reject(ledger, position_id, reason="outside_regular_session"):
    prepared = ledger.prepare_exit(position_id)
    assert prepared.disposition is P.SUBMIT
    claim = ledger.claim_dispatch(prepared.action.client_order_id)
    assert claim.disposition is C.CLAIMED
    assert ledger.set_status(prepared.action.client_order_id, "rejected", reason=reason)
    return prepared.action.client_order_id


# --------------------------------------------------------------------------
# Ordinary close and half-day boundaries (exact, inclusive/exclusive)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("reason", ["stop", "target"])
@pytest.mark.parametrize("now, expected", [
    (CLOSE - MICRO, P.SUBMIT),                 # last regular microsecond
    (CLOSE, P.WAIT_OUTSIDE_REGULAR_SESSION),   # 16:00:00 ET: closed
    (CLOSE + timedelta(hours=2), P.WAIT_OUTSIDE_REGULAR_SESSION),
    (NEXT_OPEN - MICRO, P.WAIT_OUTSIDE_REGULAR_SESSION),
    (NEXT_OPEN, P.SUBMIT),                     # 09:30:00 ET: open
])
def test_ordinary_close_boundaries(reason, now, expected):
    _, pid = seeded()
    _, ledger = make(now)
    assert ledger.observe_exit(Obs(pid, reason)).disposition is O.STORED
    result = ledger.prepare_exit(pid)
    assert result.disposition is expected
    if expected is P.SUBMIT:
        assert result.action.exit_reason == reason and result.action.client_order_id.endswith(":exit:1")
        assert attempt_of(pid) == 1
    else:
        assert result.action is None and orders_of(pid) == [] and attempt_of(pid) == 0
        assert ledger.slot_state(pid) is not None and pid in ledger.pending_exit_position_ids()
        assert pid in ledger.pending_position_ids()  # the legacy worker surface still sees it


@pytest.mark.parametrize("now, expected", [
    (HALF_CLOSE - MICRO, P.SUBMIT),                # 12:59:59.999999 ET
    (HALF_CLOSE, P.WAIT_OUTSIDE_REGULAR_SESSION),  # 13:00:00 ET: half-day close
    (HALF_CLOSE + timedelta(hours=1), P.WAIT_OUTSIDE_REGULAR_SESSION),  # 14:00 ET is NOT regular on a half-day
    (HALF_NEXT_OPEN - MICRO, P.WAIT_OUTSIDE_REGULAR_SESSION),
    (HALF_NEXT_OPEN, P.SUBMIT),
])
def test_half_day_closes_at_13_00_et(now, expected):
    _, pid = seeded(opened_at=HALF_OPENED)
    _, ledger = make(now)
    assert ledger.observe_exit(Obs(pid, "stop", trigger_ts=HALF_OPENED + timedelta(hours=1))).disposition is O.STORED
    assert ledger.prepare_exit(pid).disposition is expected


def test_weekend_and_holiday_are_outside_regular_hours():
    for when in (datetime(2026, 9, 19, 15, 0, tzinfo=UTC),   # Saturday 11:00 ET
                 datetime(2026, 9, 7, 15, 0, tzinfo=UTC)):   # Labor Day 11:00 ET
        _, pid = seeded()
        _, ledger = make(when)
        assert ledger.observe_exit(Obs(pid)).disposition is O.STORED
        assert ledger.prepare_exit(pid).disposition is P.WAIT_OUTSIDE_REGULAR_SESSION
        assert orders_of(pid) == []
        _remove()


# --------------------------------------------------------------------------
# The documented defect: rejected after the bell, then service passes overnight
# --------------------------------------------------------------------------

def test_rejected_after_the_bell_mints_no_new_attempt_overnight_then_one_at_the_open():
    _, pid = seeded()
    clock, ledger = make(CLOSE - timedelta(seconds=30))
    assert ledger.observe_exit(Obs(pid, "stop")).disposition is O.STORED
    first = reserve_and_reject(ledger, pid)             # 19:59:30Z; venue clock already past the bell
    assert first.endswith(":exit:1") and attempt_of(pid) == 1

    passes = 0
    clock.now = CLOSE + timedelta(seconds=1)
    while clock.now < NEXT_OPEN - timedelta(seconds=1):  # every 6 s for a stretch, then hourly
        result = ledger.prepare_exit(pid)
        assert result.disposition is P.WAIT_OUTSIDE_REGULAR_SESSION and result.action is None
        passes += 1
        clock.now += timedelta(seconds=6) if passes < 50 else timedelta(minutes=45)
    clock.now = NEXT_OPEN - MICRO
    assert ledger.prepare_exit(pid).disposition is P.WAIT_OUTSIDE_REGULAR_SESSION
    assert passes >= 50

    closes = orders_of(pid)                              # actual rejection history preserved
    assert [o.client_order_id for o in closes] == [first]
    assert closes[0].status == "rejected" and closes[0].reject_reason == "outside_regular_session"
    assert attempt_of(pid) == 1

    clock.now = NEXT_OPEN                                # exactly one permitted attempt
    resumed = ledger.prepare_exit(pid)
    assert resumed.disposition is P.SUBMIT and resumed.action.client_order_id.endswith(":exit:2")
    assert ledger.prepare_exit(pid).action.client_order_id == resumed.action.client_order_id  # reused, not re-minted
    assert [o.client_order_id.rsplit(":", 1)[1] for o in orders_of(pid)] == ["1", "2"] and attempt_of(pid) == 2
    assert ledger.claim_dispatch(resumed.action.client_order_id).disposition is C.CLAIMED


def test_ids_stay_monotonic_and_normal_retry_delay_applies_again_in_regular_hours():
    _, pid = seeded()
    clock, ledger = make(CLOSE - timedelta(seconds=30))
    ledger.observe_exit(Obs(pid))
    reserve_and_reject(ledger, pid)                      # :exit:1
    clock.now = NEXT_OPEN
    assert reserve_and_reject(ledger, pid).endswith(":exit:2")
    clock.now = NEXT_OPEN + timedelta(seconds=3)
    assert ledger.prepare_exit(pid).disposition is P.WAIT_RETRY_DELAY   # existing 5 s delay, unchanged
    clock.now = NEXT_OPEN + timedelta(seconds=6)
    assert reserve_and_reject(ledger, pid).endswith(":exit:3")
    assert [o.status for o in orders_of(pid)] == ["rejected"] * 3


def test_original_stop_observed_outside_hours_is_retained_and_resumes_at_the_open():
    _, pid = seeded()
    clock, ledger = make(CLOSE + timedelta(hours=2))
    assert ledger.observe_exit(Obs(pid, "target")).disposition is O.STORED
    for _ in range(5):
        assert ledger.prepare_exit(pid).disposition is P.WAIT_OUTSIDE_REGULAR_SESSION
    assert orders_of(pid) == [] and attempt_of(pid) == 0
    clock.now = NEXT_OPEN
    result = ledger.prepare_exit(pid)
    assert result.disposition is P.SUBMIT and result.action.exit_reason == "target"
    assert result.action.client_order_id.endswith(":exit:1")


# --------------------------------------------------------------------------
# Exclusivity: a prior active or uncertain close is checked first
# --------------------------------------------------------------------------

def test_submitted_close_stays_exclusive_outside_hours():
    _, pid = seeded()
    clock, ledger = make(CLOSE - timedelta(seconds=30))
    ledger.observe_exit(Obs(pid))
    prepared = ledger.prepare_exit(pid)
    assert ledger.claim_dispatch(prepared.action.client_order_id).disposition is C.CLAIMED
    assert ledger.set_status(prepared.action.client_order_id, "submitted", venue_order_id="sim-1")
    for when in (CLOSE + timedelta(minutes=30), NEXT_OPEN - MICRO, NEXT_OPEN):
        clock.now = when
        assert ledger.prepare_exit(pid).disposition is P.WAIT_ACTIVE_ORDER   # existing policy, not the session wait
    assert len(orders_of(pid)) == 1 and orders_of(pid)[0].status == "submitted"


def test_dispatch_uncertain_eod_close_stays_exclusive_and_is_not_cancelled_outside_hours():
    _, pid = seeded()
    clock, ledger = make(CLOSE - timedelta(seconds=30))
    assert ledger.observe_exit(Obs(pid, "eod_flatten", trigger_price=101.0)).disposition is O.STORED
    eod = ledger.prepare_exit(pid)
    assert eod.disposition is P.SUBMIT and eod.action.exit_reason == "eod_flatten"
    assert ledger.claim_dispatch(eod.action.client_order_id).disposition is C.CLAIMED    # marker committed
    assert ledger.observe_exit(Obs(pid, "stop")).disposition is O.FALLBACK_STORED
    for when in (CLOSE + timedelta(minutes=30), NEXT_OPEN):
        clock.now = when
        assert ledger.prepare_exit(pid).disposition is P.WAIT_UNCERTAIN_DISPATCH
    only = orders_of(pid)
    assert len(only) == 1 and only[0].status == "approved" and only[0].exit_dispatch_started_at is not None


# --------------------------------------------------------------------------
# EOD control cases: fallback is held; original placement rule is untouched
# --------------------------------------------------------------------------

def test_eod_fallback_after_expiry_waits_for_regular_hours_then_places_once():
    _, pid = seeded()
    clock, ledger = make(CLOSE - timedelta(seconds=50))
    assert ledger.observe_exit(Obs(pid, "eod_flatten", trigger_price=101.0)).disposition is O.STORED
    clock.now = CLOSE + timedelta(seconds=30)            # EOD placement expired; first stop is the durable fallback
    assert ledger.observe_exit(Obs(pid, "stop")).disposition is O.FALLBACK_STORED
    for when in (CLOSE + timedelta(seconds=31), CLOSE + timedelta(hours=3), NEXT_OPEN - MICRO):
        clock.now = when
        assert ledger.prepare_exit(pid).disposition is P.WAIT_OUTSIDE_REGULAR_SESSION
    assert orders_of(pid) == [] and attempt_of(pid) == 0
    slot = ledger.slot_state(pid)
    assert slot.eod_expired and slot.fallback_reason == "stop"

    clock.now = NEXT_OPEN
    result = ledger.prepare_exit(pid)
    assert result.disposition is P.SUBMIT and result.action.exit_reason == "stop"
    assert result.action.client_order_id.endswith(":exit:1")
    claim = ledger.claim_dispatch(result.action.client_order_id)
    assert claim.disposition is C.CLAIMED
    assert orders_of(pid)[0].exit_dispatch_started_at is not None   # fallback keeps its durable marker


def test_eod_original_inside_its_window_is_not_affected_by_the_guard():
    _, pid = seeded()
    _, ledger = make(CLOSE - timedelta(seconds=10))
    assert ledger.observe_exit(Obs(pid, "eod_flatten", trigger_price=101.0)).disposition is O.STORED
    result = ledger.prepare_exit(pid)
    assert result.disposition is P.SUBMIT and result.action.exit_reason == "eod_flatten"
    assert ledger.claim_dispatch(result.action.client_order_id).disposition is C.CLAIMED


# --------------------------------------------------------------------------
# Final guard: the boundary can pass between prepare and claim
# --------------------------------------------------------------------------

def test_claim_after_the_bell_sends_nothing_writes_no_marker_and_the_reservation_is_reused():
    _, pid = seeded()
    clock, ledger = make(CLOSE - timedelta(seconds=2))
    ledger.observe_exit(Obs(pid))
    prepared = ledger.prepare_exit(pid)
    assert prepared.disposition is P.SUBMIT
    clock.now = CLOSE
    claim = ledger.claim_dispatch(prepared.action.client_order_id)
    assert claim.disposition is C.WAIT_OUTSIDE_REGULAR_SESSION and not claim.send
    assert ledger.prepare_exit(pid).disposition is P.WAIT_OUTSIDE_REGULAR_SESSION   # unsent reservation is held too
    held = orders_of(pid)
    assert len(held) == 1 and held[0].status == "approved" and held[0].exit_dispatch_started_at is None
    clock.now = NEXT_OPEN
    again = ledger.prepare_exit(pid)
    assert again.disposition is P.SUBMIT and again.action.client_order_id == prepared.action.client_order_id
    assert attempt_of(pid) == 1 and ledger.claim_dispatch(again.action.client_order_id).disposition is C.CLAIMED


def test_fallback_claim_at_the_next_close_is_held_without_a_dispatch_marker():
    _, pid = seeded()
    clock, ledger = make(CLOSE - timedelta(seconds=50))
    ledger.observe_exit(Obs(pid, "eod_flatten", trigger_price=101.0))
    clock.now = CLOSE + timedelta(seconds=30)
    ledger.observe_exit(Obs(pid, "stop"))
    next_close = datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
    clock.now = next_close - timedelta(seconds=1)
    prepared = ledger.prepare_exit(pid)
    assert prepared.disposition is P.SUBMIT
    clock.now = next_close
    assert ledger.claim_dispatch(prepared.action.client_order_id).disposition is C.WAIT_OUTSIDE_REGULAR_SESSION
    assert orders_of(pid)[0].exit_dispatch_started_at is None


# --------------------------------------------------------------------------
# Resume runs the fresh committed-position / entry / pending-fill checks
# --------------------------------------------------------------------------

def _held_overnight(qty=5):
    _, pid = seeded(qty=qty)
    clock, ledger = make(CLOSE + timedelta(hours=1))
    ledger.observe_exit(Obs(pid))
    assert ledger.prepare_exit(pid).disposition is P.WAIT_OUTSIDE_REGULAR_SESSION
    return clock, ledger, pid


def test_resume_sizes_the_close_from_the_committed_quantity_at_that_moment():
    clock, ledger, pid = _held_overnight(qty=5)
    with SessionLocal.begin() as s:
        s.get(Position, pid).qty = 3                       # accounting changed while the request waited
    clock.now = NEXT_OPEN
    result = ledger.prepare_exit(pid)
    assert result.disposition is P.SUBMIT and result.action.qty == 3
    assert orders_of(pid)[0].qty == 3


def test_resume_waits_for_a_committed_fill_without_its_position_receipt():
    clock, ledger, pid = _held_overnight()
    with SessionLocal.begin() as s:
        trade_id = s.get(Position, pid).trade_id
        s.add(Fill(client_order_id=f"{trade_id}:entry", execution_venue="simulated",
                   venue_fill_id=f"{trade_id}:late", qty=1, price=100, venue_ts=CLOSE))
    clock.now = NEXT_OPEN
    assert ledger.prepare_exit(pid).disposition is P.WAIT_PENDING_FILL
    assert orders_of(pid) == [] and attempt_of(pid) == 0


def test_resume_cancels_a_working_entry_before_any_close():
    clock, ledger, pid = _held_overnight()
    with SessionLocal.begin() as s:
        trade_id = s.get(Position, pid).trade_id
        s.add(Order(client_order_id=f"{trade_id}:entry2", trade_id=trade_id, execution_mode="simulated",
                    execution_venue="simulated", symbol="ZZSESS", side="BUY", position_effect="open",
                    qty=2, order_type="market", status="submitted"))
    clock.now = NEXT_OPEN
    result = ledger.prepare_exit(pid)
    assert result.disposition is P.CANCEL_ENTRY and result.action.client_order_id.endswith(":entry2")
    assert orders_of(pid) == [] and attempt_of(pid) == 0


def test_flat_position_during_the_wait_is_inert_at_the_open():
    clock, ledger, pid = _held_overnight()
    with SessionLocal.begin() as s:
        p = s.get(Position, pid)
        p.qty, p.status = 0, "closed"
    clock.now = NEXT_OPEN
    assert ledger.prepare_exit(pid).disposition is P.POSITION_CLOSED
    assert orders_of(pid) == []


# --------------------------------------------------------------------------
# Real ExecutionEngine worker + real SimulatedVenue
# --------------------------------------------------------------------------

class VenueClock(MarketClock):
    """The venue judges the same injected wall clock the ledger does."""

    def __init__(self, wall):
        super().__init__()
        self.wall = wall
        self.force_closed = False

    def is_regular_session(self, ts=None):
        return (not self.force_closed) and super().is_regular_session(ts or self.wall())


class Provider:
    def __init__(self, venue):
        self.venue = venue

    def get_execution_venue(self):
        return self.venue


class Harness:
    async def build(self, wall, symbol):
        self.trade_id, _ = None, None
        with SessionLocal.begin() as s:
            trade = Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=NAME,
                          strategy_version="v1", direction="BUY", symbol=symbol, decision="approved", status="open")
            s.add(trade)
            s.flush()
            entry_id = f"{trade.trade_id}:entry"
            s.add(Order(client_order_id=entry_id, trade_id=trade.trade_id, execution_mode="simulated",
                        execution_venue="simulated", symbol=symbol, side="BUY", position_effect="open",
                        qty=5, order_type="market", status="filled"))
            s.flush()
            s.add(Fill(client_order_id=entry_id, execution_venue="simulated", venue_fill_id=f"{entry_id}:f1",
                       qty=5, price=100, venue_ts=DAY_OPEN))
            self.trade_id = trade.trade_id
        self.symbol = symbol
        self.wall = wall
        self.venue_clock = VenueClock(wall)
        self.bus = EventBus()
        await self.bus.start()
        self.venue = SimulatedVenue(clock=self.venue_clock)
        await self.venue.connect()
        self.place_calls, self.cancel_calls = [], []
        real_place, real_cancel = self.venue.place_order, self.venue.cancel_order

        async def place(instruction):
            self.place_calls.append(instruction.client_order_id)
            return await real_place(instruction)

        async def cancel(order_id):
            self.cancel_calls.append(order_id)
            return await real_cancel(order_id)

        self.venue.place_order, self.venue.cancel_order = place, cancel
        self.portfolio = PortfolioState("simulated", ledger=PostgresPositionLedger(SessionLocal), bus=self.bus)
        await self.portfolio.start()
        await self.portfolio.refresh()
        with SessionLocal.begin() as s:
            position = s.scalar(select(Position).where(Position.trade_id == self.trade_id))
            position.stop, position.target = 90, 120
            self.pid = position.position_id
        await self.portfolio.refresh()
        self.ledger = PostgresExitLedger(SessionLocal, clock=wall, eod_lead_seconds=60)
        order_ledger = PostgresOrderLedger(SessionLocal)
        self.engine = ExecutionEngine(self.bus, order_ledger, order_ledger, fill_ledger=PostgresFillLedger(SessionLocal),
                                      exit_ledger=self.ledger, portfolio_state=self.portfolio,
                                      venue_provider=Provider(self.venue))
        self.engine.start()
        return self

    async def close(self):
        await self.engine.stop()
        await self.portfolio.stop()
        await self.venue.disconnect()
        await self.bus.stop()

    def observe(self, reason="stop"):
        intent = ExitIntent(self.pid, self.symbol, "BUY", 5, reason, 89 if reason != "target" else 121,
                            DAY_OPEN + timedelta(hours=5))
        assert self.ledger.observe_exit(intent).acknowledged


@pytest.mark.asyncio
async def test_engine_overnight_passes_make_no_venue_call_and_one_attempt_at_the_open(caplog):
    wall = Clock(CLOSE - timedelta(seconds=30))
    h = await Harness().build(wall, "ZZSESSENG")
    try:
        caplog.set_level(logging.WARNING)
        h.venue_clock.force_closed = True                # the venue's own clock is past the bell
        h.observe("stop")
        await h.engine._service_exits()                  # in-session per the ledger: attempt 1 is rejected by the venue
        closes = orders_of(h.pid)
        assert [o.status for o in closes] == ["rejected"] and closes[0].reject_reason == "outside_regular_session"
        assert len(h.place_calls) == 1
        first_id = closes[0].client_order_id

        for step in range(47):                           # repeated service passes overnight, all before 13:30Z
            wall.now = CLOSE + (timedelta(seconds=6 * (step + 1)) if step < 30 else timedelta(hours=step - 29))
            assert wall.now < NEXT_OPEN
            await h.engine._service_exits()
        wall.now = NEXT_OPEN - MICRO
        await h.engine._service_exits()
        closes = orders_of(h.pid)
        assert [o.client_order_id for o in closes] == [first_id] and attempt_of(h.pid) == 1
        assert h.place_calls == [first_id] and h.cancel_calls == []
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]   # a quiet wait, not an error loop

        h.venue_clock.force_closed = False               # regular hours return
        wall.now = NEXT_OPEN
        await h.engine._service_exits()
        closes = orders_of(h.pid)
        assert [o.status for o in closes] == ["rejected", "submitted"]
        assert closes[1].client_order_id.endswith(":exit:2") and closes[0].reject_reason == "outside_regular_session"
        assert len(h.place_calls) == 2
        await h.engine._service_exits()
        assert len(orders_of(h.pid)) == 2 and len(h.place_calls) == 2   # the submitted close is exclusive
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_engine_original_stop_observed_after_the_bell_never_touches_the_venue_until_the_open():
    wall = Clock(CLOSE + timedelta(hours=1))
    h = await Harness().build(wall, "ZZSESSORIG")
    try:
        h.observe("target")
        for _ in range(8):
            await h.engine._service_exits()
        assert orders_of(h.pid) == [] and h.place_calls == [] and h.cancel_calls == [] and attempt_of(h.pid) == 0
        assert h.ledger.slot_state(h.pid) is not None
        wall.now = NEXT_OPEN
        await h.engine._service_exits()
        closes = orders_of(h.pid)
        assert len(closes) == 1 and closes[0].exit_reason == "target" and closes[0].status == "submitted"
        assert h.place_calls == [closes[0].client_order_id]
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_engine_boundary_between_prepare_and_claim_sends_nothing_then_reuses_the_reservation():
    wall = Clock(CLOSE - timedelta(seconds=2))
    h = await Harness().build(wall, "ZZSESSRACE")
    try:
        h.observe("stop")
        real_prepare = h.ledger.prepare_exit

        def prepare_then_bell(position_id):
            result = real_prepare(position_id)
            wall.now = CLOSE                              # the bell rings after the reservation commits
            return result

        h.ledger.prepare_exit = prepare_then_bell
        await h.engine._service_exits()
        h.ledger.prepare_exit = real_prepare
        held = orders_of(h.pid)
        assert len(held) == 1 and held[0].status == "approved" and held[0].exit_dispatch_started_at is None
        assert h.place_calls == []
        wall.now = NEXT_OPEN
        await h.engine._service_exits()
        closes = orders_of(h.pid)
        assert [o.client_order_id for o in closes] == [held[0].client_order_id] and closes[0].status == "submitted"
        assert attempt_of(h.pid) == 1 and h.place_calls == [held[0].client_order_id]
    finally:
        await h.close()
