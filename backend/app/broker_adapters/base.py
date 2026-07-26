"""
BrokerAdapter — the abstract contract every broker integration must
satisfy. See docs/architecture/system-design.md §4.1. Nothing above this
layer knows IBKR (or any other broker) exists; Market Data Engine (Phase 4)
and Execution Engine (Phase 6) will depend only on this interface.

`Candle` reuses the Event Bus's CandleClosed payload model directly — a
BrokerAdapter's job is producing already-normalized data in exactly that
shape (§4.2: "Normalize raw broker payloads into a single internal
Tick/Candle schema"), and symbol context for get_historical() comes from
the method's own `symbol` parameter, so the payload itself doesn't need it.

`Tick` is NOT a reuse of PriceUpdated — see confirmed decision #15 for why:
on_tick()'s callback takes only a Tick (no separate symbol argument), but
one adapter instance serves many subscribed symbols, so Tick has to carry
symbol itself. PriceUpdated omits symbol because the EventEnvelope carries
it instead. The ingest bridge (app/services/ibkr_ingest.py) converts
between the two when publishing onto the bus.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.events.market_data import CandleClosed as Candle

__all__ = ["BrokerAdapter", "Candle", "Tick", "OrderRequest", "OrderAck", "Position"]


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


class BrokerAdapter(ABC):
    """
    place_order()/cancel_order() exist to satisfy this interface now
    (same "widen the schema, implement narrow" pattern as confirmed
    decision #6's GovernorDecision) but are NOT wired to any HTTP route
    or consumer in Phase 3, on purpose: only the Governor should ever be
    able to trigger a real order, and the Governor doesn't exist until
    Phase 5/6. IBKRAdapter's implementations of these two raise
    NotImplementedError until then — see its docstring.
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def subscribe(self, symbols: list[str]) -> None: ...

    @abstractmethod
    async def unsubscribe(self, symbols: list[str]) -> None: ...

    @abstractmethod
    async def get_historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]: ...

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderAck: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def on_tick(self, callback: Callable[[Tick], None]) -> None: ...
