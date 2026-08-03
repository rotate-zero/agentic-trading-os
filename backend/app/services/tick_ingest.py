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
from datetime import datetime

from app.broker_adapters.base import MarketDataProvider, Tick
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated

logger = logging.getLogger(__name__)


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
