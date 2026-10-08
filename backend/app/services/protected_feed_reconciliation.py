"""Additive feed requests for restored simulated positions and working orders.

The execution ledger is authoritative. This owner does not infer delivery from
an adapter's local subscription record or from subscribe() returning.

Task `protected-feed-reconciliation-status` adds a read-only, immutable
snapshot of what the owner last attempted (``get_snapshot()``). The snapshot is
request evidence only: a symbol found in the adapter's local inventory or a
subscribe() call that returned says nothing about delivery or protection.
Failures are reported as fixed codes/classifications; raw exception text stays
in the logs.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker_adapters.base import SubscriptionInventory
from app.models.execution_ledger import Order, Position
from app.portfolio_state.reconciliation import NON_TERMINAL_ORDER_STATUSES
from app.services import broker_registry

logger = logging.getLogger(__name__)


# Per-symbol request outcomes (request evidence only).
OUTCOME_LOCALLY_PRESENT = "locally_present"      # already in the adapter's local inventory; no request made
OUTCOME_REQUEST_RETURNED = "request_returned"    # subscribe() returned without raising
OUTCOME_REQUEST_FAILED = "request_failed"        # subscribe() (or its connection guard) raised
OUTCOME_NO_OUTCOME = "no_outcome"                # the cycle ended before this symbol had a result

# Latest-cycle outcomes. ``None`` on the snapshot means no cycle has finished.
CYCLE_COMPLETED = "completed"
CYCLE_COMPLETED_WITH_FAILURES = "completed_with_failures"
CYCLE_READ_FAILED = "protected_set_read_failed"
CYCLE_NO_PROVIDER = "no_streaming_provider"
CYCLE_PROVIDER_DISCONNECTED = "provider_disconnected"
CYCLE_PROVIDER_CHECK_FAILED = "provider_check_failed"
CYCLE_INTERRUPTED = "interrupted"                # stopping, provider replaced/disconnected mid-cycle, or cancelled

READ_SUCCEEDED = "succeeded"
READ_FAILED = "failed"


def _utcnow() -> datetime:
    # Module-level so tests can pin the clock.
    return datetime.now(timezone.utc)


def _classify_error(exc: BaseException) -> str:
    """Coarse, fixed classification. Never derived from the exception text."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "connection"
    return "other"


@dataclass(frozen=True)
class ProviderIdentity:
    provider_id: str
    class_name: str


@dataclass(frozen=True)
class ProtectedSetRead:
    """One successful protected-set read. An empty ``symbols`` tuple is a real,
    successful empty set; a failed read never produces one of these."""
    read_at: datetime
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ProtectedSymbolRequest:
    symbol: str
    outcome: str
    error_class: str | None = None  # only for OUTCOME_REQUEST_FAILED


@dataclass(frozen=True)
class RequestRecord:
    """Per-symbol request outcomes of the latest cycle that reached the
    request phase (provider present and connected). Retained, with its own
    timestamp and provider, across later cycles that fail earlier."""
    recorded_at: datetime
    provider: ProviderIdentity
    inventory_available: bool
    requests: tuple[ProtectedSymbolRequest, ...]


@dataclass(frozen=True)
class ProtectedFeedSnapshot:
    """Immutable-by-contract view: frozen dataclasses holding only scalars,
    datetimes and tuples, so a consumer cannot mutate the owner's state."""
    running: bool
    cycle_in_progress: bool
    interval_seconds: float
    attempts_started: int
    attempts_completed: int
    last_attempt_at: datetime | None
    last_completed_at: datetime | None
    last_cycle_outcome: str | None
    last_read_attempt_at: datetime | None
    last_read_outcome: str | None          # READ_SUCCEEDED / READ_FAILED / None
    last_set_read: ProtectedSetRead | None  # last SUCCESSFUL read; kept across a later failure
    last_provider: ProviderIdentity | None  # what the latest provider lookup saw
    last_provider_connected: bool | None
    request_record: RequestRecord | None


class _Attempt:
    """Mutable accumulator private to one cycle; folded into the snapshot once."""

    def __init__(self, started_at: datetime) -> None:
        self.started_at = started_at
        self.outcome: str | None = None
        self.read_at: datetime | None = None
        self.read_symbols: frozenset[str] | None = None
        self.read_failed = False
        self.provider_looked_up = False
        self.provider: ProviderIdentity | None = None
        self.provider_connected: bool | None = None
        self.reached_requests = False
        self.inventory_available = False
        self.order: list[str] = []
        self.results: dict[str, tuple[str, str | None]] = {}


def _provider_identity(provider: object) -> ProviderIdentity:
    provider_id = getattr(provider, "provider_id", None)
    return ProviderIdentity(
        provider_id=provider_id if isinstance(provider_id, str) and provider_id else "unknown",
        class_name=type(provider).__name__,
    )


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
        self._snapshot = ProtectedFeedSnapshot(
            running=False, cycle_in_progress=False, interval_seconds=interval_seconds,
            attempts_started=0, attempts_completed=0, last_attempt_at=None,
            last_completed_at=None, last_cycle_outcome=None, last_read_attempt_at=None,
            last_read_outcome=None, last_set_read=None, last_provider=None,
            last_provider_connected=None, request_record=None,
        )

    def get_snapshot(self) -> ProtectedFeedSnapshot:
        """Read-only status. Synchronous and I/O-free: it takes no lock, awaits
        nothing, and never starts a cycle, reads the database, subscribes or
        touches a provider. The returned object is frozen and shares no mutable
        structure with this owner."""
        task = self._task
        return replace(self._snapshot, running=task is not None and not task.done())

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
            attempt = _Attempt(_utcnow())
            self._snapshot = replace(
                self._snapshot, cycle_in_progress=True,
                attempts_started=self._snapshot.attempts_started + 1,
                last_attempt_at=attempt.started_at,
            )
            try:
                await self._cycle(attempt)
            finally:
                # Also runs on cancellation: the cycle is recorded as interrupted.
                self._record(attempt)

    def _record(self, attempt: _Attempt) -> None:
        """Fold one finished cycle into the snapshot (synchronous, no awaits)."""
        current = self._snapshot
        changes: dict = {
            "cycle_in_progress": False,
            "attempts_completed": current.attempts_completed + 1,
            "last_completed_at": _utcnow(),
            "last_cycle_outcome": attempt.outcome or CYCLE_INTERRUPTED,
        }
        if attempt.read_symbols is not None and attempt.read_at is not None:
            changes["last_read_attempt_at"] = attempt.read_at
            changes["last_read_outcome"] = READ_SUCCEEDED
            changes["last_set_read"] = ProtectedSetRead(attempt.read_at, tuple(sorted(attempt.read_symbols)))
        elif attempt.read_failed and attempt.read_at is not None:
            # The previous successful read is deliberately left untouched.
            changes["last_read_attempt_at"] = attempt.read_at
            changes["last_read_outcome"] = READ_FAILED
        if attempt.provider_looked_up:
            changes["last_provider"] = attempt.provider
            changes["last_provider_connected"] = attempt.provider_connected
        if attempt.reached_requests and attempt.provider is not None:
            changes["request_record"] = RequestRecord(
                recorded_at=attempt.started_at,
                provider=attempt.provider,
                inventory_available=attempt.inventory_available,
                requests=tuple(
                    ProtectedSymbolRequest(symbol, *attempt.results.get(symbol, (OUTCOME_NO_OUTCOME, None)))
                    for symbol in attempt.order
                ),
            )
        self._snapshot = replace(current, **changes)

    async def _cycle(self, attempt: _Attempt) -> None:
        try:
            symbols = await asyncio.to_thread(read_protected_symbols, self._session_factory)
        except Exception:
            logger.exception("Protected feed symbol read failed; retrying next cycle")
            attempt.read_failed = True
            attempt.read_at = _utcnow()
            attempt.outcome = CYCLE_READ_FAILED
            return
        attempt.read_symbols = frozenset(symbols)
        attempt.read_at = _utcnow()
        if self._stopping:
            attempt.outcome = CYCLE_INTERRUPTED
            return
        provider = broker_registry.get_streaming_provider()
        attempt.provider_looked_up = True
        if provider is None:
            logger.warning("Protected feed request deferred: no streaming provider")
            attempt.outcome = CYCLE_NO_PROVIDER
            return
        attempt.provider = _provider_identity(provider)
        try:
            connected = provider.is_connected()
        except Exception:
            logger.exception("Protected feed provider connection check failed")
            attempt.outcome = CYCLE_PROVIDER_CHECK_FAILED
            return
        attempt.provider_connected = bool(connected)
        if not connected:
            logger.warning("Protected feed request deferred: streaming provider disconnected")
            attempt.outcome = CYCLE_PROVIDER_DISCONNECTED
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

        attempt.reached_requests = True
        attempt.inventory_available = inventory is not None
        attempt.order = sorted(symbols)
        if inventory is not None:
            for symbol in attempt.order:
                if symbol in inventory:
                    attempt.results[symbol] = (OUTCOME_LOCALLY_PRESENT, None)

        # Inventory-unavailable providers receive repeat requests under
        # the provider's idempotent subscribe contract. Each call is
        # isolated so a partial failure leaves other symbols retryable.
        failed = False
        for symbol in sorted(symbols - inventory if inventory is not None else symbols):
            if self._stopping or broker_registry.get_streaming_provider() is not provider:
                attempt.outcome = CYCLE_INTERRUPTED
                return
            try:
                if not provider.is_connected():
                    attempt.provider_connected = False
                    attempt.outcome = CYCLE_PROVIDER_DISCONNECTED
                    return
                await provider.subscribe([symbol])
                attempt.results[symbol] = (OUTCOME_REQUEST_RETURNED, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Protected feed subscription request failed for %s", symbol)
                attempt.results[symbol] = (OUTCOME_REQUEST_FAILED, _classify_error(exc))
                failed = True
        attempt.outcome = CYCLE_COMPLETED_WITH_FAILURES if failed else CYCLE_COMPLETED
