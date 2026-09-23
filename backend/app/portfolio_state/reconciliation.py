"""
Restart recovery and reconciliation against an `OrderVenue` — design
doc §6.9, step 3 ("Execution Engine recovery"). This task's own scope
(§4.6: "Portfolio State Engine ... restart recovery per §6.9 —
reconcile non-terminal ledger orders against `OrderVenue.get_order`/
`list_open_orders`/`get_fills`") places this logic inside
`portfolio_state/` rather than `execution_engine/` (sibling task,
decision execution-authorizer-and-engine, off this delivery's file
boundary) — the actual process-startup SEQUENCE (§6.9's numbered
steps 1-6 as a whole) is that sibling's to wire; `reconcile_with_venue`
below is the callable piece steps 2-3 need, built and tested here in
isolation against `SimulatedVenue`.

**What this does NOT do:** step 4 (OutcomeRecorder), step 5 (Position
Monitor re-arm), and step 6 (subscribe to the bus) are all sibling-task
territory (`trading_intelligence/`'s `OutcomeRecorder`,
`position_monitor/`, and the bus subscription itself all require
modules or event payloads this delivery's file boundary excludes).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker_adapters.order_venue import OrderInstruction, OrderVenue
from app.models.execution_ledger import Fill, Order
from app.portfolio_state.engine import PortfolioState

logger = logging.getLogger(__name__)

__all__ = ["ReconciliationReport", "reconcile_with_venue"]

NON_TERMINAL_ORDER_STATUSES = ("approved", "submitted", "partially_filled", "unknown")

# submitted/partially_filled/filled progression only — cancelled/rejected/expired are set by
# explicit branches below, never "advanced into" via this rank comparison.
_FILL_PROGRESS_RANK = {"submitted": 1, "partially_filled": 2, "filled": 3}


@dataclass
class ReconciliationReport:
    """What `reconcile_with_venue` found and did. `has_discrepancy`
    is the signal the caller (Execution Engine's startup sequence,
    §6.9 step 6) must act on: halt new entries, alert, never
    auto-adopt or discard (I13, I14) — this module raises no
    exception for a discrepancy; it reports one, since deciding what
    "halt new entries" means operationally belongs to whoever owns
    the pipeline's entry point, not to this reconciliation pass."""

    cancelled_stale_entries: list[str] = field(default_factory=list)
    resubmitted_exits: list[str] = field(default_factory=list)
    expired_orders: list[str] = field(default_factory=list)
    advanced_orders: list[str] = field(default_factory=list)  # venue knew it; status/fills caught up
    discrepancies: list[str] = field(default_factory=list)

    @property
    def has_discrepancy(self) -> bool:
        return bool(self.discrepancies)


async def reconcile_with_venue(
    session: Session,
    venue: OrderVenue,
    portfolio_state: PortfolioState,
) -> ReconciliationReport:
    """§6.9 step 3, run against `portfolio_state.execution_mode`'s
    orders only. Caller's responsibility: call `portfolio_state.
    rebuild_from_ledger(session)` FIRST (§6.9 step 2 precedes step 3),
    and to have already connected `venue` (`await venue.connect()`)."""
    report = ReconciliationReport()
    execution_mode = portfolio_state.execution_mode

    orders = session.execute(
        select(Order).where(Order.execution_mode == execution_mode, Order.status.in_(NON_TERMINAL_ORDER_STATUSES))
    ).scalars().all()

    for order in orders:
        venue_report = await venue.get_order(order.client_order_id)

        if venue_report is None:
            await _reconcile_unknown_to_venue(session, venue, order, report)
            continue

        await _reconcile_known_to_venue(session, venue, order, venue_report, portfolio_state, report)

    session.commit()

    await _check_open_orders_discrepancy(session, venue, execution_mode, report)
    await _check_positions_discrepancy(venue, portfolio_state, report)

    return report


async def _reconcile_unknown_to_venue(session: Session, venue: OrderVenue, order: Order, report: ReconciliationReport) -> None:
    if order.status == "approved":
        if order.position_effect == "open":
            order.status = "cancelled"
            order.reject_reason = "stale_opportunity_not_resubmitted"
            report.cancelled_stale_entries.append(order.client_order_id)
        else:
            instruction = OrderInstruction(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,  # type: ignore[arg-type]
                qty=order.qty,
                order_type=order.order_type,  # type: ignore[arg-type]
                limit_price=float(order.limit_price) if order.limit_price is not None else None,
                position_effect=order.position_effect,  # type: ignore[arg-type]
            )
            ack = await venue.place_order(instruction)
            if ack.status == "submitted":
                order.status = "submitted"
                order.venue_order_id = ack.venue_order_id
                report.resubmitted_exits.append(order.client_order_id)
            else:
                order.status = "rejected"
                order.reject_reason = ack.reason
    else:
        # submitted / partially_filled / unknown, and the venue genuinely has no record —
        # its own fills already in the ledger stand (design doc §6.9); only the order's
        # status changes.
        order.status = "expired"
        order.reject_reason = "venue_lost_state_on_restart"
        report.expired_orders.append(order.client_order_id)
    session.flush()


async def _reconcile_known_to_venue(
    session: Session,
    venue: OrderVenue,
    order: Order,
    venue_report,
    portfolio_state: PortfolioState,
    report: ReconciliationReport,
) -> None:
    venue_fills = await venue.get_fills(order.client_order_id)
    applied_any = False
    for update in venue_fills:
        if update.venue_fill_id is None:
            continue  # a pure status update, not a fill
        already = session.execute(
            select(Fill).where(Fill.execution_venue == order.execution_venue, Fill.venue_fill_id == update.venue_fill_id)
        ).scalar_one_or_none()
        if already is not None:
            continue  # dedupe on venue_fill_id (I11) — already in the ledger
        fill = Fill(
            client_order_id=order.client_order_id,
            execution_venue=order.execution_venue,
            venue_fill_id=update.venue_fill_id,
            qty=update.fill_qty,
            price=update.fill_price,
            venue_ts=update.venue_ts,
            commission=update.commission,
        )
        session.add(fill)
        session.flush()  # assign ledger_seq
        portfolio_state.apply_fill(session, fill)
        applied_any = True

    if venue_report.status in ("filled", "cancelled", "rejected"):
        if order.status != venue_report.status:
            order.status = venue_report.status
            applied_any = True
    elif venue_report.status in _FILL_PROGRESS_RANK:
        new_rank = _FILL_PROGRESS_RANK[venue_report.status]
        current_rank = _FILL_PROGRESS_RANK.get(order.status, 0)
        if new_rank > current_rank:
            order.status = venue_report.status
            applied_any = True

    if applied_any:
        report.advanced_orders.append(order.client_order_id)
    session.flush()


async def _check_open_orders_discrepancy(session: Session, venue: OrderVenue, execution_mode: str, report: ReconciliationReport) -> None:
    venue_open_ids = {o.client_order_id for o in await venue.list_open_orders()}
    ledger_open_ids = {
        row[0]
        for row in session.execute(
            select(Order.client_order_id).where(
                Order.execution_mode == execution_mode, Order.status.in_(("submitted", "partially_filled"))
            )
        ).all()
    }
    unknown_to_ledger = venue_open_ids - ledger_open_ids
    for client_order_id in sorted(unknown_to_ledger):
        report.discrepancies.append(
            f"venue reports open order {client_order_id!r} the ledger does not know about"
        )


async def _check_positions_discrepancy(venue: OrderVenue, portfolio_state: PortfolioState, report: ReconciliationReport) -> None:
    venue_positions = {p.symbol: p for p in await venue.get_positions()}
    snapshot = portfolio_state.get_snapshot()
    if snapshot is None:
        report.discrepancies.append("Portfolio State unavailable: unresolved accounting or commit failure")
        return
    ledger_positions = snapshot.positions
    symbols = set(venue_positions) | set(ledger_positions)
    for symbol in sorted(symbols):
        venue_pos = venue_positions.get(symbol)
        ledger_pos = ledger_positions.get(symbol)
        venue_qty = venue_pos.qty if venue_pos is not None else 0
        ledger_qty = ledger_pos.qty if ledger_pos is not None else 0
        if venue_qty != ledger_qty:
            report.discrepancies.append(
                f"{symbol}: venue reports qty={venue_qty}, ledger/Portfolio State reports qty={ledger_qty}"
            )
