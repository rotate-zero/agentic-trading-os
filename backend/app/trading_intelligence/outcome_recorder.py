"""Simulated outcome writer. Events wake this worker; applied receipts supply facts."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.ledger_transaction import ledger_transaction
from app.models.execution_ledger import Fill, Order, Position, PositionFillReceipt, Trade, TradeReservation
from app.portfolio_state.accounting import apply_fill, decimal
from app.portfolio_state.postgres import _decode
from app.schemas.events.envelope import EventType
from app.schemas.events.execution import OrderFilled, PositionClosed
from app.schemas.performance import StrategyOutcome
from app.trading_intelligence.performance import record_strategy_outcome_in_session
from app.trading_intelligence.state_snapshot import capture_strategy_outcome_snapshots

logger = logging.getLogger(__name__)
_STOP = object()
_CENT = Decimal("0.000001")


class OutcomeBlocked(Exception):
    """The durable ledger cannot support a truthful outcome without repair."""


def _price(value):
    result = decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)
    if result <= 0:
        raise ValueError("price must be positive")
    return result


def realized_r(entry, exit, basis, direction):
    """Direction-aware planned-risk R, using the immutable structural basis."""
    entry, exit, basis = _price(entry), _price(exit), _price(basis)
    risk = abs(entry - basis)
    if not risk or direction not in {"BUY", "SELL"}:
        raise ValueError("R basis is unavailable")
    return float(((exit - entry) * (1 if direction == "BUY" else -1)) / risk)


def _capture(symbol: str, suffix: str, *, missing_reason: str = "engine_cold_start"):
    market = context = None
    reasons = {}
    try:
        snapshots = capture_strategy_outcome_snapshots(symbol)
        market, context = snapshots.market_state, snapshots.context
    except Exception:
        logger.exception("Outcome snapshot capture failed for %s", symbol)
        missing_reason = "snapshot_capture_error"
    for key, value in ((f"market_state_at_{suffix}", market), (f"context_at_{suffix}", context)):
        if value is None:
            reasons[key] = missing_reason
    return market, context, reasons


class OutcomeRecorder:
    def __init__(self, bus, session_factory, *, snapshot_max_lag_seconds=60, sweep_interval_seconds=60,
                 batch_size=100):
        self._bus = bus
        self._sessions = session_factory
        self._lag = snapshot_max_lag_seconds
        self._interval = sweep_interval_seconds
        self._batch_size = batch_size
        self._queue = asyncio.Queue()
        self._queued = set()
        self._worker = None
        self._sweeper = None
        self._started_at = datetime.now(timezone.utc)
        self._subscribed = False
        self._scan_cursor = None

    async def start(self):
        if self._worker is not None:
            return
        self._started_at = datetime.now(timezone.utc)
        self._bus.subscribe(EventType.ORDER_FILLED, self._on_fill)
        self._bus.subscribe(EventType.POSITION_CLOSED, self._on_close)
        self._subscribed = True
        try:
            self._worker = asyncio.create_task(self._work(), name="outcome-recorder")
            await self._startup_scan()
            self._sweeper = asyncio.create_task(self._sweep(), name="outcome-recorder-sweep")
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        if self._subscribed:
            self._bus.unsubscribe(EventType.ORDER_FILLED, self._on_fill)
            self._bus.unsubscribe(EventType.POSITION_CLOSED, self._on_close)
            self._subscribed = False
        if self._sweeper is not None:
            self._sweeper.cancel()
            try:
                await self._sweeper
            except asyncio.CancelledError:
                pass
            self._sweeper = None
        if self._worker is not None:
            await self._queue.join()
            self._queue.put_nowait(_STOP)
            await self._worker
            self._worker = None

    def _enqueue(self, kind, identity):
        key = kind, identity
        if key not in self._queued:
            self._queued.add(key)
            self._queue.put_nowait(key)

    def _on_fill(self, envelope):
        payload = OrderFilled.model_validate(envelope.payload)
        self._enqueue("entry", payload.order_id)

    def _on_close(self, envelope):
        payload = PositionClosed.model_validate(envelope.payload)
        if payload.trade_id:
            self._enqueue("close", UUID(payload.trade_id))

    async def _startup_scan(self):
        cursor = None
        while True:
            rows = await asyncio.to_thread(self._pending_rows, cursor)
            for closed_at, trade_id in rows:
                self._enqueue("close", trade_id)
            if len(rows) < self._batch_size:
                return
            cursor = rows[-1]
            await asyncio.sleep(0)

    async def scan(self):
        """Enqueue one bounded page, rotating through older pending closures."""
        rows = await asyncio.to_thread(self._pending_rows, self._scan_cursor)
        if not rows and self._scan_cursor is not None:
            self._scan_cursor = None
            rows = await asyncio.to_thread(self._pending_rows, None)
        for closed_at, trade_id in rows:
            self._enqueue("close", trade_id)
        if rows:
            self._scan_cursor = rows[-1]

    def _pending_rows(self, after):
        with self._sessions() as session:
            query = (select(Position.closed_at, Trade.trade_id).join(Trade, Position.trade_id == Trade.trade_id)
                .where(Trade.decision == "approved", Trade.status == "closed", Trade.execution_mode == "simulated",
                       Trade.origin == "auto", Trade.outcome_id.is_(None),
                       func.coalesce(Trade.outcome_status, "pending") != "blocked")
                .order_by(Position.closed_at, Trade.trade_id).limit(self._batch_size))
            if after is not None:
                query = query.where(tuple_(Position.closed_at, Trade.trade_id) > after)
            return session.execute(query).all()

    async def _sweep(self):
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.scan()
            except Exception:
                logger.exception("OutcomeRecorder sweep failed; next sweep will retry")

    async def _work(self):
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                kind, identity = item
                if kind == "entry":
                    await self.capture_entry(identity)
                else:
                    await self.record_trade(identity)
            except Exception:
                logger.exception("OutcomeRecorder work item failed: %s", item)
            finally:
                self._queued.discard(item)
                self._queue.task_done()

    async def capture_entry(self, order_id):
        details = await asyncio.to_thread(self._entry_candidate, order_id)
        if details is None:
            return
        trade_id, symbol, first_ts = details
        reason = "engine_state_lost_on_restart" if first_ts < self._started_at else "engine_cold_start"
        market, context, reasons = _capture(symbol, "entry", missing_reason=reason)
        try:
            await asyncio.to_thread(self._store_entry, trade_id, market, context, reasons)
        except Exception:
            logger.exception("Entry snapshot unavailable for trade_id=%s; outcome will record NULL + reason", trade_id)

    def _entry_candidate(self, order_id):
        with self._sessions() as session:
            order = session.scalar(select(Order).where(Order.client_order_id == order_id))
            if order is None or order.position_effect != "open":
                return None
            trade = session.get(Trade, order.trade_id)
            if trade is None or trade.status == "closed" or (trade.execution_mode, trade.execution_venue, trade.origin, trade.decision) != (
                "simulated", "simulated", "auto", "approved") or trade.entry_snapshot_captured_at is not None or trade.entry_snapshot_missing_reasons is not None:
                return None
            fills = session.scalars(select(Fill).join(Order, Fill.client_order_id == Order.client_order_id)
                                    .where(Order.trade_id == trade.trade_id, Order.position_effect == "open")
                                    .order_by(Fill.ledger_seq).limit(2)).all()
            if len(fills) != 1:  # a later fill must never be labelled the entry snapshot
                return None
            return trade.trade_id, trade.symbol, fills[0].venue_ts

    def _store_entry(self, trade_id, market, context, reasons):
        # Same table-before-row order as ledger_transaction and commit_fill.
        with ledger_transaction(self._sessions, SQLAlchemyError) as session:
            trade = session.scalar(select(Trade).where(Trade.trade_id == trade_id).with_for_update())
            if trade is None or trade.status == "closed" or trade.entry_snapshot_captured_at is not None or trade.entry_snapshot_missing_reasons is not None:
                return
            trade.entry_market_state = market
            trade.entry_context = context
            trade.entry_snapshot_missing_reasons = reasons or None
            trade.entry_snapshot_captured_at = datetime.now(timezone.utc)

    async def record_trade(self, trade_id):
        details = await asyncio.to_thread(self._close_candidate, trade_id)
        if details is None:
            return "skipped"
        symbol, closed_at = details
        now = datetime.now(timezone.utc)
        if closed_at is None or (now - closed_at).total_seconds() > self._lag:
            market = context = None
            reasons = {"market_state_at_exit": "recorder_unavailable", "context_at_exit": "recorder_unavailable"}
        else:
            market, context, reasons = _capture(symbol, "exit")
        try:
            return await asyncio.to_thread(self._record_locked, trade_id, market, context, reasons)
        except SQLAlchemyError:
            logger.exception("OutcomeRecorder transient failure trade_id=%s", trade_id)
            try:
                await asyncio.to_thread(self._mark_retry, trade_id)
            except Exception:
                logger.exception("OutcomeRecorder could not mark pending_retry trade_id=%s", trade_id)
            return "pending_retry"

    def _close_candidate(self, trade_id):
        with self._sessions() as session:
            trade = session.get(Trade, trade_id)
            if trade is None or trade.outcome_id is not None or trade.outcome_status == "blocked" or trade.status != "closed":
                return None
            position = session.scalar(select(Position).where(Position.trade_id == trade_id))
            closed_at = position.closed_at if position is not None else None
            return trade.symbol, closed_at

    def _record_locked(self, trade_id, market, context, exit_reasons):
        try:
            with ledger_transaction(self._sessions, SQLAlchemyError) as session:
                trade = session.scalar(select(Trade).where(Trade.trade_id == trade_id).with_for_update())
                if trade is None or trade.outcome_id is not None or trade.outcome_status == "blocked" or trade.status != "closed":
                    return "skipped"
                try:
                    outcome = self._build(session, trade, market, context, exit_reasons)
                except OutcomeBlocked as exc:
                    trade.outcome_status = "blocked"
                    logger.error("OutcomeRecorder blocked trade_id=%s reason=%s", trade_id, exc)
                    return "blocked"
                except (ValidationError, ValueError, TypeError, KeyError) as exc:
                    trade.outcome_status = "blocked"
                    logger.error("OutcomeRecorder blocked trade_id=%s reason=contract_validation_failed: %s", trade_id, exc)
                    return "blocked"
                record_strategy_outcome_in_session(session, outcome)
                session.flush()
                trade.outcome_id = outcome.outcome_id
                trade.outcome_status = "recorded"
            return "recorded"
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return self._block_after_failure(trade_id, "contract_validation_failed", exc)
        except IntegrityError as exc:
            return self._block_after_failure(trade_id, "write_rejected", exc)

    def _block_after_failure(self, trade_id, reason, exc):
        logger.error("OutcomeRecorder blocked trade_id=%s reason=%s: %s", trade_id, reason, exc)
        with ledger_transaction(self._sessions, SQLAlchemyError) as session:
            trade = session.scalar(select(Trade).where(Trade.trade_id == trade_id).with_for_update())
            if trade is not None and trade.outcome_id is None:
                trade.outcome_status = "blocked"
        return "blocked"

    def _mark_retry(self, trade_id):
        with ledger_transaction(self._sessions, SQLAlchemyError) as session:
            trade = session.scalar(select(Trade).where(Trade.trade_id == trade_id).with_for_update())
            if trade is not None and trade.outcome_id is None and trade.outcome_status != "blocked":
                trade.outcome_status = "pending_retry"

    def _build(self, session, trade, market, context, exit_reasons):
        if (trade.decision, trade.origin, trade.execution_mode, trade.execution_venue) != (
            "approved", "auto", "simulated", "simulated"):
            raise OutcomeBlocked("unsupported_mode")
        positions = session.scalars(select(Position).where(Position.trade_id == trade.trade_id)).all()
        if len(positions) != 1 or positions[0].status != "closed":
            raise OutcomeBlocked("multi_position_trade")
        projection = positions[0]
        receipts = session.scalars(select(PositionFillReceipt).where(
            PositionFillReceipt.position_id == projection.position_id).order_by(PositionFillReceipt.ledger_seq)).all()
        if not receipts:
            raise OutcomeBlocked("ledger_inconsistent")
        days = {receipt.trading_day for receipt in receipts}
        if len(days) != 1:
            raise OutcomeBlocked("multi_day_position")
        state = None
        first = last = None
        for receipt in receipts:
            try:
                fill = _decode(receipt.fill_data)
                if (fill.trade_id, fill.execution_mode, fill.execution_venue, fill.symbol) != (
                    trade.trade_id, "simulated", "simulated", trade.symbol):
                    raise OutcomeBlocked("ledger_inconsistent")
                source_fill = session.get(Fill, receipt.ledger_seq)
                source_order = session.scalar(select(Order).where(Order.client_order_id == fill.client_order_id))
                if source_fill is None or source_order is None or source_fill.anomaly or (
                    source_fill.client_order_id, source_fill.execution_venue, source_fill.venue_fill_id,
                    source_fill.qty, decimal(source_fill.price), source_fill.venue_ts,
                    None if source_fill.commission is None else decimal(source_fill.commission),
                ) != (
                    fill.client_order_id, fill.execution_venue, fill.venue_fill_id,
                    fill.qty, fill.price, fill.venue_ts, fill.commission,
                ) or (
                    source_order.trade_id, source_order.execution_mode, source_order.execution_venue,
                    source_order.symbol, source_order.side, source_order.position_effect,
                ) != (
                    fill.trade_id, fill.execution_mode, fill.execution_venue,
                    fill.symbol, fill.side, fill.position_effect,
                ):
                    raise OutcomeBlocked("ledger_inconsistent")
                state = apply_fill(state, fill, new_position_id=projection.position_id if state is None else None).position
            except (ValueError, TypeError, KeyError, ArithmeticError):
                raise OutcomeBlocked("ledger_inconsistent") from None
            if first is None:
                first = fill
            last = fill
        if state.status != "closed" or state.qty != 0 or state.entry_qty != state.exit_qty or (
            (state.trade_id, state.execution_mode, state.execution_venue, state.symbol, state.side) !=
            (projection.trade_id, projection.execution_mode, projection.execution_venue, projection.symbol, projection.side) or
            state.opened_at != projection.opened_at or state.closed_at != projection.closed_at or
            state.avg_price.quantize(_CENT, rounding=ROUND_HALF_UP) != projection.avg_price or
            state.realized_pnl.quantize(_CENT, rounding=ROUND_HALF_UP) != projection.realized_pnl):
            raise OutcomeBlocked("ledger_inconsistent")
        if first.position_effect != "open" or last.position_effect != "close":
            raise OutcomeBlocked("ledger_inconsistent")
        order = session.scalar(select(Order).where(Order.client_order_id == last.client_order_id))
        if order is None or order.trade_id != trade.trade_id or order.position_effect != "close" or not order.exit_reason:
            raise OutcomeBlocked("exit_reason_unavailable")
        open_orders = session.scalars(select(Order).where(Order.trade_id == trade.trade_id, Order.position_effect == "open")).all()
        filled_by_order = {o.client_order_id: 0 for o in open_orders}
        for receipt in receipts:
            data = receipt.fill_data
            if data["client_order_id"] in filled_by_order:
                filled_by_order[data["client_order_id"]] += data["qty"]
        if any((o.status not in {"cancelled", "rejected"} and filled_by_order[o.client_order_id] < o.qty)
               or filled_by_order[o.client_order_id] > o.qty for o in open_orders):
            raise OutcomeBlocked("multi_position_trade")
        thesis = trade.thesis or {}
        evidence = thesis.get("evidence")
        if not isinstance(evidence, dict):
            raise OutcomeBlocked("evidence_unavailable")
        decision = trade.decision_record or {}
        try:
            basis = _price(thesis["structural_invalidation"])
            if basis != _price(decision["structural_invalidation"]):
                raise ValueError("basis differs")
            r = realized_r(state.avg_price, state.exit_price, basis, trade.direction)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            raise OutcomeBlocked("r_basis_unavailable") from None
        reservation = session.get(TradeReservation, trade.trade_id)
        if reservation is None:
            raise OutcomeBlocked("ledger_inconsistent")
        entry_reasons = dict(trade.entry_snapshot_missing_reasons or {})
        if trade.entry_snapshot_captured_at is None and not entry_reasons:
            entry_reasons = {"market_state_at_entry": "recorder_unavailable", "context_at_entry": "recorder_unavailable"}
        for key, value in (("market_state_at_entry", trade.entry_market_state), ("context_at_entry", trade.entry_context)):
            if value is None:
                entry_reasons.setdefault(key, "recorder_unavailable")
        setup = datetime.fromisoformat(decision["setup_detected_at"])
        decided = datetime.fromisoformat(decision["decided_at"])
        commission = state.fees
        return StrategyOutcome(
            outcome_id=uuid4(), opportunity_id=trade.trade_id, schema_version=2,
            strategy_name=trade.strategy_name, strategy_version=trade.strategy_version,
            symbol=trade.symbol, origin=trade.origin, is_backtest=False, backtest_run_id=None,
            execution_mode=trade.execution_mode, execution_venue=trade.execution_venue,
            trading_day=next(iter(days)), setup_detected_at=setup, signal_confirmed_at=None,
            decided_at=decided, entry_filled_at=first.venue_ts, exit_filled_at=last.venue_ts,
            holding_seconds=int((last.venue_ts - first.venue_ts).total_seconds()),
            direction=trade.direction, entry_price=float(_price(state.avg_price)), entry_qty=state.entry_qty,
            exit_price=float(_price(state.exit_price)), exit_qty=state.exit_qty,
            commission_total=None if commission is None else float(commission),
            slippage_entry=float(_price(state.avg_price) - _price(reservation.reference_price)),
            realized_pnl=float(state.realized_pnl - commission if commission is not None else state.realized_pnl),
            realized_r=r, exit_reason=order.exit_reason,
            structural_invalidation=float(basis), structural_target=float(_price(thesis["structural_target"])),
            final_stop=float(_price(thesis["final_stop"])), final_target=float(_price(thesis["final_target"])),
            confidence_at_signal=float(decimal(thesis["confidence"])), evidence=evidence,
            market_state_at_entry=trade.entry_market_state, context_at_entry=trade.entry_context,
            market_state_at_exit=market, context_at_exit=context,
            snapshot_missing_reasons={**entry_reasons, **exit_reasons} or None,
            feature_snapshot_id=None,
        )
