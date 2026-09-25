"""
FillLedgerPort — the fill-ingestion persistence seam ExecutionEngine's
fill processing writes through (design doc §6.3 step 6: "INSERT fills
row ... advance order status monotonically ... COMMIT fill + order
status ... together"). New in this delivery (entry-lifecycle-wiring) —
`OrderLedgerPort` (ports.py, decision execution-authorizer-and-engine)
deliberately scopes itself to "the idempotent orders ledger
insert/update (§6.3 steps 2 and 5)" only; step 6 (fill ingestion) is a
separate concern with a separate, narrower persistence contract, so it
gets its own Protocol here rather than widening `OrderLedgerPort` or
`PostgresOrderLedger` (which this delivery was explicitly told to reuse
unmodified, not rebuild or extend).

**Why `execution_venue` is not a caller-supplied field.** A
`VenueOrderUpdate`/`OrderUpdate` carries no venue identity at all (see
`broker_adapters/order_venue.py`) — the only authoritative source is
the `orders` row itself, written once at submission
(`OrderLedgerPort.update_order_status(..., execution_venue=...)`).
`PostgresFillLedger.record_fill()` reads it from that row inside its
own transaction rather than trusting a value threaded in from the
live venue_provider, which could in principle point at a different
object than the one that actually placed this order.

**Why an unmatched-order fill raises instead of persisting anomaly-
flagged (design doc I14).** `fills.client_order_id` has a database
FOREIGN KEY onto `orders.client_order_id` (models/execution_ledger.py,
outside this task's file boundary) — a fill for a `client_order_id`
with no `orders` row is unrepresentable under the current schema, full
stop; there is no `anomaly` value that makes an FK violation
insertable. This is not reachable via `SimulatedVenue` in this slice:
it only ever calls a registered callback for a `client_order_id` it
was itself asked to `place_order()`, and `ExecutionEngine` always
completes its own idempotent `insert_order()` (§6.3 step 2) before
ever calling `place_order()` (step 4) — so by the time any fill can
exist for an id, that id's `orders` row is already committed. Judgment
call, flagged here rather than hidden: `record_fill()` fails loud
(`FillLedgerError`, logged, no publish) rather than attempting a
constraint-violating write for a case this slice cannot actually
produce. A future real venue that could report a genuinely unmatched
fill would need this reconsidered.

Overfill (a cumulative fill quantity exceeding `order.qty` for an
order whose `orders` row DOES exist) has no such schema conflict —
`fills.anomaly` supports it directly — and is handled per I14: the
fill is still persisted, flagged `anomaly="overfill"`, and the order's
status still advances (whatever the venue itself reports). "Halt NEW
entries" for an unresolved anomaly is this delivery's own
`governor/portfolio_state_reader.py` adapter's job (see that module),
not this one's — this port only ever persists and reports; it does
not gate anything upstream.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.models.execution_ledger import Fill, Order

logger = logging.getLogger(__name__)

# Mirrors portfolio_state/reconciliation.py's own `_FILL_PROGRESS_RANK`
# (same domain fact, expressed independently rather than importing a
# private name out of a package this task's file boundary doesn't own).
_FILL_PROGRESS_RANK = {"approved": 0, "submitted": 1, "partially_filled": 2, "filled": 3}


@dataclass(frozen=True)
class FillRecord:
    client_order_id: str
    venue_fill_id: str
    qty: int
    price: float
    venue_ts: datetime
    status: Literal["partially_filled", "filled"]
    """The venue's own reported resulting order status for THIS update
    (`VenueOrderUpdate.status` / `OrderUpdate.status`) — trusted
    directly as the candidate new ledger status rather than
    recomputed, matching `reconcile_with_venue`'s own convention of
    deferring to "the venue's own view" (order_venue.py)."""
    commission: float | None = None


@dataclass(frozen=True)
class FillInsertResult:
    inserted: bool
    """False = exact duplicate delivery (dedup on (execution_venue,
    venue_fill_id)) — a no-op, not an error (I11): the caller must not
    publish OrderFilled again for it."""
    symbol: str
    side: Literal["BUY", "SELL"]
    order_status: str
    """The order's status as of right now — whether or not THIS call
    advanced it (a stale/out-of-order update leaves it unchanged, per
    I11's "status only ever moving forward")."""
    anomaly: str | None = None
    """None, or the STORED fill's own anomaly ('overfill' — the only
    value reachable via this port, see module docstring)."""


class FillLedgerError(Exception):
    """A required persistence step could not be durably completed.
    ExecutionEngine treats this as fatal for the current fill: no
    OrderFilled publish, logged loudly, worker moves on — the same
    posture OrderLedgerError already gets from ExecutionEngine's order
    path."""


class FillLedgerPort(Protocol):
    def record_fill(self, fill: FillRecord) -> FillInsertResult:
        """Idempotent insert into `fills` keyed on
        (execution_venue, venue_fill_id) — execution_venue read from
        the fill's own `orders` row, never caller-supplied — COMMITTED
        together with the order's status advance in one transaction
        (I8). Raises FillLedgerError on a genuine persistence fault,
        including a fill for a `client_order_id` with no `orders` row
        (see this module's docstring for why that is not the same as
        I14's anomaly-flagging path)."""
        ...


class PostgresFillLedger:
    def __init__(self, session_factory):
        self._sessions = session_factory

    @contextmanager
    def _transaction(self):
        try:
            with self._sessions() as session:
                if session.in_transaction():
                    raise FillLedgerError("fill ledger adapter requires a fresh owned transaction")
                with session.begin():
                    session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                    session.execute(text("SET LOCAL synchronous_commit = on"))
                    # SHARE ROW EXCLUSIVE on `orders` is also taken by
                    # PostgresOrderLedger (db/ledger_transaction.py) and by
                    # PostgresPositionLedger (portfolio_state/postgres.py) —
                    # this table is the deliberate serialization point between
                    # all three writers, so a fill can never be recorded
                    # against a half-written order row or be read mid-write
                    # by the position ledger's own replay.
                    session.execute(text("LOCK TABLE orders, fills IN SHARE ROW EXCLUSIVE MODE"))
                    yield session
        except FillLedgerError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError) as exc:
            raise FillLedgerError(f"fill ledger transaction failed: {exc}") from exc

    def record_fill(self, fill: FillRecord) -> FillInsertResult:
        with self._transaction() as session:
            order = session.scalar(select(Order).where(Order.client_order_id == fill.client_order_id))
            if order is None:
                raise FillLedgerError(
                    f"fill {fill.venue_fill_id!r} for unknown order {fill.client_order_id!r} "
                    "— cannot persist (fills.client_order_id FK requires an existing orders row)"
                )

            existing = session.scalar(
                select(Fill).where(Fill.execution_venue == order.execution_venue, Fill.venue_fill_id == fill.venue_fill_id)
            )
            if existing is not None:
                return FillInsertResult(False, order.symbol, order.side, order.status, existing.anomaly)

            prior_qty = session.scalar(
                select(func.coalesce(func.sum(Fill.qty), 0)).where(Fill.client_order_id == fill.client_order_id)
            ) or 0
            cumulative = prior_qty + fill.qty
            anomaly = "overfill" if cumulative > order.qty else None
            if anomaly:
                logger.error(
                    "Fill %s for %s is an overfill (cumulative=%d > order.qty=%d) — persisted, flagged, "
                    "entry acceptance halts per I14 until reviewed",
                    fill.venue_fill_id, fill.client_order_id, cumulative, order.qty,
                )

            row = Fill(
                client_order_id=fill.client_order_id, execution_venue=order.execution_venue,
                venue_fill_id=fill.venue_fill_id, qty=fill.qty, price=fill.price,
                venue_ts=fill.venue_ts, commission=fill.commission, anomaly=anomaly,
            )
            session.add(row)

            if _FILL_PROGRESS_RANK.get(fill.status, 0) > _FILL_PROGRESS_RANK.get(order.status, 0):
                order.status = fill.status
            else:
                logger.warning(
                    "Fill %s for %s reports status=%r, not forward of current order.status=%r — "
                    "fill persisted, order status left unchanged (I11)",
                    fill.venue_fill_id, fill.client_order_id, fill.status, order.status,
                )
            session.flush()
            result = FillInsertResult(True, order.symbol, order.side, order.status, anomaly)
        return result
