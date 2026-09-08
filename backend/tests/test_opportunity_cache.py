"""OpportunityCache tests — no DB, no Scheduler dependency. Publishes
synthetic OpportunityCreated envelopes directly onto a real EventBus
(same "talk to the engine via the bus, not via a stub" posture
test_market_state_engine.py uses for FeaturesUpdated) — this cache has
no persistence layer to fake around, so unlike test_market_state_engine.py
none of this needs Postgres or a `_db_available()` skip guard.
"""
from __future__ import annotations

import asyncio

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventEnvelope, EventType
from app.strategy_engine.base_strategy import Opportunity
from app.trading_intelligence.opportunity_cache import OpportunityCache

from datetime import datetime, timezone


def _make_opportunity(strategy: str = "ORB", direction: str = "BUY", confidence: float = 0.7) -> Opportunity:
    return Opportunity(
        strategy=strategy,
        version=f"{strategy.lower()}_v1",
        direction=direction,
        confidence=confidence,
        structural_invalidation=100.0,
        structural_target=105.0,
        evidence={"conditions": {}, "reason": "test fixture", "basis": "live"},
        setup_detected_at=datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc),
    )


async def test_get_snapshot_is_empty_before_anything_published():
    cache = OpportunityCache(EventBus())
    assert cache.get_snapshot() == {"symbols": {}}
    assert cache.get_snapshot(symbol="NVDA") == {"symbols": {}}


async def test_caches_latest_opportunity_per_symbol_and_strategy():
    bus = EventBus()
    await bus.start()
    try:
        cache = OpportunityCache(bus)
        cache.start()

        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB"), symbol="NVDA"))
        await asyncio.sleep(0.05)  # let the normal-lane queue dispatch

        snapshot = cache.get_snapshot()
        assert set(snapshot["symbols"].keys()) == {"NVDA"}
        assert set(snapshot["symbols"]["NVDA"].keys()) == {"ORB"}
        assert snapshot["symbols"]["NVDA"]["ORB"]["direction"] == "BUY"
        assert snapshot["symbols"]["NVDA"]["ORB"]["confidence"] == 0.7
        # `received_at` is this cache's own wall-clock read, not Opportunity's
        # own setup_detected_at — see get_snapshot()'s own docstring.
        assert "received_at" in snapshot["symbols"]["NVDA"]["ORB"]
        assert snapshot["symbols"]["NVDA"]["ORB"]["received_at"] != snapshot["symbols"]["NVDA"]["ORB"]["setup_detected_at"]
    finally:
        await bus.stop()


async def test_second_opportunity_from_same_strategy_and_symbol_overwrites_not_accumulates():
    """Retention decision (#114): last ONE per (symbol, strategy), not a
    rolling list — see module docstring."""
    bus = EventBus()
    await bus.start()
    try:
        cache = OpportunityCache(bus)
        cache.start()

        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", confidence=0.5), symbol="NVDA"))
        await asyncio.sleep(0.05)
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", confidence=0.9), symbol="NVDA"))
        await asyncio.sleep(0.05)

        snapshot = cache.get_snapshot(symbol="NVDA")
        assert snapshot["symbols"]["NVDA"]["ORB"]["confidence"] == 0.9  # latest wins, no list
    finally:
        await bus.stop()


async def test_different_strategies_for_same_symbol_coexist():
    bus = EventBus()
    await bus.start()
    try:
        cache = OpportunityCache(bus)
        cache.start()

        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB"), symbol="NVDA"))
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("Gap"), symbol="NVDA"))
        await asyncio.sleep(0.05)

        snapshot = cache.get_snapshot(symbol="NVDA")
        assert set(snapshot["symbols"]["NVDA"].keys()) == {"ORB", "Gap"}
    finally:
        await bus.stop()


async def test_symbol_filter_excludes_other_symbols():
    bus = EventBus()
    await bus.start()
    try:
        cache = OpportunityCache(bus)
        cache.start()

        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB"), symbol="NVDA"))
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB"), symbol="AAPL"))
        await asyncio.sleep(0.05)

        assert cache.get_snapshot(symbol="AAPL")["symbols"].keys() == {"AAPL"}
        assert cache.get_snapshot()["symbols"].keys() == {"NVDA", "AAPL"}


    finally:
        await bus.stop()


async def test_absent_symbol_is_simply_missing_not_a_fabricated_placeholder():
    cache = OpportunityCache(EventBus())
    snapshot = cache.get_snapshot(symbol="TSLA")
    assert snapshot == {"symbols": {}}
    assert "TSLA" not in snapshot["symbols"]


async def test_envelope_with_no_symbol_is_dropped_not_guessed():
    bus = EventBus()
    await bus.start()
    try:
        cache = OpportunityCache(bus)
        cache.start()

        envelope = EventEnvelope(
            event_type=EventType.OPPORTUNITY_CREATED,
            symbol=None,
            payload=_make_opportunity("ORB").model_dump(mode="json"),
        )
        await bus.publish(envelope)
        await asyncio.sleep(0.05)

        assert cache.get_snapshot() == {"symbols": {}}
    finally:
        await bus.stop()


async def test_payload_missing_strategy_key_is_dropped_not_guessed():
    bus = EventBus()
    await bus.start()
    try:
        cache = OpportunityCache(bus)
        cache.start()

        payload = _make_opportunity("ORB").model_dump(mode="json")
        del payload["strategy"]
        envelope = EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol="NVDA", payload=payload)
        await bus.publish(envelope)
        await asyncio.sleep(0.05)

        assert cache.get_snapshot() == {"symbols": {}}
    finally:
        await bus.stop()


async def test_stop_is_a_no_op_and_does_not_raise():
    cache = OpportunityCache(EventBus())
    cache.start()
    await cache.stop()  # should not raise — see module docstring on why this is trivial


def test_get_opportunity_cache_is_a_lazy_singleton():
    import app.trading_intelligence.opportunity_cache as module

    module._opportunity_cache = None  # reset for test isolation, same pattern any singleton test needs
    try:
        first = module.get_opportunity_cache(EventBus())
        second = module.get_opportunity_cache(EventBus())  # second bus arg ignored once already constructed
        assert first is second
    finally:
        module._opportunity_cache = None
