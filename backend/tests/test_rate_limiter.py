import asyncio
import time

import pytest

from app.core.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_calls_within_budget_do_not_wait():
    limiter = RateLimiter(max_calls=5, period_seconds=60)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # all 5 should be near-instant


@pytest.mark.asyncio
async def test_call_beyond_budget_waits_for_window_to_clear():
    # Small period so the test doesn't take 60s to prove the point.
    limiter = RateLimiter(max_calls=2, period_seconds=0.3)
    await limiter.acquire()
    await limiter.acquire()

    start = time.monotonic()
    await limiter.acquire()  # 3rd call must wait for the window to clear
    elapsed = time.monotonic() - start
    assert elapsed >= 0.25  # allow a little slack, but it must have actually waited


@pytest.mark.asyncio
async def test_calls_in_window_reports_accurately():
    limiter = RateLimiter(max_calls=5, period_seconds=60)
    assert limiter.calls_in_window() == 0
    await limiter.acquire()
    await limiter.acquire()
    assert limiter.calls_in_window() == 2


def test_invalid_arguments_rejected():
    with pytest.raises(ValueError):
        RateLimiter(max_calls=0, period_seconds=60)
    with pytest.raises(ValueError):
        RateLimiter(max_calls=5, period_seconds=0)


@pytest.mark.asyncio
async def test_concurrent_acquires_are_serialized_correctly():
    """Confirms the lock actually prevents a burst of concurrent
    coroutines from all sneaking past the budget at once — the failure
    mode that would matter most in practice (many symbols polling
    simultaneously)."""
    limiter = RateLimiter(max_calls=3, period_seconds=0.3)

    results = []

    async def call(i):
        await limiter.acquire()
        results.append(i)

    await asyncio.gather(*(call(i) for i in range(6)))
    assert len(results) == 6  # all eventually succeeded, none lost
    assert limiter.calls_in_window() <= 3  # never more than budget in-window at any check
