"""
Tests for `app/position_monitor/engine.py`'s `PositionMonitor` — a real
EventBus, a fake `PositionReader` (§1.6: no concrete adapter ships with
this module, same fork-1 precedent `test_execution_engine.py` and
`test_governor_engine.py` both already set for their own narrow ports),
and an injectable `MarketClock` reading a fixed instant rather than
wall-clock (§1.8's own restart-safety/backtest-safety invariant).

Covers §6's own required list: stop, target, the stop-wins-tie case (EX-8),
and idempotency (no second intent for an already-closing position) — plus
held-symbol filtering (module docstring) and the "no stop/target configured"
honest-absence case (ports.py's own docstring). EOD-flatten is now timer-driven
and is covered by `test_position_monitor_eod.py`; the old exact-close event
expectation was replaced here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.market_clock import MarketClock
from app.core.config import Settings
from app.event_bus.bus import WILDCARD, EventBus
from app.event_bus.events import make_envelope
from app.position_monitor.engine import PositionMonitor, _Bar, _evaluate
from app.position_monitor.ports import PositionView
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import OrderFilled
from app.schemas.events.market_data import CandleClosed, PriceUpdated

# Tuesday 2026-09-22 — not a holiday, not a half-day (see core/market_clock.py's
# _HOLIDAYS_2026/_HALF_DAYS_2026). Regular session 13:30-20:00 UTC.
_OPENED_AT = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
_SESSION_CLOSE_UTC = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
_BEFORE_CLOSE = datetime(2026, 9, 22, 19, 59, tzinfo=timezone.utc)

_FIXED_CLOCK = MarketClock()


def test_journal_settings_defaults_and_validation() -> None:
    settings = Settings(_env_file=None)
    assert (settings.position_monitor_max_journal_symbols,
            settings.position_monitor_max_ticks_per_symbol,
            settings.position_monitor_unowed_retention_seconds) == (100, 2000, 60)
    for field, bad in (("position_monitor_max_journal_symbols", 0),
                       ("position_monitor_max_ticks_per_symbol", -1),
                       ("position_monitor_unowed_retention_seconds", float("inf"))):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{field: bad})


def _long_position(**overrides) -> PositionView:
    base = dict(
        position_id=uuid4(),
        symbol="AAPL",
        side="BUY",
        qty=10,
        stop=95.0,
        target=110.0,
        opened_at=_OPENED_AT,
    )
    base.update(overrides)
    return PositionView(**base)


def _short_position(**overrides) -> PositionView:
    base = dict(
        position_id=uuid4(),
        symbol="AAPL",
        side="SELL",
        qty=5,
        stop=110.0,
        target=90.0,
        opened_at=_OPENED_AT,
    )
    base.update(overrides)
    return PositionView(**base)


@dataclass
class _FakePositionReader:
    positions: list[PositionView] = field(default_factory=list)
    unavailable: bool = False
    fail_reads: int = 0

    def get_open_positions(self) -> tuple[PositionView, ...]:
        if self.unavailable or self.fail_reads:
            if self.fail_reads:
                self.fail_reads -= 1
            raise RuntimeError("snapshot unavailable")
        return tuple(self.positions)


def _tick_envelope(symbol: str, price: float, ts: datetime) -> EventEnvelope:
    return make_envelope(EventType.PRICE_UPDATED, PriceUpdated(price=price, size=1, exchange_ts=ts), symbol=symbol)


def _candle_envelope(symbol: str, *, o: float, h: float, low: float, c: float, ts: datetime) -> EventEnvelope:
    return make_envelope(
        EventType.CANDLE_CLOSED,
        CandleClosed(timeframe="1m", open=o, high=h, low=low, close=c, volume=100, candle_ts=ts),
        symbol=symbol,
    )


# --- pure _evaluate() tests (no asyncio/EventBus needed) -------------------


def test_evaluate_long_stop_touched_by_tick() -> None:
    position = _long_position()
    bar = _Bar(high=94.0, low=94.0, close=94.0, ts=_OPENED_AT)
    intent = _evaluate(position, bar)
    assert intent is not None
    assert intent.exit_reason == "stop"
    assert intent.trigger_price == 95.0


def test_evaluate_no_stop_or_target_configured_never_exits_on_price() -> None:
    position = _long_position(stop=None, target=None)
    bar = _Bar(high=10_000.0, low=0.01, close=1.0, ts=_OPENED_AT)
    intent = _evaluate(position, bar)
    assert intent is None  # honest absence — no stop/target means price alone never triggers an exit


# --- engine-level tests, real EventBus + fake PositionReader ---------------


def _make_monitor(bus: EventBus, positions: list[PositionView]) -> tuple[PositionMonitor, _FakePositionReader]:
    reader = _FakePositionReader(positions)
    monitor = PositionMonitor(bus, reader, clock=_FIXED_CLOCK)
    return monitor, reader


async def _wait_until(predicate, description, *, observed=None, timeout=5.0, interval=0.005) -> None:
    """Poll `predicate()` until it is truthy, or fail after `timeout` seconds.

    Replaces fixed `asyncio.sleep` guesses for tests that expect a result: the
    wait ends as soon as the positive signal appears and the bound is only a
    failure ceiling. `observed()` (optional) is evaluated on timeout so the
    failure says what the monitor actually held.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            seen = observed() if observed is not None else "n/a"
            raise AssertionError(f"timed out after {timeout:.1f}s waiting for: {description} (last observed: {seen})")
        await asyncio.sleep(interval)


async def _wait_for_intents(monitor: PositionMonitor, count: int, *, symbol: str | None = None) -> None:
    await _wait_until(
        lambda: len(monitor.get_exit_intents(symbol)) >= count,
        f"{count} exit intent(s)" + ("" if symbol is None else f" for {symbol}"),
        observed=lambda: monitor.get_exit_intents(symbol),
    )


async def _direct(monitor: PositionMonitor, *envelopes: EventEnvelope) -> None:
    for envelope in envelopes:
        monitor._on_market_event(envelope)
    await asyncio.wait_for(monitor._queue.join(), 2)


async def test_retained_reversal_replays_after_unavailable_snapshot_without_pulse() -> None:
    pos = _long_position()
    reader = _FakePositionReader([pos], unavailable=True)
    calls = []
    monitor = PositionMonitor(EventBus(), reader, wall_clock=lambda: _BEFORE_CLOSE,
                              pulse_interval_seconds=None, recovery_interval_seconds=.01,
                              on_observation=calls.append)
    monitor.start()
    try:
        breach = _OPENED_AT + timedelta(seconds=1)
        await _direct(monitor, _tick_envelope("AAPL", 94, breach),
                      _tick_envelope("AAPL", 101, breach + timedelta(seconds=1)))
        assert monitor.get_exit_intents() == ()
        assert monitor.protection_diagnostics()["snapshot_unavailable"]
        reader.unavailable = False
        await _wait_for_intents(monitor, 1)
        intent = monitor.get_exit_intents()[0]
        assert (intent.exit_reason, intent.trigger_price, intent.trigger_ts) == ("stop", 95, breach)
        await _direct(monitor, _tick_envelope("AAPL", 93, breach + timedelta(seconds=2)))
        monitor._enqueue_replay(monitor._seq)
        await monitor._queue.join()
        assert len(calls) == 1 and len(monitor.get_observations()) == 1
        assert not monitor.protection_diagnostics()["snapshot_unavailable"]
    finally:
        await monitor.stop()


async def test_ready_flat_then_visible_replays_in_arrival_order_with_entry_boundary() -> None:
    opened = _OPENED_AT
    pos = _long_position(opened_at=opened)
    reader = _FakePositionReader([])
    monitor = PositionMonitor(EventBus(), reader, wall_clock=lambda: _BEFORE_CLOSE,
                              pulse_interval_seconds=None, recovery_interval_seconds=.01)
    monitor.start()
    try:
        await _direct(monitor, _tick_envelope("AAPL", 90, opened - timedelta(microseconds=1)),
                      _tick_envelope("AAPL", 111, opened),
                      _tick_envelope("AAPL", 90, opened + timedelta(seconds=1)))
        assert monitor.get_exit_intents() == ()
        reader.positions.append(pos)
        await _wait_for_intents(monitor, 1)
        intent = monitor.get_exit_intents()[0]
        assert (intent.exit_reason, intent.trigger_ts) == ("target", opened)
    finally:
        await monitor.stop()


async def test_worker_read_failure_recovers_without_second_tick() -> None:
    pos = _long_position()
    reader = _FakePositionReader([pos], fail_reads=1)
    monitor = PositionMonitor(EventBus(), reader, wall_clock=lambda: _BEFORE_CLOSE,
                              pulse_interval_seconds=None, recovery_interval_seconds=.01)
    monitor.start()
    try:
        ts = _OPENED_AT + timedelta(seconds=2)
        await _direct(monitor, _tick_envelope("AAPL", 94, ts))
        await _wait_for_intents(monitor, 1)
        assert monitor.get_exit_intents()[0].trigger_ts == ts
        assert monitor.protection_diagnostics()["incident_counts"]["snapshot_unavailable"] == 1
    finally:
        await monitor.stop()


async def test_replay_respects_candle_and_pulse_sequence_boundaries() -> None:
    pos = _long_position(stop=95, target=110)
    reader = _FakePositionReader([pos])
    monitor = PositionMonitor(EventBus(), reader, wall_clock=lambda: _BEFORE_CLOSE,
                              pulse_interval_seconds=None)
    monitor.start()
    try:
        await _direct(monitor, _tick_envelope("AAPL", 100, _OPENED_AT))
        monitor._on_market_event(_candle_envelope("AAPL", o=100, h=111, low=99, c=100,
                                                  ts=_OPENED_AT + timedelta(seconds=1)))
        monitor._on_market_event(_tick_envelope("AAPL", 90, _OPENED_AT + timedelta(seconds=2)))
        await monitor._queue.join()
        assert monitor.get_exit_intents()[0].exit_reason == "target"
        assert monitor.get_exit_intents()[0].trigger_ts == _OPENED_AT + timedelta(seconds=1)
    finally:
        await monitor.stop()


async def test_journal_limits_expiry_and_symbol_isolation() -> None:
    elapsed = [0.0]
    reader = _FakePositionReader([])
    monitor = PositionMonitor(EventBus(), reader, wall_clock=lambda: _BEFORE_CLOSE,
                              monotonic_clock=lambda: elapsed[0], max_journal_symbols=2,
                              max_ticks_per_symbol=2, unowed_retention_seconds=60,
                              pulse_interval_seconds=None)
    monitor.start()
    try:
        await _direct(monitor, *(_tick_envelope("AAPL", price, _OPENED_AT + timedelta(seconds=i))
                                 for i, price in enumerate((100, 101, 102))))
        assert monitor.protection_diagnostics()["incident_counts"]["tick_overflow"] == 1
        await _direct(monitor, _tick_envelope("BBB", 100, _OPENED_AT),
                      _tick_envelope("CCC", 100, _OPENED_AT))
        diagnostic = monitor.protection_diagnostics()
        assert diagnostic["journaled_symbols"] == 2
        assert diagnostic["incident_counts"]["symbol_capacity_loss"] == 1
        elapsed[0] = 61
        monitor._enqueue_replay(monitor._seq)
        await monitor._queue.join()
        assert monitor.protection_diagnostics()["journaled_ticks"] == 0
        assert monitor.protection_diagnostics()["lost_window"]
    finally:
        await monitor.stop()


async def test_owed_tick_survives_retention_while_snapshot_unavailable() -> None:
    elapsed = [0.0]
    pos = _long_position()
    reader = _FakePositionReader([pos], unavailable=True)
    monitor = PositionMonitor(EventBus(), reader, wall_clock=lambda: _BEFORE_CLOSE,
                              monotonic_clock=lambda: elapsed[0], unowed_retention_seconds=60,
                              pulse_interval_seconds=None)
    monitor.start()
    try:
        await _direct(monitor, _tick_envelope("AAPL", 94, _OPENED_AT))
        elapsed[0] = 120
        monitor._enqueue_replay(monitor._seq)
        await monitor._queue.join()
        assert monitor.protection_diagnostics()["journaled_ticks"] == 1
        reader.unavailable = False
        monitor._enqueue_replay(monitor._seq)
        await monitor._queue.join()
        assert monitor.get_exit_intents()[0].trigger_ts == _OPENED_AT
    finally:
        await monitor.stop()


async def test_failed_observation_registration_does_not_advance_position_progress(monkeypatch) -> None:
    pos = _long_position()
    monitor, _ = _make_monitor(EventBus(), [pos])
    original = monitor._register
    attempts = [0]

    def fail_once(kind, intent):
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("injected registration failure")
        return original(kind, intent)

    monkeypatch.setattr(monitor, "_register", fail_once)
    monitor.start()
    try:
        await _direct(monitor, _tick_envelope("AAPL", 94, _OPENED_AT))
        assert monitor._progress.get(pos.position_id, 0) == 0
        monitor._enqueue_replay(monitor._seq)
        await monitor._queue.join()
        assert attempts[0] == 2
        assert monitor.get_exit_intents()[0].trigger_ts == _OPENED_AT
    finally:
        await monitor.stop()


async def test_fill_pending_marker_preserves_tick_and_reports_invisible_fill() -> None:
    elapsed = [0.0]
    reader = _FakePositionReader([])
    monitor = PositionMonitor(EventBus(), reader, wall_clock=lambda: _BEFORE_CLOSE,
                              monotonic_clock=lambda: elapsed[0], unowed_retention_seconds=60,
                              pulse_interval_seconds=None)
    monitor.start()
    try:
        filled = make_envelope(
            EventType.ORDER_FILLED,
            OrderFilled(order_id="entry", side="BUY", qty=1, fill_price=100, fill_ts=_OPENED_AT),
            symbol="AAPL",
        )
        await _direct(monitor, filled, _tick_envelope("AAPL", 94, _OPENED_AT))
        elapsed[0] = 61
        monitor._enqueue_replay(monitor._seq)
        await monitor._queue.join()
        diagnostic = monitor.protection_diagnostics()
        assert diagnostic["journaled_ticks"] == 1
        assert diagnostic["incident_counts"]["fill_invisible"] == 1
        assert diagnostic["fill_pending_symbols"] == ["AAPL"]
        reader.positions.append(_long_position(opened_at=_OPENED_AT))
        monitor._enqueue_replay(monitor._seq)
        await monitor._queue.join()
        assert monitor.get_exit_intents()[0].exit_reason == "stop"
        assert monitor.protection_diagnostics()["fill_pending_symbols"] == []
    finally:
        await monitor.stop()


async def _settle(bus: EventBus, monitor: PositionMonitor, *envelopes: EventEnvelope, timeout: float = 5.0) -> None:
    """Publish `envelopes` and return only once the monitor has finished them.

    This is the barrier for every "no intent / no second intent" assertion:
    elapsed time never proves absence, so absence is asserted only after the
    published inputs were fully processed. Two stages:

    1. Bus dispatch. A wildcard probe records each published envelope (by object
       identity — the bus hands handlers the same object) once the bus lane has
       dispatched it. `EventBus` has no public flush, and the monitor's
       subscriber (`_on_market_event`) is synchronous, so by the time this
       probe's wake-up runs, the subscriber has run for that envelope too:
       both handlers were scheduled in the same dispatch step, before the probe
       could wake this coroutine. This is also what proves an unheld-symbol
       envelope was seen and dropped by the subscriber's held-symbol filter,
       which never reaches the monitor's own queue.
    2. Worker. `monitor._queue.join()` returns once the monitor's worker has
       finished every item the subscriber enqueued (the same private signal
       `test_position_monitor_eod.py`'s `drain()` uses).
    """
    pending = {id(envelope) for envelope in envelopes}
    dispatched = asyncio.Event()

    def probe(envelope: EventEnvelope) -> None:
        pending.discard(id(envelope))
        if not pending:
            dispatched.set()

    bus.subscribe_all(probe)
    try:
        for envelope in envelopes:
            await bus.publish(envelope)
        await asyncio.wait_for(dispatched.wait(), timeout)
        await asyncio.wait_for(monitor._queue.join(), timeout)
    finally:
        bus.unsubscribe(WILDCARD, probe)


@pytest.mark.asyncio
async def test_stop_exit_produces_intent_for_long_position() -> None:
    bus = EventBus()
    await bus.start()
    position = _long_position()
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        await bus.publish(_tick_envelope("AAPL", 94.5, _OPENED_AT))
        await _wait_for_intents(monitor, 1)

        intents = monitor.get_exit_intents()
        assert len(intents) == 1
        assert intents[0].position_id == position.position_id
        assert intents[0].exit_reason == "stop"
        assert intents[0].trigger_price == 95.0
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_target_exit_produces_intent_for_short_position() -> None:
    bus = EventBus()
    await bus.start()
    position = _short_position()
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        await bus.publish(_tick_envelope("AAPL", 89.0, _OPENED_AT))
        await _wait_for_intents(monitor, 1)

        intents = monitor.get_exit_intents()
        assert len(intents) == 1
        assert intents[0].exit_reason == "target"
        assert intents[0].trigger_price == 90.0
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_stop_wins_tie_when_one_candle_touches_both(caplog) -> None:
    bus = EventBus()
    await bus.start()
    position = _long_position(stop=95.0, target=105.0)
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        # High crosses target, low crosses stop, in the same bar.
        await _settle(bus, monitor, _candle_envelope("AAPL", o=100.0, h=110.0, low=90.0, c=100.0, ts=_OPENED_AT))
        await _wait_for_intents(monitor, 1)

        intents = monitor.get_exit_intents()
        assert len(intents) == 1
        assert intents[0].exit_reason == "stop"  # EX-8: stop wins on a same-bar tie
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_event_at_session_close_no_longer_labels_eod() -> None:
    """EOD moved off the event path (simulated-eod-monitor-handoff): a tick or
    candle at/after the close never produces `eod_flatten`. The timer-driven
    EOD path is covered by `test_position_monitor_eod.py`."""
    bus = EventBus()
    await bus.start()
    position = _long_position(stop=1.0, target=1_000.0)  # wide: only EOD could fire
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        await _settle(
            bus,
            monitor,
            _tick_envelope("AAPL", 100.0, _BEFORE_CLOSE),
            _tick_envelope("AAPL", 101.5, _SESSION_CLOSE_UTC),
            _candle_envelope("AAPL", o=100.0, h=102.0, low=99.0, c=101.0, ts=_SESSION_CLOSE_UTC),
        )

        assert monitor.get_exit_intents() == ()
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_idempotent_no_second_intent_for_already_closing_position() -> None:
    bus = EventBus()
    await bus.start()
    position = _long_position()
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        await bus.publish(_tick_envelope("AAPL", 94.0, _OPENED_AT))  # stop touched
        await _wait_for_intents(monitor, 1)
        first = monitor.get_exit_intents()
        assert len(first) == 1

        # More ticks arrive after the intent — even ones that would also
        # independently qualify (e.g. an even lower stop-touch, or later
        # a target-range price) must not produce a second intent.
        await _settle(
            bus,
            monitor,
            _tick_envelope("AAPL", 90.0, _OPENED_AT),
            _tick_envelope("AAPL", 200.0, _OPENED_AT),
        )

        second = monitor.get_exit_intents()
        assert second == first  # unchanged — same single ExitIntent, not replaced or duplicated
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_unheld_symbol_events_are_ignored() -> None:
    bus = EventBus()
    await bus.start()
    position = _long_position(symbol="AAPL")
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        # MSFT is not held — even a price that would trigger AAPL's stop
        # must produce nothing, since it isn't AAPL's price.
        await _settle(bus, monitor, _tick_envelope("MSFT", 1.0, _OPENED_AT))

        assert monitor.get_exit_intents() == ()
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_get_exit_intents_filters_by_symbol() -> None:
    bus = EventBus()
    await bus.start()
    aapl = _long_position(symbol="AAPL", stop=95.0, target=110.0)
    msft = _long_position(symbol="MSFT", stop=200.0, target=250.0)
    monitor, _ = _make_monitor(bus, [aapl, msft])
    monitor.start()
    try:
        await bus.publish(_tick_envelope("AAPL", 94.0, _OPENED_AT))  # AAPL stop
        await bus.publish(_tick_envelope("MSFT", 199.0, _OPENED_AT))  # MSFT stop
        await _wait_for_intents(monitor, 2)

        assert len(monitor.get_exit_intents()) == 2
        aapl_only = monitor.get_exit_intents(symbol="AAPL")
        assert len(aapl_only) == 1
        assert aapl_only[0].symbol == "AAPL"
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_multiple_positions_same_symbol_evaluated_independently() -> None:
    bus = EventBus()
    await bus.start()
    tight_stop = _long_position(symbol="AAPL", stop=99.0, target=200.0)
    wide_stop = _long_position(symbol="AAPL", stop=50.0, target=200.0)
    monitor, _ = _make_monitor(bus, [tight_stop, wide_stop])
    monitor.start()
    try:
        await _settle(bus, monitor, _tick_envelope("AAPL", 98.0, _OPENED_AT))  # trips tight_stop only
        await _wait_for_intents(monitor, 1)

        intents = monitor.get_exit_intents()
        assert len(intents) == 1
        assert intents[0].position_id == tight_stop.position_id
    finally:
        await monitor.stop()
        await bus.stop()
