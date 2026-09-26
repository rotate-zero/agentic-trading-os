"""Blocked history reads (and blocked CPU-bound reclustering — see the
`daily_levels_lookback_days` test below) must leave the ASGI event loop
available to /health."""
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


async def test_blocked_daily_levels_lookback_leaves_health_responsive(monkeypatch):
    """GET /intelligence/state's optional `daily_levels_lookback_days` path
    (decision #62) used to run its reclustering synchronously, in-line,
    inside this async handler — genuine CPU-bound work
    (`indicators/daily_levels.py`'s `cluster_daily_levels`), not I/O, but
    with the identical symptom the parametrized test above already covers
    for a blocking DB read: it monopolizes the single event-loop thread
    for its duration, so a slow one stalls every other concurrent request
    this process is serving. This delivery (`daily-levels-lookback-
    offload`) moved it behind `asyncio.to_thread(_compute_daily_levels_
    lookback, ...)` — same offload convention, same regression shape.

    Not folded into the parametrized case above: /state requires a
    `symbol` query param neither sibling route does, and its response is
    a `{"symbol", "timeframes", "daily_levels"}` envelope, not a single
    `{response_key: [...]}` list — different enough to need its own
    assertions rather than a third parametrize row.

    Omitting `daily_levels_lookback_days` entirely (the default,
    pre-computed path) never calls `_compute_daily_levels_lookback` at
    all, so it is untouched by this offload and untested here — see
    `test_intelligence_routes.py` for that path's own coverage.
    """
    started = threading.Event()
    release = threading.Event()

    def blocked_recluster(*args):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test never released _compute_daily_levels_lookback")
        return []

    monkeypatch.setattr(intelligence_routes, "_compute_daily_levels_lookback", blocked_recluster)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        state_task = asyncio.create_task(
            client.get(
                "/intelligence/state",
                params={"symbol": "ZCONCURRENCY-DL-TEST", "daily_levels_lookback_days": 30},
            )
        )
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

        state_response = await asyncio.wait_for(state_task, timeout=5)

    assert state_response.status_code == 200
    body = state_response.json()
    assert body["symbol"] == "ZCONCURRENCY-DL-TEST"
    assert body["timeframes"] == {}
    assert body["daily_levels"] == []
