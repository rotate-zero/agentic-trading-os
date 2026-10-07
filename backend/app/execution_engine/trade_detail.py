"""Curated, read-only lifecycle detail for ONE persisted authorization (Trade).

`trades` is the authorization ledger; every other lifecycle table links back to
it through a real foreign key (verified against `models/execution_ledger.py`):

    orders.trade_id        -> trades.trade_id      (entry AND close orders)
    positions.trade_id     -> trades.trade_id
    fills.client_order_id  -> orders.client_order_id   (so fills reach a trade via its orders)
    exit_requests.position_id -> positions.position_id (so requests reach a trade via its positions)
    trades.outcome_id      -> strategy_outcomes.outcome_id

Every linked population is read in full: there is no `LIMIT` anywhere in this
module, so the recent-list caps of the sibling routes cannot truncate a detail.
All statements run in ONE worker-owned REPEATABLE READ, server-enforced
READ ONLY transaction, so the collections describe a single consistent
snapshot. Nothing here writes, locks, reconciles, retries or records.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.execution_engine.authorization_history import AUTHORIZATION_COLUMNS, serialize_authorization
from app.models.execution_ledger import ExitRequest, Fill, Order, Position, Trade
from app.models.trading_intelligence import StrategyOutcomeRecord


def _utc(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _exact(value: Decimal | float | int | None) -> str | None:
    # Numeric(18, 6) columns arrive as Decimal; str() keeps every stored digit. NULL stays None.
    return None if value is None else str(value)


def read_execution_trade_detail(trade_id: uuid.UUID) -> dict[str, Any] | None:
    """Return the trade's authorization plus its complete linked lifecycle, or None if unknown.

    Rejected trades are valid details: their downstream collections are simply
    empty and `outcome` is all-null. Order of each collection is a strict total
    order (ledger key, or timestamp then primary key) so repeated reads never
    reshuffle rows. The recorded outcome status is returned exactly as stored;
    no failure reason is inferred (reasons for pending_retry/blocked exist only
    in logs).
    """
    session = SessionLocal()
    try:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
        trade = session.execute(
            select(*AUTHORIZATION_COLUMNS, Trade.direction, Trade.origin, Trade.status,
                   Trade.outcome_id, Trade.outcome_status, Trade.updated_at)
            .where(Trade.trade_id == trade_id)
        ).one_or_none()
        if trade is None:
            return None

        orders = session.execute(
            select(Order).where(Order.trade_id == trade_id).order_by(Order.id.asc())
        ).scalars().all()
        fills = session.execute(
            select(Fill, Order.symbol)
            .join(Order, Fill.client_order_id == Order.client_order_id)
            .where(Order.trade_id == trade_id)
            .order_by(Fill.ledger_seq.asc())
        ).all()
        positions = session.execute(
            select(Position).where(Position.trade_id == trade_id)
            .order_by(Position.opened_at.asc(), Position.position_id.asc())
        ).scalars().all()
        exit_requests = session.execute(
            select(ExitRequest, Position.symbol, Position.status, Position.qty)
            .join(Position, Position.position_id == ExitRequest.position_id)
            .where(Position.trade_id == trade_id)
            .order_by(ExitRequest.trigger_ts.asc(), ExitRequest.position_id.asc())
        ).all()
        outcome = None
        if trade.outcome_id is not None:
            outcome = session.get(StrategyOutcomeRecord, trade.outcome_id)

        payload = {
            "trade": {
                **serialize_authorization(trade),
                "direction": trade.direction,
                "origin": trade.origin,
                "status": trade.status,
                "updated_at": _utc(trade.updated_at),
            },
            "orders": [
                {
                    "id": row.id,
                    "client_order_id": row.client_order_id,
                    "position_id": None if row.position_id is None else str(row.position_id),
                    "symbol": row.symbol,
                    "side": row.side,
                    "position_effect": row.position_effect,
                    "order_type": row.order_type,
                    "qty": row.qty,
                    "limit_price": _exact(row.limit_price),
                    "status": row.status,
                    "execution_mode": row.execution_mode,
                    "execution_venue": row.execution_venue,
                    "exit_reason": row.exit_reason,
                    "reject_reason": row.reject_reason,
                    "created_at": _utc(row.created_at),
                    "updated_at": _utc(row.updated_at),
                }
                for row in orders
            ],
            "fills": [
                {
                    "ledger_seq": fill.ledger_seq,
                    "client_order_id": fill.client_order_id,
                    "symbol": symbol,
                    "execution_venue": fill.execution_venue,
                    "venue_fill_id": fill.venue_fill_id,
                    "qty": fill.qty,
                    "price": _exact(fill.price),
                    "venue_ts": _utc(fill.venue_ts),
                    "commission": _exact(fill.commission),
                    "anomaly": fill.anomaly,
                    "created_at": _utc(fill.created_at),
                }
                for fill, symbol in fills
            ],
            "positions": [
                {
                    "position_id": str(row.position_id),
                    "symbol": row.symbol,
                    "side": row.side,
                    "qty": row.qty,
                    "status": row.status,
                    "avg_price": _exact(row.avg_price),
                    "stop": _exact(row.stop),
                    "target": _exact(row.target),
                    "opened_at": _utc(row.opened_at),
                    "closed_at": _utc(row.closed_at),
                    "realized_pnl": _exact(row.realized_pnl),
                    "exit_attempt": row.exit_attempt,
                }
                for row in positions
            ],
            "exit_requests": [
                {
                    "position_id": str(request.position_id),
                    "symbol": symbol,
                    "exit_reason": request.exit_reason,
                    "trigger_price": _exact(request.trigger_price),
                    "trigger_ts": _utc(request.trigger_ts),
                    "retry_after": _utc(request.retry_after),
                    "created_at": _utc(request.created_at),
                    "position_status": position_status,
                    "remaining_qty": remaining_qty,
                    "eod_flatten_at": _utc(request.eod_flatten_at),
                    "eod_close_at": _utc(request.eod_close_at),
                    "eod_expired_at": _utc(request.eod_expired_at),
                    "fallback_reason": request.fallback_reason,
                    "fallback_trigger_price": _exact(request.fallback_trigger_price),
                    "fallback_trigger_ts": _utc(request.fallback_trigger_ts),
                }
                for request, symbol, position_status, remaining_qty in exit_requests
            ],
            "outcome": {
                "outcome_status": trade.outcome_status,
                "outcome_id": None if trade.outcome_id is None else str(trade.outcome_id),
                "summary": None if outcome is None else {
                    "exit_reason": outcome.exit_reason,
                    "entry_price": _exact(outcome.entry_price),
                    "entry_qty": _exact(outcome.entry_qty),
                    "exit_price": _exact(outcome.exit_price),
                    "exit_qty": _exact(outcome.exit_qty),
                    "commission_total": _exact(outcome.commission_total),
                    "realized_pnl": _exact(outcome.realized_pnl),
                    "realized_r": _exact(outcome.realized_r),
                    "entry_filled_at": _utc(outcome.entry_filled_at),
                    "exit_filled_at": _utc(outcome.exit_filled_at),
                    "holding_seconds": outcome.holding_seconds,
                },
            },
        }
        return payload
    finally:
        session.rollback()
        session.close()
