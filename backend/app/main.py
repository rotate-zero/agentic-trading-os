from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import backtest, broker, dev, finnhub_data, health, intelligence, market, market_data, scanner
from app.api.routes.finnhub_data import connect_finnhub
from app.api.routes.market_data import connect_polygon
from app.api.websocket import channels
from app.api.websocket.channels import get_gateway
from app.context_engine.engine import get_context_engine
from app.context_engine.fundamentals_refresh import get_fundamentals_refresh_jobs
from app.core.config import get_settings
from app.core.error_handling import UnhandledExceptionMiddleware
from app.core.logging import configure_logging
from app.event_bus.bus import get_event_bus
from app.feature_engine.engine import get_feature_engine
from app.market_state_engine.engine import get_market_state_engine
from app.services import broker_registry
from app.services.candle_recorder import CandleRecorder
from app.services.live_tick_relay import get_live_tick_relay
from app.strategy_engine.scheduler import get_strategy_scheduler
from app.trading_intelligence.level_interaction_engine import get_level_interaction_engine

logger = logging.getLogger(__name__)


# Read-only startup status for GET /health/execution-startup
# (execution-startup-status). Tracks the same three real outcomes the
# execution-pipeline try/except below already produces (decision #179's
# fail-closed contract) — "ready", "reconciliation_blocked", or
# "startup_failed" — never a fourth "in progress" value, since routes are
# only served after this lifespan's startup section has already finished
# one way or another. "unavailable" is not built here: it's whatever a
# route sees when app.state.execution_startup_status is still its
# pre-lifespan default (None) or has been reset by the `finally` block
# below (shutdown, or partway through rollback) — the same "no active
# lifespan" posture app.state.world_view_portfolio_reader/position_monitor
# already use. Deliberately narrow fields: reason_code is one of a fixed
# set of safe, non-identifying codes (never str(exc)), and
# discrepancy_count is only ever a plain count, never the discrepancy
# list itself — no exception text, database credentials, or raw
# reconciliation contents ever reach this dict.
def _execution_startup_status(
    status: str, *, reason_code: str | None = None, discrepancy_count: int | None = None
) -> dict:
    return {"status": status, "reason_code": reason_code, "discrepancy_count": discrepancy_count}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.debug)

    bus = get_event_bus()
    await bus.start()

    # Starts unconditionally, independent of whether Finnhub/Polygon end up
    # connected below — it's just a CandleClosed subscriber, ready for
    # whenever ticks start flowing regardless of which provider ends up
    # supplying them. A DB that isn't reachable yet doesn't block startup or
    # crash the app (see CandleRecorder's own docstring) — it degrades to
    # "not recording," the same soft-fail posture as an unconfigured
    # Finnhub/Polygon key below, not a hard dependency this app can't start
    # without.
    candle_recorder = CandleRecorder(bus)
    candle_recorder.start()

    # Throttled tick-fluidity relay (decision #72) — same unconditional-start
    # posture as CandleRecorder just above: it's a PriceUpdated subscriber
    # with an empty active-symbol set until something (a manual POST
    # /market/active-symbols call today; Market Scanner eventually) tells it
    # otherwise, so starting it costs nothing when no symbols are active yet.
    tick_relay = get_live_tick_relay(bus)
    tick_relay.start()

    # Feature Engine's first indicator (Phase 4 kickoff, confirmed decision
    # #45) — like CandleRecorder, starts unconditionally: it's just a
    # CandleClosed subscriber, ready for whenever ticks start flowing,
    # independent of which provider (if any) ends up connected below.
    feature_engine = get_feature_engine(bus)
    feature_engine.start()

    # Trading Intelligence's first engine (confirmed decision #46) —
    # consumes FeatureEngine's FeaturesUpdated output. Same unconditional-
    # start posture as everything else here: it's just a subscriber, ready
    # whenever ticks start flowing.
    level_interaction_engine = get_level_interaction_engine(bus)
    level_interaction_engine.start()

    # Market State Engine (decision #93 for per-symbol, decision #97 for
    # M3's SPY/QQQ/IWM cross-symbol synthesis on top of it). Subscriber,
    # same as LevelInteractionEngine above — stops AFTER the bus in
    # shutdown below, not before like ContextEngine (see that comment for
    # why the two engines differ on this).
    market_state_engine = get_market_state_engine(bus)
    market_state_engine.start()

    # Context Engine (decision #96 — M1 remainder built): CalendarProvider
    # (market-wide), FundamentalsProvider + NewsFlagProvider (per-symbol,
    # decision #96's second aggregation path). Unconditional start —
    # CalendarProvider needs no external API; Fundamentals/News each
    # soft-fail internally (empty API key, ETF guard, Finnhub errors) so
    # there's nothing here worth gating start() on.
    context_engine = get_context_engine(bus)
    context_engine.start()

    # Strategy Scheduler (decision #112/#114, strategy-engine-design.md
    # §10 D10) — Stage 2. Subscriber (FeaturesUpdated for caching,
    # MarketStateChanged as the actual evaluate() trigger — see
    # scheduler.py's module docstring for why the split), so stops AFTER
    # the bus in shutdown below, same category as MarketStateEngine
    # above, not before like ContextEngine. Placed after both
    # MarketStateEngine and ContextEngine start — not load-bearing at
    # startup time itself (both are read lazily, per-event), but this is
    # the last piece of the intelligence pipeline in start order, which
    # is the more readable place for it.
    strategy_scheduler = get_strategy_scheduler(bus)
    strategy_scheduler.start()

    # OpportunityCache (Track B, decision #114 original/#115 restored —
    # see confirmed-decisions.md for the collision note) — the
    # OpportunityCreated read-side this Scheduler's own publish above now
    # actually feeds. This completes the manual merge Track A's own
    # session flagged but didn't finish (its comment here said "merge
    # both additions by hand" — this push never did, so this cache was
    # built and tested but never actually subscribed in the live app
    # until now). A bus subscriber like StrategyScheduler just above, so
    # it follows the same unconditional-start, stop-after-bus posture.
    # Local import (not hoisted to this file's top-of-file import block)
    # so this whole addition stays a single, self-contained block.
    from app.trading_intelligence.opportunity_cache import get_opportunity_cache

    opportunity_cache = get_opportunity_cache(bus)
    opportunity_cache.start()

    # FundamentalsRefreshJobs — the only writer to symbol_fundamentals
    # (decision #96). Soft-fails its own start() (logs + no-ops) when no
    # Finnhub key is configured, same posture as the broker
    # auto-connects below.
    fundamentals_refresh_jobs = get_fundamentals_refresh_jobs()
    fundamentals_refresh_jobs.start()

    gateway = get_gateway()
    gateway.attach()

    # Auto-connect Finnhub and/or Polygon on startup (if configured) —
    # unlike IBKR, which requires an external Gateway app + 2FA and so
    # stays manual-connect-only (see app/api/routes/broker.py), both are
    # just API-key-authenticated cloud services. Soft-fail on each: a
    # missing key or a connect error must not crash the whole app over an
    # optional data source.
    #
    # Calls the SAME connect_finnhub()/connect_polygon() functions the
    # manual /finnhub/connect and /market-data/connect routes use —
    # not a separate inline implementation. An earlier version of this
    # constructed adapters directly here, which auto-connected
    # successfully but left the route modules' own _provider references
    # still None, making /finnhub/status, /market-data/subscribe, etc.
    # silently useless after auto-connect. Caught by an actual startup
    # test against a running server, not by unit tests (which call
    # routes directly, bypassing main.py's lifespan entirely).
    #
    # Fixed priority at startup, not dynamic runtime negotiation
    # (confirmed decision #33): Finnhub (real-time) claims streaming
    # first if configured; Polygon always claims historical, and only
    # also claims streaming as a fallback if Finnhub isn't configured.
    if settings.finnhub_api_key:
        try:
            await connect_finnhub()
            logger.info("Finnhub auto-connected on startup (real-time WebSocket streaming)")
        except Exception:  # noqa: BLE001 — optional data source, app must still boot
            logger.exception("Finnhub auto-connect failed on startup — continuing without it")
    else:
        logger.info("FINNHUB_API_KEY not set — skipping Finnhub auto-connect")

    if settings.polygon_api_key:
        try:
            await connect_polygon()
            if broker_registry.get_streaming_provider() is broker_registry.get_historical_provider():
                logger.info(
                    "Polygon auto-connected on startup (historical + streaming-fallback, "
                    "15-min delayed — no faster source configured)"
                )
            else:
                logger.info("Polygon auto-connected on startup (historical only — Finnhub already streaming)")
        except Exception:  # noqa: BLE001 — optional data source, app must still boot
            logger.exception("Polygon auto-connect failed on startup — continuing without it")
    else:
        logger.info("POLYGON_API_KEY not set — skipping Polygon auto-connect")

    # Execution pipeline (entry-lifecycle-wiring and simulated EOD integration) — wires decisions #171
    # (AuthorizerStub, ExecutionEngine: entry orders, built against fakes)
    # and #172 (execution ledger, SimulatedVenue, Portfolio State: also
    # built against fakes) together against real Postgres-backed adapters,
    # per design doc §6.9's restart-recovery sequence: rebuild -> connect
    # -> reconcile -> resume. settings.execution_mode is validated to
    # "simulated" at Settings construction (core/config.py's own
    # field_validator) — nothing else is reachable past that point, so no
    # separate startup-mode-refusal check is needed here (§6.2 "layer 1").
    # Local imports, kept together — same self-contained-block convention
    # OpportunityCache above already uses.
    from app.broker_adapters.simulated_venue import SimulatedVenue
    from app.db.session import SessionLocal
    from app.execution_engine.engine import get_execution_engine
    from app.execution_engine.fill_ledger import PostgresFillLedger
    from app.execution_engine.exit_ledger import PostgresExitLedger
    from app.execution_engine.postgres import PostgresOrderLedger
    from app.governor.engine import get_authorizer_stub
    from app.governor.portfolio_state_reader import PortfolioStateAdapter
    from app.governor.postgres import PostgresTradeLedger
    from app.portfolio_state.engine import PortfolioState
    from app.portfolio_state.postgres import PostgresPositionLedger
    from app.portfolio_state.reconciliation import reconcile_with_venue
    from app.trading_intelligence.outcome_recorder import OutcomeRecorder
    from app.position_monitor.engine import ExitIntent, PositionMonitor
    from app.position_monitor.handoff import ObservationState
    from app.position_monitor.portfolio_state_reader import PortfolioStatePositionReader
    from app.models.execution_ledger import ExitRequest, Position
    from sqlalchemy import select

    authorizer_stub = None
    execution_engine = None
    portfolio_state = None
    position_monitor = None
    outcome_recorder = None
    execution_venue = None
    app.state.world_view_portfolio_reader = None
    app.state.position_monitor = None
    app.state.execution_startup_status = None
    try:
        execution_venue = SimulatedVenue(event_bus=bus)
        await execution_venue.connect()

        # Apply any fill committed before its notification was delivered
        # through the production receipt-producing adapter before the
        # Session compatibility reconciliation inspects the checkpoint.
        position_ledger = PostgresPositionLedger(SessionLocal)
        portfolio_state = PortfolioState(
            execution_mode=settings.execution_mode, ledger=position_ledger, bus=bus
        )
        await portfolio_state.start()

        # §6.9 step 2 (rebuild) + step 3 (reconcile) — a throwaway,
        # Session-mode PortfolioState (no ledger/bus): the OLD Session-
        # based reconciliation API decision #172 built (engine.py's own
        # "Existing reconciliation API" section), deliberately separate
        # from the event-worker instance below — PortfolioState itself
        # refuses to let one object own both APIs (_require_session_mode).
        recon_portfolio_state = PortfolioState(execution_mode=settings.execution_mode)
        with SessionLocal() as recon_session:
            recon_portfolio_state.rebuild_from_ledger(recon_session)
            reconciliation_report = await reconcile_with_venue(
                recon_session, execution_venue, recon_portfolio_state
            )

        # Reconciliation can expire or cancel orders after the production
        # worker's initial restore. Refresh before the governor reads it.
        if not reconciliation_report.has_discrepancy:
            await portfolio_state.refresh()

        if reconciliation_report.has_discrepancy:
            # I13: never silently proceed on a discrepancy. This task's own
            # resolution of reconciliation.py's own open question ("what
            # 'halt new entries' means operationally belongs to whoever
            # owns the pipeline's entry point"): log CRITICAL with the
            # full list, and simply never wire AuthorizerStub/
            # ExecutionEngine/Portfolio State below — no OrderApproved can
            # be produced or accepted, while the rest of the app (market
            # data, Feature Engine, ...) still boots normally.
            logger.critical(
                "Execution ledger/venue reconciliation found %d discrepancy(ies) on startup — "
                "entry acceptance stays OFF: %s",
                len(reconciliation_report.discrepancies),
                reconciliation_report.discrepancies,
            )
            await execution_venue.disconnect()
            await portfolio_state.stop()
            portfolio_state = None
            execution_venue = None
            app.state.execution_startup_status = _execution_startup_status(
                "reconciliation_blocked",
                reason_code="reconciliation_discrepancy",
                discrepancy_count=len(reconciliation_report.discrepancies),
            )
        else:
            broker_registry.set_execution_venue(execution_venue)

            order_ledger = PostgresOrderLedger(SessionLocal)
            trade_ledger = PostgresTradeLedger(SessionLocal)
            fill_ledger = PostgresFillLedger(SessionLocal)
            exit_ledger = PostgresExitLedger(SessionLocal)
            portfolio_state_reader = PortfolioStateAdapter(portfolio_state, SessionLocal)

            authorizer_stub = get_authorizer_stub(bus, trade_ledger, portfolio_state_reader)

            # order_ledger doubles as decision_authorization — PostgresOrderLedger
            # implements both OrderLedgerPort and DecisionAuthorizationPort.
            execution_engine = get_execution_engine(
                bus, order_ledger, order_ledger, fill_ledger, exit_ledger, portfolio_state
            )
            # Restore durable EOD expiry and both observation slots before
            # the monitor accepts market events. The same ledger instance
            # drives subsequent observation and placement work.
            position_monitor = PositionMonitor(
                bus, PortfolioStatePositionReader(portfolio_state),
                on_observation=execution_engine.on_observation,
                max_journal_symbols=settings.position_monitor_max_journal_symbols,
                max_ticks_per_symbol=settings.position_monitor_max_ticks_per_symbol,
                unowed_retention_seconds=settings.position_monitor_unowed_retention_seconds,
            )
            with SessionLocal() as recovery_session:
                recovery_rows = recovery_session.execute(
                    select(ExitRequest, Position).join(Position, ExitRequest.position_id == Position.position_id)
                    .where(Position.execution_mode == "simulated", Position.qty > 0, Position.status != "closed")
                    .order_by(ExitRequest.created_at)
                ).all()
            cancelled_unsent = False
            for request, position in recovery_rows:
                expiry = await asyncio.to_thread(exit_ledger.advance_eod_expiry, position.position_id)
                cancelled_unsent = cancelled_unsent or expiry.cancelled_order_id is not None
                slot = await asyncio.to_thread(exit_ledger.slot_state, position.position_id)
                if slot is None:
                    raise RuntimeError("exit request disappeared during startup hydration")
                original = ExitIntent(
                    position_id=position.position_id, symbol=position.symbol, side=position.side,
                    qty=position.qty, exit_reason=request.exit_reason,
                    trigger_price=float(request.trigger_price), trigger_ts=request.trigger_ts,
                    eod_flatten_at=(slot.eod_flatten_at.astimezone(timezone.utc)
                                    if slot.eod_flatten_at is not None else None),
                    eod_close_at=(slot.eod_close_at.astimezone(timezone.utc)
                                  if slot.eod_close_at is not None else None),
                )
                kind = "eod" if slot.is_eod else "protective"
                state = (ObservationState.EXPIRED if slot.is_eod and slot.eod_expired
                         else ObservationState.ACKNOWLEDGED)
                position_monitor.restore_observation(kind, original, state)
                if slot.fallback_reason is not None:
                    fallback = ExitIntent(
                        position_id=position.position_id, symbol=position.symbol, side=position.side,
                        qty=position.qty, exit_reason=slot.fallback_reason,
                        trigger_price=float(slot.fallback_trigger_price), trigger_ts=slot.fallback_trigger_ts,
                    )
                    position_monitor.restore_observation("protective", fallback, ObservationState.ACKNOWLEDGED)
            if cancelled_unsent:
                await portfolio_state.refresh()  # expiry cancelled approved orders after the first refresh
            execution_engine.bind_position_monitor(position_monitor)
            authorizer_stub.start()
            execution_engine.start()
            position_monitor.start()
            app.state.position_monitor = position_monitor

            # Publish this read dependency only after reconciliation, restore,
            # and the entry pipeline have all succeeded. World View never
            # owns or starts a second Portfolio State instance.
            app.state.world_view_portfolio_reader = portfolio_state

            # Last line of the success path, deliberately — "ready" means the
            # whole block above (venue connect, reconcile, restore, all three
            # workers) already completed without raising.
            app.state.execution_startup_status = _execution_startup_status("ready")

            # Recorder failure cannot change an otherwise ready execution pipeline.
            try:
                outcome_recorder = OutcomeRecorder(
                    bus, SessionLocal,
                    snapshot_max_lag_seconds=settings.outcome_snapshot_max_lag_seconds,
                    sweep_interval_seconds=settings.outcome_sweep_interval_seconds,
                )
                await outcome_recorder.start()
            except Exception:
                logger.critical("OutcomeRecorder failed to start; closed outcomes remain recoverable", exc_info=True)
                outcome_recorder = None

            logger.info(
                "Execution pipeline started (mode=%s, venue=%s) — reconciliation: %d advanced, "
                "%d expired, %d cancelled stale entries, %d resubmitted exits",
                settings.execution_mode,
                execution_venue.venue_id,
                len(reconciliation_report.advanced_orders),
                len(reconciliation_report.expired_orders),
                len(reconciliation_report.cancelled_stale_entries),
                len(reconciliation_report.resubmitted_exits),
            )
    except Exception:  # noqa: BLE001 — the execution pipeline is not (yet) load-bearing for the
        # rest of the app (market data, Feature Engine, Context Engine, ... all run without it) —
        # a DB outage or a reconciliation failure here must not crash the whole process, same
        # soft-fail posture as the optional Finnhub/Polygon auto-connects just above.
        logger.exception("Execution pipeline failed to start — rolling back entry pipeline")
        # Set before rollback, not after: a route hit while rollback is still
        # draining workers below must already see "startup_failed", not
        # "unavailable" (only the `finally` block's later reset means that).
        # reason_code is a fixed safe constant, never the caught exception's
        # own str() — that text can carry stack detail, DSNs, or other
        # internals this route must never surface.
        app.state.execution_startup_status = _execution_startup_status(
            "startup_failed", reason_code="startup_exception"
        )
        # Revoke the placer before awaiting any worker drain. deactivate() closes
        # both entry callbacks before their queued work can run; the bus
        # itself stays live for unrelated routes and subscribers.
        broker_registry.clear_execution_venue()
        app.state.world_view_portfolio_reader = None
        app.state.position_monitor = None
        if authorizer_stub is not None:
            authorizer_stub.deactivate()
        if execution_engine is not None:
            execution_engine.deactivate()
        if authorizer_stub is not None:
            await authorizer_stub.stop()
        if position_monitor is not None:
            await position_monitor.stop()
        if execution_engine is not None:
            await execution_engine.stop()
        if portfolio_state is not None:
            await portfolio_state.stop()
        if outcome_recorder is not None:
            await outcome_recorder.stop()
        if execution_venue is not None:
            await execution_venue.disconnect()
        authorizer_stub = execution_engine = portfolio_state = position_monitor = execution_venue = None
        logger.error("Execution pipeline rollback complete — entry acceptance stays OFF")

    logger.info("%s started (debug=%s)", settings.app_name, settings.debug)
    # context-universe-hot-add: expose the RUNNING engine to the scanner
    # universe route; cleared first thing at shutdown (below) so a late
    # addition cannot reach an engine that is stopping.
    app.state.context_engine = context_engine
    try:
        yield
    finally:
        app.state.context_engine = None
        app.state.world_view_portfolio_reader = None
        app.state.position_monitor = None
        # Same "no active lifespan" reset as the two lines above — a route
        # hit after shutdown must report "unavailable", not a stale "ready"/
        # "reconciliation_blocked"/"startup_failed" from before this process
        # started stopping.
        app.state.execution_startup_status = None
        # try/finally added deliberately (confirmed decision #47) — found
        # via a real, reproducible bug, not by inspection. Without it, an
        # exception raised anywhere inside the `async with
        # app.router.lifespan_context(app):` block (which is exactly how
        # this app's own test suite exercises real engine behavior — see
        # test_intelligence_routes.py) gets thrown INTO this generator
        # at the `yield` above. A bare `yield` with no try/finally means
        # that exception propagates straight out, skipping every line
        # below entirely — the Event Bus and all three engines never get
        # told to stop, their background tasks are simply abandoned
        # (later surfacing as "Task was destroyed but it is pending!"
        # warnings, often during an unrelated LATER test), and — the part
        # that actually mattered — abandoned tasks don't stop touching
        # the database just because nobody's watching anymore. That's
        # what was racing test cleanup: not a timing window in the
        # stop() sequence itself, but shutdown never running at all.
        for provider in broker_registry.get_all_active_providers():
            await provider.disconnect()

        # ContextEngine stops here, BEFORE the bus — it's a pure
        # publisher, not a subscriber like the engines below, so decision
        # #47's "bus stops first" reasoning (bounding a subscriber's
        # drain to a fixed backlog) doesn't apply to it; see engine.py's
        # own docstring for the full distinction (decision #92).
        await context_engine.stop()
        await fundamentals_refresh_jobs.stop()

        # The monitor owns a timer independent of the bus. Unsubscribe and
        # stop that producer before the bus/Execution drain so no shutdown
        # pulse can create a fresh exit observation while workers stop.
        if position_monitor is not None:
            await position_monitor.stop()

        # Bus stops FIRST, deliberately, not last — separately confirmed
        # (decision #47) via the same debugging session. With engines
        # stopped before the bus: while CandleRecorder.stop() is still
        # draining, the bus is STILL dispatching fresh CandleClosed events
        # to FeatureEngine (not yet told to stop) and, transitively, fresh
        # FeaturesUpdated events to LevelInteractionEngine, right up until
        # each is told to stop in turn. Each engine's own stop() correctly
        # awaits full completion, but "full completion" of an
        # ever-refilling queue has no natural bound. Stopping the bus
        # first cuts off new dispatch at the source: every engine then
        # drains only whatever was ALREADY in its own queue before the
        # bus stopped — a fixed, bounded backlog — so "await engine.stop()"
        # actually means what it says.
        await bus.stop()
        await candle_recorder.stop()
        await tick_relay.stop()
        await feature_engine.stop()
        await level_interaction_engine.stop()
        await market_state_engine.stop()
        # Subscriber (see startup comment above) — stops after the bus,
        # same category as market_state_engine/level_interaction_engine
        # just above. Trivial in practice (scheduler.py's module
        # docstring: no queue, no background task to drain).
        await strategy_scheduler.stop()
        # OpportunityCache (Track B) — same subscriber posture, stops
        # after the bus alongside strategy_scheduler just above. Also
        # trivial in practice (opportunity_cache.py's own docstring: no
        # queue, nothing to drain).
        await opportunity_cache.stop()

    # Execution pipeline — same "stops after
        # the bus" posture as everything else in this block: each engine's
        # own stop() then only has to drain a fixed, already-queued
        # backlog. Producer-to-consumer order (authorizer -> execution
        # engine -> Portfolio State) so each stage stops enqueueing new
        # work for the next before that next stage itself stops. None-
        # guarded: the whole block above is best-effort (soft-fails on a
        # DB outage or a reconciliation discrepancy), so any of these may
        # never have started.
        if authorizer_stub is not None:
            await authorizer_stub.stop()
        if execution_engine is not None:
            await execution_engine.stop()
        if portfolio_state is not None:
            await portfolio_state.stop()
        if outcome_recorder is not None:
            await outcome_recorder.stop()
        if execution_venue is not None:
            await execution_venue.disconnect()
            broker_registry.clear_execution_venue()

        logger.info("%s stopped", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    # Order matters here — Starlette's add_middleware() prepends, so
    # wrap-order is the REVERSE of call-order. Adding
    # UnhandledExceptionMiddleware first, then CORSMiddleware, means
    # CORSMiddleware ends up wrapping UnhandledExceptionMiddleware —
    # required so a response built by UnhandledExceptionMiddleware
    # actually passes back through CORSMiddleware's header injection.
    # Reversing this order silently breaks it again (confirmed decision
    # #37) — the two are not a plain "add these two middlewares"
    # independent pair, this ordering is load-bearing.
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(dev.router)
    app.include_router(broker.router)
    app.include_router(market.router)
    app.include_router(market_data.router)
    app.include_router(finnhub_data.router)
    app.include_router(intelligence.router)
    app.include_router(backtest.router)
    app.include_router(scanner.router)
    app.include_router(channels.router)

    return app


app = create_app()
