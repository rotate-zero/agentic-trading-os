"""Blocked history reads must leave the ASGI event loop available to /health."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

import app.api.routes.intelligence as intelligence_routes
from app.main import app


@pytest.mark.parametrize(
    ("path", "helper_name", "response_key"),
    [
        ("/intelligence/strategy-outcomes", "_fetch_strategy_outcomes", "outcomes"),
        ("/intelligence/backtest-runs", "_fetch_backtest_runs", "backtest_runs"),
    ],
)
async def test_blocked_history_read_leaves_health_responsive(monkeypatch, path, helper_name, response_key):
    started = threading.Event()
    release = threading.Event()

    def blocked_read(*args):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError(f"test never released {helper_name}")
        return []

    monkeypatch.setattr(intelligence_routes, helper_name, blocked_read)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        history_task = asyncio.create_task(client.get(path))
        try:
            async def wait_until_started():
                while not started.is_set():
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_until_started(), timeout=5)
            health_response = await asyncio.wait_for(client.get("/health"), timeout=2)
            assert health_response.status_code == 200
            assert health_response.json()["status"] == "ok"
        finally:
            release.set()

        history_response = await asyncio.wait_for(history_task, timeout=5)

    assert history_response.status_code == 200
    assert history_response.json() == {response_key: []}
