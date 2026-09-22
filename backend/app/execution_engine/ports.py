"""
Narrow Protocols the Execution Engine depends on, plus their supporting
dataclasses. No concrete implementation lives here — mirrors governor/
ports.py's own reasoning (see that module's docstring for the full fork-1
rationale) applied to three separate seams this task calls into but does
not own the far side of:

  - `OrderVenue` — §6.4's port. The sibling `execution-ledger-and-venue`
    task builds the real `SimulatedVenue`; this is a local Protocol
    matching that interface so this task doesn't wait for it (per this
    task's own standing instruction). `SimulatedVenue` is expected to
    satisfy this Protocol structurally (Python's `Protocol` needs no
    inheritance) once it lands — reconciled at merge, see TESTING.md.
  - `OrderLedgerPort` — the idempotent `orders` ledger insert/update
    (§6.3 steps 2 and 5). Same fork-1 ownership split as governor's
    `TradeLedgerPort`: the real `orders` table + migration belong to the
    sibling ledger task, not this one.
  - `DecisionAuthorizationPort` — a narrow READ over the same underlying
    `trades` ledger governor/ports.py's `TradeLedgerPort` WRITES to (I2's
    authorization gate, AC #19 entry-gate half: "an entry OrderApproved
    with no committed authorizer decision is refused and logged"). A
    separate, narrower Protocol rather than reusing TradeLedgerPort
    itself — this module only ever needs one yes/no read, never a write,
    and the two packages should not depend on each other's ports module.
  - `ExecutionVenueProvider` — "obtained via the `execution` registry
    role" (§6.3) describes a role `backend/app/services/broker_registry.py`
    gains — that file is on this task's "must not touch" list (the
    sibling ledger/venue task owns adding it). `default_execution_venue_
    provider()` below duck-types onto whatever `get_execution_venue`
    attribute that module eventually exposes, so this task's code needs
    no edit once the sibling's registry role merges.

This task's own tests exercise ExecutionEngine against in-memory/fake
implementations of all three — the same explicit, documented departure
from "real Postgres 16" this project's usual testing philosophy calls
for, scoped to these three seams only (see test_execution_engine.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Protocol

logger = logging.getLogger(__name__)


# --- OrderVenue port (§6.4) ------------------------------------------------


@dataclass(frozen=True)
class VenueOrderInstruction:
    client_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    order_type: Literal["market", "limit"]
    limit_price: float | None
    position_effect: Literal["open", "close"]


@dataclass(frozen=True)
class VenueAck:
    status: Literal["submitted", "rejected"]
    reason: str | None = None
    venue_order_id: str | None = None


@dataclass(frozen=True)
class VenueOrderUpdate:
    """Shape matches §6.4's on_order_update table row: "pushes
    client_order_id, venue_order_id, status, optional venue_fill_id, fill
    qty/price, cumulative/leaves qty, venue timestamp, optional
    commission." Reused as the return shape for get_order/list_open_orders
    (a status snapshot) and get_fills (one instance per reported fill) —
    this task's own Execution Engine does not call any of these three
    (fill processing is out of scope here, see package docstring); they
    exist on the Protocol so SimulatedVenue has a complete, real interface
    to implement against, and so a later fill-processing task doesn't need
    to widen this Protocol to get them."""

    client_order_id: str
    venue_order_id: str | None
    status: str
    venue_fill_id: str | None = None
    fill_qty: int | None = None
    fill_price: float | None = None
    cumulative_qty: int | None = None
    leaves_qty: int | None = None
    venue_ts: datetime | None = None
    commission: float | None = None


class OrderVenue(Protocol):
    @property
    def venue_id(self) -> str: ...

    @property
    def supported_modes(self) -> frozenset[str]: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    async def place_order(self, instruction: VenueOrderInstruction) -> VenueAck:
        """Idempotent on client_order_id — a known ID returns the original
        acknowledgement rather than creating a second order (I10)."""
        ...

    async def cancel_order(self, client_order_id: str) -> None: ...

    async def get_order(self, client_order_id: str) -> VenueOrderUpdate | None:
        """Reconciliation after a restart or timeout; None = the venue has
        no record."""
        ...

    async def list_open_orders(self) -> list[VenueOrderUpdate]: ...

    async def get_fills(self, client_order_id: str) -> list[VenueOrderUpdate]:
        """Reconciliation: fills the process missed."""
        ...

    async def get_positions(self) -> list[dict[str, Any]]: ...

    def on_order_update(self, callback: Callable[[VenueOrderUpdate], None]) -> None: ...


class ExecutionVenueProvider(Protocol):
    def get_execution_venue(self) -> OrderVenue | None:
        """None = no venue currently configured/registered — the
        mode/venue check (AC #5 venue-refusal half) treats this the same
        as a venue whose supported_modes doesn't include the order's
        mode: reject, never routed."""
        ...


def default_execution_venue_provider() -> OrderVenue | None:
    """Duck-types onto `broker_registry.get_execution_venue()` if that
    attribute exists (the sibling `execution-ledger-and-venue` task's own
    scope, per §6.3/§6.4 — `broker_registry.py` is on this task's "must
    not touch" list). Returns None — never raises — if the attribute
    isn't there yet, which the caller's mode/venue check already treats
    as "no venue configured" (fail-closed, consistent with I6). This
    function needs no edit once that registry role merges."""
    try:
        from app.services import broker_registry
    except ImportError:
        return None
    getter = getattr(broker_registry, "get_execution_venue", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 — a broken registry must not crash order placement's mode check
        logger.exception("default_execution_venue_provider: broker_registry.get_execution_venue() raised")
        return None


# --- Order ledger port (§6.3 steps 2, 5) ------------------------------------


@dataclass(frozen=True)
class OrderRecord:
    client_order_id: str
    trade_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    position_effect: Literal["open", "close"]
    qty: int
    order_type: Literal["market", "limit"]
    limit_price: float | None
    execution_mode: str | None
    status: Literal["approved", "submitted", "rejected"]
    created_at: datetime
    reason: str | None = None
    execution_venue: str | None = None


@dataclass(frozen=True)
class OrderInsertResult:
    order: OrderRecord
    inserted: bool
    """True = a new row was created (this is the first delivery of this
    client_order_id). False = a duplicate — `order` is the STORED row
    from the original insert; the caller must send nothing further to a
    venue (§6.3 step 2: "conflict => duplicate => log, return the stored
    row, send nothing" — AC #7 client-order-id-mint half)."""


class OrderLedgerError(Exception):
    """Raised by an OrderLedgerPort implementation when a required
    persistence step could not be durably completed. ExecutionEngine
    treats this as fatal for the current OrderApproved: no venue call, no
    OrderStatusChanged publish (fork 1: "no event publication or venue
    submission after a required persistence failure"), logged loudly,
    worker moves on to the next queued item."""


class OrderLedgerPort(Protocol):
    def insert_order(self, record: OrderRecord) -> OrderInsertResult:
        """Idempotent insert keyed on client_order_id, COMMITTED before any
        venue call (I8). Raises OrderLedgerError on a genuine persistence
        fault — never for an ordinary duplicate, which is `inserted=False`
        on a normal return, not an exception."""
        ...

    def update_order_status(
        self,
        client_order_id: str,
        status: Literal["submitted", "rejected"],
        *,
        reason: str | None = None,
        execution_venue: str | None = None,
    ) -> None:
        """COMMIT the outcome of the mode/venue check or the venue's own
        ack (§6.3 step 5), status only ever moving forward (I11). Raises
        OrderLedgerError on a genuine persistence fault."""
        ...


# --- Decision-authorization read port (I2, AC #19 entry-gate half) --------


class DecisionAuthorizationPort(Protocol):
    def has_committed_decision(self, opportunity_id: str) -> bool:
        """True iff governor's TradeLedgerPort has a committed, approved
        decision row for this opportunity_id. A narrow read over the SAME
        underlying trades ledger governor/ports.py's TradeLedgerPort
        writes to — see this module's own docstring for why this is a
        separate Protocol rather than a shared one."""
        ...
