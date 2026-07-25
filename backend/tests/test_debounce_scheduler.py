import asyncio

import pytest

from app.core.debounce_scheduler import DebounceScheduler


@pytest.mark.asyncio
async def test_first_trigger_runs_immediately():
    calls: list[int] = []
    sched = DebounceScheduler(lambda: calls.append(1), min_interval=1.0, max_interval=10.0)

    await sched.trigger()
    assert calls == [1]


@pytest.mark.asyncio
async def test_rapid_triggers_are_floored_to_min_interval():
    calls: list[int] = []
    sched = DebounceScheduler(lambda: calls.append(1), min_interval=0.2, max_interval=10.0)

    await sched.trigger()  # runs immediately -> 1 call
    await sched.trigger()  # too soon, marks pending
    await sched.trigger()  # still too soon, no additional pending run scheduled
    assert calls == [1]

    await asyncio.sleep(0.3)  # let the floor lift
    assert calls == [1, 1]  # the single pending catch-up run fired, not three


@pytest.mark.asyncio
async def test_ceiling_loop_runs_callback_even_with_no_triggers():
    calls: list[int] = []
    sched = DebounceScheduler(lambda: calls.append(1), min_interval=0.05, max_interval=0.15)

    await sched.start()
    try:
        assert calls == []  # nothing yet — no trigger() call made
        await asyncio.sleep(0.35)
        # ~0.35s / 0.15s ceiling => at least 2 ceiling-driven runs
        assert len(calls) >= 2
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_invalid_intervals_rejected():
    with pytest.raises(ValueError):
        DebounceScheduler(lambda: None, min_interval=5.0, max_interval=1.0)
    with pytest.raises(ValueError):
        DebounceScheduler(lambda: None, min_interval=0, max_interval=1.0)
