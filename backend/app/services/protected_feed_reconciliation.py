"""Additive feed requests for restored simulated positions and working orders.

The execution ledger is authoritative. This owner does not infer delivery from
an adapter's local subscription record or from subscribe() returning.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker_adapters.base import SubscriptionInventory
from app.models.execution_ledger import Order, Position
from app.portfolio_state.reconciliation import NON_TERMINAL_ORDER_STATUSES
from app.services import broker_registry

logger = logging.getLogger(__name__)


def read_protected_symbols(session_factory: Callable[[], Session]) -> frozenset[str]:
    """Read one complete, simulated-only symbol projection in an owned session."""
    with session_factory() as session:
        with session.begin():
            positions = session.scalars(
                select(Position.symbol).where(
                    Position.execution_mode == "simulated", Position.qty > 0,
                    Position.status.in_(("open", "closing")),
                )
            ).all()
            orders = session.scalars(
                select(Order.symbol).where(
                    Order.execution_mode == "simulated",
                    Order.status.in_(NON_TERMINAL_ORDER_STATUSES),
                )
            ).all()
    return frozenset(positions).union(orders)


class ProtectedFeedReconciler:
    """One lifecycle owner; one cycle at a time, with a 60-second retry."""

    def __init__(self, session_factory: Callable[[], Session], *, interval_seconds: float = 60.0) -> None:
        self._session_factory = session_factory
        self._interval_seconds = interval_seconds
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="protected-feed-reconciliation")

    async def stop(self) -> None:
        self._stopping = True
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # A directly invoked cycle also owns the lock; settle it before
        # provider shutdown, without admitting any new subscription work.
        async with self._lock:
            pass

    async def _run(self) -> None:
        while not self._stopping:
            await self.reconcile_once()
            if self._stopping:
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def reconcile_once(self) -> None:
        if self._stopping or self._lock.locked():
            return
        async with self._lock:
            try:
                symbols = await asyncio.to_thread(read_protected_symbols, self._session_factory)
            except Exception:
                logger.exception("Protected feed symbol read failed; retrying next cycle")
                return
            if self._stopping:
                return
            provider = broker_registry.get_streaming_provider()
            if provider is None:
                logger.warning("Protected feed request deferred: no streaming provider")
                return
            try:
                connected = provider.is_connected()
            except Exception:
                logger.exception("Protected feed provider connection check failed")
                return
            if not connected:
                logger.warning("Protected feed request deferred: streaming provider disconnected")
                return

            inventory: set[str] | None = None
            if isinstance(provider, SubscriptionInventory):
                try:
                    raw = provider.get_subscription_snapshot()
                    if not isinstance(raw, (tuple, list, set, frozenset)) or not all(
                        isinstance(symbol, str) for symbol in raw
                    ):
                        raise ValueError("invalid subscription inventory")
                    inventory = set(raw)
                except Exception:
                    logger.exception("Protected feed inventory unavailable; requesting all protected symbols")

            # Inventory-unavailable providers receive repeat requests under
            # the provider's idempotent subscribe contract. Each call is
            # isolated so a partial failure leaves other symbols retryable.
            for symbol in sorted(symbols - inventory if inventory is not None else symbols):
                if self._stopping or broker_registry.get_streaming_provider() is not provider:
                    return
                try:
                    if not provider.is_connected():
                        return
                    await provider.subscribe([symbol])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Protected feed subscription request failed for %s", symbol)
