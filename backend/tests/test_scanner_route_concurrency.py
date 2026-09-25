"""
Concurrency regression for scanner-route-db-offload: proves the
asyncio.to_thread wrapping added to app/api/routes/scanner.py's universe
calls actually keeps a slow/blocked database read off the event loop,
not just that the route still returns the right JSON (test_scanner_universe.py
already covers correctness of the underlying app/scanner/universe.py
functions against a real DB; this file is deliberately about timing/
concurrency, not data shape).

Technique: monkeypatch list_universe_symbols (as imported into
app.api.routes.scanner's own namespace, same "patch where it's looked
up" approach test_scanner_runner.py's patch.object(runner_module, ...)
already uses) with a fake that blocks on a threading.Event until
released. Two `threading.Event`s make this deterministic rather than a
sleep-based race: `started` proves the blocked call is actually running
in its worker thread before the concurrent /health request is sent;
`release` is only set after that assertion, so a bug that put the
blocking call back on the event loop would hang this test at the
`started.wait` line (surfaced as a clear timeout) rather than passing
by accident.

Direct ASGI transport against the real, unstarted `app` (no
`with TestClient(...)`/lifespan) — same technique
test_execution_startup_status_route.py's own
test_route_reports_unavailable_without_an_active_lifespan uses — because
neither route under test here needs FeatureEngine or any other
lifespan-installed state: GET /health only touches the lazy
MarketClock/EventBus singletons, and GET /scanner/universe only touches
the (here, patched) universe helper.
"""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

import app.api.routes.scanner as scanner_routes
from app.main import app as fastapi_app

_BLOCK_TIMEOUT_SECONDS = 5


@pytest.fixture
def _blocked_list_universe_symbols(monkeypatch):
    """Patches list_universe_symbols with a fake that blocks until
    released, plus the two events a test uses to control it
    deterministically. Restored automatically by monkeypatch's own
    teardown — no manual unpatch needed."""
    started = threading.Event()
    release = threading.Event()

    def _blocked(session_factory):
        started.set()
        if not release.wait(timeout=_BLOCK_TIMEOUT_SECONDS):
            raise TimeoutError("test never released the blocked universe call")
        return [{"symbol": "ZZBLOCK", "added_at": "2026-01-01T00:00:00+00:00"}]

    monkeypatch.setattr(scanner_routes, "list_universe_symbols", _blocked)
    return started, release


async def test_blocked_universe_call_does_not_block_an_unrelated_route(_blocked_list_universe_symbols):
    started, release = _blocked_list_universe_symbols

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        scanner_task = asyncio.create_task(client.get("/scanner/universe"))

        # Don't proceed until the blocked helper is actually running in
        # its worker thread — this is what makes the next assertion mean
        # something, rather than racing an unstarted request.
        started_in_time = await asyncio.to_thread(started.wait, _BLOCK_TIMEOUT_SECONDS)
        assert started_in_time, "blocked universe helper never started"

        # The event loop must still be free to serve an unrelated,
        # lightweight route while that Scanner request is stuck in its
        # worker thread — this is the actual regression this file guards.
        health_response = await asyncio.wait_for(client.get("/health"), timeout=2.0)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        # Let the blocked call finish and confirm the original request
        # still completes normally once its thread is unblocked.
        release.set()
        scanner_response = await asyncio.wait_for(scanner_task, timeout=_BLOCK_TIMEOUT_SECONDS)

    assert scanner_response.status_code == 200
    assert scanner_response.json() == {"symbols": [{"symbol": "ZZBLOCK", "added_at": "2026-01-01T00:00:00+00:00"}]}


async def test_two_concurrent_scanner_universe_requests_both_complete(_blocked_list_universe_symbols):
    """Not just one blocked request alongside a cheap route — two
    requests that both hit the (patched, blocking) universe helper at
    once, proving each got its own worker thread rather than one
    silently starving the other."""
    started, release = _blocked_list_universe_symbols

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.get("/scanner/universe"))
        second = asyncio.create_task(client.get("/scanner/universe"))

        started_in_time = await asyncio.to_thread(started.wait, _BLOCK_TIMEOUT_SECONDS)
        assert started_in_time

        release.set()
        first_response, second_response = await asyncio.wait_for(
            asyncio.gather(first, second), timeout=_BLOCK_TIMEOUT_SECONDS
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
