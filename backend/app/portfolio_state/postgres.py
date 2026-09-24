"""Synchronous PostgreSQL PositionLedgerPort; one owned transaction per call.

A table-lock barrier supplies a safe committed prefix even when Identity
allocation and transaction commit order differ. See migration 0013. This
conservative implementation serializes ledger operations across modes.
No venue operations, order status writes, event publication, or startup wiring.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.market_clock import MarketClock
from app.models.execution_ledger import Fill, Order, PortfolioStateCursor, Position, PositionFillReceipt, Trade, TradeReservation

from .accounting import LedgerFill, MODES, ZERO, apply_fill, decimal
from .legacy import _fill, _write_position
from .ports import CommitResult, DailyAmounts, InFlightOrder, LedgerState, PositionLedgerError, RealizedFill, TERMINAL_STATUSES


def _encode(fill):
    return {key: str(value) if isinstance(value, (Decimal, UUID)) else
            value.isoformat() if isinstance(value, datetime) else value
            for key, value in asdict(fill).items()}


def _decode(data):
    data = dict(data)
    data["trade_id"] = UUID(data["trade_id"])
    data["venue_ts"] = datetime.fromisoformat(data["venue_ts"])
    for key in ("price", "commission", "stop", "target"):
        if data[key] is not None:
            data[key] = decimal(data[key])
    return LedgerFill(**data)


class PostgresPositionLedger:
    def __init__(self, session_factory, *, clock=None):
        """Factory must return a fresh, idle SQLAlchemy Session per call."""
        self._sessions = session_factory
        self._clock = clock or MarketClock()

    @contextmanager
    def _transaction(self):
        try:
            with self._sessions() as session:
                if session.in_transaction():
                    raise PositionLedgerError("adapter requires a fresh owned transaction")
                with session.begin():
                    # A snapshot taken before waiting could omit an older writer.
                    session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                    session.execute(text("SET LOCAL synchronous_commit = on"))
                    session.execute(text(
                        "LOCK TABLE trades, orders, trade_reservations, fills, positions, portfolio_state_cursor, "
                        "position_fill_receipts IN SHARE ROW EXCLUSIVE MODE"
                    ))
                    policy = session.execute(text(
                        "SELECT a.attidentity, s.seqcache FROM pg_attribute a "
                        "JOIN pg_sequence s ON s.seqrelid = "
                        "pg_get_serial_sequence('fills', 'ledger_seq')::regclass "
                        "WHERE a.attrelid = 'fills'::regclass AND a.attname = 'ledger_seq'"
                    )).one()
                    if policy != ("a", 1):
                        raise PositionLedgerError("unsafe fill sequence policy; migration 0013 required")
                    yield session
        except PositionLedgerError:
            raise
        except (SQLAlchemyError, ValueError, TypeError, KeyError) as exc:
            raise PositionLedgerError(f"position ledger transaction failed: {exc}") from exc

    @staticmethod
    def _mode(mode):
        if mode not in MODES:
            raise PositionLedgerError("unknown execution mode")

    @staticmethod
    def _rows(session, mode):
        rows = session.execute(
            select(Fill, Order, Trade).join(Order, Order.client_order_id == Fill.client_order_id)
            .join(Trade, Trade.trade_id == Order.trade_id)
            .where(Order.execution_mode == mode).order_by(Fill.ledger_seq)
        ).all()
        for fill, order, trade in rows:
            if fill.anomaly:
                raise PositionLedgerError(f"anomalous fill {fill.ledger_seq}: {fill.anomaly}")
            if (order.execution_mode, order.execution_venue, order.symbol) != (
                trade.execution_mode, trade.execution_venue, trade.symbol
            ) or fill.execution_venue != order.execution_venue:
                raise PositionLedgerError("inconsistent fill/order/trade metadata")
        return rows

    @staticmethod
    def _order(order, trade, filled_qty, reservation=None):
        if (order.execution_mode, order.execution_venue, order.symbol) != (
            trade.execution_mode, trade.execution_venue, trade.symbol
        ):
            raise PositionLedgerError("inconsistent order/trade metadata")
        if order.qty <= 0 or filled_qty > order.qty:
            raise PositionLedgerError("invalid order quantity or overfill")
        if reservation is not None and (order.trade_id, order.side, order.qty, order.position_effect) != (
            reservation.trade_id, trade.direction, reservation.qty, "open"
        ):
            raise PositionLedgerError("order differs from durable reservation")
        thesis = trade.thesis or {}
        return InFlightOrder(
            order.client_order_id, order.symbol, order.side, order.qty,
            order.position_effect, order.execution_mode, order.status, filled_qty,
            decimal(reservation.reference_price) if reservation is not None else
            None if order.limit_price is None else decimal(order.limit_price),
            None if thesis.get("final_stop") is None else decimal(thesis["final_stop"]),
        )

    @staticmethod
    def _reservation(reservation, trade):
        if trade.decision != "approved" or reservation.client_order_id != f"{trade.trade_id}:entry":
            raise PositionLedgerError("invalid approved reservation")
        thesis = trade.thesis or {}
        return InFlightOrder(reservation.client_order_id, trade.symbol, trade.direction,
            reservation.qty, "open", trade.execution_mode, "approved", 0,
            decimal(reservation.reference_price),
            None if thesis.get("final_stop") is None else decimal(thesis["final_stop"]))

    def _state(self, session, mode):
        self._mode(mode)
        cursor_row = session.get(PortfolioStateCursor, mode)
        cursor = cursor_row.last_applied_ledger_seq if cursor_row else 0
        rows = self._rows(session, mode)
        receipts = session.scalars(select(PositionFillReceipt).where(
            PositionFillReceipt.execution_mode == mode).order_by(PositionFillReceipt.ledger_seq)).all()
        if [r.ledger_seq for r in receipts] != [f.ledger_seq for f, _, _ in rows if f.ledger_seq <= cursor]:
            raise PositionLedgerError("incomplete applied fill receipts; legacy checkpoint requires explicit recovery")
        if cursor != (receipts[-1].ledger_seq if receipts else 0):
            raise PositionLedgerError("cursor does not match applied fill receipts")
        active, positions, daily, quantities = {}, {}, {}, {}
        sources = {f.ledger_seq: (f, o, t) for f, o, t in rows}
        for receipt in receipts:
            fill = _decode(receipt.fill_data)
            source = _fill(*sources[receipt.ledger_seq])
            # Thesis can change; receipts preserve the original stop/target.
            if replace(source, stop=fill.stop, target=fill.target) != fill or (
                receipt.execution_venue, receipt.venue_fill_id
            ) != fill.key:
                raise PositionLedgerError("conflicting fill identity or changed source facts")
            prior = active.get(fill.symbol)
            if prior is None and receipt.position_id in positions:
                raise PositionLedgerError("closed position identity reused")
            result = apply_fill(prior, fill, new_position_id=receipt.position_id)
            position = result.position
            if position.position_id != receipt.position_id or result.realized_delta != receipt.gross_pnl:
                raise PositionLedgerError("invalid realized-fill attribution")
            if receipt.trading_day != self._clock.trading_day(fill.venue_ts):
                raise PositionLedgerError("invalid fill trading day")
            positions[position.position_id] = position
            if position.qty:
                active[fill.symbol] = position
            else:
                active.pop(fill.symbol, None)
            key = fill.symbol, receipt.trading_day
            amount = daily.get(key, DailyAmounts(*key))
            daily[key] = replace(amount,
                profit=amount.profit + max(result.realized_delta, ZERO),
                loss=amount.loss + max(-result.realized_delta, ZERO),
                reported_fees=amount.reported_fees + (fill.commission or ZERO),
                unknown_fee_count=amount.unknown_fee_count + (fill.commission is None))
            quantities[fill.client_order_id] = quantities.get(fill.client_order_id, 0) + fill.qty
        stored = session.scalars(select(Position).where(Position.execution_mode == mode)).all()
        if {p.position_id for p in stored} != set(positions):
            raise PositionLedgerError("positions do not match durable fill history")
        for row in stored:
            position = positions[row.position_id]
            for field in ("trade_id", "execution_mode", "execution_venue", "symbol", "side", "qty", "opened_at", "closed_at", "status"):
                if getattr(row, field) != getattr(position, field):
                    raise PositionLedgerError(f"position projection mismatch: {field}")
            for field in ("avg_price", "realized_pnl"):
                if getattr(row, field) != getattr(position, field).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP):
                    raise PositionLedgerError(f"position projection mismatch: {field}")
            if position.qty:
                active[position.symbol] = replace(position,
                    stop=None if row.stop is None else decimal(row.stop),
                    target=None if row.target is None else decimal(row.target), exit_attempt=row.exit_attempt)
        orders = session.execute(select(Order, Trade).join(Trade).where(Order.execution_mode == mode)).all()
        entry_trades = {o.trade_id for o, _ in orders if o.position_effect == "open"}
        approved = session.scalars(select(Trade).where(Trade.execution_mode == mode, Trade.decision == "approved")).all()
        reservations = session.execute(select(TradeReservation, Trade).join(Trade).where(Trade.execution_mode == mode)).all()
        reserved_trades = {r.trade_id for r, _ in reservations}
        if any(t.trade_id not in entry_trades and t.trade_id not in reserved_trades for t in approved):
            raise PositionLedgerError("approved reservation has no durable order quantity")
        inflight = []
        reservation_by_order = {r.client_order_id: r for r, _ in reservations}
        for order, trade in orders:
            value = self._order(order, trade, quantities.get(order.client_order_id, 0), reservation_by_order.get(order.client_order_id))
            if order.status not in TERMINAL_STATUSES:
                inflight.append(value)
        order_ids = {o.client_order_id for o, _ in orders}
        for reservation, trade in reservations:
            value = self._reservation(reservation, trade)
            if reservation.client_order_id not in order_ids:
                inflight.append(value)
        return LedgerState(mode, cursor, datetime.now(timezone.utc), tuple(active.values()), tuple(inflight), tuple(daily.values()))

    def load_state(self, execution_mode):
        with self._transaction() as session:
            state = self._state(session, execution_mode)
        return state

    def pending_fills(self, execution_mode, after_cursor):
        self._mode(execution_mode)
        with self._transaction() as session:
            state = self._state(session, execution_mode)
            if state.cursor != after_cursor:
                raise PositionLedgerError("cursor conflict")
            fills = tuple(_fill(*row) for row in self._rows(session, execution_mode) if row[0].ledger_seq > after_cursor)
        return fills

    def get_order(self, client_order_id):
        with self._transaction() as session:
            pair = session.execute(select(Order, Trade).join(Trade).where(Order.client_order_id == client_order_id)).one_or_none()
            if pair is None:
                reserved = session.execute(select(TradeReservation, Trade).join(Trade).where(
                    TradeReservation.client_order_id == client_order_id)).one_or_none()
                if reserved is None:
                    return None
                self._state(session, reserved[1].execution_mode)
                return self._reservation(*reserved)
            order, trade = pair
            self._state(session, order.execution_mode)
            receipts = session.scalars(select(PositionFillReceipt).where(PositionFillReceipt.execution_mode == order.execution_mode)).all()
            filled = sum(r.fill_data["qty"] for r in receipts if r.fill_data["client_order_id"] == client_order_id)
            reservation = session.scalar(select(TradeReservation).where(TradeReservation.client_order_id == client_order_id))
            result = self._order(order, trade, filled, reservation)
        return result

    def commit_fill(self, application):
        fill = application.fill
        self._mode(fill.execution_mode)
        with self._transaction() as session:
            state = self._state(session, fill.execution_mode)
            receipt = session.scalar(select(PositionFillReceipt).where(
                PositionFillReceipt.execution_venue == fill.execution_venue,
                PositionFillReceipt.venue_fill_id == fill.venue_fill_id))
            if receipt is not None:
                if _decode(receipt.fill_data) != fill:
                    raise PositionLedgerError("conflicting fill identity")
                result = CommitResult(False, state)
            else:
                if state.cursor != application.expected_cursor:
                    raise PositionLedgerError("cursor conflict")
                pending = [row for row in self._rows(session, fill.execution_mode) if row[0].ledger_seq > state.cursor]
                if not pending or _fill(*pending[0]) != fill:
                    raise PositionLedgerError("application is not the first authoritative pending fill")
                prior = next((p for p in state.positions if p.symbol == fill.symbol), None)
                if prior is None and session.get(Position, application.position.position_id) is not None:
                    raise PositionLedgerError("position identity already used")
                computed = apply_fill(prior, fill, new_position_id=application.position.position_id)
                realized = RealizedFill(fill.execution_mode, fill.execution_venue, fill.venue_fill_id,
                    fill.ledger_seq, computed.position.position_id, fill.symbol,
                    self._clock.trading_day(fill.venue_ts), fill.venue_ts, computed.realized_delta, fill.commission)
                if computed.position != application.position or realized != application.realized:
                    raise PositionLedgerError("application disagrees with authoritative accounting")
                _write_position(session, computed.position)
                session.flush()
                session.add(PositionFillReceipt(ledger_seq=fill.ledger_seq, execution_mode=fill.execution_mode,
                    execution_venue=fill.execution_venue, venue_fill_id=fill.venue_fill_id,
                    position_id=computed.position.position_id, fill_data=_encode(fill),
                    trading_day=realized.trading_day, gross_pnl=realized.gross_pnl))
                cursor = session.get(PortfolioStateCursor, fill.execution_mode)
                if cursor is None:
                    cursor = PortfolioStateCursor(execution_mode=fill.execution_mode)
                    session.add(cursor)
                cursor.last_applied_ledger_seq = fill.ledger_seq
                session.flush()
                result = CommitResult(True, self._state(session, fill.execution_mode))
        # Exiting the context commits; acknowledgement is never returned early.
        return result
