"""Reusable, opt-in scanner observation worker. No application startup wiring."""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.scanner.runner import ScanResult, run_scan
from app.scanner.universe import DbUniverseProvider, UniverseProvider

SCAN_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class ObservedScanRow:
    symbol: str
    score: float
    inputs_available: int
    features: Mapping[str, float]
    # `scanner-observation-source-timestamps`: candle_ts of the 1m FeatureSet
    # that supplied this row's score inputs (captured by run_scan from the
    # same snapshot). Belongs to the retained successful result: a later
    # failed cycle replaces only error/attempt fields, never rows. None =
    # unknown (never fabricated).
    source_candle_ts: datetime | None = None


@dataclass(frozen=True)
class ObservationSnapshot:
    universe: tuple[str, ...] = ()
    results: tuple[ObservedScanRow, ...] = ()
    skipped: tuple[str, ...] = ()
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    running: bool = False
    cycle_running: bool = False


class ScannerObservationWorker:
    """One timer and at most one cycle. All public methods use its owning loop.

    The universe provider is synchronous and owns a fresh session per read.
    stop() drains an in-flight to_thread read before returning; a stuck DB
    operation can therefore delay shutdown. Its result is never published
    after stop begins, even if the thread completes later.
    """

    def __init__(
        self,
        *,
        eligible: Callable[[], bool],
        universe_provider: UniverseProvider | None = None,
        scan: Callable[..., tuple[list[ScanResult], list[str]]] = run_scan,
        settings: Settings | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._eligible = eligible
        self._universe_provider = universe_provider or DbUniverseProvider(SessionLocal)
        self._scan = scan
        self._settings = settings or get_settings()
        self._monotonic = monotonic
        self._wait = wait
        self._now = now
        self._lifecycle_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._generation = 0
        self._snapshot = ObservationSnapshot()

    def get_snapshot(self) -> ObservationSnapshot:
        return self._snapshot

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._task is not None and self._task.done():
                self._task.result()
                self._task = None
            if self._task is not None:
                return
            self._generation += 1
            self._stop_event = asyncio.Event()
            self._snapshot = replace(self._snapshot, running=True, cycle_running=False)
            self._task = asyncio.create_task(self._run(self._generation, self._stop_event))

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self._task is None:
                return
            self._generation += 1  # invalidate any in-flight publication
            self._stop_event.set()
            self._snapshot = replace(self._snapshot, running=False)
            cancelled = False
            try:
                # Shield the owner task: cancelling a stop caller must not
                # cancel asyncio.to_thread while its DB thread keeps running.
                while not self._task.done():
                    try:
                        await asyncio.shield(self._task)
                    except asyncio.CancelledError:
                        cancelled = True
                self._task.result()
            finally:
                self._task = None
                self._snapshot = replace(self._snapshot, running=False, cycle_running=False)
            if cancelled:
                raise asyncio.CancelledError

    async def _wait_or_stop(self, delay: float, stop_event: asyncio.Event) -> None:
        timer = asyncio.create_task(self._wait(delay))
        stopper = asyncio.create_task(stop_event.wait())
        try:
            await asyncio.wait((timer, stopper), return_when=asyncio.FIRST_COMPLETED)
            if timer.done() and not timer.cancelled():
                timer.result()
        finally:
            for task in (timer, stopper):
                if not task.done():
                    task.cancel()
            await asyncio.gather(timer, stopper, return_exceptions=True)

    async def _run(self, generation: int, stop_event: asyncio.Event) -> None:
        try:
            next_due = self._monotonic()  # first admitted cycle is immediate
            while not stop_event.is_set():
                current = self._monotonic()
                if current < next_due:
                    await self._wait_or_stop(next_due - current, stop_event)
                    continue
                if stop_event.is_set():
                    break
                # Advance to the first future boundary. Missed boundaries are
                # coalesced; completion after a deadline can cause one immediate
                # cycle, never a loop of historical cycles.
                next_due += (math.floor((current - next_due) / SCAN_INTERVAL_SECONDS) + 1) * SCAN_INTERVAL_SECONDS
                try:
                    admitted = self._eligible()
                except Exception as exc:
                    self._snapshot = replace(
                        self._snapshot,
                        last_attempt_at=self._now(),
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                    continue
                if admitted:
                    await self._cycle(generation, stop_event)
        except Exception as exc:
            if generation == self._generation:
                self._snapshot = replace(
                    self._snapshot, last_error=f"{type(exc).__name__}: {exc}"
                )
        finally:
            if generation == self._generation:
                self._snapshot = replace(self._snapshot, running=False, cycle_running=False)

    async def _cycle(self, generation: int, stop_event: asyncio.Event) -> None:
        attempted_at = self._now()
        self._snapshot = replace(
            self._snapshot, last_attempt_at=attempted_at, last_error=None, cycle_running=True
        )
        try:
            # DbUniverseProvider creates and closes its Session in this
            # thread. Never pass a Session across the event-loop boundary.
            universe = tuple(await asyncio.to_thread(self._universe_provider.get_core_universe))
            if stop_event.is_set() or generation != self._generation:
                return
            results, skipped = self._scan(
                list(universe),
                weight_rvol=self._settings.scanner_weight_rvol,
                weight_gap=self._settings.scanner_weight_gap,
                weight_session_change=self._settings.scanner_weight_session_change,
                weight_premarket_volume_ratio=self._settings.scanner_weight_premarket_volume_ratio,
            )
            # Copy mutable runner payloads before publication. FeatureEngine
            # snapshots and the scorer stay on this owning event loop.
            rows = tuple(
                ObservedScanRow(
                    r.symbol,
                    r.score,
                    r.inputs_available,
                    MappingProxyType(dict(r.features)),
                    # getattr: an injected `scan` double that predates the
                    # field yields "unknown", not an AttributeError.
                    getattr(r, "source_candle_ts", None),
                )
                for r in results
            )
            if not stop_event.is_set() and generation == self._generation:
                self._snapshot = ObservationSnapshot(
                    universe=universe,
                    results=rows,
                    skipped=tuple(skipped),
                    last_attempt_at=attempted_at,
                    last_success_at=self._now(),
                    running=True,
                    cycle_running=False,
                )
        except Exception as exc:
            if not stop_event.is_set() and generation == self._generation:
                self._snapshot = replace(
                    self._snapshot,
                    last_error=f"{type(exc).__name__}: {exc}",
                    cycle_running=False,
                )
        finally:
            if generation == self._generation and self._snapshot.cycle_running:
                self._snapshot = replace(self._snapshot, cycle_running=False)
