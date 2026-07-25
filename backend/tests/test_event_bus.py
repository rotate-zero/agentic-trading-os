import asyncio

import pytest

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.dev import DevPing
from app.schemas.events.envelope import EventType
from app.schemas.events.execution import GovernorDecision


@pytest.mark.asyncio
async def test_normal_event_reaches_subscriber():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe(EventType.DEV_PING, lambda env: received.append(env))

        await bus.publish(make_envelope(EventType.DEV_PING, DevPing(message="hi", lane="normal")))
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].payload["message"] == "hi"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_wildcard_subscriber_receives_all_event_types():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []
        bus.subscribe_all(lambda env: received.append(env.event_type))

        await bus.publish(make_envelope(EventType.DEV_PING, DevPing(message="a", lane="normal")))
        await bus.publish(
            make_envelope(EventType.GOVERNOR_DECISION, GovernorDecision(action="rejected", reasons=["x"]))
        )
        await asyncio.sleep(0.05)

        assert set(received) == {EventType.DEV_PING, EventType.GOVERNOR_DECISION}
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_critical_lane_is_not_blocked_by_slow_normal_subscriber():
    """
    The whole point of confirmed decision #9: a slow/backed-up normal lane
    must never delay a critical event. Simulate a slow normal-lane
    subscriber and confirm the critical event still lands promptly.
    """
    bus = EventBus()
    await bus.start()
    try:
        normal_started = asyncio.Event()

        async def slow_normal_handler(_env) -> None:
            normal_started.set()
            await asyncio.sleep(2.0)  # much longer than the critical event's budget

        critical_received = asyncio.Event()
        bus.subscribe(EventType.DEV_PING, slow_normal_handler)
        bus.subscribe(EventType.GOVERNOR_DECISION, lambda env: critical_received.set())

        # Publish a normal event that will hang its handler for 2s...
        await bus.publish(make_envelope(EventType.DEV_PING, DevPing(message="slow", lane="normal")))
        await normal_started.wait()

        # ...then publish a critical event. It must arrive well before the
        # normal-lane handler finishes, because it's on an isolated queue.
        await bus.publish(
            make_envelope(EventType.GOVERNOR_DECISION, GovernorDecision(action="rejected", reasons=["x"]))
        )

        await asyncio.wait_for(critical_received.wait(), timeout=0.5)
        assert critical_received.is_set()
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_subscriber_exception_does_not_break_the_bus():
    bus = EventBus()
    await bus.start()
    try:
        received: list = []

        def bad_handler(_env):
            raise RuntimeError("boom")

        bus.subscribe(EventType.DEV_PING, bad_handler)
        bus.subscribe(EventType.DEV_PING, lambda env: received.append(env))

        await bus.publish(make_envelope(EventType.DEV_PING, DevPing(message="hi", lane="normal")))
        await asyncio.sleep(0.05)

        assert len(received) == 1  # the well-behaved subscriber still ran
    finally:
        await bus.stop()
