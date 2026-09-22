"""
AuthorizerStub — subscribes to OpportunityCreated, runs rules.py's fixed
pipeline against a fresh read of Portfolio State / MarketClock / Settings
every time, and publishes TradePlanned -> GovernorDecision -> OrderApproved
(approved) or PlanRejected (rejected). See package docstring
(governor/__init__.py) and docs/architecture/execution-engine-design.md
§6.1-6.2.

Publish sequence, resolved from this task's own scope item 1 text
("Emits TradePlanned -> GovernorDecision -> OrderApproved/PlanRejected")
read against §6.1's data-flow diagram and TradePlanned's own required
fields (entry/stop/size have no defaults — genuinely unavailable for an
early rejection, e.g. rule 0's execution_mode check, before rule 5 ever
computes a reference price or size): TradePlanned and GovernorDecision
are published ONLY on the approved path, immediately before OrderApproved.
A rejected decision publishes PlanRejected alone. This is a judgment
call — flagged in the decision entry, overridable.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

from app.core.config import Settings, get_settings
from app.core.market_clock import MarketClock, get_market_clock
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.governor.ports import (
    LedgerCommitError,
    PortfolioStateReader,
    TradeDecisionRecord,
    TradeLedgerPort,
)
from app.governor.reference_price import ReferencePriceTracker
from app.governor.rules import (
    AuthorizationContext,
    LimitsSnapshot,
    OpportunityView,
    evaluate_authorization,
)
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import GovernorDecision, OrderApproved, PlanRejected, TradePlanned
from app.strategy_engine.base_strategy import Opportunity
from app.trading_intelligence.state_snapshot import capture_strategy_outcome_snapshots

logger = logging.getLogger(__name__)

_STOP_SENTINEL = object()


def _r_multiple(entry: float, stop: float, target: float | None) -> float | None:
    if target is None:
        return None
    risk = abs(entry - stop)
    if risk == 0:
        return None
    return round(abs(target - entry) / risk, 4)


class AuthorizerStub:
    def __init__(
        self,
        bus: EventBus,
        trade_ledger: TradeLedgerPort,
        portfolio_state: PortfolioStateReader,
        *,
        execution_mode_provider: Callable[[], str | None] | None = None,
        settings: Settings | None = None,
        market_clock: MarketClock | None = None,
        reference_price_tracker: ReferencePriceTracker | None = None,
    ) -> None:
        self._bus = bus
        self._trade_ledger = trade_ledger
        self._portfolio_state = portfolio_state
        # Deliberately NOT `get_settings().execution_mode` — that setting
        # is appended by the sibling `execution-ledger-and-venue` task in
        # its own config.py block (this task's scope item 5 only adds the
        # three limits below), so it may not exist on `Settings` yet.
        # `getattr(..., None)` is fail-closed either way: missing entirely
        # reads as None, which rule 0 rejects exactly like any other
        # non-"simulated" value (AC #3/#4) — layer 1 (startup refusal) is
        # main.py wiring, out of this task's file boundary; this is layer
        # 2 (per-decision refusal).
        self._execution_mode_provider = execution_mode_provider or (
            lambda: getattr(get_settings(), "execution_mode", None)
        )
        self._settings = settings or get_settings()
        self._market_clock = market_clock or get_market_clock()
        self._reference_price = reference_price_tracker or ReferencePriceTracker()

        self._queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        self._bus.subscribe(EventType.OPPORTUNITY_CREATED, self._on_opportunity_created)
        self._reference_price.start(self._bus)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="authorizer-stub")
        logger.info("AuthorizerStub started — subscribed to OpportunityCreated")

    async def stop(self) -> None:
        """Poison-pill drain — same shape and reasoning as
        LevelInteractionEngine.stop() (decision #84): a plain
        task.cancel() can return while a commit_decision() call is still
        in-flight inside asyncio.to_thread, and this engine's own commit
        writes to the same kind of shared ledger state that motivated
        that fix originally."""
        if self._worker_task is not None and not self._worker_task.done():
            await self._queue.put(_STOP_SENTINEL)
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        self._reference_price.stop()

    # --- Event Bus subscriber (must stay fast) ------------------------------

    def _on_opportunity_created(self, envelope: EventEnvelope) -> None:
        if envelope.symbol is None:
            logger.warning("OpportunityCreated received with no envelope.symbol — dropped")
            return
        self._queue.put_nowait({"symbol": envelope.symbol, "payload": envelope.payload})

    # --- background worker ---------------------------------------------------

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP_SENTINEL:
                    self._queue.task_done()
                    break
                try:
                    await self._process_one(item)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001 — one bad Opportunity must not stall the rest
                    logger.exception(
                        "AuthorizerStub failed to process OpportunityCreated for %s",
                        item.get("symbol") if isinstance(item, dict) else "?",
                    )
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _process_one(self, item: dict[str, Any]) -> None:
        symbol = item["symbol"]
        payload = item["payload"]

        # Re-validated — deliberately NOT the raw-dict trust boundary
        # OpportunityCache uses for its own passive caching. This is the
        # first real consumer that acts on an Opportunity with financial
        # consequences; a malformed payload should reject cleanly here,
        # not raise deep inside rules.py. Judgment call, documented.
        try:
            opportunity = Opportunity.model_validate(payload)
        except Exception:
            logger.exception(
                "OpportunityCreated for %s failed validation — dropped, no decision committed", symbol
            )
            return

        now = datetime.now(timezone.utc)
        execution_mode = self._execution_mode_provider()
        is_regular_session = self._market_clock.is_regular_session(now)
        snapshots = await asyncio.to_thread(capture_strategy_outcome_snapshots, symbol)
        trading_day = self._market_clock.trading_day(now)
        portfolio = await asyncio.to_thread(
            self._portfolio_state.get_snapshot, execution_mode or "unknown", trading_day
        )
        reference_price = self._reference_price.get(symbol)

        limits = LimitsSnapshot(
            max_concurrent_positions=self._settings.execution_max_concurrent_positions,
            fixed_notional_usd=self._settings.execution_fixed_notional_usd,
            daily_loss_cap_usd=self._settings.execution_daily_loss_cap_usd,
        )
        ctx = AuthorizationContext(
            symbol=symbol,
            opportunity=OpportunityView(
                strategy=opportunity.strategy,
                version=opportunity.version,
                direction=opportunity.direction,
                confidence=opportunity.confidence,
                structural_invalidation=opportunity.structural_invalidation,
                structural_target=opportunity.structural_target,
                status=opportunity.status,
                setup_detected_at=opportunity.setup_detected_at,
            ),
            execution_mode=execution_mode,
            is_regular_session=is_regular_session,
            market_state_snapshot_present=snapshots.market_state is not None,
            context_snapshot_present=snapshots.context is not None,
            portfolio=portfolio,
            reference_price=reference_price,
            now=now,
        )
        result = evaluate_authorization(ctx, limits)

        # opportunity_id is minted here, exactly once, ONLY on the accepted
        # path (EX-9: "mint at the authorizer's acceptance") — a rejected
        # decision never gets one. Real UUID (not an arbitrary string):
        # `strategy_outcomes.opportunity_id` (decision #120) and
        # strategy-engine-design.md's own sketch both type it as UUID.
        opportunity_id: str | None = None
        client_order_id: str | None = None
        if result.decision == "approved":
            opportunity_id = str(uuid4())
            client_order_id = f"{opportunity_id}:entry"

        record = TradeDecisionRecord(
            symbol=symbol,
            strategy=opportunity.strategy,
            strategy_version=opportunity.version,
            direction=opportunity.direction,
            decision=result.decision,
            reasons=result.reasons,
            limits_snapshot=limits.as_dict(),
            execution_mode=execution_mode,
            market_state_snapshot_present=ctx.market_state_snapshot_present,
            context_snapshot_present=ctx.context_snapshot_present,
            structural_invalidation=opportunity.structural_invalidation,
            structural_target=opportunity.structural_target,
            confidence_at_signal=opportunity.confidence,
            setup_detected_at=opportunity.setup_detected_at,
            decided_at=now,
            opportunity_id=opportunity_id,
            client_order_id=client_order_id,
            qty=result.qty,
            reference_price=result.reference_price,
        )

        try:
            await asyncio.to_thread(self._trade_ledger.commit_decision, record)
        except LedgerCommitError:
            logger.exception(
                "AuthorizerStub: commit_decision failed for %s (opportunity_id=%s) — no event published",
                symbol,
                opportunity_id,
            )
            return

        if result.decision == "rejected":
            await self._bus.publish(
                make_envelope(EventType.PLAN_REJECTED, PlanRejected(symbol=symbol, reasons=result.reasons), symbol=symbol)
            )
            return

        assert (
            opportunity_id is not None
            and client_order_id is not None
            and result.qty is not None
            and result.reference_price is not None
        )

        direction_long_short: Literal["long", "short"] = "long" if opportunity.direction == "BUY" else "short"
        trade_planned = TradePlanned(
            direction=direction_long_short,
            entry=result.reference_price,
            stop=opportunity.structural_invalidation,
            target=opportunity.structural_target,
            size=result.qty,
            r_multiple=_r_multiple(result.reference_price, opportunity.structural_invalidation, opportunity.structural_target),
        )
        await self._bus.publish(make_envelope(EventType.TRADE_PLANNED, trade_planned, symbol=symbol))

        governor_decision = GovernorDecision(action="approved", reasons=[])
        await self._bus.publish(make_envelope(EventType.GOVERNOR_DECISION, governor_decision, symbol=symbol))

        order_approved = OrderApproved(
            order_id=client_order_id,
            symbol=symbol,
            side=opportunity.direction,
            qty=result.qty,
            order_type="market",
            position_effect="open",
        )
        await self._bus.publish(make_envelope(EventType.ORDER_APPROVED, order_approved, symbol=symbol))


_authorizer_stub: AuthorizerStub | None = None


def get_authorizer_stub(
    bus: EventBus | None = None,
    trade_ledger: TradeLedgerPort | None = None,
    portfolio_state: PortfolioStateReader | None = None,
) -> AuthorizerStub:
    """Lazy singleton, same pattern as get_level_interaction_engine()/
    get_market_state_engine()/get_opportunity_cache(). `trade_ledger`/
    `portfolio_state` MUST be supplied on first construction in this
    delivery — no default concrete implementation exists to fall back to
    (fork 1: the real ledger and Portfolio State belong to modules this
    task doesn't own/build). main.py is NOT wired to call this in this
    delivery (outside this task's file boundary) — see TESTING.md for the
    follow-up wiring still needed once the sibling ledger/venue land."""
    global _authorizer_stub
    if _authorizer_stub is None:
        if trade_ledger is None or portfolio_state is None:
            raise RuntimeError(
                "get_authorizer_stub() requires trade_ledger and portfolio_state on first call "
                "(no default TradeLedgerPort/PortfolioStateReader exists yet in this delivery)"
            )
        _authorizer_stub = AuthorizerStub(bus or get_event_bus(), trade_ledger, portfolio_state)
    return _authorizer_stub
