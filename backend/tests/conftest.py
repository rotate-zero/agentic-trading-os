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

Also blanks FINNHUB_API_KEY/POLYGON_API_KEY for the duration of every
test, regardless of what's actually in a real .env file on whatever
machine runs these tests — found via a real bug, not a hypothetical:
once real API keys were configured locally for actual usage,
`TestClient(app)`'s __enter__ started running main.py's REAL lifespan
startup on every single test, which auto-connects real
Finnhub/Polygon providers as a side effect and silently overwrites
whatever fake registry state a test had carefully set up — confirmed
via an actual captured traceback showing a genuine HTTPS request to
api.polygon.io firing during a supposedly-isolated unit test. Six tests
failed from this alone, none of them because of an actual bug in
application code (see confirmed-decisions.md #38).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_app_singletons(monkeypatch: pytest.MonkeyPatch):
    import app.api.routes.finnhub_data as finnhub_data_module
    import app.api.routes.market_data as market_data_module
    import app.api.websocket.channels as channels_module
    import app.api.websocket.manager as manager_module
    import app.context_engine.engine as context_engine_module
    import app.core.config as config_module
    import app.event_bus.bus as bus_module
    import app.feature_engine.engine as feature_engine_module
    import app.market_state_engine.engine as market_state_engine_module
    import app.services.live_tick_relay as live_tick_relay_module
    import app.trading_intelligence.level_interaction_engine as level_interaction_engine_module
    from app.services import broker_registry

    # Explicit empty string, not delenv: pydantic-settings' BaseSettings
    # reads the .env FILE as a fallback source independent of the
    # process's real os.environ — delenv only removes an OS-level
    # override that likely was never set in the first place (a real key
    # sitting in .env isn't a shell-exported OS env var). Only a SET
    # (even to "") takes priority over the .env file's own value.
    monkeypatch.setenv("FINNHUB_API_KEY", "")
    monkeypatch.setenv("POLYGON_API_KEY", "")
    config_module.get_settings.cache_clear()  # lru_cache — must clear or the blanked env is never actually read

    def _reset():
        bus_module._event_bus = None
        manager_module._manager = None
        channels_module._gateway = None
        feature_engine_module._feature_engine = None  # confirmed decision #47 — same reasoning as _event_bus above
        level_interaction_engine_module._level_interaction_engine = None  # ditto
        live_tick_relay_module._live_tick_relay = None  # ditto — decision #72
        context_engine_module._context_engine = None  # ditto — decision #92, missed when that engine was built, fixed here (#93)
        market_state_engine_module._market_state_engine = None  # ditto — decision #93
        broker_registry.clear_all()
        # market_data.py and finnhub_data.py each keep their own local
        # provider reference (see their module docstrings for why) —
        # broker_registry.clear_all() doesn't reach these, so reset
        # explicitly or a provider "connected" in one test leaks into
        # the next.
        market_data_module._provider = None
        finnhub_data_module._provider = None

    _reset()
    yield
    _reset()
    config_module.get_settings.cache_clear()  # restore real settings for anything running after this fixture
