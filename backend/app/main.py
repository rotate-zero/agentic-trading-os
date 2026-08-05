from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import broker, dev, finnhub_data, health, market, market_data
from app.api.routes.finnhub_data import connect_finnhub
from app.api.routes.market_data import connect_polygon
from app.api.websocket import channels
from app.api.websocket.channels import get_gateway
from app.core.config import get_settings
from app.core.error_handling import UnhandledExceptionMiddleware
from app.core.logging import configure_logging
from app.event_bus.bus import get_event_bus
from app.services import broker_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.debug)

    bus = get_event_bus()
    await bus.start()

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
    yield

    for provider in broker_registry.get_all_active_providers():
        await provider.disconnect()
    await bus.stop()
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
    app.include_router(channels.router)

    return app


app = create_app()
