"""
SCRATCH reproduction — NOT a committed test (file name deliberately does not match
`test_*.py`; run it by explicit path). Characterizes CURRENT behaviour; every assertion
passes today. If production behaviour is later fixed, the `*_lost` assertions below are
the ones expected to flip.

Real paths only: real FastAPI lifespan, real EventBus, real PositionMonitor, real
PortfolioState + PostgresPositionLedger, real SimulatedVenue, real Postgres.
The ONLY intervention is a threading.Event gate around the real
`PostgresPositionLedger.pending_fills` call, so Portfolio State's snapshot stays
unavailable (`_ready=False`, `get_snapshot() is None`) for as long as we choose,
instead of racing a few milliseconds of DB work.

Run (from backend/):
    python -m pytest tests/scratch_dropped_trigger_repro.py -q -s
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.governor.engine as governor_engine_module
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.event_bus.bus import get_event_bus
from app.main import app as fastapi_app
from app.models.execution_ledger import ExitRequest, Fill, Order, Position, Trade
from app.portfolio_state.postgres import PostgresPositionLedger
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated
from app.services import broker_registry
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots
from tests.test_main_execution_pipeline import (  # noqa: F401  (database = autouse cleanup fixture)
    NAME, NOW, SYMBOL, _opportunity_envelope, _price_envelope, _wait_for, database,
)

DROP_LOG = "positions unavailable in subscriber; event dropped"
STOP = 90.0
TRIGGER_TS = NOW + timedelta(milliseconds=200)  # distinct from the 100.0 entry tick (equal ts: first tick wins)


class Timeline:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.rows: list[tuple[float, str]] = []

    def mark(self, label: str) -> None:
        self.rows.append((time.monotonic() - self.t0, label))

    def dump(self) -> None:
        print("\n--- timeline ---")
        for t, label in self.rows:
            print(f"  +{t:6.3f}s  {label}")


class Gate:
    """Blocks the FIRST armed call of the real ledger's pending_fills."""

    def __init__(self) -> None:
        self.armed = False
        self.entered = threading.Event()
        self.release = threading.Event()


@pytest.fixture
def gate(monkeypatch):
    g = Gate()
    original = PostgresPositionLedger.pending_fills

    def gated(self, execution_mode, after_cursor):
        if g.armed and not g.entered.is_set():
            g.entered.set()
            assert g.release.wait(20), "scratch gate never released"
        return original(self, execution_mode, after_cursor)

    monkeypatch.setattr(PostgresPositionLedger, "pending_fills", gated)
    yield g
    g.release.set()  # never leave a worker thread parked


@pytest.fixture(autouse=True)
def _session(monkeypatch):
    monkeypatch.setattr(MarketClock, "is_regular_session", lambda self, ts=None: True)
    monkeypatch.setattr(
        governor_engine_module, "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots(market_state={"trend_score": 1.0}, context={"news": []}),
    )


def _tick(price: float, ts) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.PRICE_UPDATED, symbol=SYMBOL,
        payload=PriceUpdated(price=price, size=100, exchange_ts=ts).model_dump(mode="json"),
    )


def _candle(low: float, high: float, ts) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.CANDLE_CLOSED, symbol=SYMBOL,
        payload=CandleClosed(timeframe="1m", open=100.0, high=high, low=low, close=100.0,
                             volume=1000, candle_ts=ts).model_dump(mode="json"),
    )


def _exit_requests(position_id) -> list[ExitRequest]:
    with SessionLocal() as s:
        return list(s.scalars(select(ExitRequest).where(ExitRequest.position_id == position_id)).all())


def _drive_to_dropped_trigger(client, gate, caplog, tl: Timeline, trigger_price: float = 89.0):
    """entry fill committed -> snapshot unavailable -> trigger delivered -> snapshot available."""
    caplog.set_level(logging.ERROR, logger="app.position_monitor.engine")
    monitor = fastapi_app.state.position_monitor
    portfolio = fastapi_app.state.world_view_portfolio_reader
    bus = get_event_bus()

    client.portal.call(bus.publish, _price_envelope())
    client.portal.call(bus.publish, _opportunity_envelope())

    def entry_state():
        with SessionLocal() as s:
            trade = s.scalar(select(Trade).where(Trade.strategy_name == NAME))
            order = None if trade is None else s.scalar(select(Order).where(Order.trade_id == trade.trade_id))
            return (None if trade is None else trade.decision, None if order is None else order.status)

    _wait_for(lambda: entry_state() == ("approved", "submitted"), "entry submitted", describe=entry_state)
    tl.mark("entry order submitted at venue")

    gate.armed = True
    venue = broker_registry.get_execution_venue()
    client.portal.call(venue.ingest_tick, SYMBOL, 100.0, NOW)

    # Portfolio State's worker is now parked inside the real ledger read that follows OrderFilled.
    assert gate.entered.wait(10), "Portfolio State never started syncing the fill"
    with SessionLocal() as s:
        trade = s.scalar(select(Trade).where(Trade.strategy_name == NAME))
        fills = s.scalar(select(func.count()).select_from(Fill))
        position_rows = s.scalar(select(func.count()).select_from(Position).where(Position.trade_id == trade.trade_id))
    assert fills == 1, "entry fill must already be committed in the execution ledger"
    assert position_rows == 0, "Portfolio State has not applied the fill yet"
    assert portfolio.get_snapshot() is None, "snapshot must be unavailable"
    assert monitor.get_exit_intents() == ()
    tl.mark("entry fill COMMITTED (fills=1, positions=0); Portfolio State parked; get_snapshot() is None")

    client.portal.call(bus.publish, _tick(trigger_price, TRIGGER_TS))
    _wait_for(lambda: any(DROP_LOG in r.getMessage() for r in caplog.records),
              "monitor logs the dropped event", describe=lambda: [r.getMessage() for r in caplog.records])
    tl.mark(f"TRIGGER tick {trigger_price} (stop={STOP}) delivered -> monitor logged 'event dropped'")
    cached = monitor._tick_cache.get(SYMBOL)
    assert cached is not None and cached.price == trigger_price, "tick was cached (EOD use only), not evaluated"
    assert monitor.get_exit_intents() == () and monitor.pending_observations() == ()

    gate.release.set()

    def visible():
        snap = portfolio.get_snapshot()
        return snap is not None and any(p.symbol == SYMBOL and p.qty > 0 for p in snap.positions.values())

    _wait_for(visible, "snapshot available with the open position")
    tl.mark("snapshot AVAILABLE, position visible (stop=90, price already below it)")
    with SessionLocal() as s:
        position = s.scalar(select(Position).where(Position.trade_id == trade.trade_id))
        assert position.status == "open" and float(position.stop) == STOP
    return monitor, portfolio, bus, trade, position, venue


def _quiet_window(monitor, position, tl: Timeline, seconds: float = 3.5) -> None:
    """Bounded NEGATIVE window: >= 3 real 1s timer pulses and no further market event."""
    time.sleep(seconds)
    assert _exit_requests(position.position_id) == []
    assert monitor.get_exit_intents() == () and monitor.pending_observations() == ()
    tl.mark(f"after {seconds}s with no second trigger: exit_requests=0, exit_intents=0, pending=0")


def test_1_dropped_trigger_is_lost_without_a_second_event(gate, caplog):
    tl = Timeline()
    with TestClient(fastapi_app) as client:
        monitor, _, _, trade, position, _ = _drive_to_dropped_trigger(client, gate, caplog, tl)
        _quiet_window(monitor, position, tl)
        with SessionLocal() as s:
            assert s.scalar(select(func.count()).select_from(Order).where(Order.trade_id == trade.trade_id)) == 1
            assert s.get(Position, position.position_id).status == "open"
        tl.mark("only the entry order exists; position still open and unprotected")
    tl.dump()


def test_2_later_tick_still_beyond_stop_recovers(gate, caplog):
    tl = Timeline()
    with TestClient(fastapi_app) as client:
        monitor, _, bus, trade, position, _ = _drive_to_dropped_trigger(client, gate, caplog, tl)
        _quiet_window(monitor, position, tl)
        later = NOW + timedelta(seconds=5)
        client.portal.call(bus.publish, _tick(88.5, later))
        _wait_for(lambda: len(_exit_requests(position.position_id)) == 1, "exit request from the later tick")
        req = _exit_requests(position.position_id)[0]
        assert req.exit_reason == "stop" and float(req.trigger_price) == STOP
        assert req.trigger_ts == later, "recorded trigger time is the LATER tick, not the dropped one"
        tl.mark(f"later tick 88.5 @ +5s -> exit request created (trigger_ts={req.trigger_ts.isoformat()}, original lost)")
    tl.dump()


def test_3_price_that_reverted_before_next_event_is_lost_forever(gate, caplog):
    tl = Timeline()
    with TestClient(fastapi_app) as client:
        monitor, _, bus, trade, position, _ = _drive_to_dropped_trigger(client, gate, caplog, tl)
        _quiet_window(monitor, position, tl)
        client.portal.call(bus.publish, _tick(95.0, NOW + timedelta(seconds=5)))  # back above stop
        time.sleep(1.5)
        assert _exit_requests(position.position_id) == []
        assert monitor.get_exit_intents() == ()
        tl.mark("price recovered to 95 (> stop 90): stop breach is permanently unobserved; no exit ever")
    tl.dump()


def test_4_next_candle_close_recovers_late_and_unbounded_by_entry_time(gate, caplog):
    tl = Timeline()
    with TestClient(fastapi_app) as client:
        monitor, _, bus, trade, position, _ = _drive_to_dropped_trigger(client, gate, caplog, tl)
        _quiet_window(monitor, position, tl)
        # (a) a candle that closes after the position exists and whose low pierced the stop
        client.portal.call(bus.publish, _candle(low=88.0, high=101.0, ts=NOW))
        _wait_for(lambda: len(_exit_requests(position.position_id)) == 1, "exit request from the candle")
        tl.mark("CandleClosed(low=88) -> exit request created (recovery is incidental and candle-latency late)")
    tl.dump()


def test_5_adjacent_candle_with_pre_entry_range_also_triggers(gate, caplog):
    """Adjacent observation (not fixed here): _evaluate() never checks bar time vs opened_at."""
    tl = Timeline()
    with TestClient(fastapi_app) as client:
        monitor, _, bus, trade, position, _ = _drive_to_dropped_trigger(client, gate, caplog, tl, trigger_price=95.0)
        # A bar stamped an hour BEFORE entry; its low pierces the stop. The trigger tick above was benign (95).
        client.portal.call(bus.publish, _candle(low=88.0, high=101.0, ts=position.opened_at - timedelta(hours=1)))
        _wait_for(lambda: len(_exit_requests(position.position_id)) == 1, "exit request from a pre-entry bar")
        tl.mark("pre-entry candle (opened_at-1h, low=88) -> exit request created: candle replay is not entry-bounded")
    tl.dump()


# --- Ungated: is the race reachable on the real, un-instrumented path? ---------------
@pytest.mark.parametrize("run", range(8))
def test_6_ungated_race_trigger_published_as_soon_as_fill_row_is_visible(caplog, run):
    """No gate. Adversarial timing: the trigger tick is published the instant the entry
    fill row is visible in the execution ledger. Consistency check: dropped <=> no exit."""
    caplog.set_level(logging.ERROR, logger="app.position_monitor.engine")
    with TestClient(fastapi_app) as client:
        monitor = fastapi_app.state.position_monitor
        portfolio = fastapi_app.state.world_view_portfolio_reader
        bus = get_event_bus()
        client.portal.call(bus.publish, _price_envelope())
        client.portal.call(bus.publish, _opportunity_envelope())

        def submitted():
            with SessionLocal() as s:
                t = s.scalar(select(Trade).where(Trade.strategy_name == NAME))
                o = None if t is None else s.scalar(select(Order).where(Order.trade_id == t.trade_id))
                return o is not None and o.status == "submitted"

        _wait_for(submitted, "entry submitted")
        venue = broker_registry.get_execution_venue()
        client.portal.call(venue.ingest_tick, SYMBOL, 100.0, NOW)

        def fill_rows():
            with SessionLocal() as s:
                return s.scalar(select(func.count()).select_from(Fill))

        t_fill = None
        while True:
            if fill_rows() == 1:
                t_fill = time.monotonic()
                break
        snap_none_at_publish = portfolio.get_snapshot() is None
        client.portal.call(bus.publish, _tick(89.0, TRIGGER_TS))
        t_pub = time.monotonic()

        def visible():
            snap = portfolio.get_snapshot()
            return snap is not None and any(p.symbol == SYMBOL and p.qty > 0 for p in snap.positions.values())

        _wait_for(visible, "position visible")
        t_vis = time.monotonic()
        time.sleep(1.2)
        with SessionLocal() as s:
            pos = s.scalar(select(Position).join(Trade, Trade.trade_id == Position.trade_id).where(Trade.strategy_name == NAME))
            exits = len(_exit_requests(pos.position_id))
        dropped = any(DROP_LOG in r.getMessage() for r in caplog.records)
        print(f"\nrun={run} snapshot_None_at_publish={snap_none_at_publish} dropped={dropped} "
              f"exit_requests={exits} fill_visible->tick_published={1000*(t_pub-t_fill):.1f}ms "
              f"fill_visible->snapshot_visible={1000*(t_vis-t_fill):.1f}ms")
        assert (exits == 0) if dropped else (exits == 1), "dropped <=> no exit request"
