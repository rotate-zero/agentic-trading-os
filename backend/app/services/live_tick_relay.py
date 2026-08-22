"""
LiveTickRelay — confirmed decision #72. Throttles the existing raw
PriceUpdated tick stream into a bounded-rate PriceSnapshot per symbol, for
"tick fluidity" on the chart's currently-forming bar: the wick/close of the
last (not-yet-closed) candle breathing with real trades, without pushing
every single tick to the frontend.

Deliberately built as a NEW, standalone Event Bus subscriber rather than a
change to TickIngestBridge (app/services/tick_ingest.py). That class is
explicitly documented (confirmed decision #16) as Phase-3-throwaway —
"gets replaced wholesale, not extended, when the real Phase 4 Market Data
Engine is built" — so patching a throttle into its internals would be a
live conflict with that decision. This relay reads the SAME PriceUpdated
events TickIngestBridge already publishes on the bus and keeps its own
independent per-symbol accumulator; it shares no state and has no
dependency on tick_ingest.py at all, so it can be deleted or folded into
the real Market Data Engine later without touching that module either way.

Scope is deliberately narrow, matching what was actually asked for: a
small, explicitly-set "active symbols" list (max 8 — Saqib's own number,
sized for "whatever the scanning process currently flags as most active,"
not the full ~100-symbol universe), throttled to one snapshot per symbol
per flush interval (default 5s). This is NOT the ~100-symbol Feature
Engine data path and is NOT a new candle resolution — it only ever
describes the CURRENT, still-forming 1m bar. Closed-bar behavior
(CandleClosed / CandleRecorder / FeatureEngine) is completely unaffected;
this relay never publishes CandleClosed and never persists anything.

Honest-state discipline (matches tick_ingest.py's own "a symbol with zero
trades produces no candle" rule): a symbol with no new ticks since the
last flush is simply skipped that cycle — never re-published with a
fabricated flat bar, and never a synthetic snapshot for a symbol that
hasn't traded at all yet this minute.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import PriceSnapshot

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE_SYMBOLS = 8
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0


class _LiveBar:
    """One symbol's currently-forming bar, accumulated independently of
    TickIngestBridge's own (private) bucket — see module docstring for why
    this isn't shared/reused instead."""

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

    def to_snapshot(self) -> PriceSnapshot:
        return PriceSnapshot(
            timeframe="1m",
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            candle_ts=self.minute_ts,
        )


class LiveTickRelay:
    def __init__(
        self,
        bus: EventBus,
        *,
        max_active_symbols: int = DEFAULT_MAX_ACTIVE_SYMBOLS,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._bus = bus
        self._max_active_symbols = max_active_symbols
        self._flush_interval_seconds = flush_interval_seconds

        self._active_symbols: set[str] = set()
        self._bars: dict[str, _LiveBar] = {}
        self._dirty: set[str] = set()

        self._queue: asyncio.Queue[tuple[str, float, int, datetime]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None

    # --- active-symbol management -----------------------------------------

    def set_active_symbols(self, symbols: list[str]) -> None:
        """Replaces the actively-monitored set wholesale — intended caller
        is whatever selection process (Market Scanner, eventually; a manual
        call meanwhile) decides "these are the most-active symbols right
        now." Raises ValueError over max_active_symbols rather than
        silently truncating: this is a config endpoint, and a caller
        expecting all N symbols tracked should find out immediately if
        that's not what happened, not lose symbols quietly to a log line.
        """
        deduped = list(dict.fromkeys(symbols))  # de-dupe, preserve order — no meaning attached to order today
        if len(deduped) > self._max_active_symbols:
            raise ValueError(
                f"{len(deduped)} symbols given, max {self._max_active_symbols} — "
                "narrow the selection before calling set_active_symbols()."
            )

        new_set = set(deduped)
        removed = self._active_symbols - new_set
        for symbol in removed:
            self._bars.pop(symbol, None)
            self._dirty.discard(symbol)

        self._active_symbols = new_set
        logger.info("LiveTickRelay active symbols set to %s", sorted(new_set))

    def get_active_symbols(self) -> list[str]:
        return sorted(self._active_symbols)

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._bus.subscribe(EventType.PRICE_UPDATED, self._on_price_updated)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="live-tick-relay-worker")
        self._flush_task = asyncio.create_task(self._flush_loop(), name="live-tick-relay-flush")
        logger.info(
            "LiveTickRelay started (max %s active symbols, %ss flush interval)",
            self._max_active_symbols, self._flush_interval_seconds,
        )

    async def stop(self) -> None:
        """Awaits actual task completion, not just cancellation scheduling
        — same reasoning as CandleRecorder/FeatureEngine's stop() (decision
        #47): a bare .cancel() with no await can leave this task still
        touching self._bars/self._dirty after stop() returns."""
        for task in (self._worker_task, self._flush_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._worker_task = None
        self._flush_task = None

    # --- Event Bus subscriber (must stay fast — same reasoning as CandleRecorder) ---

    def _on_price_updated(self, envelope: EventEnvelope) -> None:
        symbol = envelope.symbol
        if symbol is None or symbol not in self._active_symbols:
            return  # not one of the small set this relay tracks — cheap to skip, nothing to enqueue
        price = envelope.payload.get("price")
        size = envelope.payload.get("size")
        exchange_ts = envelope.payload.get("exchange_ts")
        if price is None or size is None or exchange_ts is None:
            return  # malformed — skip rather than crash the queue on one bad envelope
        ts = datetime.fromisoformat(exchange_ts) if isinstance(exchange_ts, str) else exchange_ts
        self._queue.put_nowait((symbol, float(price), int(size), ts))

    # --- background worker: applies ticks to the in-memory accumulator ------

    async def _worker_loop(self) -> None:
        try:
            while True:
                symbol, price, size, exchange_ts = await self._queue.get()
                try:
                    self._apply_tick(symbol, price, size, exchange_ts)
                except Exception:  # noqa: BLE001 — one bad tick must not stall the other active symbols
                    logger.exception("LiveTickRelay failed to apply a tick for %s", symbol)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    def _apply_tick(self, symbol: str, price: float, size: int, exchange_ts: datetime) -> None:
        minute_ts = exchange_ts.replace(second=0, microsecond=0)
        bar = self._bars.get(symbol)
        if bar is None or bar.minute_ts != minute_ts:
            # Either the first tick this relay has seen for this symbol, or
            # the clock rolled over to a new minute — start a fresh bar.
            # This relay never publishes the OLD bar as a closed candle
            # (that's CandleClosed/TickIngestBridge's job, not this one's)
            # — it's simply discarded here, since by the time the next
            # flush fires, the new bar is what's actually current.
            bar = _LiveBar(minute_ts, price, size)
            self._bars[symbol] = bar
        else:
            bar.add(price, size)
        self._dirty.add(symbol)

    # --- periodic flush: publishes throttled snapshots -----------------------

    async def _flush_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._flush_interval_seconds)
                await self._flush_dirty_bars()
        except asyncio.CancelledError:
            pass

    async def _flush_dirty_bars(self) -> None:
        # Snapshot-and-clear the dirty set up front — a tick arriving for a
        # symbol while this loop is mid-publish then correctly marks it
        # dirty again for the NEXT cycle, rather than being lost.
        symbols = list(self._dirty)
        self._dirty.clear()
        for symbol in symbols:
            bar = self._bars.get(symbol)
            if bar is None or symbol not in self._active_symbols:
                continue  # removed via set_active_symbols mid-cycle — nothing to publish
            await self._bus.publish(
                make_envelope(EventType.PRICE_SNAPSHOT, bar.to_snapshot(), symbol=symbol)
            )


_live_tick_relay: LiveTickRelay | None = None


def get_live_tick_relay(bus: EventBus | None = None, **kwargs: Any) -> LiveTickRelay:
    """Lazy singleton, matching get_feature_engine()'s existing pattern
    (confirmed decision #47) — lets routes reach the SAME instance
    main.py's lifespan started. Tests should construct LiveTickRelay(...)
    directly for an isolated instance, the same as FeatureEngine's own
    tests do, rather than going through this."""
    global _live_tick_relay
    if _live_tick_relay is None:
        _live_tick_relay = LiveTickRelay(bus or get_event_bus(), **kwargs)
    return _live_tick_relay
