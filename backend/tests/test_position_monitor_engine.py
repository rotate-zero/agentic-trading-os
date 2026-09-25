"""
Tests for `app/position_monitor/engine.py`'s `PositionMonitor` — a real
EventBus, a fake `PositionReader` (§1.6: no concrete adapter ships with
this module, same fork-1 precedent `test_execution_engine.py` and
`test_governor_engine.py` both already set for their own narrow ports),
and an injectable `MarketClock` reading a fixed instant rather than
wall-clock (§1.8's own restart-safety/backtest-safety invariant).

Covers §6's own required list: stop, target, EOD-flatten, the
stop-wins-tie case (EX-8), and idempotency (no second intent for an
already-closing position) — plus held-symbol filtering (module
docstring) and the "no stop/target configured" honest-absence case
(ports.py's own docstring).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.market_clock import MarketClock
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.position_monitor.engine import PositionMonitor, _Bar, _evaluate
from app.position_monitor.ports import PositionView
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated

# Tuesday 2026-09-22 — not a holiday, not a half-day (see core/market_clock.py's
# _HOLIDAYS_2026/_HALF_DAYS_2026). Regular session 13:30-20:00 UTC.
_OPENED_AT = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
_SESSION_CLOSE_UTC = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
_BEFORE_CLOSE = datetime(2026, 9, 22, 19, 59, tzinfo=timezone.utc)

_FIXED_CLOCK = MarketClock()


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

    def get_open_positions(self) -> tuple[PositionView, ...]:
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
    intent = _evaluate(position, bar, _FIXED_CLOCK)
    assert intent is not None
    assert intent.exit_reason == "stop"
    assert intent.trigger_price == 95.0


def test_evaluate_no_stop_or_target_configured_never_exits_on_price() -> None:
    position = _long_position(stop=None, target=None)
    bar = _Bar(high=10_000.0, low=0.01, close=1.0, ts=_OPENED_AT)
    intent = _evaluate(position, bar, _FIXED_CLOCK)
    assert intent is None  # honest absence — no stop/target means price alone never triggers an exit


# --- engine-level tests, real EventBus + fake PositionReader ---------------


def _make_monitor(bus: EventBus, positions: list[PositionView]) -> tuple[PositionMonitor, _FakePositionReader]:
    reader = _FakePositionReader(positions)
    monitor = PositionMonitor(bus, reader, clock=_FIXED_CLOCK)
    return monitor, reader


@pytest.mark.asyncio
async def test_stop_exit_produces_intent_for_long_position() -> None:
    bus = EventBus()
    await bus.start()
    position = _long_position()
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        await bus.publish(_tick_envelope("AAPL", 94.5, _OPENED_AT))
        await asyncio.sleep(0.1)

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
        await asyncio.sleep(0.1)

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
        await bus.publish(_candle_envelope("AAPL", o=100.0, h=110.0, low=90.0, c=100.0, ts=_OPENED_AT))
        await asyncio.sleep(0.1)

        intents = monitor.get_exit_intents()
        assert len(intents) == 1
        assert intents[0].exit_reason == "stop"  # EX-8: stop wins on a same-bar tie
    finally:
        await monitor.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_eod_flatten_triggers_at_real_session_close() -> None:
    bus = EventBus()
    await bus.start()
    # Wide stop/target so only EOD-flatten can fire.
    position = _long_position(stop=1.0, target=1_000.0)
    monitor, _ = _make_monitor(bus, [position])
    monitor.start()
    try:
        # Before close: neither stop/target nor EOD should fire.
        await bus.publish(_tick_envelope("AAPL", 100.0, _BEFORE_CLOSE))
        await asyncio.sleep(0.1)
        assert monitor.get_exit_intents() == ()

        # At the real regular-session close instant: EOD-flatten fires.
        await bus.publish(_tick_envelope("AAPL", 101.5, _SESSION_CLOSE_UTC))
        await asyncio.sleep(0.1)

        intents = monitor.get_exit_intents()
        assert len(intents) == 1
        assert intents[0].exit_reason == "eod_flatten"
        assert intents[0].trigger_price == 101.5  # the bar's own close/price, not a fabricated value
        assert intents[0].trigger_ts == _SESSION_CLOSE_UTC
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
        await asyncio.sleep(0.1)
        first = monitor.get_exit_intents()
        assert len(first) == 1

        # More ticks arrive after the intent — even ones that would also
        # independently qualify (e.g. an even lower stop-touch, or later
        # a target-range price) must not produce a second intent.
        await bus.publish(_tick_envelope("AAPL", 90.0, _OPENED_AT))
        await bus.publish(_tick_envelope("AAPL", 200.0, _OPENED_AT))
        await asyncio.sleep(0.1)

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
        await bus.publish(_tick_envelope("MSFT", 1.0, _OPENED_AT))
        await asyncio.sleep(0.1)

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
        await asyncio.sleep(0.1)

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
        await bus.publish(_tick_envelope("AAPL", 98.0, _OPENED_AT))  # trips tight_stop only
        await asyncio.sleep(0.1)

        intents = monitor.get_exit_intents()
        assert len(intents) == 1
        assert intents[0].position_id == tight_stop.position_id
    finally:
        await monitor.stop()
        await bus.stop()
