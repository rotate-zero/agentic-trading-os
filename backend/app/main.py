from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import broker, dev, finnhub_data, health, intelligence, market, market_data
from app.api.routes.finnhub_data import connect_finnhub
from app.api.routes.market_data import connect_polygon
from app.api.websocket import channels
from app.api.websocket.channels import get_gateway
from app.core.config import get_settings
from app.core.error_handling import UnhandledExceptionMiddleware
from app.core.logging import configure_logging
from app.event_bus.bus import get_event_bus
from app.feature_engine.engine import get_feature_engine
from app.services import broker_registry
from app.services.candle_recorder import CandleRecorder
from app.services.live_tick_relay import get_live_tick_relay
from app.trading_intelligence.level_interaction_engine import get_level_interaction_engine

logger = logging.getLogger(__name__)


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

    logger.info("%s started (debug=%s)", settings.app_name, settings.debug)
    try:
        yield
    finally:
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
    app.include_router(channels.router)

    return app


app = create_app()
