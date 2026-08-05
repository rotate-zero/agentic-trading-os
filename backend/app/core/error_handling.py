"""
Converts any unhandled exception into a clean JSON 500 response — as a
plain ASGI middleware, NOT an @app.exception_handler(Exception) decorator.

That distinction matters and isn't cosmetic (confirmed decision #37,
found via a real bug report — a browser reported "CORS policy" for what
was actually an uncaught exception with no CORS violation at all):
Starlette's own build_middleware_stack() routes any handler registered
for the bare Exception class to ServerErrorMiddleware specifically, which
is *always* the outermost layer, unconditionally wrapping CORSMiddleware.
A response built by that handler is sent directly via the raw ASGI
`send`, bypassing CORSMiddleware's header-injection entirely — verified
by reading Starlette 0.46.2's actual source
(starlette/middleware/errors.py's ServerErrorMiddleware.__call__), not
assumed from a blog post. This happens on a real production server
exactly as much as in tests; it's not a test-only artifact.

This class is a normal ASGI middleware instead, which only fixes the
problem if it's registered so that CORSMiddleware ends up wrapping IT
(see main.py's add_middleware() call order and its comment — Starlette's
add_middleware() prepends, so call order is the reverse of wrap order).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class UnhandledExceptionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def _send(message: Message) -> Awaitable[None] | None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            return await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:  # noqa: BLE001 — this IS the catch-all, by design
            logger.exception("Unhandled exception on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                # Streaming had already begun — nothing safe to send at
                # this point without corrupting the response. Re-raise so
                # it's at least logged/visible upstream, matching
                # Starlette's own ServerErrorMiddleware behavior in this
                # same edge case.
                raise
            response = JSONResponse(status_code=500, content={"detail": f"Internal error: {exc}"})
            await response(scope, receive, send)
