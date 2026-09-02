"""ContextEngine tests — aggregator behavior only (provider merge, event
shape, start/stop lifecycle). CalendarProvider itself is covered by
test_calendar_provider.py; here it's exercised via fake providers so
these tests don't depend on MarketClock's real-time behavior.
"""
from __future__ import annotations

import asyncio

from app.context_engine.engine import ContextEngine
from app.context_engine.provider import ContextProvider
from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventType


class _FakeProvider(ContextProvider):
    def __init__(self, name: str, output: dict) -> None:
        self.name = name
        self._output = output

    async def evaluate(self) -> dict:
        return self._output


async def test_evaluate_all_merges_provider_output_by_name_and_publishes():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.CONTEXT_CHANGED, lambda env: received.append(env))

        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {"session": "open"})])
        payload = await engine.evaluate_all()

        assert payload.providers == {"calendar": {"session": "open"}}

        await asyncio.sleep(0.05)  # let the normal-lane queue dispatch
        assert len(received) == 1
        assert received[0].payload["providers"] == {"calendar": {"session": "open"}}
        assert received[0].symbol is None  # market-wide event, §10.1
    finally:
        await bus.stop()


async def test_evaluate_all_calls_every_registered_provider_independently():
    bus = EventBus()
    await bus.start()
    try:
        engine = ContextEngine(
            bus,
            providers=[_FakeProvider("one", {"a": 1}), _FakeProvider("two", {"b": 2})],
        )
        payload = await engine.evaluate_all()
        assert payload.providers == {"one": {"a": 1}, "two": {"b": 2}}
    finally:
        await bus.stop()


async def test_start_fires_immediate_evaluation_before_first_boundary():
    """The loop's whole point (module docstring): don't wait for the
    first session boundary to have any state at all."""
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.CONTEXT_CHANGED, lambda env: received.append(env))

        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {"session": "open"})])
        engine.start()
        await asyncio.sleep(0.05)
        assert len(received) == 1

        await engine.stop()
    finally:
        await bus.stop()


async def test_stop_cancels_cleanly_without_hanging():
    bus = EventBus()
    await bus.start()
    try:
        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {"session": "open"})])
        engine.start()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(engine.stop(), timeout=1.0)
    finally:
        await bus.stop()
