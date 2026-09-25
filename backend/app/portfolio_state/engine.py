"""Portfolio State: queue/worker, committed read cache, Session compatibility.

Events are wake-ups, never accounting inputs: OrderFilled has no unique fill
identity. Replay the ledger's safe prefix, commit each fill, THEN publish.
No venue calls, exit policy, outcome writing, or live startup wiring.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from app.core.market_clock import MarketClock, get_market_clock
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import OrderApproved, OrderFilled, OrderStatusChanged, PositionClosed
from app.schemas.events.market_data import PriceUpdated

from .accounting import MODES, PositionState, apply_fill, aware, decimal
from .ports import FillApplication, InFlightOrder, LedgerState, ORDER_STATUSES, PositionLedgerError, PositionLedgerPort, RealizedFill
from .snapshot import PortfolioSnapshot, build_snapshot

logger = logging.getLogger(__name__)
_STOP = object()
_REFRESH = object()

__all__ = ["PortfolioState", "PositionState", "InFlightOrder", "PortfolioSnapshot"]


class PortfolioState:
    def __init__(
        self, execution_mode: str, *, clock: MarketClock | None = None,
        ledger: PositionLedgerPort | None = None, bus: EventBus | None = None,
    ) -> None:
        if execution_mode not in MODES:
            raise ValueError("unknown execution mode")
        self.execution_mode = execution_mode
        self._clock = clock or get_market_clock()
        self._ledger = ledger
        self._bus = bus
        self._state: LedgerState | None = None
        self._ready = False
        self._marks: dict[str, tuple[Decimal, datetime]] = {}
        self._unresolved_orders: set[str] = set()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._subscribed = False
        self._accepting = False

    def get_snapshot(self, symbol: str | None = None, *, trading_day: date | None = None) -> PortfolioSnapshot | None:
        """Detached snapshot for this mode, filtered when symbol is given.

        None = not restored, blocked/stale, or an unknown symbol. A restored
        empty ledger is known flat. Daily amounts remain None if history is
        incomplete. Read-day rollover never closes or resets a position.
        """
        if not self._ready or self._state is None:
            return None
        return build_snapshot(self._state, self._marks, trading_day or self._clock.trading_day(), symbol)

    def _install_state(self, state: LedgerState) -> None:
        if state.execution_mode != self.execution_mode:
            raise PositionLedgerError("checkpoint belongs to another execution mode")
        aware(state.as_of)
        symbols = [p.symbol for p in state.positions]
        if len(symbols) != len(set(symbols)):
            raise PositionLedgerError("multiple positions for one symbol in a mode are not representable")
        if any(p.execution_mode != self.execution_mode or p.qty <= 0 or p.status == "closed" for p in state.positions):
            raise PositionLedgerError("invalid open position checkpoint")
        if any(o.execution_mode != self.execution_mode for o in state.orders):
            raise PositionLedgerError("order checkpoint mixes execution modes")
        if any(o.status not in ORDER_STATUSES or o.qty <= 0 or not 0 <= o.filled_qty <= o.qty for o in state.orders):
            raise PositionLedgerError("invalid order checkpoint")
        old_ids = {p.symbol: p.position_id for p in self._state.positions} if self._state else {}
        new_ids = {p.symbol: p.position_id for p in state.positions}
        self._state = state
        self._marks = {s: m for s, m in self._marks.items() if old_ids.get(s) == new_ids.get(s) and s in new_ids}
        self._ready = not state.problems and not self._unresolved_orders

    def update_mark(self, symbol: str, price: float | Decimal, ts: datetime) -> None:
        if self._state is None or not any(p.symbol == symbol for p in self._state.positions):
            return
        aware(ts)
        if ts < next(p.opened_at for p in self._state.positions if p.symbol == symbol):
            return
        price = decimal(price)
        if price <= 0:
            raise ValueError("mark must be positive")
        previous = self._marks.get(symbol)
        if previous is None or ts > previous[1]:
            self._marks[symbol] = price, ts

    async def start(self) -> None:
        if self._accepting:
            return
        if self._ledger is None or self._bus is None:
            raise RuntimeError("event worker requires a PositionLedgerPort and EventBus")
        if not self._subscribed:
            for event_type in (EventType.ORDER_APPROVED, EventType.ORDER_FILLED,
                               EventType.ORDER_STATUS_CHANGED, EventType.PRICE_UPDATED):
                self._bus.subscribe(event_type, self._on_event)
            self._subscribed = True
        self._accepting = True
        # Subscribe before restore so order notifications during startup queue.
        self._queue.put_nowait(_REFRESH)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="portfolio-state")
        await self._queue.join()

    async def stop(self) -> None:
        self._accepting = False
        if self._subscribed and self._bus is not None:
            for event_type in (EventType.ORDER_APPROVED, EventType.ORDER_FILLED,
                               EventType.ORDER_STATUS_CHANGED, EventType.PRICE_UPDATED):
                self._bus.unsubscribe(event_type, self._on_event)
            self._subscribed = False
        if self._worker_task is not None:
            self._queue.put_nowait(_STOP)
            await self._worker_task
            self._worker_task = None
        self._ready = False
        self._marks.clear()

    async def refresh(self) -> None:
        """Explicit ledger catch-up (also needed for cancellations: current
        OrderStatusChanged only represents rejection). No polling is wired.
        """
        if not self._accepting:
            raise RuntimeError("portfolio worker is not started")
        self._ready = False
        self._queue.put_nowait(_REFRESH)
        await self._queue.join()

    def _on_event(self, envelope: EventEnvelope) -> None:
        if not self._accepting:
            return
        if envelope.event_type == EventType.PRICE_UPDATED and (
            self._state is None or not any(p.symbol == envelope.symbol for p in self._state.positions)
        ):
            return  # bus has no symbol subscription filter
        self._queue.put_nowait(envelope.model_copy(deep=True))
        if envelope.event_type != EventType.PRICE_UPDATED:
            self._ready = False  # queued accounting is not yet a current snapshot

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                if item is _REFRESH:
                    await self._synchronize()
                elif item.event_type == EventType.PRICE_UPDATED:
                    tick = PriceUpdated.model_validate(item.payload)
                    self.update_mark(item.symbol, tick.price, tick.exchange_ts)
                else:
                    schema = {
                        EventType.ORDER_APPROVED: OrderApproved,
                        EventType.ORDER_FILLED: OrderFilled,
                        EventType.ORDER_STATUS_CHANGED: OrderStatusChanged,
                    }[item.event_type]
                    notification = schema.model_validate(item.payload)
                    self._unresolved_orders.add(notification.order_id)
                    await self._synchronize()
            except Exception:
                self._ready = False
                logger.exception("Portfolio State processing failed; snapshot unavailable until successful refresh")
            finally:
                self._queue.task_done()

    async def _synchronize(self) -> None:
        assert self._ledger is not None and self._bus is not None
        self._ready = False
        self._install_state(await asyncio.to_thread(self._ledger.load_state, self.execution_mode))
        self._ready = False
        assert self._state is not None
        if self._state.problems:
            raise PositionLedgerError("unresolved ledger anomalies: " + ", ".join(self._state.problems))
        fills = await asyncio.to_thread(self._ledger.pending_fills, self.execution_mode, self._state.cursor)
        last = self._state.cursor
        for fill in fills:
            if fill.execution_mode != self.execution_mode or fill.ledger_seq <= last:
                raise PositionLedgerError("pending fills are not an ordered, mode-scoped prefix")
            last = fill.ledger_seq
            position = next((p for p in self._state.positions if p.symbol == fill.symbol), None)
            result = apply_fill(position, fill, new_position_id=uuid4() if position is None else None)
            attribution = RealizedFill(
                fill.execution_mode, fill.execution_venue, fill.venue_fill_id, fill.ledger_seq,
                result.position.position_id, fill.symbol, self._clock.trading_day(fill.venue_ts),
                fill.venue_ts, result.realized_delta, fill.commission,
            )
            committed = await asyncio.to_thread(
                self._ledger.commit_fill,
                FillApplication(fill, result.position, attribution, self._state.cursor),
            )
            if committed.state.cursor < fill.ledger_seq:
                raise PositionLedgerError("commit returned a checkpoint behind its fill")
            self._install_state(committed.state)
            self._ready = False
            if committed.applied and result.position.status == "closed":
                p = result.position
                payload = PositionClosed(
                    position_id=str(p.position_id), exit_price=p.exit_price, realized_pnl=p.realized_pnl,
                    r_multiple_achieved=None, r_multiple_missing_reason="immutable_risk_basis_unavailable",
                    closed_ts=p.closed_at, trade_id=str(p.trade_id), execution_mode=p.execution_mode,
                    execution_venue=p.execution_venue, realized_profit=p.realized_profit,
                    realized_loss=p.realized_loss, fees=p.fees, reported_fees=p.reported_fees,
                    unknown_fee_count=p.unknown_fee_count,
                )
                # No outbox: a crash/publish failure here loses this notification.
                # Restart recovers the closure from persistence, not this bus.
                await self._bus.publish(make_envelope(EventType.POSITION_CLOSED, payload, symbol=p.symbol))
            if not committed.applied:
                # A concurrent/replayed application can return a checkpoint
                # ahead of this prefetched batch. Reload before further math.
                self._queue.put_nowait(_REFRESH)
                return
        for order_id in tuple(self._unresolved_orders):
            order = await asyncio.to_thread(self._ledger.get_order, order_id)
            if order is not None:
                self._unresolved_orders.remove(order_id)
                # Other-mode notifications are harmless wake-ups: only the
                # mode-scoped read-back above supplies accounting inputs.
        self._ready = not self._state.problems and not self._unresolved_orders

    # Existing reconciliation API. This is NOT a PositionLedgerPort adapter.
    def apply_fill(self, session, fill) -> PositionState | None:
        self._require_session_mode()
        from .legacy import apply_session_fill
        return apply_session_fill(self, session, fill)

    def rebuild_from_ledger(self, session, *, full_rebuild: bool = False) -> PortfolioSnapshot | None:
        self._require_session_mode()
        from .legacy import rebuild_session
        return rebuild_session(self, session, full_rebuild=full_rebuild)

    def _require_session_mode(self) -> None:
        if self._ledger is not None:
            raise RuntimeError("Session API and PositionLedgerPort cannot both own the same instance")
