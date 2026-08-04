"""
Finnhub market-data provider, implementing MarketDataProvider only — same
reasoning as PolygonAdapter (confirmed decision #28): no execution
capability to fake.

Two things verified before writing this, not assumed:

1. Finnhub's `finnhub-python` package ships NO WebSocket client at all —
   confirmed by inspecting the installed package (empty result searching
   for "socket"/"ws"/"stream" in its exports). The WebSocket layer here is
   built directly on the `websockets` library (already a dependency, for
   `polygon-api-client`), following Finnhub's documented raw protocol.

2. Finnhub's free tier gives genuine real-time WebSocket streaming for US
   equities, BUT `/stock/candle` (historical OHLCV) was moved behind a
   paywall and returns 403 on free keys — confirmed via a real GitHub
   issue from a user hitting exactly that, not from older tutorials that
   predate the change. get_historical() raises
   HistoricalDataUnavailableError rather than pretending to work; use
   PolygonAdapter for backfill instead (confirmed decision #32) — the two
   providers are complementary, not redundant.

WebSocket protocol (confirmed via Finnhub's own docs + multiple current
working examples, format has been stable for years):
  connect:      wss://ws.finnhub.io?token=API_KEY
  subscribe:    {"type": "subscribe", "symbol": "AAPL"}
  unsubscribe:  {"type": "unsubscribe", "symbol": "AAPL"}
  trade msg:    {"type": "trade", "data": [{"s": "AAPL", "p": 234.5, "t": 1234567890123, "v": 100, "c": [...]}]}
  keepalive:    {"type": "ping"}  — sent periodically, safe to ignore

Note: "1 API key can only open 1 connection at a time" per Finnhub's own
docs — this adapter assumes exactly that (one connection, many symbols
subscribed on it), not one connection per symbol.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import finnhub
import websockets

from app.broker_adapters.base import (
    Candle,
    HistoricalDataUnavailableError,
    MarketDataProvider,
    Tick,
)
from app.core.config import get_settings
from app.core.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_WS_URL_TEMPLATE = "wss://ws.finnhub.io?token={token}"


class FinnhubAdapter(MarketDataProvider):
    def __init__(
        self,
        api_key: str | None = None,
        max_calls_per_minute: int | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.finnhub_api_key
        if not self._api_key:
            raise ValueError(
                "FinnhubAdapter requires an API key (FINNHUB_API_KEY in .env, or pass api_key=...)"
            )

        # REST client used only for quote()-style calls, if ever needed —
        # NOT for historical candles (see module docstring). Rate-limited
        # the same way PolygonAdapter's REST calls are, just a much more
        # generous budget (60/min vs Polygon's 5/min).
        self._rest_client = finnhub.Client(api_key=self._api_key)
        self._rate_limiter = RateLimiter(
            max_calls=max_calls_per_minute or settings.finnhub_max_calls_per_minute,
            period_seconds=60.0,
        )

        self._ws: websockets.ClientConnection | None = None
        self._connected = False
        self._symbols: set[str] = set()
        self._tick_callbacks: list[Callable[[Tick], None]] = []
        self._listen_task: asyncio.Task | None = None

    # --- MarketDataProvider interface --------------------------------------

    async def connect(self) -> None:
        url = _WS_URL_TEMPLATE.format(token=self._api_key)
        self._ws = await websockets.connect(url)
        self._connected = True
        self._listen_task = asyncio.create_task(self._listen(), name="finnhub-ws-listen")
        logger.info("FinnhubAdapter connected (real-time WebSocket — genuinely live, not delayed)")

    async def disconnect(self) -> None:
        self._connected = False
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        logger.info("FinnhubAdapter disconnected")

    def is_connected(self) -> bool:
        return self._connected

    async def subscribe(self, symbols: list[str]) -> None:
        if self._ws is None:
            raise RuntimeError("FinnhubAdapter.subscribe() called before connect()")
        for symbol in symbols:
            if symbol in self._symbols:
                continue
            await self._ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
            self._symbols.add(symbol)
            logger.info("FinnhubAdapter subscribed to %s", symbol)

    async def unsubscribe(self, symbols: list[str]) -> None:
        if self._ws is None:
            return
        for symbol in symbols:
            if symbol not in self._symbols:
                continue
            await self._ws.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))
            self._symbols.discard(symbol)

    async def get_historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        raise HistoricalDataUnavailableError(
            provider="Finnhub",
            reason=(
                "/stock/candle is paywalled on the free tier (confirmed: real 403 on a "
                "free key, not assumed). Use PolygonAdapter for historical backfill — "
                "the two providers are complementary, not interchangeable."
            ),
        )

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self._tick_callbacks.append(callback)

    # --- internals ------------------------------------------------------

    async def _listen(self) -> None:
        assert self._ws is not None
        try:
            async for raw_message in self._ws:
                try:
                    message = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("FinnhubAdapter received non-JSON message, ignoring")
                    continue
                self._handle_message(message)
        except websockets.ConnectionClosed:
            self._connected = False
            logger.warning(
                "FinnhubAdapter's WebSocket closed unexpectedly. No auto-reconnect yet — "
                "that's Phase 4's Market Data Engine (ConnectionManager), same reasoning "
                "as IBKRAdapter's _on_disconnected. Call connect() again to restore it."
            )
        except asyncio.CancelledError:
            pass

    def _handle_message(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "ping":
            return  # keepalive, nothing to do
        if msg_type != "trade":
            return  # news or another message type this adapter doesn't handle yet

        for trade in message.get("data", []):
            symbol = trade.get("s")
            price = trade.get("p")
            volume = trade.get("v")
            ts_ms = trade.get("t")
            if symbol is None or price is None or ts_ms is None:
                continue  # malformed entry — skip rather than crash the whole batch

            tick = Tick(
                symbol=symbol,
                price=price,
                size=int(volume or 0),
                exchange_ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
            )
            for callback in self._tick_callbacks:
                callback(tick)
