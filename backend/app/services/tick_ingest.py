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
from datetime import datetime, timedelta, timezone

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
    granularity would be fabricating precision that isn't there."""

    def __init__(self, provider: MarketDataProvider, bus: EventBus) -> None:
        self._bus = bus
        self._buckets: dict[str, _MinuteBucket] = {}
        provider.on_tick(self._on_tick)
        # Wall-clock-driven close — see _flush_loop's docstring for the bug
        # this fixes. Self-starting here (constructor, not an explicit
        # start()) mirrors provider.on_tick(self._on_tick) immediately
        # above: this bridge has no other lifecycle hook today (it's
        # constructed inline by connect routes, not centrally managed by
        # main.py's lifespan), so "alive for as long as this instance
        # exists" already has to be true of the tick callback registration
        # too. stop() (below) is the matching teardown, called by
        # broker_registry when a bridge is retired.
        self._flush_task: asyncio.Task | None = asyncio.create_task(
            self._flush_loop(), name="tick-ingest-flush"
        )

    def stop(self) -> None:
        """Cancels the wall-clock flush loop. Called by broker_registry
        (take_over_streaming/clear_streaming_provider) when this bridge is
        retired, so it doesn't keep running forever against a provider
        that's no longer the active stream — every connect route already
        creates a fresh TickIngestBridge per connection, so without this
        each reconnect would leak one more background task."""
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None

    def _on_tick(self, tick: Tick) -> None:
        # on_tick's callback is sync per the MarketDataProvider interface,
        # but EventBus.publish() is async — hand off to the running loop.
        asyncio.create_task(self._handle_tick(tick))

    async def _handle_tick(self, tick: Tick) -> None:
        await self._bus.publish(
            make_envelope(
                EventType.PRICE_UPDATED,
                PriceUpdated(price=tick.price, size=tick.size, exchange_ts=tick.exchange_ts),
                symbol=tick.symbol,
            )
        )

        minute_ts = tick.exchange_ts.replace(second=0, microsecond=0)
        bucket = self._buckets.get(tick.symbol)

        if bucket is not None and bucket.minute_ts != minute_ts:
            # The clock rolled over to a new minute — the previous bucket
            # is now final, so publish it as a closed candle.
            await self._bus.publish(
                make_envelope(EventType.CANDLE_CLOSED, bucket.to_candle_closed(), symbol=tick.symbol)
            )
            bucket = None

        if bucket is None:
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
            while True:
                await asyncio.sleep(self._seconds_until_next_flush())
                await self._flush_stale_buckets(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            pass

    def _seconds_until_next_flush(self) -> float:
        now = datetime.now(timezone.utc)
        next_boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1) + _FLUSH_MARGIN
        return max(0.0, (next_boundary - now).total_seconds())

    async def _flush_stale_buckets(self, now: datetime) -> None:
        current_minute = now.replace(second=0, microsecond=0)
        for symbol in list(self._buckets.keys()):
            bucket = self._buckets.get(symbol)
            if bucket is not None and bucket.minute_ts < current_minute:
                # Popped BEFORE the publish await, not after — a tick for
                # this symbol arriving while we're awaiting the publish
                # call below then starts a fresh bucket instead of racing
                # this same bucket to a second CandleClosed. (_handle_tick's
                # own rollover check doesn't pop before its await either —
                # same known, accepted race as always existed there for two
                # rapid same-symbol ticks; not newly introduced by this fix,
                # and still fine to leave given this whole module is
                # explicitly throwaway-quality, replaced wholesale by the
                # real Market Data Engine in Phase 4, not patched forever.)
                del self._buckets[symbol]
                await self._bus.publish(
                    make_envelope(EventType.CANDLE_CLOSED, bucket.to_candle_closed(), symbol=symbol)
                )
