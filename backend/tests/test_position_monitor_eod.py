"""
Focused tests for Position Monitor's simulated-EOD timer, tick cache and
pending/acknowledged observation handoff (`simulated-eod-monitor-handoff`,
decision #185, design doc §6.6, acceptance rows A1/A2/A11-A14).

No database, no Execution Engine, no exit ledger: a fake `PositionReader`, an
injected wall clock, a fixed `MarketClock`, and `pulse_interval_seconds=None`
so every pulse is enqueued explicitly and deterministically. Market events are
usually delivered by calling the subscriber callback directly so a test can
control arrival order relative to a pulse without yielding to the worker.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.market_clock import MarketClock
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.position_monitor.engine import ExitIntent, PositionMonitor
from app.position_monitor.handoff import Observation, ObservationState, ReleaseReason
from app.position_monitor.ports import PositionView
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated

UTC = timezone.utc
# Tuesday 2026-09-22: regular day, close 16:00 EDT = 20:00 UTC, lead 60 s.
OPENED = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)
FLATTEN = datetime(2026, 9, 22, 19, 59, tzinfo=UTC)
CLOSE = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)
CLOCK = MarketClock()


@dataclass
class FakeReader:
    positions: list[PositionView] = field(default_factory=list)

    def get_open_positions(self) -> tuple[PositionView, ...]:
        return tuple(self.positions)


class WallClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def position(**overrides) -> PositionView:
    base = dict(position_id=uuid4(), symbol="AAPL", side="BUY", qty=10, stop=50.0, target=500.0, opened_at=OPENED)
    base.update(overrides)
    return PositionView(**base)


def tick(symbol: str, price: float, ts: datetime) -> EventEnvelope:
    return make_envelope(EventType.PRICE_UPDATED, PriceUpdated(price=price, size=1, exchange_ts=ts), symbol=symbol)


def raw_tick(symbol: str, payload: dict) -> EventEnvelope:
    return EventEnvelope(event_type=EventType.PRICE_UPDATED, version=1, symbol=symbol, payload=payload)


def candle(symbol: str, *, high: float, low: float, close: float, ts: datetime) -> EventEnvelope:
    return make_envelope(
        EventType.CANDLE_CLOSED,
        CandleClosed(timeframe="1m", open=close, high=high, low=low, close=close, volume=1, candle_ts=ts),
        symbol=symbol,
    )


@pytest.fixture
async def make():
    made: list[PositionMonitor] = []

    def factory(positions, now=FLATTEN, *, lead=60, **kwargs):
        reader = FakeReader(list(positions))
        wall = WallClock(now)
        monitor = PositionMonitor(
            EventBus(), reader, clock=CLOCK, wall_clock=wall, eod_lead_seconds=lead,
            pulse_interval_seconds=kwargs.pop("pulse_interval_seconds", None), **kwargs,
        )
        monitor.start()
        made.append(monitor)
        return monitor, reader, wall

    yield factory
    for monitor in made:
        await monitor.stop()


async def drain(monitor: PositionMonitor) -> None:
    await asyncio.wait_for(monitor._queue.join(), timeout=2)


async def pulse(monitor: PositionMonitor) -> None:
    assert monitor.enqueue_pulse()
    await drain(monitor)


async def feed(monitor: PositionMonitor, *envelopes: EventEnvelope) -> None:
    for envelope in envelopes:
        monitor._on_market_event(envelope)
    await drain(monitor)


def kinds(monitor: PositionMonitor) -> list[str]:
    return [o.kind for o in monitor.get_observations()]


# --- ExitIntent: additive extension ------------------------------------------


def test_exit_intent_stop_target_construction_unchanged() -> None:
    intent = ExitIntent(uuid4(), "AAPL", "BUY", 1, "stop", 95.0, OPENED)
    assert intent.eod_flatten_at is None and intent.eod_close_at is None


def test_exit_intent_eod_fields_only_for_eod_and_ordered_utc() -> None:
    pid = uuid4()
    ok = ExitIntent(pid, "AAPL", "BUY", 1, "eod_flatten", 100.0, OPENED, eod_flatten_at=FLATTEN, eod_close_at=CLOSE)
    assert ok.eod_close_at == CLOSE
    with pytest.raises(ValueError):
        ExitIntent(pid, "AAPL", "BUY", 1, "stop", 95.0, OPENED, eod_flatten_at=FLATTEN, eod_close_at=CLOSE)
    with pytest.raises(ValueError):
        ExitIntent(pid, "AAPL", "BUY", 1, "eod_flatten", 100.0, OPENED)
    with pytest.raises(ValueError):
        ExitIntent(pid, "AAPL", "BUY", 1, "eod_flatten", 100.0, OPENED, eod_flatten_at=CLOSE, eod_close_at=FLATTEN)
    with pytest.raises(ValueError):  # naive
        ExitIntent(pid, "AAPL", "BUY", 1, "eod_flatten", 100.0, OPENED,
                   eod_flatten_at=FLATTEN.replace(tzinfo=None), eod_close_at=CLOSE)
    et = timezone(timedelta(hours=-4))
    with pytest.raises(ValueError):  # aware but not UTC
        ExitIntent(pid, "AAPL", "BUY", 1, "eod_flatten", 100.0, OPENED,
                   eod_flatten_at=FLATTEN.astimezone(et), eod_close_at=CLOSE.astimezone(et))


def test_constructor_validates_lead_and_defaults_to_settings() -> None:
    with pytest.raises(ValueError):
        PositionMonitor(EventBus(), FakeReader(), eod_lead_seconds=0)
    with pytest.raises(ValueError):
        PositionMonitor(EventBus(), FakeReader(), eod_lead_seconds=901)
    with pytest.raises(ValueError):
        PositionMonitor(EventBus(), FakeReader(), pulse_interval_seconds=0)
    assert PositionMonitor(EventBus(), FakeReader())._eod_lead_seconds == 60


# --- boundary pulses (A1) ------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expect_eod"),
    [
        (FLATTEN - timedelta(seconds=1), False),
        (FLATTEN, True),  # inclusive
        (CLOSE - timedelta(microseconds=1), True),
        (CLOSE, False),  # exclusive
        (CLOSE + timedelta(minutes=5), False),
    ],
)
async def test_pulse_boundaries(make, now, expect_eod) -> None:
    pos = position()
    # tick stamped before every tested `now`, so only the window decides
    monitor, _, wall = make([pos], now=FLATTEN - timedelta(seconds=30))
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    wall.now = now
    await pulse(monitor)
    assert kinds(monitor) == (["eod"] if expect_eod else [])


async def test_no_boundary_tick_needed_and_bounds_recorded(make) -> None:
    pos = position()
    monitor, _, wall = make([pos], now=OPENED + timedelta(hours=1))
    await feed(monitor, tick("AAPL", 101.0, OPENED + timedelta(minutes=30)))  # hours before the window
    wall.now = FLATTEN
    await pulse(monitor)
    (obs,) = monitor.get_observations()
    assert obs.intent.exit_reason == "eod_flatten"
    assert obs.intent.trigger_ts == OPENED + timedelta(minutes=30)  # exchange stamp, not wall time
    assert obs.intent.trigger_price == 101.0
    assert (obs.intent.eod_flatten_at, obs.intent.eod_close_at) == (FLATTEN, CLOSE)
    assert obs.state is ObservationState.PENDING


async def test_configured_lead_is_used(make) -> None:
    pos = position()
    monitor, _, wall = make([pos], now=OPENED + timedelta(hours=1), lead=300)
    await feed(monitor, tick("AAPL", 100.0, OPENED + timedelta(minutes=1)))
    wall.now = CLOSE - timedelta(seconds=301)
    await pulse(monitor)
    assert kinds(monitor) == []
    wall.now = CLOSE - timedelta(seconds=300)
    await pulse(monitor)
    assert kinds(monitor) == ["eod"]
    assert monitor.get_observations()[0].intent.eod_flatten_at == CLOSE - timedelta(seconds=300)


async def test_half_day_window_uses_13_00_et(make) -> None:
    opened = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)  # day after Thanksgiving, EST
    close = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    pos = position(opened_at=opened)
    monitor, _, wall = make([pos], now=opened + timedelta(minutes=1))
    await feed(monitor, tick("AAPL", 100.0, opened + timedelta(minutes=1)))
    wall.now = close - timedelta(seconds=61)
    await pulse(monitor)
    assert kinds(monitor) == []
    wall.now = close - timedelta(seconds=60)
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.eod_close_at == close


# --- calendar ------------------------------------------------------------------


async def test_holiday_and_weekend_have_no_window_but_protective_still_works(make) -> None:
    for opened in (datetime(2026, 9, 7, 15, 0, tzinfo=UTC), datetime(2026, 9, 26, 15, 0, tzinfo=UTC)):  # Labor Day; Saturday
        pos = position(opened_at=opened, stop=95.0)
        monitor, _, wall = make([pos], now=opened + timedelta(minutes=1))
        await feed(monitor, tick("AAPL", 100.0, opened + timedelta(minutes=1)))
        wall.now = datetime.combine(opened.date(), datetime.min.time(), tzinfo=UTC) + timedelta(hours=19, minutes=59, seconds=30)
        await pulse(monitor)
        assert kinds(monitor) == []
        await feed(monitor, tick("AAPL", 90.0, opened + timedelta(minutes=2)))
        assert kinds(monitor) == ["protective"]


async def test_unsupported_entry_year_skips_eod_logs_bounded_and_keeps_protective(make, caplog) -> None:
    opened = datetime(2027, 3, 2, 15, 0, tzinfo=UTC)
    pos = position(opened_at=opened, stop=95.0)
    monitor, _, wall = make([pos], now=opened + timedelta(minutes=1))
    with caplog.at_level(logging.WARNING, logger="app.position_monitor.engine"):
        for _ in range(3):
            await pulse(monitor)  # must not raise or kill the worker
    assert kinds(monitor) == []
    assert sum("no EOD calendar coverage" in r.message for r in caplog.records) == 1  # bounded
    await feed(monitor, tick("AAPL", 90.0, opened + timedelta(minutes=2)))
    assert kinds(monitor) == ["protective"]


# --- missing / stale / pre-entry / future ticks (A2, A13) -----------------------


async def test_missing_tick_creates_nothing_and_logs_bounded(make, caplog) -> None:
    pos = position()
    start = CLOSE - timedelta(seconds=900)
    monitor, _, wall = make([pos], now=start, lead=900)  # 15-minute window keeps every step inside it
    with caplog.at_level(logging.WARNING, logger="app.position_monitor.engine"):
        await pulse(monitor)
        await pulse(monitor)
        wall.now = start + timedelta(seconds=30)
        await pulse(monitor)
        assert sum("no eligible post-opening tick" in r.message for r in caplog.records) == 1
        wall.now = start + timedelta(seconds=61)
        await pulse(monitor)
        assert sum("no eligible post-opening tick" in r.message for r in caplog.records) == 2
    assert kinds(monitor) == []


async def test_five_minute_old_post_opening_tick_is_eligible(make) -> None:
    pos = position()
    stamp = FLATTEN - timedelta(minutes=5)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 123.0, stamp))
    await pulse(monitor)
    (obs,) = monitor.get_observations()
    assert (obs.intent.trigger_ts, obs.intent.trigger_price) == (stamp, 123.0)


async def test_pre_entry_same_day_tick_cannot_label(make) -> None:
    pos = position()
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, OPENED - timedelta(seconds=1)))
    await pulse(monitor)
    assert kinds(monitor) == []


async def test_tick_exactly_at_opened_at_is_eligible(make) -> None:
    pos = position()
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, OPENED))
    await pulse(monitor)
    assert kinds(monitor) == ["eod"]


async def test_reopened_symbol_new_position_needs_its_own_post_opening_tick(make) -> None:
    first = position(opened_at=OPENED)
    monitor, reader, _ = make([first], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, OPENED + timedelta(hours=1)))
    reader.positions = [position(opened_at=OPENED + timedelta(hours=3))]  # new UUID, reopened later same day
    await pulse(monitor)
    assert kinds(monitor) == []  # the 15:00 tick predates the new position
    await feed(monitor, tick("AAPL", 101.0, OPENED + timedelta(hours=4)))
    await pulse(monitor)
    assert kinds(monitor) == ["eod"]


async def test_previous_day_tick_cannot_label(make) -> None:
    pos = position(opened_at=datetime(2026, 9, 22, 14, 0, tzinfo=UTC))
    monitor, _, wall = make([pos], now=FLATTEN)
    # stamped after opened_at by construction impossible for an earlier day, so use a next-day
    # stamp: <= wall time requires wall time next day, where the entry-day window is closed.
    wall.now = datetime(2026, 9, 23, 19, 59, 30, tzinfo=UTC)
    await feed(monitor, tick("AAPL", 100.0, datetime(2026, 9, 23, 19, 59, tzinfo=UTC)))
    await pulse(monitor)
    assert kinds(monitor) == []  # no next-day catch-up


async def test_future_naive_and_invalid_ticks_never_enter_cache(make) -> None:
    # No stop/target: the event path still evaluates any tick for protective exits
    # (unchanged behaviour), so isolate the cache-validity rule under test.
    pos = position(stop=None, target=None)
    monitor, _, _ = make([pos], now=FLATTEN)
    good_ts = FLATTEN - timedelta(seconds=10)
    await feed(monitor, tick("AAPL", 100.0, good_ts))
    await feed(
        monitor,
        tick("AAPL", 999.0, FLATTEN + timedelta(seconds=1)),  # future
        raw_tick("AAPL", {"price": 998.0, "size": 1, "exchange_ts": "2026-09-22T19:59:20"}),  # naive
        raw_tick("AAPL", {"price": 0.0, "size": 1, "exchange_ts": (good_ts + timedelta(seconds=5)).isoformat()}),
        raw_tick("AAPL", {"price": -3.0, "size": 1, "exchange_ts": (good_ts + timedelta(seconds=6)).isoformat()}),
        raw_tick("AAPL", {"price": float("nan"), "size": 1, "exchange_ts": (good_ts + timedelta(seconds=7)).isoformat()}),
        raw_tick("AAPL", {"price": float("inf"), "size": 1, "exchange_ts": (good_ts + timedelta(seconds=8)).isoformat()}),
        raw_tick("AAPL", {"garbage": True}),
    )
    await pulse(monitor)
    (obs,) = monitor.get_observations()
    assert (obs.intent.trigger_ts, obs.intent.trigger_price) == (good_ts, 100.0)


# --- cache ordering (A14) --------------------------------------------------------


async def test_older_tick_never_moves_cache_backwards(make) -> None:
    pos = position()
    monitor, _, _ = make([pos], now=FLATTEN)
    newer, older = FLATTEN - timedelta(seconds=10), FLATTEN - timedelta(seconds=20)
    await feed(monitor, tick("AAPL", 101.0, newer), tick("AAPL", 55.0, older))
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.trigger_price == 101.0


async def test_equal_time_first_received_tick_wins_held_path(make) -> None:
    pos = position()
    monitor, _, _ = make([pos], now=FLATTEN)
    ts = FLATTEN - timedelta(seconds=10)
    await feed(monitor, tick("AAPL", 100.0, ts), tick("AAPL", 101.0, ts))
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.trigger_price == 100.0


async def test_equal_time_first_received_tick_wins_unheld_path(make) -> None:
    monitor, reader, _ = make([], now=FLATTEN)  # AAPL not held when the ticks arrive
    ts = FLATTEN - timedelta(seconds=10)
    await feed(monitor, tick("AAPL", 100.0, ts), tick("AAPL", 101.0, ts))
    reader.positions.append(position())
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.trigger_price == 100.0


async def test_unheld_tick_cached_before_filter_labels_position_opened_later(make) -> None:
    monitor, reader, _ = make([], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, OPENED + timedelta(minutes=1)))
    reader.positions.append(position())
    await pulse(monitor)
    assert kinds(monitor) == ["eod"]


async def test_tick_after_queued_pulse_is_later_work_even_when_cached_directly(make) -> None:
    monitor, reader, _ = make([], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    assert monitor.enqueue_pulse()  # queued, not yet processed
    monitor._on_market_event(tick("AAPL", 101.0, FLATTEN - timedelta(seconds=5)))  # unheld -> cached at arrival
    reader.positions.append(position())
    await drain(monitor)
    assert kinds(monitor) == []  # this pulse must not see the later arrival; retries next pulse
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.trigger_price == 101.0


# --- candles (A14) -------------------------------------------------------------------


async def test_candles_never_label_eod_or_overwrite_tick(make) -> None:
    pos = position(stop=50.0, target=500.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, candle("AAPL", high=110.0, low=90.0, close=100.0, ts=FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    assert kinds(monitor) == []  # candles alone: no tick, no EOD
    await feed(monitor, tick("AAPL", 101.0, FLATTEN - timedelta(seconds=20)))
    await feed(monitor, candle("AAPL", high=110.0, low=90.0, close=107.0, ts=FLATTEN - timedelta(seconds=10)))
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.trigger_price == 101.0  # tick price, not candle close


async def test_entry_spanning_candle_cannot_supply_pre_entry_label(make) -> None:
    pos = position()
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, candle("AAPL", high=110.0, low=90.0, close=100.0, ts=OPENED - timedelta(minutes=1)))
    await pulse(monitor)
    assert kinds(monitor) == []


async def test_delayed_candle_after_eod_still_drives_protective_stop(make) -> None:
    pos = position(stop=95.0, target=500.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=20)))
    await pulse(monitor)
    assert kinds(monitor) == ["eod"]
    # delayed candle (bar-open stamped long before the EOD tick) arrives after EOD
    await feed(monitor, candle("AAPL", high=101.0, low=90.0, close=99.0, ts=OPENED + timedelta(hours=1)))
    assert kinds(monitor) == ["eod", "protective"]
    protective = monitor.get_observations()[1].intent
    assert (protective.exit_reason, protective.trigger_price) == ("stop", 95.0)
    assert monitor.get_observations()[0].intent.exit_reason == "eod_flatten"  # unchanged


# --- stop/target precedence (A11) ---------------------------------------------------


async def test_pulse_checks_stop_before_eod_on_eligible_tick(make) -> None:
    pos = position(stop=95.0)
    monitor, reader, _ = make([], now=FLATTEN)
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=20)))  # arrived unheld: never evaluated
    reader.positions.append(pos)
    await pulse(monitor)
    assert kinds(monitor) == ["protective"]
    assert monitor.get_observations()[0].intent.exit_reason == "stop"


async def test_pulse_checks_target_before_eod_on_eligible_tick(make) -> None:
    pos = position(side="SELL", stop=200.0, target=90.0)
    monitor, reader, _ = make([], now=FLATTEN)
    await feed(monitor, tick("AAPL", 80.0, FLATTEN - timedelta(seconds=20)))
    reader.positions.append(pos)
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.exit_reason == "target"


async def test_stop_wins_when_eligible_tick_touches_both(make) -> None:
    pos = position(stop=100.0, target=100.0)  # degenerate: one price touches both
    monitor, reader, _ = make([], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=20)))
    reader.positions.append(pos)
    await pulse(monitor)
    assert monitor.get_observations()[0].intent.exit_reason == "stop"


async def test_pending_protective_first_suppresses_eod(make) -> None:
    pos = position(stop=95.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=20)))  # held: stop observed by event
    await pulse(monitor)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=10)))
    await pulse(monitor)
    assert kinds(monitor) == ["protective"]


async def test_queued_event_is_processed_before_a_later_pulse(make) -> None:
    pos = position(stop=95.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    monitor._on_market_event(tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))  # queued first
    assert monitor.enqueue_pulse()  # queued second, no yield in between
    await drain(monitor)
    assert kinds(monitor) == ["protective"]  # the stop tick was processed before the pulse


async def test_event_arriving_after_queued_pulse_is_later_work(make) -> None:
    pos = position(stop=95.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    assert monitor.enqueue_pulse()  # queued first
    monitor._on_market_event(tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))  # arrives after
    await drain(monitor)
    assert kinds(monitor) == ["eod", "protective"]
    obs = monitor.get_observations()
    assert obs[0].sequence < obs[1].sequence
    assert obs[0].intent.trigger_price == 100.0  # labelled by the pre-pulse tick


async def test_eod_never_suppresses_protective_and_diagnostic_stays_one_per_position(make) -> None:
    pos = position(stop=95.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    assert monitor.acknowledge_observation(pos.position_id, "eod")
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))
    assert kinds(monitor) == ["eod", "protective"]
    (diag,) = monitor.get_exit_intents()  # one row per position, first intent, as before
    assert diag.exit_reason == "eod_flatten"


async def test_protective_first_created_stays_first_only_one_protective(make) -> None:
    pos = position(stop=95.0, target=105.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=30)), tick("AAPL", 110.0, FLATTEN - timedelta(seconds=20)))
    (obs,) = monitor.get_observations()
    assert obs.intent.exit_reason == "stop"


# --- repeated pulses (A1) ------------------------------------------------------------


async def test_repeated_pulses_create_one_eod_with_stable_label(make) -> None:
    pos = position()
    monitor, _, wall = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    for _ in range(5):
        await pulse(monitor)
    await feed(monitor, tick("AAPL", 105.0, FLATTEN - timedelta(seconds=5)))
    wall.now = FLATTEN + timedelta(seconds=30)
    await pulse(monitor)
    (obs,) = monitor.get_observations()
    assert obs.intent.trigger_price == 100.0  # not relabelled
    assert len(monitor.pending_observations()) == 1


async def test_pulses_are_coalesced_while_one_is_queued(make) -> None:
    monitor, _, _ = make([position()], now=FLATTEN)
    assert monitor.enqueue_pulse() is True
    assert monitor.enqueue_pulse() is False
    await drain(monitor)
    assert monitor.enqueue_pulse() is True
    await drain(monitor)


async def test_reader_failure_on_pulse_does_not_kill_worker(make) -> None:
    class Boom:
        def get_open_positions(self):
            raise RuntimeError("snapshot unavailable")

    monitor = PositionMonitor(EventBus(), Boom(), clock=CLOCK, wall_clock=WallClock(FLATTEN),
                              eod_lead_seconds=60, pulse_interval_seconds=None)
    monitor.start()
    try:
        await pulse(monitor)
        await pulse(monitor)  # worker still alive
    finally:
        await monitor.stop()


# --- handoff: pending / acknowledged / failure (A12) ----------------------------------


async def test_callbacks_do_not_acknowledge(make) -> None:
    pos = position(stop=95.0)
    legacy: list[ExitIntent] = []
    seen: list[Observation] = []
    monitor, _, _ = make([pos], now=FLATTEN, on_exit_intent=legacy.append, on_observation=seen.append)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))
    assert [o.kind for o in seen] == ["eod", "protective"]
    assert all(o.state is ObservationState.PENDING for o in seen)
    assert [i.exit_reason for i in legacy] == ["stop"]  # legacy callback: stop/target only, never EOD
    assert [o.state for o in monitor.get_observations()] == [ObservationState.PENDING] * 2
    assert [o.kind for o in monitor.pending_observations()] == ["eod", "protective"]


async def test_acknowledge_after_commit_is_idempotent_and_per_kind(make) -> None:
    pos = position(stop=95.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    assert monitor.acknowledge_observation(pos.position_id, "eod") is False  # nothing yet
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    assert monitor.acknowledge_observation(pos.position_id, "eod") is True
    assert monitor.acknowledge_observation(pos.position_id, "eod") is True
    assert monitor.acknowledge_observation(pos.position_id, "protective") is False
    assert monitor.pending_observations() == ()
    await pulse(monitor)  # an acknowledged EOD slot is not recreated
    assert kinds(monitor) == ["eod"]


async def test_callback_failure_keeps_slot_pending_and_worker_alive(make) -> None:
    pos = position(stop=95.0)

    def boom(_):
        raise RuntimeError("queue closed")

    monitor, _, _ = make([pos], now=FLATTEN, on_exit_intent=boom, on_observation=boom)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))
    assert [o.kind for o in monitor.pending_observations()] == ["eod", "protective"]  # retained for retry


async def test_db_failure_needs_no_release_slot_stays_pending_and_is_not_duplicated(make) -> None:
    pos = position()
    monitor, _, wall = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    first = monitor.pending_observations()
    wall.now = FLATTEN + timedelta(seconds=20)
    await pulse(monitor)  # consumer's commit failed; nothing was told to the monitor
    assert monitor.pending_observations() == first  # same slot, no overwrite, no duplicate


async def test_two_positions_keep_ordered_independent_slots(make) -> None:
    a, b = position(symbol="AAPL"), position(symbol="MSFT")
    monitor, _, _ = make([a, b], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)), tick("MSFT", 200.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    assert [o.intent.symbol for o in monitor.pending_observations()] == ["AAPL", "MSFT"]
    assert [o.intent.symbol for o in monitor.get_observations(symbol="MSFT")] == ["MSFT"]


async def test_release_window_closed_expires_only_eod(make) -> None:
    pos = position(stop=95.0)
    monitor, _, wall = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    assert monitor.release_observation(pos.position_id, "eod", ReleaseReason.WINDOW_CLOSED) is True
    assert monitor.get_observations()[0].state is ObservationState.EXPIRED
    assert monitor.pending_observations() == ()
    assert monitor.acknowledge_observation(pos.position_id, "eod") is False  # expired stays expired
    wall.now = FLATTEN + timedelta(seconds=10)
    await pulse(monitor)
    assert kinds(monitor) == ["eod"]  # not recreated, even while the wall clock is still in window
    await feed(monitor, tick("AAPL", 90.0, FLATTEN + timedelta(seconds=5)))  # protective unaffected
    assert kinds(monitor) == ["eod", "protective"]
    assert [o.kind for o in monitor.pending_observations()] == ["protective"]


async def test_window_closed_refused_for_protective_and_for_acknowledged_eod(make) -> None:
    pos = position(stop=95.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))
    assert monitor.release_observation(pos.position_id, "protective", ReleaseReason.WINDOW_CLOSED) is False
    assert monitor.get_observations()[0].state is ObservationState.PENDING
    pos2 = position(symbol="MSFT")
    monitor.__dict__["_position_reader"].positions.append(pos2)
    await feed(monitor, tick("MSFT", 100.0, FLATTEN - timedelta(seconds=10)))
    await pulse(monitor)
    assert monitor.acknowledge_observation(pos2.position_id, "eod")
    assert monitor.release_observation(pos2.position_id, "eod", ReleaseReason.WINDOW_CLOSED) is False


async def test_release_position_closed_discards_slots_and_blocks_new_ones(make) -> None:
    pos = position(stop=95.0)
    monitor, _, _ = make([pos], now=FLATTEN)
    await feed(monitor, tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    await pulse(monitor)
    assert monitor.release_observation(pos.position_id, "eod", ReleaseReason.POSITION_CLOSED) is True
    assert monitor.get_observations() == ()
    await feed(monitor, tick("AAPL", 90.0, FLATTEN - timedelta(seconds=5)))
    await pulse(monitor)
    assert monitor.get_observations() == ()


async def test_release_invalid_eod_requires_strictly_newer_tick(make) -> None:
    pos = position()
    monitor, _, wall = make([pos], now=FLATTEN)
    stamp = FLATTEN - timedelta(seconds=30)
    await feed(monitor, tick("AAPL", 100.0, stamp))
    await pulse(monitor)
    assert monitor.release_observation(pos.position_id, "eod", ReleaseReason.INVALID) is True
    assert monitor.get_observations() == ()
    await pulse(monitor)
    assert monitor.get_observations() == ()  # same tick is not re-offered every second
    await feed(monitor, tick("AAPL", 100.0, stamp))  # equal-time replay
    await pulse(monitor)
    assert monitor.get_observations() == ()
    await feed(monitor, tick("AAPL", 101.0, stamp + timedelta(seconds=1)))
    await pulse(monitor)
    assert [o.intent.trigger_price for o in monitor.get_observations()] == [101.0]


async def test_release_unknown_slot_is_a_noop(make) -> None:
    monitor, _, _ = make([position()], now=FLATTEN)
    assert monitor.release_observation(uuid4(), "eod", ReleaseReason.INVALID) is False
    assert monitor.release_observation(uuid4(), "eod", ReleaseReason.WINDOW_CLOSED) is False


# --- shutdown / timer -----------------------------------------------------------------


async def test_timer_enqueues_pulses_on_the_same_queue_and_produces_eod() -> None:
    pos = position()
    reader = FakeReader([pos])
    wall = WallClock(FLATTEN)
    monitor = PositionMonitor(EventBus(), reader, clock=CLOCK, wall_clock=wall, eod_lead_seconds=60,
                              pulse_interval_seconds=0.01)
    monitor.start()
    try:
        monitor._on_market_event(tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
        for _ in range(200):
            if monitor.pending_observations():
                break
            await asyncio.sleep(0.01)
        assert kinds(monitor) == ["eod"]
    finally:
        await monitor.stop()


async def test_shutdown_stops_timer_and_rejects_new_work() -> None:
    pos = position(stop=95.0)
    seen: list[Observation] = []
    monitor = PositionMonitor(EventBus(), FakeReader([pos]), clock=CLOCK, wall_clock=WallClock(FLATTEN),
                              eod_lead_seconds=60, pulse_interval_seconds=0.01, on_observation=seen.append)
    monitor.start()
    monitor._on_market_event(tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert [o.kind for o in seen] == ["eod"]
    timer = monitor._timer_task
    await asyncio.wait_for(monitor.stop(), timeout=2)
    assert timer is not None and timer.done()
    assert monitor._timer_task is None and monitor._worker_task is None
    assert monitor.enqueue_pulse() is False
    monitor._on_market_event(tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))  # ignored after stop
    await asyncio.sleep(0.05)
    assert kinds(monitor) == ["eod"]  # nothing decided after stop
    assert monitor.acknowledge_observation(pos.position_id, "eod") is True  # consumer may still ack a commit
    await monitor.stop()  # idempotent


async def test_stop_before_any_pulse_and_with_timer_disabled_is_clean() -> None:
    monitor = PositionMonitor(EventBus(), FakeReader(), clock=CLOCK, wall_clock=WallClock(FLATTEN),
                              eod_lead_seconds=60, pulse_interval_seconds=None)
    monitor.start()
    assert monitor._timer_task is None
    await asyncio.wait_for(monitor.stop(), timeout=2)


async def test_works_through_the_real_event_bus() -> None:
    bus = EventBus()
    await bus.start()
    pos = position(stop=95.0)
    monitor = PositionMonitor(bus, FakeReader([pos]), clock=CLOCK, wall_clock=WallClock(FLATTEN),
                              eod_lead_seconds=60, pulse_interval_seconds=None)
    monitor.start()
    try:
        await bus.publish(tick("AAPL", 100.0, FLATTEN - timedelta(seconds=30)))
        await asyncio.sleep(0.1)
        assert monitor.enqueue_pulse()
        await drain(monitor)
        await bus.publish(tick("AAPL", 90.0, FLATTEN - timedelta(seconds=10)))
        await asyncio.sleep(0.1)
        assert kinds(monitor) == ["eod", "protective"]
    finally:
        await monitor.stop()
        await bus.stop()
