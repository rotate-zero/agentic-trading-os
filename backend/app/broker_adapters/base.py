"""
MarketDataProvider / BrokerAdapter — the abstract contracts every market
data or broker integration must satisfy. See
docs/architecture/system-design.md §4.1 and confirmed decision #28.
Nothing above this layer knows IBKR, Polygon, Databento, or any other
provider exists; Market Data Engine (Phase 4) and Execution Engine
(Phase 6) depend only on these interfaces.

Split rationale (confirmed decision #28): a pure market-data vendor
(Polygon, Databento, ...) has no honest way to implement place_order,
cancel_order, or get_positions — there's no broker behind it, ever, not
"not wired up yet." Before this split, IBKRAdapter's place_order/
cancel_order raised NotImplementedError to mean "not wired in Phase 3 on
purpose, but could be" (see confirmed decision #6's "widen now, implement
narrow" pattern). That same NotImplementedError from a data-only adapter
would mean something structurally different — "can never work" — and
conflating those two meanings was the actual problem MarketDataProvider
fixes. BrokerAdapter extends MarketDataProvider with the three
execution-only methods; a data vendor implements MarketDataProvider
directly and simply doesn't have those methods to get wrong.

`Candle` reuses the Event Bus's CandleClosed payload model directly — a
provider's job is producing already-normalized data in exactly that shape
(§4.2: "Normalize raw broker payloads into a single internal Tick/Candle
schema"), and symbol context for get_historical() comes from the method's
own `symbol` parameter, so the payload itself doesn't need it.

`Tick` is NOT a reuse of PriceUpdated — see confirmed decision #15 for why:
on_tick()'s callback takes only a Tick (no separate symbol argument), but
one provider instance serves many subscribed symbols, so Tick has to carry
symbol itself. PriceUpdated omits symbol because the EventEnvelope carries
it instead. The ingest bridge (app/services/tick_ingest.py) converts
between the two when publishing onto the bus.

Note on this file's folder name: app/broker_adapters/ predates this split
(IBKR came first) and now also hosts pure data providers, which aren't
brokers. The name stayed rather than trigger a repo-wide import rename for
what's ultimately a cosmetic fix — worth revisiting if it starts to
actually confuse things, not before.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.events.market_data import CandleClosed as Candle

__all__ = [
    "MarketDataProvider",
    "BrokerAdapter",
    "Candle",
    "Tick",
    "OrderRequest",
    "OrderAck",
    "Position",
    "SymbolNotFoundError",
    "HistoricalDataUnavailableError",
]


class HistoricalDataUnavailableError(Exception):
    """
    Raised when a provider structurally cannot serve get_historical() —
    not a transient failure, a capability gap. First needed for
    FinnhubAdapter: Finnhub's free tier serves real-time WebSocket
    streaming but returns a 403 on historical stock candles (moved behind
    a paywall — confirmed via a real failed request, not assumed from
    older docs). Distinct from SymbolNotFoundError, which means "this
    symbol specifically doesn't resolve"; this means "this provider can
    never answer this question, for any symbol." Same honesty principle
    as BrokerAdapter.place_order() raising NotImplementedError for a
    provider with no execution capability — don't pretend to support
    something you structurally can't.
    """

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"{provider} cannot serve historical data: {reason}")


class SymbolNotFoundError(Exception):
    """
    Raised when a provider can't resolve a symbol to a tradeable
    instrument/contract. Lives here (not in a specific adapter module)
    since it's a cross-provider concern — any future provider, broker or
    data-only, should raise this same type, so callers only need one
    import regardless of which concrete provider is active.
    """

    def __init__(self, symbol: str, provider: str = "IBKR") -> None:
        self.symbol = symbol
        self.provider = provider
        super().__init__(f"{provider} could not resolve symbol {symbol!r} to a tradeable instrument")


class Tick(BaseModel):
    symbol: str
    price: float
    size: int
    exchange_ts: datetime


class OrderRequest(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None


class OrderAck(BaseModel):
    order_id: str
    status: Literal["submitted", "rejected"]
    reason: str | None = None


class Position(BaseModel):
    symbol: str
    qty: int
    avg_cost: float


class MarketDataProvider(ABC):
    """
    The common subset both pure data vendors (Polygon, Databento, ...) and
    full brokers (IBKR) can honestly satisfy: connect, stream, backfill.
    No execution — see BrokerAdapter below for that.
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    async def subscribe(self, symbols: list[str]) -> None: ...

    @abstractmethod
    async def unsubscribe(self, symbols: list[str]) -> None: ...

    @abstractmethod
    async def get_historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]: ...

    @abstractmethod
    def on_tick(self, callback: Callable[[Tick], None]) -> None: ...


class BrokerAdapter(MarketDataProvider):
    """
    Extends MarketDataProvider with execution. Only implement this if
    there's an actual broker behind the adapter — a pure data vendor
    should implement MarketDataProvider directly and stop there.

    place_order()/cancel_order() exist to satisfy this interface now
    (same "widen the schema, implement narrow" pattern as confirmed
    decision #6's GovernorDecision) but are NOT wired to any HTTP route
    or consumer as of Phase 3, on purpose: only the Governor should ever
    be able to trigger a real order, and the Governor doesn't exist until
    Phase 5/6. IBKRAdapter's implementations of these two raise
    NotImplementedError until then — see its docstring. That
    NotImplementedError means "not wired yet, but could be" specifically
    because IBKRAdapter is a BrokerAdapter with a real broker behind it —
    contrast with a MarketDataProvider-only adapter, which simply has no
    such methods to raise from.
    """

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderAck: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...
