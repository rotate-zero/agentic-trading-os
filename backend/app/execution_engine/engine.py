"""
ExecutionEngine — consumes OrderApproved (critical lane), mints/verifies
the deterministic client-order ID, performs an idempotent ledger insert,
checks the configured venue supports the order's execution mode, and
calls OrderVenue.place_order(). See package docstring
(execution_engine/__init__.py) for the original delivery's scope
boundary (entry orders only). Fill processing (§6.3 step 6) was added
by entry-lifecycle-wiring: when a FillLedgerPort is supplied, this
engine also registers OrderVenue.on_order_update(), persists each fill,
advances the order's ledger status, and publishes OrderFilled — see
_process_venue_update() below. Still entry-only: a non-"open"
position_effect is still dropped (see _process_one()), and exits
remain EX-5/EX-12 territory.

Own queue + worker (I7): OrderApproved is only ever enqueued by the Event
Bus subscriber callback (`_on_order_approved`, must stay fast); the actual
`await venue.place_order(...)` call — which could legitimately block —
happens inside this engine's OWN worker task, off the Event Bus's own
dispatch path, so a slow/stuck venue delays only this engine's own
backlog, never an unrelated critical-lane event elsewhere (AC #20).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import Settings, get_settings
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.execution_engine.fill_ledger import FillLedgerError, FillLedgerPort, FillRecord
from app.execution_engine.ports import (
    DecisionAuthorizationPort,
    ExecutionVenueProvider,
    OrderLedgerError,
    OrderLedgerPort,
    OrderRecord,
    VenueOrderInstruction,
    default_execution_venue_provider,
)
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import OrderApproved, OrderFilled, OrderStatusChanged

logger = logging.getLogger(__name__)

_STOP_SENTINEL = object()

_ENTRY_SUFFIX = ":entry"


class _NoVenueProvider:
    """Default ExecutionVenueProvider — wraps
    default_execution_venue_provider() (ports.py) so ExecutionEngine
    always has a provider object to call, with or without a constructor
    override."""

    def get_execution_venue(self):  # -> OrderVenue | None
        return default_execution_venue_provider()


class ExecutionEngine:
    def __init__(
        self,
        bus: EventBus,
        order_ledger: OrderLedgerPort,
        decision_authorization: DecisionAuthorizationPort,
        *,
        fill_ledger: FillLedgerPort | None = None,
        venue_provider: ExecutionVenueProvider | None = None,
        execution_mode_provider: Callable[[], str | None] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._bus = bus
        self._order_ledger = order_ledger
        self._decision_authorization = decision_authorization
        # Optional (entry-lifecycle-wiring): fill processing (on_order_update
        # registration, §6.3 step 6) is only wired when a FillLedgerPort is
        # supplied — None preserves this engine's pre-existing entry-only
        # behavior exactly (every caller/test that predates this delivery
        # keeps working unmodified).
        self._fill_ledger = fill_ledger
        self._venue_provider = venue_provider or _NoVenueProvider()
        # Same defensive getattr as governor/engine.py — execution_mode is
        # the sibling `execution-ledger-and-venue` task's own config.py
        # addition, may not exist on Settings yet at merge time.
        self._execution_mode_provider = execution_mode_provider or (
            lambda: getattr(get_settings(), "execution_mode", None)
        )
        self._settings = settings or get_settings()

        self._queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._accepting = False

    def start(self) -> None:
        self._accepting = True
        self._bus.subscribe(EventType.ORDER_APPROVED, self._on_order_approved)
        if self._fill_ledger is not None:
            # §6.3 step 6: registered once, at start — main.py's startup
            # sequence connects/registers the venue BEFORE calling this
            # engine's own start() (§6.9: rebuild -> connect -> reconcile ->
            # resume), so the venue is already available here whenever fill
            # processing is actually wired. No venue configured yet is not
            # fatal — same "no venue" posture _process_one() already takes
            # for the order path (AC #5 venue-refusal half).
            venue = self._venue_provider.get_execution_venue()
            if venue is not None:
                venue.on_order_update(self._on_venue_update)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="execution-engine")
        logger.info("ExecutionEngine started — subscribed to OrderApproved")

    async def stop(self) -> None:
        """Poison-pill drain — same shape as LevelInteractionEngine.stop()
        (decision #84) / AuthorizerStub.stop(): a plain task.cancel() can
        return while a venue call or ledger commit is still in-flight."""
        self._bus.unsubscribe(EventType.ORDER_APPROVED, self._on_order_approved)
        if self._worker_task is not None and not self._worker_task.done():
            await self._queue.put(_STOP_SENTINEL)
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        self._accepting = False
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

    def deactivate(self) -> None:
        """Reject callbacks and queued work before rollback awaits anything."""
        self._accepting = False

    # --- Event Bus subscriber (must stay fast — I7) -------------------------

    def _on_order_approved(self, envelope: EventEnvelope) -> None:
        if not self._accepting:
            return
        self._queue.put_nowait(dict(envelope.payload))

    def _on_venue_update(self, update: Any) -> None:
        """Venue callback (must stay fast, same I7 reasoning as
        `_on_order_approved` above) — called SYNCHRONOUSLY from inside
        `SimulatedVenue._dispatch()`, itself reachable from a
        PriceUpdated bus-subscriber callback (`_on_price_updated_
        envelope`) that must also stay fast. `put_nowait` onto this
        engine's OWN queue, tagged so `_worker_loop` can tell it apart
        from an `OrderApproved` payload dict — processed strictly
        in-order by the SAME single worker task (design doc §6.1's
        diagram puts fill ingestion in the same execution queue), which
        is what guarantees a fill for an order can never be processed
        before that same order's own `_process_one()` (which placed it
        at the venue in the first place) has already committed."""
        if self._accepting:
            self._queue.put_nowait(("venue_update", update))

    # --- background worker -----------------------------------------------------

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP_SENTINEL:
                    self._queue.task_done()
                    break
                try:
                    if not self._accepting:
                        pass
                    elif isinstance(item, tuple) and len(item) == 2 and item[0] == "venue_update":
                        await self._process_venue_update(item[1])
                    else:
                        await self._process_one(item)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001 — one bad order/update must not stall the rest
                    logger.exception("ExecutionEngine failed to process queued item: %r", item)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _process_one(self, payload: dict[str, Any]) -> None:
        try:
            order_approved = OrderApproved.model_validate(payload)
        except Exception:
            logger.exception("OrderApproved failed validation — dropped: %r", payload)
            return

        if order_approved.position_effect != "open":
            # Reduce-only/exit path — explicitly out of scope (needs EX-5,
            # still open). Extension point: a later task adds the
            # reduce-only-against-Portfolio-State check and handles this
            # branch instead of dropping it.
            logger.warning(
                "OrderApproved %s has position_effect=%r — exit path not built in this delivery, dropped",
                order_approved.order_id,
                order_approved.position_effect,
            )
            return

        client_order_id = order_approved.order_id
        if not client_order_id.endswith(_ENTRY_SUFFIX):
            logger.warning("OrderApproved has a malformed client-order ID %r — dropped", client_order_id)
            return
        trade_id = client_order_id[: -len(_ENTRY_SUFFIX)]

        # I2 authorization gate (AC #19 entry-gate half): an entry
        # OrderApproved with no committed authorizer decision is refused
        # and logged, BEFORE any ledger row is written for it.
        has_decision = await asyncio.to_thread(self._decision_authorization.has_committed_decision, trade_id)
        if not has_decision:
            logger.error(
                "OrderApproved %s (trade_id=%s) has no committed authorizer decision — refused, no ledger row written",
                client_order_id,
                trade_id,
            )
            await self._publish_rejected(
                order_approved.order_id, "no_committed_decision", execution_venue=None, symbol=order_approved.symbol
            )
            return

        execution_mode = self._execution_mode_provider()
        record = OrderRecord(
            client_order_id=client_order_id,
            trade_id=trade_id,
            symbol=order_approved.symbol,
            side=order_approved.side,
            position_effect=order_approved.position_effect,
            qty=order_approved.qty,
            order_type=order_approved.order_type,
            limit_price=order_approved.limit_price,
            execution_mode=execution_mode,
            status="approved",
            created_at=datetime.now(timezone.utc),
        )

        try:
            insert_result = await asyncio.to_thread(self._order_ledger.insert_order, record)
        except OrderLedgerError:
            logger.exception(
                "ExecutionEngine: insert_order failed for %s — no venue call, no event published", client_order_id
            )
            return

        if not insert_result.inserted:
            # Duplicate delivery of the same OrderApproved (AC #7
            # client-order-id-mint half): the stored row already exists —
            # log, send nothing further, no second venue submission.
            logger.info("OrderApproved %s already in the ledger — duplicate delivery, no venue call", client_order_id)
            return

        # Route the committed instruction. Concrete adapters verify incoming
        # terms against the durable authorization before returning it.
        record = insert_result.order
        execution_mode = record.execution_mode

        # Mode/venue check (§6.3 step 3; AC #5 venue-refusal half) —
        # never routed to a venue that doesn't support this execution_mode.
        venue = self._venue_provider.get_execution_venue()
        if venue is None:
            await self._reject_after_insert(
                client_order_id, "no_execution_venue_configured", execution_venue=None, symbol=order_approved.symbol
            )
            return
        if execution_mode not in venue.supported_modes:
            await self._reject_after_insert(
                client_order_id, "mode_not_supported", execution_venue=venue.venue_id, symbol=order_approved.symbol
            )
            return
        if record.execution_venue is not None and venue.venue_id != record.execution_venue:
            await self._reject_after_insert(
                client_order_id, "execution_venue_mismatch", execution_venue=venue.venue_id, symbol=record.symbol
            )
            return

        instruction = VenueOrderInstruction(
            client_order_id=client_order_id,
            symbol=record.symbol,
            side=record.side,
            qty=record.qty,
            order_type=record.order_type,
            limit_price=record.limit_price,
            position_effect=record.position_effect,
        )
        ack = await venue.place_order(instruction)

        if ack.status == "rejected":
            await self._reject_after_insert(
                client_order_id,
                ack.reason or "venue_rejected",
                execution_venue=venue.venue_id,
                symbol=order_approved.symbol,
            )
            return

        try:
            await asyncio.to_thread(
                self._order_ledger.update_order_status,
                client_order_id,
                "submitted",
                execution_venue=venue.venue_id,
            )
        except OrderLedgerError:
            logger.exception(
                "ExecutionEngine: update_order_status(submitted) failed for %s — order was placed at the venue "
                "but the ledger commit failed; reconciliation is out of scope for this delivery (§6.9)",
                client_order_id,
            )
            return

        logger.info("OrderApproved %s submitted to venue %s", client_order_id, venue.venue_id)

    # --- fill processing (§6.3 step 6, entry-lifecycle-wiring) --------------

    async def _process_venue_update(self, update: Any) -> None:
        if update.venue_fill_id is None:
            # A pure status update (a cancel ack, a plain rejection notice
            # with no fill attached) — not built in this delivery. The
            # entry path never cancels; a real cancel/exit path is EX-5/
            # EX-12 territory, out of this task's scope exactly like the
            # position_effect != "open" branch in _process_one() above.
            logger.info(
                "Venue order update for %s carries no fill (status=%s) — not processed in this delivery",
                update.client_order_id, update.status,
            )
            return
        if self._fill_ledger is None:
            # Unreachable in practice — _on_venue_update is only ever
            # registered when self._fill_ledger is not None (start()) —
            # kept as a defensive guard, not a silent drop.
            logger.error("Fill %s for %s received with no FillLedgerPort configured — dropped",
                         update.venue_fill_id, update.client_order_id)
            return

        fill = FillRecord(
            client_order_id=update.client_order_id,
            venue_fill_id=update.venue_fill_id,
            qty=update.fill_qty,
            price=update.fill_price,
            venue_ts=update.venue_ts,
            status=update.status,
            commission=update.commission,
        )
        try:
            result = await asyncio.to_thread(self._fill_ledger.record_fill, fill)
        except FillLedgerError:
            logger.exception(
                "ExecutionEngine: record_fill failed for %s (fill %s) — no event published",
                update.client_order_id, update.venue_fill_id,
            )
            return

        if not result.inserted:
            # Duplicate delivery of the same fill (I11) — the stored row
            # already exists, log, publish nothing further.
            logger.info(
                "Fill %s for %s already in the ledger — duplicate delivery, no publish",
                update.venue_fill_id, update.client_order_id,
            )
            return

        order_filled = OrderFilled(
            order_id=update.client_order_id,
            side=result.side,
            qty=update.fill_qty,
            fill_price=update.fill_price,
            fill_ts=update.venue_ts,
        )
        await self._bus.publish(make_envelope(EventType.ORDER_FILLED, order_filled, symbol=result.symbol))
        logger.info(
            "Fill %s for %s committed (order_status=%s%s) — OrderFilled published",
            update.venue_fill_id, update.client_order_id, result.order_status,
            f", anomaly={result.anomaly}" if result.anomaly else "",
        )

    async def _reject_after_insert(
        self, client_order_id: str, reason: str, *, execution_venue: str | None, symbol: str
    ) -> None:
        try:
            await asyncio.to_thread(
                self._order_ledger.update_order_status,
                client_order_id,
                "rejected",
                reason=reason,
                execution_venue=execution_venue,
            )
        except OrderLedgerError:
            logger.exception(
                "ExecutionEngine: update_order_status(rejected) failed for %s — no event published",
                client_order_id,
            )
            return
        await self._publish_rejected(client_order_id, reason, execution_venue=execution_venue, symbol=symbol)

    async def _publish_rejected(
        self, client_order_id: str, reason: str, *, execution_venue: str | None, symbol: str
    ) -> None:
        status_changed = OrderStatusChanged(
            order_id=client_order_id, status="rejected", reason=reason, execution_venue=execution_venue
        )
        await self._bus.publish(make_envelope(EventType.ORDER_STATUS_CHANGED, status_changed, symbol=symbol))


_execution_engine: ExecutionEngine | None = None


def get_execution_engine(
    bus: EventBus | None = None,
    order_ledger: OrderLedgerPort | None = None,
    decision_authorization: DecisionAuthorizationPort | None = None,
    fill_ledger: FillLedgerPort | None = None,
) -> ExecutionEngine:
    """Lazy singleton, same pattern as get_authorizer_stub()/
    get_level_interaction_engine(). `order_ledger`/`decision_authorization`
    MUST be supplied on first construction — no default concrete
    implementation exists to fall back to. `fill_ledger` is optional
    (entry-lifecycle-wiring): omit it to get exactly this delivery's
    predecessor behavior (entry orders only, no fill processing wired);
    main.py's lifespan() supplies it for the real running app."""
    global _execution_engine
    if _execution_engine is None:
        if order_ledger is None or decision_authorization is None:
            raise RuntimeError(
                "get_execution_engine() requires order_ledger and decision_authorization on first call "
                "(no default OrderLedgerPort/DecisionAuthorizationPort exists)"
            )
        _execution_engine = ExecutionEngine(
            bus or get_event_bus(), order_ledger, decision_authorization, fill_ledger=fill_ledger
        )
    return _execution_engine
