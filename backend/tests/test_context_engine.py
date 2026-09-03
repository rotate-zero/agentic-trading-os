"""ContextEngine tests — aggregator behavior only (provider merge, event
shape, start/stop lifecycle). CalendarProvider itself is covered by
test_calendar_provider.py; here it's exercised via fake providers so
these tests don't depend on MarketClock's real-time behavior.
"""
from __future__ import annotations

import asyncio

from app.context_engine.engine import ContextEngine
from app.context_engine.provider import ContextProvider, SymbolContextProvider
from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventType


class _FakeProvider(ContextProvider):
    def __init__(self, name: str, output: dict) -> None:
        self.name = name
        self._output = output

    async def evaluate(self) -> dict:
        return self._output


class _FakeSymbolProvider(SymbolContextProvider):
    def __init__(self, name: str, output_by_symbol: dict[str, dict]) -> None:
        self.name = name
        self._output_by_symbol = output_by_symbol
        self.calls: list[str] = []

    async def evaluate(self, symbol: str) -> dict:
        self.calls.append(symbol)
        return self._output_by_symbol.get(symbol, {})


async def test_evaluate_all_merges_provider_output_by_name_and_publishes():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.CONTEXT_CHANGED, lambda env: received.append(env))

        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {"session": "open"})], symbol_providers=[])
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
            symbol_providers=[],
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

        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {"session": "open"})], symbol_providers=[])
        engine._load_scanner_universe_symbols = lambda: []  # type: ignore[method-assign] — stay DB-free, no per-symbol path exercised here
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
        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {"session": "open"})], symbol_providers=[])
        engine._load_scanner_universe_symbols = lambda: []  # type: ignore[method-assign]
        engine.start()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(engine.stop(), timeout=1.0)
    finally:
        await bus.stop()


async def test_evaluate_for_symbol_merges_symbol_provider_output_and_publishes():
    """decision #96 — the second aggregation path."""
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.CONTEXT_CHANGED, lambda env: received.append(env))

        fundamentals = _FakeSymbolProvider("fundamentals", {"AAPL": {"sector": None, "industry": "Technology"}})
        news = _FakeSymbolProvider("news", {"AAPL": {"present": True, "importance": "low"}})
        engine = ContextEngine(bus, providers=[], symbol_providers=[fundamentals, news])

        payload = await engine.evaluate_for_symbol("AAPL")

        assert payload.providers == {
            "fundamentals": {"sector": None, "industry": "Technology"},
            "news": {"present": True, "importance": "low"},
        }
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].symbol == "AAPL"  # per-symbol event, unlike evaluate_all()'s symbol=None
    finally:
        await bus.stop()


async def test_evaluate_for_symbol_calls_each_provider_with_the_right_symbol():
    bus = EventBus()
    await bus.start()
    try:
        fundamentals = _FakeSymbolProvider("fundamentals", {})
        engine = ContextEngine(bus, providers=[], symbol_providers=[fundamentals])

        await engine.evaluate_for_symbol("MSFT")
        await engine.evaluate_for_symbol("NVDA")

        assert fundamentals.calls == ["MSFT", "NVDA"]
    finally:
        await bus.stop()


async def test_bootstrap_creates_one_loop_task_per_universe_symbol():
    bus = EventBus()
    await bus.start()
    try:
        fundamentals = _FakeSymbolProvider("fundamentals", {})
        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {})], symbol_providers=[fundamentals])
        # Monkeypatch the universe read so this test stays DB-free, same
        # posture as the rest of this file — a fixed fake list instead
        # of a real scanner_universe_symbols query.
        engine._load_scanner_universe_symbols = lambda: ["FAKE1", "FAKE2"]  # type: ignore[method-assign]

        engine.start()
        await asyncio.sleep(0.05)

        assert set(engine._symbol_tasks.keys()) == {"FAKE1", "FAKE2"}
        assert sorted(fundamentals.calls) == ["FAKE1", "FAKE2"]  # each got its immediate first evaluation

        await engine.stop()
        assert engine._symbol_tasks == {}
    finally:
        await bus.stop()


async def test_stop_before_bootstrap_completes_does_not_leak_symbol_tasks():
    """The race this test guards against: start() spawns the bootstrap as
    fire-and-forget, so a stop() called before it's had a chance to run
    must still end up with zero live per-symbol tasks, not tasks created
    after stop() already finished iterating."""
    bus = EventBus()
    await bus.start()
    try:
        engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {})], symbol_providers=[_FakeSymbolProvider("fundamentals", {})])
        engine._load_scanner_universe_symbols = lambda: ["FAKE1", "FAKE2", "FAKE3"]  # type: ignore[method-assign]

        engine.start()
        await asyncio.wait_for(engine.stop(), timeout=2.0)  # no sleep — races the bootstrap deliberately

        assert engine._symbol_tasks == {}
        assert engine._bootstrap_task is None
    finally:
        await bus.stop()
