"""
SimulatedVenue — the only `OrderVenue` implementation in this slice
(EX-1, decision #170). See docs/architecture/execution-engine-design.md
§6.4.

**Behaves like a broker, owns no truth (I12).** It acknowledges orders,
then reports fills through the same `on_order_update` callback a real
venue would use. It is NOT the source of position or order truth —
Portfolio State and the ledger are (design doc §6.5); this class's own
`get_positions()` is a best-effort derived view, useful only for
restart-reconciliation tests, never authoritative.

**Fill model (EX-8, option (a) — reuse conventions, write a new
incremental model).** `fill_simulator.py` (Backtest Runner) is a
look-ahead pure-function model over a fully precomputed candle list —
it cannot serve an incremental, tick-driven venue as written (design
doc F9). This class shares its CONVENTIONS (zero slippage/commission by
default, deterministic behavior, no fabricated fields — I3) but is a
genuinely new, incremental implementation:
  - A market order fills at the first tick at or after acceptance.
  - A limit order fills at the first tick that crosses its limit price.
  - Fill timestamps are the tick's own `exchange_ts` (event time, I9) —
    never `datetime.now()`.
**Parity delta, stated not hidden (EX-8):** the Backtest Runner fills
at the *next candle's open* and exits at the *stop/target price*
touched by a candle's high/low; this venue fills at the *observed
tick* and would exit (once a Position Monitor is wired, sibling task)
at whatever tick actually breached the level. The two will not produce
byte-identical fills for the same session — this is a real difference
in what each model can see, not a bug in either.

**Deterministic identity.** `venue_fill_id = "<client_order_id>:f<n>"`
— re-reported fills dedupe by construction downstream (I11). This
venue's own in-memory order/fill book answers `get_order`,
`list_open_orders`, and `get_fills`.

**Not durable (I12).** The book is a plain dict — lost when this
object is discarded/recreated (a process restart, in practice). That
is the intended behavior: recovery (§6.9) is the Execution Engine's
job, which is expected to find `get_order()` returning `None` for a
non-terminal ledger order after a restart and mark it `expired`
(`reason=venue_lost_state_on_restart`) — this class does not try to
persist anything itself.

**Session guard.** Rejects (at `place_order` time) outside the regular
session, via an injectable `MarketClock` (defaults to
`get_market_clock()`).

**Injectable for tests.** `event_bus` is optional — when given,
`connect()` subscribes this venue to `PriceUpdated` and ticks arrive
from the real pipeline; when omitted, a test can drive fills directly
via `ingest_tick(...)`. `clock` is injectable for session-boundary
tests. `partial_fill_planner` is an optional injectable strategy
(`OrderInstruction -> list[int]`, quantities summing to `instruction.qty`)
so the partial-fill path is exercised even though the default planner
always fills the whole order on the first qualifying tick (v1's stated
behavior, design doc §6.4).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.broker_adapters.order_venue import (
    ExecutionMode,
    OrderAck,
    OrderInstruction,
    OrderStatusReport,
    OrderUpdate,
    OrderUpdateCallback,
    OrderVenue,
    VenuePosition,
)
from app.core.market_clock import MarketClock, get_market_clock
from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import PriceUpdated

logger = logging.getLogger(__name__)

__all__ = ["SimulatedVenue"]

# Default planner: fill the whole order on the first qualifying tick —
# v1's stated behavior (design doc §6.4: "even though v1 fills whole").
def _fill_whole(instruction: OrderInstruction) -> list[int]:
    return [instruction.qty]


PartialFillPlanner = Callable[[OrderInstruction], list[int]]


@dataclass
class _SimOrder:
    instruction: OrderInstruction
    venue_order_id: str
    status: str = "submitted"  # submitted | partially_filled | filled | cancelled | rejected
    filled_qty: int = 0
    plan: list[int] = field(default_factory=list)
    plan_index: int = 0
    fills: list[OrderUpdate] = field(default_factory=list)
    reject_reason: str | None = None

    @property
    def leaves_qty(self) -> int:
        return self.instruction.qty - self.filled_qty


class SimulatedVenue(OrderVenue):
    """v1's only real `OrderVenue` — see module docstring."""

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        clock: MarketClock | None = None,
        partial_fill_planner: PartialFillPlanner | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._clock = clock or get_market_clock()
        self._planner: PartialFillPlanner = partial_fill_planner or _fill_whole
        self._connected = False
        self._subscribed = False
        self._orders: dict[str, _SimOrder] = {}
        # Pending orders grouped by symbol, in acceptance order — the
        # order ticks are matched against determines "first tick at or
        # after acceptance" deterministically when more than one order
        # is open on the same symbol.
        self._pending_by_symbol: dict[str, list[str]] = {}
        self._callbacks: list[OrderUpdateCallback] = []

    # --- OrderVenue identity ------------------------------------------------

    @property
    def venue_id(self) -> str:
        return "simulated"

    @property
    def supported_modes(self) -> frozenset[ExecutionMode]:
        return frozenset({"simulated"})

    # --- lifecycle ------------------------------------------------------

    async def connect(self) -> None:
        if self._event_bus is not None and not self._subscribed:
            self._event_bus.subscribe(EventType.PRICE_UPDATED, self._on_price_updated_envelope)
            self._subscribed = True
        self._connected = True

    async def disconnect(self) -> None:
        # Subscriptions on EventBus have no unsubscribe today (grep
        # confirms) — disconnect() simply stops treating this instance
        # as live; a stray late callback while unsubscribed is
        # harmless since it only touches this instance's own dict.
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # --- order placement -----------------------------------------------

    async def place_order(self, instruction: OrderInstruction) -> OrderAck:
        existing = self._orders.get(instruction.client_order_id)
        if existing is not None:
            # Idempotent replay (I10, I11) — return the original ack,
            # place nothing new.
            return self._ack_for(existing)

        if not self._connected:
            order = _SimOrder(
                instruction=instruction,
                venue_order_id=f"sim-{instruction.client_order_id}",
                status="rejected",
                reject_reason="venue_not_connected",
            )
            self._orders[instruction.client_order_id] = order
            return self._ack_for(order)

        if not self._clock.is_regular_session():
            order = _SimOrder(
                instruction=instruction,
                venue_order_id=f"sim-{instruction.client_order_id}",
                status="rejected",
                reject_reason="outside_regular_session",
            )
            self._orders[instruction.client_order_id] = order
            return self._ack_for(order)

        plan = list(self._planner(instruction))
        if sum(plan) != instruction.qty or any(q <= 0 for q in plan):
            raise ValueError(
                f"partial_fill_planner returned {plan!r}, which does not sum to "
                f"instruction.qty={instruction.qty} with all-positive quantities"
            )

        order = _SimOrder(
            instruction=instruction,
            venue_order_id=f"sim-{instruction.client_order_id}",
            status="submitted",
            plan=plan,
        )
        self._orders[instruction.client_order_id] = order
        self._pending_by_symbol.setdefault(instruction.symbol, []).append(instruction.client_order_id)
        return self._ack_for(order)

    async def cancel_order(self, client_order_id: str) -> None:
        order = self._orders.get(client_order_id)
        if order is None or order.status in ("filled", "cancelled", "rejected"):
            return  # unknown or already-terminal — no-op, not an error
        order.status = "cancelled"
        self._remove_from_pending(order)
        update = OrderUpdate(
            client_order_id=client_order_id,
            venue_order_id=order.venue_order_id,
            status="cancelled",
            cumulative_qty=order.filled_qty,
            leaves_qty=order.leaves_qty,
            reason="cancelled_by_request",
        )
        self._dispatch(update)

    # --- reconciliation reads --------------------------------------------

    async def get_order(self, client_order_id: str) -> OrderStatusReport | None:
        order = self._orders.get(client_order_id)
        if order is None:
            return None
        return self._status_report(order)

    async def list_open_orders(self) -> list[OrderStatusReport]:
        return [
            self._status_report(o)
            for o in self._orders.values()
            if o.status in ("submitted", "partially_filled")
        ]

    async def get_fills(self, client_order_id: str) -> list[OrderUpdate]:
        order = self._orders.get(client_order_id)
        if order is None:
            return []
        return list(order.fills)

    async def get_positions(self) -> list[VenuePosition]:
        """Best-effort derived view for reconciliation tests only
        (I12) — net qty/avg-cost per symbol across every fill this
        instance has recorded. Never consulted as the source of
        position truth."""
        totals: dict[str, dict[str, float]] = {}
        for order in self._orders.values():
            if order.filled_qty == 0:
                continue
            sign = 1 if order.instruction.side == "BUY" else -1
            bucket = totals.setdefault(order.instruction.symbol, {"signed_qty": 0.0, "cost": 0.0})
            for fill in order.fills:
                bucket["signed_qty"] += sign * fill.fill_qty
                bucket["cost"] += sign * fill.fill_qty * (fill.fill_price or 0.0)
        result: list[VenuePosition] = []
        for symbol, bucket in totals.items():
            signed_qty = bucket["signed_qty"]
            if signed_qty == 0:
                continue
            avg_cost = abs(bucket["cost"] / signed_qty) if signed_qty else 0.0
            result.append(
                VenuePosition(
                    symbol=symbol,
                    qty=int(abs(signed_qty)),
                    side="BUY" if signed_qty > 0 else "SELL",
                    avg_cost=avg_cost,
                )
            )
        return result

    def on_order_update(self, callback: OrderUpdateCallback) -> None:
        self._callbacks.append(callback)

    # --- tick ingestion ---------------------------------------------------

    def ingest_tick(self, symbol: str, price: float, exchange_ts: datetime) -> None:
        """Direct tick injection for tests, and the internal path the
        `PriceUpdated` bus subscription (when `event_bus` is given)
        feeds through. Matches every pending order on `symbol`, in
        acceptance order, against this single tick."""
        pending_ids = list(self._pending_by_symbol.get(symbol, []))
        for client_order_id in pending_ids:
            order = self._orders.get(client_order_id)
            if order is None or order.status not in ("submitted", "partially_filled"):
                continue
            if not self._crosses(order.instruction, price):
                continue
            self._apply_fill(order, price, exchange_ts)

    def _on_price_updated_envelope(self, envelope: EventEnvelope) -> None:
        if envelope.symbol is None:
            logger.warning("PriceUpdated envelope with no symbol — ignored by SimulatedVenue")
            return
        try:
            tick = PriceUpdated.model_validate(envelope.payload)
        except Exception:  # noqa: BLE001 — malformed payload must not crash the venue
            logger.exception("SimulatedVenue could not parse PriceUpdated payload")
            return
        self.ingest_tick(envelope.symbol, tick.price, tick.exchange_ts)

    @staticmethod
    def _crosses(instruction: OrderInstruction, price: float) -> bool:
        if instruction.order_type == "market":
            return True
        assert instruction.limit_price is not None  # OrderInstruction invariant for "limit"
        if instruction.side == "BUY":
            return price <= instruction.limit_price
        return price >= instruction.limit_price

    def _apply_fill(self, order: _SimOrder, price: float, exchange_ts: datetime) -> None:
        qty = order.plan[order.plan_index]
        order.plan_index += 1
        order.filled_qty += qty
        fill_number = len(order.fills) + 1
        order.status = "filled" if order.leaves_qty == 0 else "partially_filled"

        update = OrderUpdate(
            client_order_id=order.instruction.client_order_id,
            venue_order_id=order.venue_order_id,
            status=order.status,
            venue_fill_id=f"{order.instruction.client_order_id}:f{fill_number}",
            fill_qty=qty,
            fill_price=price,
            cumulative_qty=order.filled_qty,
            leaves_qty=order.leaves_qty,
            venue_ts=exchange_ts,
            commission=None,  # I3 — never fabricated; zero/None default, EX-8
        )
        order.fills.append(update)
        if order.status == "filled":
            self._remove_from_pending(order)
        self._dispatch(update)

    def _remove_from_pending(self, order: _SimOrder) -> None:
        pending = self._pending_by_symbol.get(order.instruction.symbol)
        if pending and order.instruction.client_order_id in pending:
            pending.remove(order.instruction.client_order_id)

    def _dispatch(self, update: OrderUpdate) -> None:
        for callback in self._callbacks:
            callback(update)

    @staticmethod
    def _ack_for(order: _SimOrder) -> OrderAck:
        if order.status == "rejected":
            return OrderAck(
                client_order_id=order.instruction.client_order_id,
                venue_order_id=order.venue_order_id,
                status="rejected",
                reason=order.reject_reason,
            )
        return OrderAck(
            client_order_id=order.instruction.client_order_id,
            venue_order_id=order.venue_order_id,
            status="submitted",
        )

    @staticmethod
    def _status_report(order: _SimOrder) -> OrderStatusReport:
        return OrderStatusReport(
            client_order_id=order.instruction.client_order_id,
            venue_order_id=order.venue_order_id,
            status=order.status,  # type: ignore[arg-type]  # _SimOrder.status is drawn from the same literal set
            filled_qty=order.filled_qty,
            leaves_qty=order.leaves_qty,
        )
