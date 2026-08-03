"""
IBKR broker adapter, using ib_async (confirmed decision #13 — ib_insync is
unmaintained). Connects to a running IB Gateway or TWS over its local API
socket. This adapter does NOT handle login or 2FA — that happens in the
Gateway/TWS application itself, before this adapter ever connects. See
backend/README.md's "IBKR connection setup" section for how to get
Gateway logged in (IB Key + IBC auto-restart, not SMS — confirmed
decision #14).

Scope note: on_tick() gives raw per-tick price updates only. Building
1-minute candles from that stream is deliberately NOT this adapter's job
(confirmed decision #16) — see app/services/tick_ingest.py.

Verification note: every method here is built against real, introspected
ib_async signatures (see the Phase 3 chat transcript / commit message),
not memory. What's NOT verified is an actual live connection — this
sandbox has no path to a running IB Gateway or an IBKR account. That has
to happen on your machine.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ib_async import IB, Stock

from app.broker_adapters.base import BrokerAdapter, Candle, OrderAck, OrderRequest, Position, SymbolNotFoundError, Tick
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ib_async's reqHistoricalDataAsync barSizeSetting strings — only the
# timeframes CandleClosed's `timeframe` field is expected to carry
# (system-design.md §10.3 examples: "1m", "5m", "1d").
_BAR_SIZE_BY_TIMEFRAME = {
    "1m": "1 min",
    "5m": "5 mins",
    "15m": "15 mins",
    "1h": "1 hour",
    "1d": "1 day",
}


def _bar_size_for(timeframe: str) -> str:
    try:
        return _BAR_SIZE_BY_TIMEFRAME[timeframe]
    except KeyError:
        raise ValueError(
            f"Unsupported timeframe for IBKR historical data: {timeframe!r} "
            f"(supported: {sorted(_BAR_SIZE_BY_TIMEFRAME)})"
        ) from None


def _duration_str(start: datetime, end: datetime) -> str:
    """ib_async's durationStr wants e.g. '3600 S' or '5 D' — IBKR's API
    accepts a handful of unit suffixes; seconds and days cover what a
    Phase 3 chart backfill needs without over-building this."""
    seconds = max(1, int((end - start).total_seconds()))
    if seconds < 86400:
        return f"{seconds} S"
    days = max(1, seconds // 86400)
    return f"{days} D"


class IBKRAdapter(BrokerAdapter):
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ) -> None:
        settings = get_settings()
        self._host = host or settings.ibkr_host
        self._port = port if port is not None else settings.ibkr_port
        self._client_id = client_id if client_id is not None else settings.ibkr_client_id

        self._ib = IB()
        self._contracts: dict[str, Stock] = {}
        self._tick_callbacks: list[Callable[[Tick], None]] = []

        self._ib.pendingTickersEvent += self._on_pending_tickers
        self._ib.disconnectedEvent += self._on_disconnected

    # --- BrokerAdapter interface --------------------------------------

    async def connect(self) -> None:
        if self._ib.isConnected():
            return
        # readonly=True: Phase 3 only streams data, never places orders
        # (place_order/cancel_order raise NotImplementedError below) —
        # connecting read-only means a bug here can't accidentally submit
        # a real order even if future code tried to.
        await self._ib.connectAsync(
            self._host, self._port, clientId=self._client_id, readonly=True
        )
        logger.info(
            "IBKRAdapter connected to %s:%s (clientId=%s, readonly=True)",
            self._host, self._port, self._client_id,
        )

    async def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()
        logger.info("IBKRAdapter disconnected")

    async def _qualify(self, contract: Stock) -> Stock:
        """
        qualifyContractsAsync() never raises for an unresolvable symbol —
        verified by reading its source: on failure it just logs a warning
        and returns None in that result slot, leaving the input contract
        object unmodified (no conId). Ignoring that return value (which
        the first version of this adapter did) means subscribing to a
        bad symbol would silently "succeed" and then fail opaquely later,
        deep inside ib_async's error-event plumbing. Checking explicitly
        here turns that into a clean, catchable error at the point of use.
        """
        (result,) = await self._ib.qualifyContractsAsync(contract)
        if result is None:
            raise SymbolNotFoundError(contract.symbol)
        return result

    async def subscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            if symbol in self._contracts:
                continue
            contract = await self._qualify(Stock(symbol, "SMART", "USD"))
            self._ib.reqMktData(contract, "", False, False)
            self._contracts[symbol] = contract
            logger.info("IBKRAdapter subscribed to %s", symbol)

    async def unsubscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            contract = self._contracts.pop(symbol, None)
            if contract is not None:
                self._ib.cancelMktData(contract)
                logger.info("IBKRAdapter unsubscribed from %s", symbol)

    async def get_historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        contract = self._contracts.get(symbol)
        if contract is None:
            contract = await self._qualify(Stock(symbol, "SMART", "USD"))

        bars = await self._ib.reqHistoricalDataAsync(
            contract,
            endDateTime=end,
            durationStr=_duration_str(start, end),
            barSizeSetting=_bar_size_for(timeframe),
            whatToShow="TRADES",
            useRTH=True,
        )
        return [
            Candle(
                timeframe=timeframe,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=int(bar.volume),
                candle_ts=bar.date if isinstance(bar.date, datetime) else datetime.fromisoformat(str(bar.date)),
            )
            for bar in bars
        ]

    async def place_order(self, order: OrderRequest) -> OrderAck:
        raise NotImplementedError(
            "Not wired in Phase 3, deliberately — only the Governor should be able "
            "to trigger a real order, and the Governor doesn't exist until Phase 5/6. "
            "See app/broker_adapters/base.py and docs/decisions/confirmed-decisions.md."
        )

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("Same reasoning as place_order() — see its docstring.")

    async def get_positions(self) -> list[Position]:
        return [
            Position(symbol=p.contract.symbol, qty=int(p.position), avg_cost=float(p.avgCost))
            for p in self._ib.positions()
        ]

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self._tick_callbacks.append(callback)

    def is_connected(self) -> bool:
        return self._ib.isConnected()

    # --- internals --------------------------------------------------------

    def _on_pending_tickers(self, tickers) -> None:
        for ticker in tickers:
            price = ticker.last
            if price is None or price != price:  # None or NaN — no trade yet
                continue
            contract = ticker.contract
            if contract is None:
                continue
            tick = Tick(
                symbol=contract.symbol,
                price=price,
                size=int(ticker.lastSize or 0),
                exchange_ts=datetime.now(),
            )
            for callback in self._tick_callbacks:
                callback(tick)

    def _on_disconnected(self) -> None:
        """
        Observability only, deliberately not reconnect logic. Auto-reconnect
        with backoff is explicitly Market Data Engine's job in Phase 4
        (system-design.md §4.2's ConnectionManager) — building it here would
        pull Phase 4 scope forward into an adapter that's supposed to stay a
        thin, replaceable wrapper around the broker SDK. For now: log loudly,
        so a dropped connection doesn't silently go quiet. is_connected()
        already reflects reality afterward without any extra state to
        maintain here, since it always asks self._ib directly rather than
        caching a status flag.
        """
        logger.warning(
            "IBKRAdapter lost its connection to %s:%s. No auto-reconnect yet — "
            "that's Phase 4's Market Data Engine (ConnectionManager), not this "
            "adapter. Call POST /broker/connect again to restore it.",
            self._host, self._port,
        )
