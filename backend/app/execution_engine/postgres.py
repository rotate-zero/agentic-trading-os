"""PostgreSQL OrderLedgerPort and DecisionAuthorizationPort for entries."""
from uuid import UUID

from sqlalchemy import select

from app.db.ledger_transaction import ledger_transaction
from app.models.execution_ledger import Order, Trade, TradeReservation
from .ports import OrderInsertResult, OrderLedgerError, OrderRecord


class PostgresOrderLedger:
    def __init__(self, session_factory):
        self._sessions = session_factory

    def has_committed_decision(self, opportunity_id):
        try:
            trade_id = UUID(opportunity_id)
        except (ValueError, TypeError, AttributeError):
            return False
        with ledger_transaction(self._sessions, OrderLedgerError) as session:
            trade = session.get(Trade, trade_id)
            approved = trade is not None and trade.decision == "approved"
        return approved

    @staticmethod
    def _record(row):
        return OrderRecord(row.client_order_id, str(row.trade_id), row.symbol, row.side,
            row.position_effect, row.qty, row.order_type, row.limit_price, row.execution_mode,
            row.status, row.created_at, row.reject_reason, row.execution_venue)

    def insert_order(self, record):
        with ledger_transaction(self._sessions, OrderLedgerError) as session:
            trade_id = UUID(record.trade_id)
            trade = session.get(Trade, trade_id)
            reservation = session.get(TradeReservation, trade_id)
            if trade is None or trade.decision != "approved" or reservation is None:
                raise OrderLedgerError("entry requires a committed approval and reservation")
            if trade.execution_mode != "simulated" or trade.execution_venue != "simulated":
                raise OrderLedgerError("only simulated approvals may create entries")
            # An approved trade ID alone is not authority for arbitrary terms.
            expected = (reservation.client_order_id, trade.symbol, trade.direction,
                        reservation.qty, "open", "market", None, trade.execution_mode)
            supplied = (record.client_order_id, record.symbol, record.side, record.qty,
                        record.position_effect, record.order_type, record.limit_price, record.execution_mode)
            if supplied != expected or record.status != "approved" or record.execution_venue not in (None, trade.execution_venue):
                raise OrderLedgerError("order terms differ from committed approval")
            row = session.scalar(select(Order).where(Order.client_order_id == record.client_order_id))
            if row is not None:
                stored = (row.client_order_id, row.symbol, row.side, row.qty, row.position_effect,
                          row.order_type, row.limit_price, row.execution_mode)
                if stored != expected or row.trade_id != trade_id or row.execution_venue != trade.execution_venue:
                    raise OrderLedgerError("conflicting stored order identity")
                result = OrderInsertResult(self._record(row), False)
            else:
                if record.created_at.tzinfo is None or record.created_at.utcoffset() is None:
                    raise OrderLedgerError("order timestamp must be timezone-aware")
                row = Order(client_order_id=record.client_order_id, trade_id=trade_id,
                    symbol=trade.symbol, side=trade.direction, qty=reservation.qty,
                    position_effect="open", order_type="market", limit_price=None,
                    execution_mode=trade.execution_mode, execution_venue=trade.execution_venue,
                    status="approved", created_at=record.created_at)
                session.add(row)
                session.flush()
                result = OrderInsertResult(self._record(row), True)
        return result

    def update_order_status(self, client_order_id, status, *, reason=None, execution_venue=None):
        with ledger_transaction(self._sessions, OrderLedgerError) as session:
            row = session.scalar(select(Order).where(Order.client_order_id == client_order_id))
            if row is None:
                raise OrderLedgerError("unknown order")
            if status not in {"submitted", "rejected"}:
                raise OrderLedgerError("unsupported order status")
            if status == "submitted" and execution_venue not in (None, row.execution_venue):
                raise OrderLedgerError("submission venue differs from committed approval")
            # A stale acknowledgement cannot regress partial fills, terminal
            # states, or a reconciliation-owned unknown state.
            if row.status == status:
                return
            if row.status != "approved" and not (row.status == "submitted" and status == "rejected"):
                return
            row.status = status
            if status == "rejected":
                row.reject_reason = reason
            # A refused candidate venue never becomes the execution venue.
