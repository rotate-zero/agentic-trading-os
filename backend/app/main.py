from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import dev, health
from app.api.websocket import channels
from app.api.websocket.channels import get_gateway
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.event_bus.bus import get_event_bus

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.debug)

    bus = get_event_bus()
    await bus.start()

    gateway = get_gateway()
    gateway.attach()

    logger.info("%s started (debug=%s)", settings.app_name, settings.debug)
    yield

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
    app.include_router(channels.router)

    return app


app = create_app()
