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
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import ClassVar

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
    provider_id: ClassVar[str] = "finnhub"

    def __init__(
        self,
        api_key: str | None = None,
        max_calls_per_minute: int | None = None,
        *,
        connect_ws: Callable[[str], Awaitable] | None = None,
        retry_wait: Callable[[float], Awaitable[None]] | None = None,
        retry_initial_seconds: float | None = None,
        retry_max_seconds: float | None = None,
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
        self._enabled = False
        self._connecting = False
        self._desired_symbols: set[str] = set()
        self._symbols: set[str] = set()
        self._tick_callbacks: list[Callable[[Tick], None]] = []
        self._listen_task: asyncio.Task | None = None
        self._connect_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._connect_ws = connect_ws
        self._retry_wait = retry_wait or asyncio.sleep
        self._retry_initial = (
            settings.finnhub_retry_initial_seconds
            if retry_initial_seconds is None else retry_initial_seconds
        )
        self._retry_max = (
            settings.finnhub_retry_max_seconds
            if retry_max_seconds is None else retry_max_seconds
        )
        if (not math.isfinite(self._retry_initial) or not math.isfinite(self._retry_max)
                or self._retry_initial <= 0 or self._retry_max < self._retry_initial):
            raise ValueError("Finnhub retry delays must be finite and 0 < initial <= max")

    # --- MarketDataProvider interface --------------------------------------

    async def connect(self) -> None:
        async with self._lifecycle_lock:
            if self._enabled:
                return  # includes an outage: the existing owner is already retrying
            self._connecting = True
            self._connect_task = asyncio.current_task()
            try:
                # Initial failures belong to this caller. No background task exists yet.
                ws = await self._open_socket()
            except Exception:
                # The transport exception may contain the token-bearing URL.
                raise ConnectionError("Finnhub WebSocket connection failed") from None
            finally:
                self._connecting = False
                self._connect_task = None
            self._ws = ws
            self._enabled = self._connected = True
            self._listen_task = asyncio.create_task(self._supervise(ws), name="finnhub-ws-owner")
            logger.info("FinnhubAdapter connected (real-time WebSocket)")

    async def disconnect(self) -> None:
        self.cancel_pending_connect()
        async with self._lifecycle_lock:
            self._enabled = self._connected = False
            task = self._listen_task
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                self._listen_task = None
            if self._ws is not None:
                await self._close_socket(self._ws)
                self._ws = None
            self._desired_symbols.clear()
            self._symbols.clear()
        logger.info("FinnhubAdapter disconnected")

    def cancel_pending_connect(self) -> None:
        """Let the route stop an initial connect before its owner lock is free."""
        task = self._connect_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def is_connected(self) -> bool:
        return self._connected

    def is_streaming_active(self) -> bool:
        """True while this owner can connect or resume, including an outage."""
        return self._connecting or self._enabled

    async def subscribe(self, symbols: list[str]) -> None:
        if not self._enabled:
            raise RuntimeError("FinnhubAdapter.subscribe() called before connect()")
        async with self._send_lock:
            for symbol in symbols:
                self._desired_symbols.add(symbol)
                if not self._connected or self._ws is None or symbol in self._symbols:
                    continue
                await self._ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                self._symbols.add(symbol)
                logger.info("FinnhubAdapter subscribed to %s", symbol)

    async def unsubscribe(self, symbols: list[str]) -> None:
        # Remove restoration intent before any await, even during backoff.
        self._desired_symbols.difference_update(symbols)
        async with self._send_lock:
            for symbol in symbols:
                if not self._connected or self._ws is None or symbol not in self._symbols:
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

    # --- diagnostics -----------------------------------------------------

    def get_subscription_snapshot(self) -> tuple[str, ...]:
        """Sorted copy of the symbols this adapter has locally recorded as
        requested (see base.SubscriptionInventory). Local bookkeeping only:
        no provider acknowledgement, delivery or capacity is implied."""
        return tuple(sorted(self._symbols))

    # --- internals ------------------------------------------------------

    async def _open_socket(self):
        connector = self._connect_ws or websockets.connect
        return await connector(_WS_URL_TEMPLATE.format(token=self._api_key))

    @staticmethod
    async def _close_socket(ws) -> None:
        try:
            await ws.close()
        except Exception:
            logger.warning("Finnhub WebSocket close failed")

    async def _supervise(self, ws) -> None:
        delay = self._retry_initial
        try:
            while self._enabled:
                try:
                    await self._listen(ws)
                except websockets.ConnectionClosed:
                    pass
                except Exception:
                    # WebSocket exceptions may include the authenticated URL.
                    logger.warning("Finnhub WebSocket listener stopped unexpectedly")
                self._connected = False
                self._symbols.clear()
                if self._ws is ws:
                    self._ws = None
                await self._close_socket(ws)
                if not self._enabled:
                    break
                logger.warning("Finnhub WebSocket closed; reconnecting")
                while self._enabled:
                    await self._retry_wait(delay)
                    if not self._enabled:
                        break
                    try:
                        ws = await self._open_socket()
                    except Exception:
                        logger.warning("Finnhub reconnect attempt failed")
                        delay = min(delay * 2, self._retry_max)
                        continue
                    self._ws = ws
                    self._connected = True
                    try:
                        async with self._send_lock:
                            for symbol in sorted(self._desired_symbols):
                                if symbol not in self._desired_symbols:
                                    continue  # unsubscribed while restoration waited for the lock
                                await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                                self._symbols.add(symbol)
                    except Exception:
                        logger.warning("Finnhub subscription restoration failed; retrying")
                        self._connected = False
                        self._symbols.clear()
                        self._ws = None
                        await self._close_socket(ws)
                        delay = min(delay * 2, self._retry_max)
                        continue
                    delay = self._retry_initial
                    logger.info("Finnhub WebSocket reconnected; local requests restored")
                    # The role may have changed while this socket was opening.
                    # Only the current owner may wake protected reconciliation.
                    from app.services import broker_registry
                    if broker_registry.get_streaming_provider() is self:
                        broker_registry.request_protected_feed_reconcile()
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self._connected = False
            self._symbols.clear()
            if self._ws is ws:
                self._ws = None
            await self._close_socket(ws)

    async def _listen(self, ws) -> None:
        async for raw_message in ws:
            if not self._enabled or self._ws is not ws:
                continue  # stale connection generation
            try:
                message = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                logger.warning("FinnhubAdapter received non-JSON message, ignoring")
                continue
            self._handle_message(message)

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
