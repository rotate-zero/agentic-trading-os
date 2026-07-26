"""
Shared test fixtures.

Resets the app's module-level singletons (Event Bus, WebSocket connection
manager, Gateway, broker registry) before and after each test that touches
app.main's `app` object via TestClient.

Why this is needed: get_event_bus() etc. cache one instance for the
lifetime of the Python process — correct for a real running server (one
process, one event loop) but not for a test session, where pytest-asyncio
gives each async test its own fresh event loop. Without this reset, a
cached EventBus's asyncio.Queue objects (bound to whichever loop first
touched them) get reused across tests running on different loops,
producing "Queue ... is bound to a different event loop" errors — not a
bug in the app itself, just a mismatch between "singleton per process"
and "fresh loop per test."
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_app_singletons():
    import app.api.websocket.channels as channels_module
    import app.api.websocket.manager as manager_module
    import app.event_bus.bus as bus_module
    from app.services import broker_registry

    def _reset():
        bus_module._event_bus = None
        manager_module._manager = None
        channels_module._gateway = None
        broker_registry.clear_active()

    _reset()
    yield
    _reset()
