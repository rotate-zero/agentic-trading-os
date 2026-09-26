"""Blocked analytics queries must leave the ASGI event loop available to /health."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from app.main import app
from app.trading_intelligence import performance_queries


@pytest.mark.parametrize(
    ("path", "query_name", "response_key"),
    [
        ("/intelligence/win-rate-by-hour", "get_win_rate_by_hour", "hourly_win_rates"),
        (
            "/intelligence/expectancy-by-session-type",
            "get_expectancy_by_session_type",
            "session_expectancy",
        ),
    ],
)
async def test_blocked_analytics_query_leaves_health_responsive(monkeypatch, path, query_name, response_key):
    started = threading.Event()
    release = threading.Event()

    def blocked_query(*, strategy_name, strategy_version, is_backtest):
        assert (strategy_name, strategy_version, is_backtest) == ("test-strategy", "v1", True)
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError(f"test never released {query_name}")
        return []

    monkeypatch.setattr(performance_queries, query_name, blocked_query)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        analytics_task = asyncio.create_task(
            client.get(path, params={"strategy_name": "test-strategy", "strategy_version": "v1", "is_backtest": "true"})
        )
        try:
            async def wait_until_started():
                while not started.is_set():
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_until_started(), timeout=2)
            health_response = await asyncio.wait_for(client.get("/health"), timeout=2)
            assert health_response.status_code == 200
            assert health_response.json()["status"] == "ok"
        finally:
            release.set()

        analytics_response = await asyncio.wait_for(analytics_task, timeout=5)

    assert analytics_response.status_code == 200
    assert analytics_response.json() == {response_key: []}
