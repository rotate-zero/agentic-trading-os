"""
OrderVenue — the narrow port between the Execution Engine and any place
that can actually fill an order. New in this delivery (decision
#172), resolving EX-3 (decision #170) per
docs/architecture/execution-engine-design.md §6.4.

`BrokerAdapter` (app/broker_adapters/base.py) is deliberately NOT
enlarged and does NOT implement this port — its job stays market-data
connectivity (it extends `MarketDataProvider`); its dormant
`place_order`/`cancel_order`/`get_positions` declarations are untouched
(design doc §10, R8 — their removal is a later decision). A venue
capable of order execution — `SimulatedVenue` today
(broker_adapters/simulated_venue.py), a future `IBKROrderVenue` later
(deferred, design doc §8) — implements `OrderVenue` instead, with its
own connection lifecycle, entirely separate from any `BrokerAdapter`
connection. The `execution` registry role
(app/services/broker_registry.py) is the only way the Execution Engine
obtains "the" venue; nothing else should import a concrete venue class
directly (I1).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

__all__ = [
    "ExecutionMode",
    "VenueOrderStatus",
    "OrderInstruction",
    "OrderAck",
    "OrderStatusReport",
    "OrderUpdate",
    "OrderUpdateCallback",
    "VenuePosition",
    "OrderVenue",
]

# The capital-mode vocabulary (EX-2, decision #170) — separate from
# `execution_venue` (which venue/broker fills are coming from).
# `core/config.py`'s `execution_mode` setting is the single runtime
# source of the CONFIGURED value; this Literal is just the shared
# vocabulary both the config and every `OrderVenue.supported_modes`
# check against, so a typo can't silently create a fourth,
# unrecognized mode string.
ExecutionMode = Literal["backtest", "simulated", "paper", "live"]

# Venue-facing order status vocabulary — distinct from, and narrower
# than, the ledger's own order state machine (approved | submitted |
# partially_filled | filled | cancelled | rejected | unknown | expired
# — design doc §6.3). A venue only ever reports what IT knows (it has
# no concept of "approved," which is pre-venue, or "unknown"/"expired,"
# which are ledger-side reconciliation outcomes); the Execution Engine
# is the one that folds venue-reported status into the ledger's fuller
# state machine.
VenueOrderStatus = Literal["submitted", "rejected", "partially_filled", "filled", "cancelled"]


class OrderInstruction(BaseModel):
    """What the Execution Engine hands a venue to place an order.
    `client_order_id` is minted upstream — deterministic,
    `"<trade_id>:entry"` / `"<trade_id>:exit:<n>"` (design doc §6.3,
    I10) — and IS the venue's idempotency key: `place_order()` must
    return the original ack for a `client_order_id` it has already
    accepted, and must never place a second order for it."""

    client_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    position_effect: Literal["open", "close"] = "open"


class OrderAck(BaseModel):
    client_order_id: str
    venue_order_id: str | None = None
    status: Literal["submitted", "rejected"]
    reason: str | None = None


class OrderStatusReport(BaseModel):
    """`get_order()`'s answer, or one row of `list_open_orders()` — the
    venue's own view of one order, for restart reconciliation (§6.9).
    `None` from `get_order()` means the venue genuinely has no record
    of this `client_order_id` — never inferred or fabricated as any
    particular status."""

    client_order_id: str
    venue_order_id: str | None = None
    status: Literal["submitted", "partially_filled", "filled", "cancelled", "rejected"]
    filled_qty: int = 0
    leaves_qty: int = 0


class OrderUpdate(BaseModel):
    """Pushed to every callback registered via `on_order_update()` —
    one order-lifecycle event. `venue_fill_id` is present only when
    this update carries a fill (design doc §6.4's callback row); a pure
    status change (a cancel ack, a rejection) leaves it `None` and
    `fill_qty` at 0. `commission` stays `None` unless the venue
    genuinely supplies one — never fabricated (I3)."""

    client_order_id: str
    venue_order_id: str | None = None
    status: VenueOrderStatus
    venue_fill_id: str | None = None
    fill_qty: int = 0
    fill_price: float | None = None
    cumulative_qty: int = 0
    leaves_qty: int = 0
    venue_ts: datetime | None = None
    commission: float | None = None
    reason: str | None = None  # populated when status == "rejected" or "cancelled"


class VenuePosition(BaseModel):
    """`get_positions()`'s answer — reconciliation input only, never
    the source of position truth (I12): Portfolio State owns that,
    replayed from the fills ledger."""

    symbol: str
    qty: int
    side: Literal["BUY", "SELL"]
    avg_cost: float


OrderUpdateCallback = Callable[[OrderUpdate], None]


class OrderVenue(ABC):
    """The only interface the Execution Engine depends on to reach a
    place that can fill orders (I1). See design doc §6.4 for the full
    member table and EX-3's resolution (decision #170): a NEW narrow
    interface, not an enlarged `BrokerAdapter`."""

    @property
    @abstractmethod
    def venue_id(self) -> str:
        """Stable identity — e.g. ``"simulated"``, ``"ibkr"`` — stored
        as `execution_venue` on every ledger row this venue produces."""

    @property
    @abstractmethod
    def supported_modes(self) -> frozenset[ExecutionMode]:
        """Subset of ``{"simulated", "paper", "live"}`` — never
        includes ``"backtest"`` (a per-run label, never a live-pipeline
        setting, per the authorizer stub's startup refusal, §6.2). The
        `execution` registry role's `set_execution_venue()` refuses to
        register a venue whose `supported_modes` excludes the
        configured `execution_mode` (I6, fail closed)."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    async def place_order(self, instruction: OrderInstruction) -> OrderAck:
        """Idempotent on `instruction.client_order_id` — a second call
        with an already-accepted id returns the original ack and places
        nothing new (I10, I11)."""

    @abstractmethod
    async def cancel_order(self, client_order_id: str) -> None:
        """Requests a cancel; the result is reported asynchronously
        through the update callback (design doc §6.4), not this
        method's return value. A cancel for an unknown or already-
        terminal id is a no-op, not an error."""

    @abstractmethod
    async def get_order(self, client_order_id: str) -> OrderStatusReport | None:
        """Reconciliation after a restart or a timeout (§6.9). `None`
        means the venue has no record of this id — never inferred as
        any particular status."""

    @abstractmethod
    async def list_open_orders(self) -> list[OrderStatusReport]:
        """Reconciliation: venue-side orders the ledger doesn't know
        about (§6.9's DISCREPANCY check)."""

    @abstractmethod
    async def get_fills(self, client_order_id: str) -> list[OrderUpdate]:
        """Reconciliation: fills the process missed while it was down
        — every `OrderUpdate` carrying a fill for this order,
        `venue_fill_id` included so the caller can dedupe (§6.9)."""

    @abstractmethod
    async def get_positions(self) -> list[VenuePosition]:
        """Reconciliation only (I12) — NEVER the source of position
        truth. Portfolio State replays the fills ledger for that."""

    @abstractmethod
    def on_order_update(self, callback: OrderUpdateCallback) -> None:
        """Registers `callback` to be invoked for every order-lifecycle
        event this venue produces (fills, status changes). Multiple
        registrations are additive — an implementation must not
        silently drop an earlier callback."""
