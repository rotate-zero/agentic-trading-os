"""Compatibility for #172's caller-owned Session API, not a new port adapter.

Reconciliation keeps apply_fill(session, fill)/rebuild_from_ledger(session).
Only the shared pure accounting module does arithmetic. Cache installation is
staged in before_commit and performed in after_commit; rollback installs nothing.
This path assumes serialized ledger writers (Identity alone is NOT commit order).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import event, select

from app.models.execution_ledger import Fill, Order, PortfolioStateCursor, Position, Trade

from .accounting import LedgerFill, PositionState, ZERO, apply_fill, decimal
from .ports import DailyAmounts, InFlightOrder, LedgerState, PositionLedgerError, TERMINAL_STATUSES

logger = logging.getLogger(__name__)


def _fill(row, order, trade) -> LedgerFill:
    thesis = trade.thesis or {}
    return LedgerFill(
        row.ledger_seq, row.venue_fill_id, row.client_order_id, order.trade_id,
        order.execution_mode, row.execution_venue, order.symbol, order.side,
        order.position_effect, row.qty, decimal(row.price), row.venue_ts,
        None if row.commission is None else decimal(row.commission),
        None if thesis.get("final_stop") is None else decimal(thesis["final_stop"]),
        None if thesis.get("final_target") is None else decimal(thesis["final_target"]),
    )


def _cursor(session, mode):
    cursor = session.execute(
        select(PortfolioStateCursor).where(PortfolioStateCursor.execution_mode == mode).with_for_update()
    ).scalar_one_or_none()
    if cursor is None:
        cursor = PortfolioStateCursor(execution_mode=mode, last_applied_ledger_seq=0)
        session.add(cursor)
        session.flush()
    return cursor


def _history(owner, session, cursor):
    """Reconstruct lifetime and per-fill daily amounts; never bucket by close date.

    The existing schema stores lifetime signed P&L only. Immutable fills supply
    the missing separate profit/loss/fee history on restart, without a migration.
    """
    rows = session.execute(select(Position).where(Position.execution_mode == owner.execution_mode)).scalars().all()
    identities = {}
    for row in rows:
        key = row.trade_id, row.symbol, row.opened_at
        if key in identities:
            raise PositionLedgerError("legacy rows cannot disambiguate repeated opening timestamps")
        identities[key] = row
    active = {}
    all_positions = {}
    daily = {}
    applied_quantities = {}
    problems = []
    history = session.execute(
        select(Fill, Order, Trade).join(Order, Order.client_order_id == Fill.client_order_id)
        .join(Trade, Trade.trade_id == Order.trade_id)
        .where(Order.execution_mode == owner.execution_mode, Fill.ledger_seq <= cursor)
        .order_by(Fill.ledger_seq)
    ).all()
    for row, order, trade in history:
        if row.anomaly:
            problems.append(f"fill:{row.ledger_seq}:{row.anomaly}")
            continue
        fill = _fill(row, order, trade)
        prior = active.get(fill.symbol)
        stored = identities.get((fill.trade_id, fill.symbol, fill.venue_ts)) if prior is None else None
        if prior is None and stored is None:
            raise PositionLedgerError(f"applied fill {fill.ledger_seq} has no durable position identity")
        result = apply_fill(prior, fill, new_position_id=stored.position_id if stored else None)
        p = result.position
        if stored is not None:
            p = replace(p, stop=None if stored.stop is None else decimal(stored.stop),
                        target=None if stored.target is None else decimal(stored.target),
                        exit_attempt=stored.exit_attempt)
        all_positions[p.position_id] = p
        if p.qty:
            active[p.symbol] = p
        else:
            active.pop(p.symbol, None)
        key = fill.symbol, owner._clock.trading_day(fill.venue_ts)
        amount = daily.get(key, DailyAmounts(*key))
        daily[key] = replace(
            amount, profit=amount.profit + max(result.realized_delta, ZERO),
            loss=amount.loss + max(-result.realized_delta, ZERO),
            reported_fees=amount.reported_fees + (fill.commission if fill.commission is not None else ZERO),
            unknown_fee_count=amount.unknown_fee_count + (fill.commission is None),
        )
        applied_quantities[order.client_order_id] = applied_quantities.get(order.client_order_id, 0) + fill.qty
    return active, all_positions, daily, applied_quantities, tuple(problems)


def _read_state(owner, session):
    session.flush()
    cursor = session.get(PortfolioStateCursor, owner.execution_mode)
    seq = cursor.last_applied_ledger_seq if cursor else 0
    active, _, daily, quantities, problems = _history(owner, session, seq)
    pending = session.execute(
        select(Fill.ledger_seq).join(Order, Order.client_order_id == Fill.client_order_id)
        .where(Order.execution_mode == owner.execution_mode, Fill.ledger_seq > seq).limit(1)
    ).scalar_one_or_none()
    if pending is not None:
        problems += ("unapplied_ledger_fills",)
    orders = session.execute(
        select(Order, Trade).join(Trade, Trade.trade_id == Order.trade_id)
        .where(Order.execution_mode == owner.execution_mode, Order.status.not_in(TERMINAL_STATUSES))
    ).all()
    inflight = []
    for order, trade in orders:
        thesis = trade.thesis or {}
        inflight.append(InFlightOrder(
            order.client_order_id, order.symbol, order.side, order.qty, order.position_effect,
            order.execution_mode, order.status, quantities.get(order.client_order_id, 0),
            None if order.limit_price is None else decimal(order.limit_price),
            None if thesis.get("final_stop") is None else decimal(thesis["final_stop"]),
        ))
    return LedgerState(owner.execution_mode, seq, datetime.now(timezone.utc), tuple(active.values()),
                       tuple(inflight), tuple(daily.values()), problems=problems)


def _watch_commit(owner, session):
    if session.in_nested_transaction():
        raise PositionLedgerError("legacy portfolio API requires an outer transaction, not a savepoint")
    owner._ready = False
    key = ("portfolio_state", id(owner))
    if key in session.info:
        return
    staged = {}
    session.info[key] = staged

    def before_commit(s):
        staged["state"] = _read_state(owner, s)

    def after_commit(s):
        state = staged.pop("state", None)
        if state is not None:
            owner._install_state(state)

    def after_rollback(s):
        staged.clear()
        # Last cache may no longer agree with the caller's retried transaction.
        owner._ready = False

    event.listen(session, "before_commit", before_commit)
    event.listen(session, "after_commit", after_commit)
    event.listen(session, "after_rollback", after_rollback)


def _write_position(session, state: PositionState):
    row = session.get(Position, state.position_id)
    if row is None:
        row = Position(position_id=state.position_id)
        session.add(row)
    for name in ("trade_id", "execution_mode", "execution_venue", "symbol", "side", "qty",
                 "avg_price", "stop", "target", "opened_at", "closed_at", "status", "exit_attempt"):
        setattr(row, name, getattr(state, name))
    row.realized_pnl = state.realized_pnl


def apply_session_fill(owner, session, row):
    _watch_commit(owner, session)
    session.flush()
    order = session.execute(select(Order).where(Order.client_order_id == row.client_order_id)).scalar_one()
    if order.execution_mode != owner.execution_mode:
        raise PositionLedgerError("fill belongs to another execution mode")
    cursor = _cursor(session, owner.execution_mode)
    if row.ledger_seq <= cursor.last_applied_ledger_seq:
        return None  # duplicate must never signal a closure a second time
    first = session.execute(
        select(Fill.ledger_seq).join(Order, Order.client_order_id == Fill.client_order_id)
        .where(Order.execution_mode == owner.execution_mode, Fill.ledger_seq > cursor.last_applied_ledger_seq)
        .order_by(Fill.ledger_seq).limit(1)
    ).scalar_one()
    if first != row.ledger_seq:
        raise PositionLedgerError("cannot advance cursor past an unapplied fill")
    active, _, _, quantities, _ = _history(owner, session, cursor.last_applied_ledger_seq)
    trade = session.get(Trade, order.trade_id)
    fill = _fill(row, order, trade)
    prior = active.get(fill.symbol)
    total = quantities.get(order.client_order_id, 0) + fill.qty
    try:
        if total > order.qty:
            raise ValueError("fill exceeds ordered quantity")
        result = apply_fill(prior, fill, new_position_id=uuid4() if prior is None else None)
    except ValueError:
        # Preserve the reported fill but never invent an oversize realized gain
        # or silently reverse a position. Anomalies block usable snapshots.
        row.anomaly = "overfill" if total > order.qty or (prior is not None and fill.qty > prior.qty and fill.position_effect == "close") else "unmatched_order"
        logger.exception("invalid ledger fill %s retained as %s; portfolio unavailable", row.ledger_seq, row.anomaly)
        cursor.last_applied_ledger_seq = row.ledger_seq
        session.flush()
        return None
    _write_position(session, result.position)
    # Execution may already have committed filled/cancelled before notification.
    # Terminal status does not invalidate a legitimate, not-yet-applied fill.
    if order.status not in TERMINAL_STATUSES:
        order.status = "filled" if total == order.qty else "partially_filled"
    cursor.last_applied_ledger_seq = row.ledger_seq
    session.flush()
    return result.position


def rebuild_session(owner, session, *, full_rebuild=False):
    _watch_commit(owner, session)
    before = owner._state.positions if owner._state else ()
    cursor = _cursor(session, owner.execution_mode)
    rows = session.execute(
        select(Fill).join(Order, Order.client_order_id == Fill.client_order_id)
        .where(Order.execution_mode == owner.execution_mode, Fill.ledger_seq > cursor.last_applied_ledger_seq)
        .order_by(Fill.ledger_seq)
    ).scalars().all()
    for row in rows:
        apply_session_fill(owner, session, row)
    # full_rebuild audits/reconstructs from fill history, WITHOUT resetting the
    # cursor or duplicating position IDs. A missing identity fails honestly.
    if full_rebuild:
        _, positions, _, _, _ = _history(owner, session, cursor.last_applied_ledger_seq)
        for position in positions.values():
            _write_position(session, position)
    session.commit()
    if before and owner._state and before != owner._state.positions:
        logger.warning("rebuild_from_ledger: memory disagreed; ledger wins for %s", owner.execution_mode)
    return owner.get_snapshot()
