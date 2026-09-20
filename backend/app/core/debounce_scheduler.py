"""
Shared event-driven + min/max-interval update policy.
See docs/decisions/confirmed-decisions.md #10.

Used by Market State Engine and Position Monitor (Phase 5) so recompute is:
  - triggered promptly by relevant events
  - floored, so a burst of events doesn't cause redundant recompute
  - ceilinged, so state can't silently go stale in a quiet market

Deliberately NOT used by Feature Engine, Governor, or Execution Engine —
those react to every event with no debounce, since staleness there is
either wrong indicator values or a safety issue.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

Callback = Callable[[], Awaitable[None] | None]


class DebounceScheduler:
    """
    Wraps a callback with a min/max recompute interval.

    - trigger(): call on every relevant upstream event. Runs the callback
      immediately if at least `min_interval` seconds have passed since the
      last run; otherwise marks a run as pending and lets the ceiling loop
      (or a later trigger) pick it up.
    - A background task guarantees the callback still runs at least once
      every `max_interval` seconds even if nothing ever calls trigger().
    """

    def __init__(
        self,
        callback: Callback,
        min_interval: float,
        max_interval: float,
        *,
        name: str = "debounce_scheduler",
    ) -> None:
        if min_interval <= 0 or max_interval <= 0:
            raise ValueError("min_interval and max_interval must be positive")
        if max_interval < min_interval:
            raise ValueError("max_interval must be >= min_interval")

        self._callback = callback
        self._min_interval = min_interval
        self._max_interval = max_interval
        self._name = name

        self._last_run: float = 0.0
        self._pending: bool = False
        self._lock = asyncio.Lock()
        self._ceiling_task: asyncio.Task | None = None
        self._pending_run_task: asyncio.Task | None = None

    async def trigger(self) -> None:
        """Call this on every relevant upstream event."""
        now = time.monotonic()
        elapsed = now - self._last_run

        if elapsed >= self._min_interval:
            await self._run()
            return

        # Too soon — mark pending and schedule exactly one catch-up run
        # at the point the floor lifts, rather than firing on every event.
        self._pending = True
        if self._pending_run_task is None or self._pending_run_task.done():
            delay = self._min_interval - elapsed
            self._pending_run_task = asyncio.create_task(self._run_after_delay(delay))

    async def _run_after_delay(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._run(only_if_pending=True)

    async def _run(self, *, only_if_pending: bool = False) -> bool:
        async with self._lock:
            if only_if_pending and not self._pending:
                return False
            self._last_run = time.monotonic()
            self._pending = False
            result = self._callback()
            if asyncio.iscoroutine(result):
                await result
            return True

    async def flush(self) -> bool:
        """Run a pending callback now and neutralize its delayed task.

        This is intentionally narrower than an unconditional run: when
        the latest trigger already ran immediately, ``flush()`` is a
        no-op. The pending check and callback share ``_lock`` with the
        delayed path, so one trigger cannot become two invocations.
        """
        ran = await self._run(only_if_pending=True)
        task = self._pending_run_task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._pending_run_task is task:
            self._pending_run_task = None
        return ran

    async def start(self) -> None:
        """Start the background ceiling loop — guarantees max_interval freshness."""
        if self._ceiling_task is not None:
            return
        self._ceiling_task = asyncio.create_task(self._ceiling_loop(), name=f"{self._name}-ceiling")

    async def stop(self) -> None:
        tasks = [task for task in (self._ceiling_task, self._pending_run_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._ceiling_task = None
        self._pending_run_task = None

    async def _ceiling_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._max_interval)
                if time.monotonic() - self._last_run >= self._max_interval:
                    await self._run()
        except asyncio.CancelledError:
            pass
