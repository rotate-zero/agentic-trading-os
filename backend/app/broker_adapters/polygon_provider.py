"""
Polygon.io market-data provider, implementing MarketDataProvider only —
not BrokerAdapter, since Polygon has no execution capability to fake
(confirmed decision #28's whole point). See confirmed decision #30 for
the plan-tier constraints this design works around.

CRITICAL SCOPE NOTE, not buried in a footnote: the free/Basic Polygon
tier gives 15-MINUTE-DELAYED data and only 5 REST calls/minute — there is
NO WebSocket access at this tier at all (confirmed by multiple current
sources; attempting real-time WebSocket subscription on this tier either
fails auth or simply isn't offered). This is NOT a live/real-time feed.
Every Tick this adapter produces carries the delayed timestamp Polygon
actually reports, not "now" — so a chart fed by this adapter will
honestly show data as of ~15 minutes ago, not pretend to be live. If this
project later moves to a paid Polygon tier, this docstring and the
comments below marking the delay/polling-specific bits are exactly what
would need to change; the MarketDataProvider interface itself would not.

Design: no WebSocket means no push stream, so on_tick() is backed by
polling app/core/rate_limiter.py-throttled REST calls
(get_aggs — confirmed available on the free tier, unlike snapshot/
last-trade endpoints, which have less certain free-tier entitlement) —
see confirmed decision #30. Polling interval defaults to 60s
(settings.polygon_poll_interval_seconds), matching the underlying 1-minute
bar granularity: polling faster than that would just re-fetch the same
bar and burn rate-limit budget for nothing.

Because each poll only ever surfaces one data point per new bar (not a
per-trade tick stream), on_tick() fires once per new delayed bar, using
that bar's close as the tick price. TickIngestBridge still works
correctly fed this way — each bucket just ends up holding exactly one
tick instead of many, which is an honest reflection of this tier's real
granularity, not a bug to work around.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from polygon import RESTClient
from polygon.exceptions import AuthError, BadResponse

from app.broker_adapters.base import Candle, HistoricalDataUnavailableError, MarketDataProvider, SymbolNotFoundError, Tick
from app.core.config import get_settings
from app.core.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_TIMEFRAME_TO_POLYGON = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
}


def _polygon_params_for(timeframe: str) -> tuple[int, str]:
    try:
        return _TIMEFRAME_TO_POLYGON[timeframe]
    except KeyError:
        raise ValueError(
            f"Unsupported timeframe for Polygon historical data: {timeframe!r} "
            f"(supported: {sorted(_TIMEFRAME_TO_POLYGON)})"
        ) from None


def _is_plan_limitation(exc: BadResponse) -> bool:
    """
    polygon-api-client's BadResponse carries no structured fields at all
    (verified by reading its source — it's a bare Exception subclass) —
    the only signal available is the raw response body text it was
    constructed with, a JSON string like
    '{"status":"NOT_AUTHORIZED","message":"Your plan doesn't include
    this data timeframe..."}'. Parsed rather than pattern-matched against
    the message text, since the message wording is more likely to change
    than the status field.
    """
    try:
        body = json.loads(str(exc))
    except (json.JSONDecodeError, TypeError):
        return False
    return body.get("status") == "NOT_AUTHORIZED"


class PolygonAdapter(MarketDataProvider):
    def __init__(
        self,
        api_key: str | None = None,
        poll_interval_seconds: int | None = None,
        max_calls_per_minute: int | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.polygon_api_key
        if not self._api_key:
            raise ValueError(
                "PolygonAdapter requires an API key (POLYGON_API_KEY in .env, or pass api_key=...)"
            )
        self._poll_interval = poll_interval_seconds or settings.polygon_poll_interval_seconds

        self._client = RESTClient(self._api_key)
        self._rate_limiter = RateLimiter(
            max_calls=max_calls_per_minute or settings.polygon_max_calls_per_minute,
            period_seconds=60.0,
        )

        self._connected = False
        self._symbols: set[str] = set()
        self._last_bar_ts: dict[str, int] = {}  # symbol -> last-seen Polygon bar timestamp (ms)
        self._tick_callbacks: list[Callable[[Tick], None]] = []
        self._poll_task: asyncio.Task | None = None

    # --- MarketDataProvider interface --------------------------------------

    async def connect(self) -> None:
        # Deliberately doesn't spend a rate-limited call just to "test"
        # the key — the tight 5/min budget is better spent on real data.
        # A bad key surfaces naturally on the first real call instead.
        self._connected = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="polygon-poll")
        logger.info(
            "PolygonAdapter connected (polling every %ss, %s calls/min budget, "
            "15-min-delayed free tier — not real-time)",
            self._poll_interval, self._rate_limiter.max_calls,
        )

    async def disconnect(self) -> None:
        self._connected = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("PolygonAdapter disconnected")

    def is_connected(self) -> bool:
        return self._connected

    async def subscribe(self, symbols: list[str]) -> None:
        self._symbols.update(symbols)
        logger.info("PolygonAdapter subscribed to %s (polling, not push)", symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            self._symbols.discard(symbol)
            self._last_bar_ts.pop(symbol, None)

    async def get_historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        multiplier, span = _polygon_params_for(timeframe)

        await self._rate_limiter.acquire()
        try:
            aggs = await asyncio.to_thread(
                self._client.get_aggs,
                ticker=symbol,
                multiplier=multiplier,
                timespan=span,
                from_=start,
                to=end,
                adjusted=True,
                sort="asc",
                limit=50000,
            )
        except AuthError as exc:
            # A real auth failure (bad/expired key) — genuinely
            # unexpected, not a known-and-handled case, so this
            # propagates as-is to UnhandledExceptionMiddleware (confirmed
            # decision #37) rather than being mis-categorized as a plan
            # limitation. Different problem, different fix (check the key
            # itself), shouldn't look the same to whoever's debugging it.
            raise
        except BadResponse as exc:
            if _is_plan_limitation(exc):
                # Found via a real account, not assumed: the free/Basic
                # tier's minute-level aggregates specifically return
                # NOT_AUTHORIZED — "15-minute delayed" (confirmed
                # decision #30) turned out to describe a latency
                # characteristic of data this plan doesn't actually grant
                # access to at all, not a promise that minute bars are
                # available-but-delayed. Distinct enough from the
                # original research to be worth re-flagging here rather
                # than silently patched over — see confirmed decision #39.
                raise HistoricalDataUnavailableError(
                    provider="Polygon",
                    reason=(
                        f"plan doesn't include {timeframe!r} timeframe data "
                        f"({exc}). Try a coarser timeframe (e.g. '1d') if your "
                        "plan supports it, or check what's actually included at "
                        "https://polygon.io/pricing before assuming this is a bug."
                    ),
                ) from exc
            raise  # some other BadResponse — genuinely unexpected, don't mask it

        if not aggs:
            # get_aggs returns an empty list for a bad/delisted ticker
            # rather than raising — same "silently succeeds" trap as
            # ib_async's qualifyContractsAsync (confirmed decision #16's
            # sibling finding). Checked explicitly, same as that fix.
            raise SymbolNotFoundError(symbol, provider="Polygon")

        return [
            Candle(
                timeframe=timeframe,
                open=agg.open,
                high=agg.high,
                low=agg.low,
                close=agg.close,
                volume=int(agg.volume),
                candle_ts=datetime.fromtimestamp(agg.timestamp / 1000, tz=timezone.utc),
            )
            for agg in aggs
        ]

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self._tick_callbacks.append(callback)

    # --- internals ------------------------------------------------------

    async def _poll_loop(self) -> None:
        try:
            while True:
                for symbol in list(self._symbols):
                    await self._poll_symbol(symbol)
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            pass

    async def _poll_symbol(self, symbol: str) -> None:
        # A short recent window, not just "the latest bar" — free-tier
        # delay means the most recent few minutes can be sparse/missing
        # if there were no trades, so look back a bit to still catch the
        # latest real bar rather than silently going quiet.
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=10)

        await self._rate_limiter.acquire()
        try:
            aggs = await asyncio.to_thread(
                self._client.get_aggs,
                ticker=symbol,
                multiplier=1,
                timespan="minute",
                from_=window_start,
                to=now,
                adjusted=True,
                sort="desc",
                limit=5,
            )
        except BadResponse as exc:
            if _is_plan_limitation(exc):
                # Same condition as get_historical() hitting
                # HistoricalDataUnavailableError (confirmed decision
                # #39) — but this is the polling path (used when Polygon
                # is the streaming fallback, not the historical route),
                # and it's a PERMANENT condition, not a transient one:
                # logging a full traceback every 60s forever for
                # something that will never resolve itself is just log
                # spam. One clear line instead.
                logger.warning(
                    "PolygonAdapter poll for %s: plan doesn't include minute-level "
                    "data (%s) — this will keep failing every cycle until the plan "
                    "changes; not logging a full traceback for it repeatedly.",
                    symbol, exc,
                )
            else:
                logger.exception("PolygonAdapter poll failed for %s", symbol)
            return
        except Exception:  # noqa: BLE001 — one bad poll must not kill the loop
            logger.exception("PolygonAdapter poll failed for %s", symbol)
            return

        if not aggs:
            return  # no new trades in the window — not an error, just quiet

        latest = aggs[0]
        if self._last_bar_ts.get(symbol) == latest.timestamp:
            return  # same bar as last poll, genuinely nothing new yet

        self._last_bar_ts[symbol] = latest.timestamp
        tick = Tick(
            symbol=symbol,
            price=latest.close,
            size=int(latest.volume),
            exchange_ts=datetime.fromtimestamp(latest.timestamp / 1000, tz=timezone.utc),
        )
        for callback in self._tick_callbacks:
            callback(tick)
