from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import broker, dev, health, market, market_data
from app.api.websocket import channels
from app.api.websocket.channels import get_gateway
from app.broker_adapters.polygon_provider import PolygonAdapter
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.event_bus.bus import get_event_bus
from app.services import broker_registry
from app.services.tick_ingest import TickIngestBridge

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.debug)

    bus = get_event_bus()
    await bus.start()

    gateway = get_gateway()
    gateway.attach()

    # Auto-connect Polygon (if configured) — unlike IBKR, which requires
    # an external Gateway app + 2FA and so stays manual-connect-only
    # (see app/api/routes/broker.py), Polygon is just an API-key-
    # authenticated cloud service, so there's no reason to make this a
    # manual step. Soft-fail: a missing key or a connect error here must
    # not crash the whole app over an optional data source.
    if settings.polygon_api_key:
        try:
            provider = PolygonAdapter()
            await provider.connect()
            bridge = TickIngestBridge(provider, bus)
            broker_registry.set_active(provider, bridge)
            logger.info("Polygon auto-connected on startup (15-min-delayed free tier)")
        except Exception:  # noqa: BLE001 — optional data source, app must still boot
            logger.exception("Polygon auto-connect failed on startup — continuing without it")
    else:
        logger.info("POLYGON_API_KEY not set — skipping Polygon auto-connect")

    logger.info("%s started (debug=%s)", settings.app_name, settings.debug)
    yield

    active = broker_registry.get_active_adapter()
    if active is not None:
        await active.disconnect()
    await bus.stop()
    logger.info("%s stopped", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

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
    app.include_router(channels.router)

    return app


app = create_app()
