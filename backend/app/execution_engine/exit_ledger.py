"""Durable, position-bound reduce-only reservations for simulated exits."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.models.execution_ledger import ExitRequest, Fill, Order, Position, PositionFillReceipt, Trade

ACTIVE = ("approved", "submitted", "partially_filled", "unknown")
TERMINAL = ("filled", "cancelled", "rejected", "expired")


class ExitLedgerError(Exception):
    """The observation or reservation was not committed safely."""


@dataclass(frozen=True)
class ExitAction:
    kind: str  # cancel_entry | submit
    client_order_id: str
    symbol: str = ""
    side: str = ""
    qty: int = 0


class PostgresExitLedger:
    def __init__(self, session_factory):
        self._sessions = session_factory

    def _transaction(self):
        # All execution writers acquire trades/orders first. This barrier
        # serializes reservations with fill and position commits.
        from contextlib import contextmanager

        @contextmanager
        def owned():
            try:
                with self._sessions() as session:
                    with session.begin():
                        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                        session.execute(text("SET LOCAL synchronous_commit = on"))
                        session.execute(text(
                            "LOCK TABLE trades, orders, trade_reservations, fills, positions, "
                            "portfolio_state_cursor, position_fill_receipts, exit_requests "
                            "IN SHARE ROW EXCLUSIVE MODE"
                        ))
                        yield session
            except ExitLedgerError:
                raise
            except (SQLAlchemyError, ValueError, TypeError, ArithmeticError) as exc:
                raise ExitLedgerError(f"exit ledger transaction failed: {exc}") from exc

        return owned()

    def observe(self, intent) -> bool:
        """Persist a monitor observation once; never use its quantity as authority."""
        if intent.exit_reason not in {"stop", "target"}:
            return False
        price = Decimal(str(intent.trigger_price))
        if not price.is_finite() or price <= 0 or intent.trigger_ts.tzinfo is None:
            raise ExitLedgerError("invalid exit observation")
        with self._transaction() as session:
            position = session.get(Position, intent.position_id)
            if position is None or position.status == "closed" or position.qty <= 0:
                return False
            if (position.execution_mode, position.execution_venue, position.symbol, position.side) != (
                "simulated", "simulated", intent.symbol, intent.side
            ):
                raise ExitLedgerError("exit observation differs from committed position")
            existing = session.get(ExitRequest, position.position_id)
            if existing is not None:
                return True
            session.add(ExitRequest(position_id=position.position_id, exit_reason=intent.exit_reason,
                                    trigger_price=price, trigger_ts=intent.trigger_ts))
        return True

    def pending_position_ids(self) -> tuple[UUID, ...]:
        with self._sessions() as session:
            return tuple(session.scalars(
                select(ExitRequest.position_id).join(Position)
                .where(Position.execution_mode == "simulated", Position.qty > 0,
                       Position.status != "closed").order_by(ExitRequest.created_at)
            ).all())

    def prepare(self, position_id: UUID) -> ExitAction | None:
        """Cancel working entries first, or commit exactly one close attempt."""
        with self._transaction() as session:
            request = session.get(ExitRequest, position_id)
            position = session.get(Position, position_id)
            if request is None or position is None or position.status == "closed" or position.qty <= 0:
                return None
            if (position.execution_mode, position.execution_venue) != ("simulated", "simulated"):
                raise ExitLedgerError("position mode or venue cannot use simulated exits")
            trade = session.get(Trade, position.trade_id)
            if trade is None or trade.decision != "approved" or (
                trade.execution_mode, trade.execution_venue, trade.symbol, trade.direction
            ) != ("simulated", "simulated", position.symbol, position.side):
                raise ExitLedgerError("position has no matching approved trade")

            orders = session.scalars(select(Order).where(Order.trade_id == trade.trade_id).order_by(Order.id)).all()
            for order in orders:
                if order.position_effect != "open" or order.status not in ACTIVE:
                    continue
                filled = session.scalar(select(func.coalesce(func.sum(Fill.qty), 0))
                    .where(Fill.client_order_id == order.client_order_id)) or 0
                if filled < order.qty:
                    return ExitAction("cancel_entry", order.client_order_id)

            # No close can be sized against stale accounting or a fill still
            # waiting for its durable position receipt.
            pending = session.scalar(
                select(Fill.ledger_seq).join(Order, Order.client_order_id == Fill.client_order_id)
                .outerjoin(PositionFillReceipt, PositionFillReceipt.ledger_seq == Fill.ledger_seq)
                .where(Order.trade_id == trade.trade_id, PositionFillReceipt.ledger_seq.is_(None)).limit(1)
            )
            if pending is not None:
                return None

            for order in orders:
                if order.position_effect == "close" and order.status in ACTIVE:
                    if order.position_id != position_id:
                        raise ExitLedgerError("active close is not linked to the observed position")
                    if order.status == "approved":
                        return ExitAction("submit", order.client_order_id, order.symbol, order.side, order.qty)
                    return None

            if request.retry_after is not None and request.retry_after > datetime.now(timezone.utc):
                return None
            attempt = position.exit_attempt + 1
            order_id = f"{trade.trade_id}:exit:{attempt}"
            side = "SELL" if position.side == "BUY" else "BUY"
            order = Order(client_order_id=order_id, trade_id=trade.trade_id,
                position_id=position_id, execution_mode="simulated", execution_venue="simulated",
                symbol=position.symbol, side=side, position_effect="close", qty=position.qty,
                order_type="market", status="approved", exit_reason=request.exit_reason)
            session.add(order)
            position.exit_attempt = attempt
            session.flush()
            action = ExitAction("submit", order_id, position.symbol, side, position.qty)
        return action

    def confirm_recovery_exit(self, client_order_id: str) -> bool:
        """Recheck the reservation immediately before sending it to the venue."""
        with self._transaction() as session:
            order = session.scalar(select(Order).where(Order.client_order_id == client_order_id))
            if order is None or order.position_effect != "close" or order.status != "approved" or order.position_id is None:
                return False
            position = session.get(Position, order.position_id)
            request = session.get(ExitRequest, order.position_id)
            if not (position and request and position.status != "closed" and position.qty == order.qty
                and position.trade_id == order.trade_id and position.symbol == order.symbol
                and order.side == ("SELL" if position.side == "BUY" else "BUY")
                and position.execution_mode == order.execution_mode == "simulated"
                and position.execution_venue == order.execution_venue == "simulated"):
                return False
            # A fill can arrive after prepare() but before placement. Never
            # send a close while an entry can still increase the position or
            # while accounting has not applied every committed fill.
            working_entry = session.scalar(select(Order.id).where(
                Order.trade_id == order.trade_id, Order.position_effect == "open",
                Order.status.in_(ACTIVE),
            ).limit(1))
            pending_fill = session.scalar(
                select(Fill.ledger_seq).join(Order, Order.client_order_id == Fill.client_order_id)
                .outerjoin(PositionFillReceipt, PositionFillReceipt.ledger_seq == Fill.ledger_seq)
                .where(Order.trade_id == order.trade_id, PositionFillReceipt.ledger_seq.is_(None)).limit(1)
            )
            return working_entry is None and pending_fill is None

    def set_status(self, client_order_id: str, status: str, *, reason: str | None = None, venue_order_id: str | None = None) -> bool:
        if status not in {"submitted", "cancelled", "rejected"}:
            raise ExitLedgerError("unsupported status transition")
        with self._transaction() as session:
            order = session.scalar(select(Order).where(Order.client_order_id == client_order_id))
            if order is None:
                raise ExitLedgerError("status for unknown order")
            allowed = (order.status == "approved" and status in {"submitted", "cancelled", "rejected"}) or (
                order.status in {"submitted", "partially_filled"} and status in {"cancelled", "rejected"}
            )
            if not allowed:
                return False
            order.status = status
            if reason is not None:
                order.reject_reason = reason
            if venue_order_id is not None:
                order.venue_order_id = venue_order_id
            if order.position_effect == "close" and status in {"cancelled", "rejected"} and order.position_id:
                request = session.get(ExitRequest, order.position_id)
                if request:
                    request.retry_after = datetime.now(timezone.utc) + timedelta(seconds=5)
        return True
