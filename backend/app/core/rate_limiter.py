"""
Async rate limiter — sliding-window token bucket. Built specifically
because Polygon.io's free tier allows only 5 REST calls/minute
(confirmed decision #30), and that budget is shared across every call
the adapter makes (polling AND historical backfill) — a naive
fire-and-forget call pattern (fine for IBKR, which has no such limit)
would get 429'd almost immediately here.

Not Polygon-specific — any provider with a hard rate ceiling can reuse
this rather than each adapter inventing its own throttling.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: float) -> None:
        if max_calls <= 0 or period_seconds <= 0:
            raise ValueError("max_calls and period_seconds must be positive")
        self._max_calls = max_calls
        self._period = period_seconds
        self._call_times: deque[float] = deque()
        self._lock = asyncio.Lock()

    @property
    def max_calls(self) -> int:
        return self._max_calls

    async def acquire(self) -> None:
        """
        Blocks until a call is safe to make, then records it. Callers
        should call this immediately before making the rate-limited call,
        not speculatively ahead of time.
        """
        async with self._lock:
            while True:
                now = time.monotonic()
                # Drop timestamps outside the current window.
                while self._call_times and now - self._call_times[0] >= self._period:
                    self._call_times.popleft()

                if len(self._call_times) < self._max_calls:
                    self._call_times.append(now)
                    return

                wait = self._period - (now - self._call_times[0])
                await asyncio.sleep(max(wait, 0.01))

    def calls_in_window(self) -> int:
        """Observability — how many calls currently count against the window."""
        now = time.monotonic()
        while self._call_times and now - self._call_times[0] >= self._period:
            self._call_times.popleft()
        return len(self._call_times)
