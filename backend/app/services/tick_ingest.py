"""
Phase-3-minimal bridge from any MarketDataProvider's raw tick stream onto
the Event Bus — publishes PriceUpdated per tick, and buckets ticks into
1-minute CandleClosed events.

Renamed from IBKRIngestBridge (confirmed decision #31): its logic never
actually depended on IBKR — it only ever called adapter.on_tick(), which
is defined on MarketDataProvider, not BrokerAdapter specifically. Once a
second provider (PolygonAdapter) needed the exact same bucketing, keeping
the IBKR-specific name and type would have meant either duplicating this
logic or lying about what the class actually does.

This is explicitly NOT the real Market Data Engine (Phase 4,
docs/architecture/system-design.md §4.2) — no multi-symbol StateCache, no
persistence, no reconnect/backoff beyond what the adapter itself does. It
exists so Phase 3's exit criterion ("live ticks for 1 symbol flow adapter
-> engine -> chart") is honestly satisfiable without pulling Phase 4's
full scope forward. Gets replaced wholesale by Market Data Engine, not
extended into it — the bucketing logic here is intentionally
throwaway-quality (see confirmed decision #16).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.broker_adapters.base import MarketDataProvider, Tick
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated

logger = logging.getLogger(__name__)

# Small buffer past the wall-clock minute boundary before the flush loop
# (see TickIngestBridge._flush_loop) force-closes a stale bucket — gives a
# tick landing right at :00.000 of the new minute a moment to be processed
# by _handle_tick first, so the two paths don't race to close the same
# bucket. 250ms is generous relative to real tick jitter and negligible
# next to the multi-second-to-tens-of-seconds delay this loop exists to fix.
_FLUSH_MARGIN = timedelta(milliseconds=250)


class BridgeState(str, Enum):
    """Lifecycle of one TickIngestBridge (task retired-tick-bridge-isolation).

    ACTIVE   — callback registered, admitting ticks, flush loop running.
    RETIRING — stop() has been called: admission is OFF (synchronously, in
               the same call), owned tasks are cancelled but may not yet
               have finished unwinding. Terminal direction only.
    RETIRED  — aclose() has awaited every owned task; nothing owned remains.
    """

    ACTIVE = "active"
    RETIRING = "retiring"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class SymbolCandleExclusions:
    """Per-symbol candle-exclusion counts of one bridge (immutable)."""

    symbol: str
    older_than_active_bucket: int
    already_closed_minute: int

    @property
    def total(self) -> int:
        return self.older_than_active_bucket + self.already_closed_minute


@dataclass(frozen=True, slots=True)
class TickBridgeDiagnostics:
    """Immutable, copied read of one bridge's candle-exclusion counters
    (task late-tick-candle-diagnostics).

    ``bridge_id`` identifies this bridge INSTANCE: two reads are comparable
    only when their ids are equal. Counts are cumulative since that
    instance's construction (``created_at``) and describe ONLY ticks the
    bridge kept out of 1m candle construction; the raw ticks were still
    published as PriceUpdated. They say nothing about ticks that never
    reached the bridge. ``by_symbol`` is sorted by symbol and holds only
    symbols with at least one exclusion.
    """

    bridge_id: str
    state: BridgeState
    created_at: datetime
    read_at: datetime
    older_than_active_bucket: int
    already_closed_minute: int
    by_symbol: tuple[SymbolCandleExclusions, ...]

    @property
    def total(self) -> int:
        return self.older_than_active_bucket + self.already_closed_minute


class _MinuteBucket:
    __slots__ = ("minute_ts", "open", "high", "low", "close", "volume")

    def __init__(self, minute_ts: datetime, price: float, size: int) -> None:
        self.minute_ts = minute_ts
        self.open = self.high = self.low = self.close = price
        self.volume = size

    def add(self, price: float, size: int) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size

    def to_candle_closed(self) -> CandleClosed:
        return CandleClosed(
            timeframe="1m",
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            candle_ts=self.minute_ts,
        )


class TickIngestBridge:
    """Registers itself as the provider's tick callback on construction.
    Works identically regardless of whether ticks arrive from a genuine
    push stream (IBKR, many ticks/minute) or from delayed REST polling
    that only has one data point per minute (Polygon's free tier — see
    PolygonAdapter's docstring). In the latter case each "bucket" just
    ends up holding a single tick, which is correct, not a bug: bucketing
    on real trade granularity when the underlying data doesn't have that
    granularity would be fabricating precision that isn't there.

    Lifecycle (task retired-tick-bridge-isolation): ACTIVE -> RETIRING
    (stop(), synchronous: admission off) -> RETIRED (aclose(): owned tasks
    settled). A retired bridge may stay registered on a provider that is
    retained for another role (e.g. historical); its callback is then inert.
    See docs/architecture/system-design.md §4.2 "Bridge retirement"."""

    def __init__(self, provider: MarketDataProvider, bus: EventBus) -> None:
        self._provider = provider
        self._bus = bus
        self._buckets: dict[str, _MinuteBucket] = {}
        # Needed even when the wall-clock flush leaves no active bucket.
        self._last_closed_minute: dict[str, datetime] = {}
        # Keep same-symbol closes ordered across the bus publish await.
        self._bucket_locks: dict[str, asyncio.Lock] = {}
        self._state = BridgeState.ACTIVE
        # Candle-exclusion diagnostics (task late-tick-candle-diagnostics).
        # Instance identity + two per-symbol counters. Entries are created
        # only at an actual exclusion, which can only happen for a symbol
        # that already owns a bucket / last-closed entry above, so storage is
        # bounded by tracked symbols and never grows per tick. Counters are
        # never reset (stop() leaves them readable, frozen).
        self._bridge_id = uuid.uuid4().hex
        self._created_at = datetime.now(timezone.utc)
        self._excluded_older_than_active: dict[str, int] = {}
        self._excluded_already_closed: dict[str, int] = {}
        # Every task this bridge creates (per-tick handlers + the flush
        # loop) is owned here until it finishes, so retirement can cancel
        # and settle exactly the work it created — and nothing else.
        self._tick_tasks: set[asyncio.Task] = set()
        self._flush_task: asyncio.Task | None = None
        self._draining: set[asyncio.Task] = set()
        # Wall-clock-driven close — see _flush_loop's docstring for the bug
        # this fixes. Self-starting here (constructor, not an explicit
        # start()) because this bridge is constructed inline by the connect
        # routes, not centrally managed by main.py's lifespan. The flush
        # task is created BEFORE the callback is registered so a missing
        # event loop fails the constructor without leaving a callback
        # registered on the provider.
        self._flush_task = asyncio.create_task(self._flush_loop(), name="tick-ingest-flush")
        try:
            provider.on_tick(self._on_tick)
        except BaseException:
            self._flush_task.cancel()
            self._flush_task = None
            self._state = BridgeState.RETIRED
            raise

    # --- lifecycle ----------------------------------------------------------

    @property
    def state(self) -> BridgeState:
        return self._state

    @property
    def is_admitting(self) -> bool:
        """True only while ACTIVE. Checked synchronously before any task is
        created and again before every publish."""
        return self._state is BridgeState.ACTIVE

    def stop(self) -> None:
        """Synchronous, idempotent retirement. When this returns:

          * admission is OFF — _on_tick creates no further work, and no
            queued/in-flight handler or flush pass can call bus.publish
            again (every publish site re-checks is_admitting first);
          * the flush loop and every not-yet-finished tick handler are
            cancelled (a handler that never started never runs);
          * partially filled minute buckets are DISCARDED, not published —
            a retired source must not emit a candle the replacement may
            also produce for the same minute;
          * the provider callback is removed if (and only if) the provider
            offers a public ``remove_tick_callback`` capability; otherwise
            it stays registered but inert.

        Not undoable, and NOT awaited: cancelled tasks may still be
        unwinding. Use ``await aclose()`` (or the registry's
        settle_retired_bridges()) to wait for them. Events already put on
        the EventBus queues before this call cannot be retracted."""
        if self._state is not BridgeState.ACTIVE:
            return
        self._state = BridgeState.RETIRING
        current = asyncio.current_task() if _has_running_loop() else None
        owned = set(self._tick_tasks)
        if self._flush_task is not None:
            owned.add(self._flush_task)
        self._draining |= owned
        self._flush_task = None
        for task in owned:
            if task is not current and not task.done():
                try:
                    task.cancel()
                except RuntimeError:  # owning event loop already closed
                    pass
        self._buckets.clear()
        self._last_closed_minute.clear()
        self._bucket_locks.clear()
        remover = getattr(self._provider, "remove_tick_callback", None)
        if callable(remover):
            try:
                remover(self._on_tick)
            except Exception:  # noqa: BLE001 — retirement must never raise
                logger.warning("Provider remove_tick_callback failed; callback left inert")

    async def aclose(self) -> None:
        """stop() + wait until every owned task has finished. Idempotent and
        safe to call concurrently (each caller awaits the same tasks).
        Never waits on a stuck bus: owned tasks are already cancelled."""
        self.stop()
        loop = asyncio.get_running_loop()
        current = asyncio.current_task()
        pending = [
            t for t in self._draining
            if t is not current and not t.done() and t.get_loop() is loop
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._draining = {t for t in self._draining if not t.done()}
        if not self._draining:
            self._state = BridgeState.RETIRED
        elif all(t.get_loop() is not loop for t in self._draining):
            # Leftovers belong to a different (closed) loop and cannot be
            # awaited here; nothing further can be settled.
            self._draining.clear()
            self._state = BridgeState.RETIRED

    # --- diagnostics ----------------------------------------------------------

    def get_diagnostics_snapshot(self) -> TickBridgeDiagnostics:
        """Synchronous, I/O-free, side-effect-free copy of the candle-exclusion
        counters. Takes no lock and awaits nothing (single-threaded event
        loop: counters change only between awaits, so a read is a consistent
        point-in-time view). The result shares no mutable state with the
        bridge and works in any lifecycle state."""
        older = dict(self._excluded_older_than_active)
        closed = dict(self._excluded_already_closed)
        rows = tuple(
            SymbolCandleExclusions(symbol, older.get(symbol, 0), closed.get(symbol, 0))
            for symbol in sorted(set(older) | set(closed))
        )
        return TickBridgeDiagnostics(
            bridge_id=self._bridge_id,
            state=self._state,
            created_at=self._created_at,
            read_at=datetime.now(timezone.utc),
            older_than_active_bucket=sum(older.values()),
            already_closed_minute=sum(closed.values()),
            by_symbol=rows,
        )

    # --- tick path ------------------------------------------------------------

    def _on_tick(self, tick: Tick) -> None:
        # on_tick's callback is sync per the MarketDataProvider interface,
        # but EventBus.publish() is async — hand off to the running loop.
        # A retired bridge's callback may still be registered on a provider
        # that was kept for another role; it must create NO new work.
        if self._state is not BridgeState.ACTIVE:
            return
        task = asyncio.create_task(self._handle_tick(tick), name="tick-ingest-tick")
        self._tick_tasks.add(task)
        task.add_done_callback(self._tick_task_done)

    def _tick_task_done(self, task: asyncio.Task) -> None:
        self._tick_tasks.discard(task)
        self._draining.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Tick handling failed", exc_info=task.exception())

    async def _handle_tick(self, tick: Tick) -> None:
        # Queued work guard: a handler scheduled while ACTIVE may first run
        # after retirement (cancellation normally prevents that; this guard
        # also covers a handler already running when it was retired).
        if self._state is not BridgeState.ACTIVE:
            return
        await self._bus.publish(
            make_envelope(
                EventType.PRICE_UPDATED,
                PriceUpdated(price=tick.price, size=tick.size, exchange_ts=tick.exchange_ts),
                symbol=tick.symbol,
            )
        )
        if self._state is not BridgeState.ACTIVE:
            return  # retired while the publish above was suspended

        lock = self._bucket_locks.setdefault(tick.symbol, asyncio.Lock())
        async with lock:
            if self._state is not BridgeState.ACTIVE:
                return  # retired while waiting for another same-symbol close
            minute_ts = tick.exchange_ts.replace(second=0, microsecond=0)
            closed_minute = self._last_closed_minute.get(tick.symbol)
            if closed_minute is not None and minute_ts <= closed_minute:
                # raw tick published, but never reopen a closed candle
                self._excluded_already_closed[tick.symbol] = self._excluded_already_closed.get(tick.symbol, 0) + 1
                return
            bucket = self._buckets.get(tick.symbol)
            if bucket is not None and minute_ts < bucket.minute_ts:
                # an older tick must not close a newer minute
                self._excluded_older_than_active[tick.symbol] = self._excluded_older_than_active.get(tick.symbol, 0) + 1
                return
            if bucket is not None and minute_ts > bucket.minute_ts:
                await self._bus.publish(
                    make_envelope(EventType.CANDLE_CLOSED, bucket.to_candle_closed(), symbol=tick.symbol)
                )
                if self._state is not BridgeState.ACTIVE:
                    return
                self._last_closed_minute[tick.symbol] = bucket.minute_ts
                self._buckets[tick.symbol] = _MinuteBucket(minute_ts, tick.price, tick.size)
            elif bucket is None:
                self._buckets[tick.symbol] = _MinuteBucket(minute_ts, tick.price, tick.size)
            else:
                bucket.add(tick.price, tick.size)

    async def _flush_loop(self) -> None:
        """
        Bug fix (confirmed decision #42): candle closes used to be entirely
        tick-triggered — _handle_tick only ever published the PREVIOUS
        minute's CandleClosed once a tick for the NEXT minute happened to
        arrive (see the rollover check above). On a quiet moment with no
        trade right at :00, that meant the previous candle — and therefore
        the new candle's "arrival" on the chart — showed up however many
        seconds late the next trade happened to be. Reported symptom: a
        09:34 candle not appearing until 09:34:42 because nothing traded
        between :00 and :42.

        This loop wakes shortly after every wall-clock minute boundary and
        force-closes any bucket whose minute has fully elapsed, regardless
        of whether a new tick has arrived yet. It only fixes the CLOSE
        side — a bucket still only opens once the first tick of a minute
        arrives (unchanged) — so a symbol with genuinely zero trades in a
        minute still correctly produces no candle for that minute, not a
        fabricated flat one.

        Runs once per symbol currently tracked, every minute — O(symbols),
        not O(ticks) — and self-heals across any gap (a provider hiccup
        that misses a few minutes still gets caught and flushed on the
        very next wake-up, since the check is "is this bucket's minute in
        the past", not "is this bucket exactly one minute old").
        """
        try:
            while self._state is BridgeState.ACTIVE:
                await asyncio.sleep(self._seconds_until_next_flush())
                await self._flush_stale_buckets(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            pass

    def _seconds_until_next_flush(self) -> float:
        now = datetime.now(timezone.utc)
        next_boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1) + _FLUSH_MARGIN
        return max(0.0, (next_boundary - now).total_seconds())

    async def _flush_stale_buckets(self, now: datetime) -> None:
        if self._state is not BridgeState.ACTIVE:
            return
        current_minute = now.replace(second=0, microsecond=0)
        for symbol in list(self._buckets.keys()):
            lock = self._bucket_locks[symbol]
            async with lock:
                if self._state is not BridgeState.ACTIVE:
                    return  # retired mid-pass: remaining buckets are discarded
                bucket = self._buckets.get(symbol)
                if bucket is not None and bucket.minute_ts < current_minute:
                    await self._bus.publish(
                        make_envelope(EventType.CANDLE_CLOSED, bucket.to_candle_closed(), symbol=symbol)
                    )
                    if self._state is not BridgeState.ACTIVE:
                        return
                    del self._buckets[symbol]
                    self._last_closed_minute[symbol] = bucket.minute_ts


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
