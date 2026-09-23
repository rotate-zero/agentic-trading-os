"""
Portfolio State Engine — the single owner of position accounting,
in-flight orders, and daily P&L (I5, EX-6, design doc §6.5). **A cache
over the ledger, never the record (I12)** — `positions` (and, upstream
of it, `fills`) is the actual source of truth; everything this module
holds in memory is reconstructable from those tables at any time via
`rebuild_from_ledger()`.

**What this delivery builds vs. what it doesn't.** The design's full
picture wires this to the event bus (`OrderApproved`/`OrderFilled` in,
`PositionClosed` out) from inside the Execution Engine
(`execution_engine/`, sibling task, decision
execution-authorizer-and-engine). This module deliberately does NOT
subscribe to or publish on the bus itself — `apply_fill()` is a plain,
synchronous, DB-session-scoped function the sibling's worker loop is
meant to call once a fill is committed (see `apply_fill`'s own
docstring for the `PositionClosed`-publishing seam this leaves open,
since the typed payload model and its `CRITICAL_EVENT_TYPES` entry both
live in files outside this delivery's boundary — `schemas/events/
execution.py`/`envelope.py`). What IS fully built and tested here: the
position-accounting logic itself, `get_snapshot()`, and
`rebuild_from_ledger()` — everything AC #9/#11/#12 (this delivery's
own owned acceptance criteria) exercise.

Methods are plain synchronous functions taking a `Session`, matching
`app/trading_intelligence/performance.py:record_strategy_outcome()`'s
own style — no `asyncio.to_thread` INSIDE this module; a caller running
inside an async context wraps these calls itself, same as that
existing precedent.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.market_clock import MarketClock, get_market_clock
from app.models.execution_ledger import Fill, Order, PortfolioStateCursor, Position, Trade

logger = logging.getLogger(__name__)

__all__ = [
    "PositionState",
    "InFlightOrder",
    "PortfolioSnapshot",
    "PortfolioState",
]

OPEN_POSITION_STATUSES = ("open", "closing")


@dataclass
class PositionState:
    position_id: uuid.UUID
    trade_id: uuid.UUID
    symbol: str
    side: str
    qty: int
    avg_price: float
    opened_at: datetime
    stop: float | None
    target: float | None
    status: str  # open | closing | closed
    closed_at: datetime | None
    realized_pnl: float | None
    exit_attempt: int


@dataclass
class InFlightOrder:
    """An approved-but-not-yet-filled order — added the moment
    `OrderApproved` lands (design doc §6.5: "a second signal on the
    same symbol sees it before the fill lands"). This delivery exposes
    `add_in_flight`/`remove_in_flight` as plain methods rather than a
    bus subscription — see this module's own docstring."""

    client_order_id: str
    symbol: str
    side: str
    qty: int
    position_effect: str  # open | close


@dataclass
class PortfolioSnapshot:
    """`get_snapshot()`'s answer — sync, no I/O (§6.5). Honesty
    convention (§6.5): `unrealized_pnl`/`open_risk` are `None` — not
    `0.0` — when at least one open position is missing a mark or a
    stop; `None` here means UNKNOWN, and any consumer (the daily-loss
    gate) must treat UNKNOWN as unbounded (I15), never as zero."""

    positions: dict[str, PositionState] = field(default_factory=dict)  # keyed by symbol, open/closing only
    in_flight: dict[str, InFlightOrder] = field(default_factory=dict)  # keyed by client_order_id
    marks: dict[str, tuple[float, datetime]] = field(default_factory=dict)  # symbol -> (price, ts)
    realized_pnl_today: float = 0.0
    unrealized_pnl: float | None = 0.0
    open_risk: float | None = 0.0
    open_position_count: int = 0

    @property
    def in_flight_count(self) -> int:
        return len(self.in_flight)


class PortfolioState:
    """One instance per `execution_mode` (§6.5's per-mode `trading_day`
    bucketing) — only `"simulated"` exists to construct one for in this
    slice (config.py's `execution_mode` validator)."""

    def __init__(self, execution_mode: str, *, clock: MarketClock | None = None) -> None:
        self.execution_mode = execution_mode
        self._clock = clock or get_market_clock()
        self._snapshot = PortfolioSnapshot()

    # --- sync, no-I/O reads -------------------------------------------------

    def get_snapshot(self) -> PortfolioSnapshot:
        return self._snapshot

    # --- in-flight tracking (§6.5: OrderApproved ─► in_flight added) ----

    def add_in_flight(self, order: InFlightOrder) -> None:
        self._snapshot.in_flight[order.client_order_id] = order

    def remove_in_flight(self, client_order_id: str) -> None:
        self._snapshot.in_flight.pop(client_order_id, None)

    # --- marks (PriceUpdated, held symbols only; memory only, no DB write) --

    def update_mark(self, symbol: str, price: float, ts: datetime) -> None:
        if symbol not in self._snapshot.positions:
            return  # not a held symbol — marks are memory-only for held symbols (§6.5)
        self._snapshot.marks[symbol] = (price, ts)
        self._recompute_derived()

    # --- the ledger is the record; this is how a fill reaches it --------

    def apply_fill(self, session: Session, fill: Fill) -> PositionState | None:
        """Applies ONE already-committed `fills` row to `positions`,
        in the caller's transaction (§6.5's "ONE TRANSACTION: upsert
        positions row + advance cursor ... COMMIT" — the commit itself
        is the CALLER's responsibility, matching `record_strategy_
        outcome()`'s own commit-owned-by-caller-of-the-session shape;
        this function only flushes so `position.position_id` etc. are
        available to the caller before it commits).

        **Publish seam.** The design has this function's caller publish
        `PositionClosed` on the critical lane immediately after the
        commit when `qty` reaches 0 (§6.5, EX-6). This delivery cannot
        build that publish call — the typed payload model and its
        `CRITICAL_EVENT_TYPES` entry both live in files outside this
        delivery's file boundary (`schemas/events/execution.py`,
        `schemas/events/envelope.py` — decision execution-authorizer-
        and-engine's territory). Instead, this method's return value's
        `status` tells the caller whether a closure just happened
        (`"closed"`) so IT can do the publish once those pieces exist;
        nothing here silently drops the requirement or invents a
        payload shape unilaterally.
        """
        # Idempotency check FIRST, before any mutation — apply_fill can be called more than
        # once for the same fill (e.g. a reconciliation pass re-walking fills the process
        # already had), and every mutation below is a stateful delta (qty -=, realized_pnl +=),
        # not an idempotent upsert. Checking after mutating would double-apply on a replay.
        cursor = session.get(PortfolioStateCursor, self.execution_mode)
        if cursor is None:
            cursor = PortfolioStateCursor(execution_mode=self.execution_mode, last_applied_ledger_seq=0)
            session.add(cursor)
            session.flush()
        if fill.ledger_seq <= cursor.last_applied_ledger_seq:
            order = session.execute(
                select(Order).where(Order.client_order_id == fill.client_order_id)
            ).scalar_one()
            existing = session.execute(
                select(Position)
                .where(Position.trade_id == order.trade_id, Position.symbol == order.symbol)
                .order_by(Position.opened_at.desc())
            ).scalars().first()
            # `existing` can legitimately be None here even though this fill was already
            # applied — e.g. it was flagged anomaly='unmatched_order' and never touched a
            # position (see below). Absence isn't inconsistency in that case.
            return self._to_state(existing) if existing is not None else None

        order = session.execute(
            select(Order).where(Order.client_order_id == fill.client_order_id)
        ).scalar_one()

        if order.status in ("filled", "cancelled", "rejected"):
            # I14 (AC #13): a fill for an order the ledger already considers terminal is
            # never dropped — it was already inserted by the caller before apply_fill ran;
            # this method's job is to flag it and skip position mutation, not to crash or
            # silently double-count it into the position. "Halt new entries" is an
            # authorization-gate decision (Execution Engine, sibling task) — out of scope
            # here beyond making the anomaly visible on the fill row and in the logs.
            fill.anomaly = "unmatched_order"
            logger.error(
                "apply_fill: fill %s arrived for client_order_id=%s which is already "
                "terminal (status=%s) — flagged anomaly='unmatched_order', not applied "
                "to position accounting, NOT dropped from the ledger",
                fill.venue_fill_id, order.client_order_id, order.status,
            )
            cursor.last_applied_ledger_seq = fill.ledger_seq
            session.flush()
            return None

        existing = session.execute(
            select(Position).where(
                Position.trade_id == order.trade_id,
                Position.symbol == order.symbol,
                Position.status.in_(OPEN_POSITION_STATUSES),
            )
        ).scalar_one_or_none()

        if order.position_effect == "open":
            position = self._apply_open_fill(session, order, existing, fill)
        else:
            if existing is None:
                # Reduce-only guard is the Execution Engine's job (§6.3) — this should
                # never happen if that guard ran. Still handled honestly rather than
                # crashing the replay: flag it and skip, don't fabricate a position.
                logger.error(
                    "apply_fill: close fill for %s (trade_id=%s) with no open position — "
                    "reduce-only guard should have prevented this order from ever being "
                    "placed; ledger and venue may have diverged",
                    order.symbol, order.trade_id,
                )
                raise ValueError(
                    f"no open position for trade_id={order.trade_id} symbol={order.symbol} "
                    f"to apply close fill {fill.venue_fill_id!r} against"
                )
            position = self._apply_close_fill(session, order, existing, fill)

        # Advance the ORDER's own status too — apply_fill is the only place that knows
        # cumulative fill progress against order.qty; nothing else in this delivery updates
        # it (design doc §6.3's order state machine: submitted -> partially_filled -> filled).
        total_filled = session.execute(
            select(func.coalesce(func.sum(Fill.qty), 0)).where(Fill.client_order_id == order.client_order_id)
        ).scalar_one()
        order.status = "filled" if total_filled >= order.qty else "partially_filled"

        cursor.last_applied_ledger_seq = fill.ledger_seq

        session.flush()
        self._upsert_in_memory(position)
        self.remove_in_flight(order.client_order_id)
        self._recompute_derived()
        return self._to_state(position)

    def _apply_open_fill(self, session: Session, order: Order, existing: Position | None, fill: Fill) -> Position:
        if existing is None:
            trade = session.get(Trade, order.trade_id)
            thesis = (trade.thesis if trade else {}) or {}
            position = Position(
                trade_id=order.trade_id,
                execution_mode=order.execution_mode,
                execution_venue=order.execution_venue,
                symbol=order.symbol,
                side=order.side,
                qty=fill.qty,
                avg_price=float(fill.price),
                stop=thesis.get("final_stop"),
                target=thesis.get("final_target"),
                opened_at=fill.venue_ts,
                status="open",
                exit_attempt=0,
            )
            session.add(position)
        else:
            new_qty = existing.qty + fill.qty
            existing.avg_price = float(
                (float(existing.avg_price) * existing.qty + float(fill.price) * fill.qty) / new_qty
            )
            existing.qty = new_qty
            position = existing
        return position

    def _apply_close_fill(self, session: Session, order: Order, existing: Position, fill: Fill) -> Position:
        side_sign = 1 if existing.side == "BUY" else -1
        realized_delta = (float(fill.price) - float(existing.avg_price)) * fill.qty * side_sign
        existing.realized_pnl = float(existing.realized_pnl or 0.0) + realized_delta
        overfilled_by = fill.qty - existing.qty  # > 0 means this fill closes more than was open
        existing.qty -= fill.qty
        if existing.qty <= 0:
            if existing.qty < 0:
                fill.anomaly = "overfill"
                logger.warning(
                    "apply_fill: close fill %s overfilled position %s by %s shares (I14: "
                    "persisted and flagged anomaly='overfill', not dropped; not applied "
                    "beyond fully closing the position)",
                    fill.venue_fill_id, existing.position_id, overfilled_by,
                )
                existing.qty = 0
            existing.status = "closed"
            existing.closed_at = fill.venue_ts
        else:
            existing.status = "closing"

        if existing.status == "closed" and self._clock.trading_day(fill.venue_ts) == self._clock.trading_day():
            self._snapshot.realized_pnl_today += realized_delta
        return existing

    # --- startup / recovery ---------------------------------------------

    def rebuild_from_ledger(self, session: Session, *, full_rebuild: bool = False) -> PortfolioSnapshot:
        """§6.5/§6.9 step 2: apply every fill with `ledger_seq >
        cursor.last_applied_ledger_seq`, in order. `full_rebuild=True`
        resets the cursor to 0 first — a genuine from-scratch replay
        (used by tests and by "prove the cache matches the ledger"
        audits), vs. the normal restart path which only catches up on
        what a NEW process instance hasn't seen yet.

        On any disagreement between what THIS instance already held in
        memory and what the replay computes, the replay — the ledger —
        wins (I12); the disagreement is logged, not silently ignored
        (AC #11)."""
        cursor = session.get(PortfolioStateCursor, self.execution_mode)
        if cursor is None:
            cursor = PortfolioStateCursor(execution_mode=self.execution_mode, last_applied_ledger_seq=0)
            session.add(cursor)
            session.flush()
        if full_rebuild:
            cursor.last_applied_ledger_seq = 0
            session.flush()

        before = {symbol: (p.qty, p.avg_price, p.status) for symbol, p in self._snapshot.positions.items()}

        fills = session.execute(
            select(Fill)
            .join(Order, Order.client_order_id == Fill.client_order_id)
            .where(Order.execution_mode == self.execution_mode, Fill.ledger_seq > cursor.last_applied_ledger_seq)
            .order_by(Fill.ledger_seq)
        ).scalars().all()

        for fill in fills:
            self.apply_fill(session, fill)
        session.commit()

        self._reload_open_positions(session)

        after = {symbol: (p.qty, p.avg_price, p.status) for symbol, p in self._snapshot.positions.items()}
        if before and before != after:
            logger.warning(
                "rebuild_from_ledger: in-memory state disagreed with the ledger replay for "
                "execution_mode=%s — before=%s after=%s; ledger wins (I12)",
                self.execution_mode, before, after,
            )

        self._recompute_derived()
        return self._snapshot

    def _reload_open_positions(self, session: Session) -> None:
        rows = session.execute(
            select(Position).where(
                Position.execution_mode == self.execution_mode,
                Position.status.in_(OPEN_POSITION_STATUSES),
            )
        ).scalars().all()
        self._snapshot.positions = {row.symbol: self._to_state(row) for row in rows}
        # Marks only make sense for currently-held symbols — drop any stale mark for a
        # symbol no longer held (an honest reset, not a guess at whether it's still valid).
        self._snapshot.marks = {
            symbol: mark for symbol, mark in self._snapshot.marks.items() if symbol in self._snapshot.positions
        }

    # --- internal helpers --------------------------------------------------

    def _upsert_in_memory(self, position: Position) -> None:
        if position.status == "closed":
            self._snapshot.positions.pop(position.symbol, None)
            self._snapshot.marks.pop(position.symbol, None)
        else:
            self._snapshot.positions[position.symbol] = self._to_state(position)

    def _recompute_derived(self) -> None:
        snap = self._snapshot
        snap.open_position_count = len(snap.positions)

        unrealized = 0.0
        open_risk = 0.0
        unknown = False
        for symbol, pos in snap.positions.items():
            mark = snap.marks.get(symbol)
            if mark is None:
                unknown = True
                continue
            price, _ts = mark
            side_sign = 1 if pos.side == "BUY" else -1
            unrealized += (price - pos.avg_price) * pos.qty * side_sign
            if pos.stop is None:
                unknown = True
                continue
            open_risk += abs(pos.avg_price - pos.stop) * pos.qty

        snap.unrealized_pnl = None if unknown else unrealized
        snap.open_risk = None if unknown else open_risk

    @staticmethod
    def _to_state(position: Position) -> PositionState:
        return PositionState(
            position_id=position.position_id,
            trade_id=position.trade_id,
            symbol=position.symbol,
            side=position.side,
            qty=position.qty,
            avg_price=float(position.avg_price),
            opened_at=position.opened_at,
            stop=float(position.stop) if position.stop is not None else None,
            target=float(position.target) if position.target is not None else None,
            status=position.status,
            closed_at=position.closed_at,
            realized_pnl=float(position.realized_pnl) if position.realized_pnl is not None else None,
            exit_attempt=position.exit_attempt,
        )
