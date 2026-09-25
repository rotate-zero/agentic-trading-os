"""GET /health/execution-startup — a read-only report of what main.py's own
execution-pipeline lifespan startup sequence (decision #179's fail-closed
contract) last produced, not a live trading-readiness check. Covers the
four states this route can report: "unavailable" for a route hit without
an active lifespan, "ready" after a clean startup, "reconciliation_blocked"
with a plain discrepancy count (never the discrepancy list itself), and
"startup_failed" after an injected failure and rollback (never the caught
exception's own text) — plus that the reported state returns to
"unavailable" once lifespan shutdown has run, matching the same reset
app.state.world_view_portfolio_reader/position_monitor already get.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import app.governor.engine as governor_engine_module
from app.core.market_clock import MarketClock
from app.main import app as fastapi_app
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots


@pytest.fixture(autouse=True)
def _clear_status():
    """Isolates this file's own "no active lifespan" assertions from
    whatever the real, process-shared `fastapi_app` singleton's state
    happens to hold after some other test file's real-lifespan run —
    the same defensive reset test_world_view_portfolio.py's own `sources`
    fixture already uses for `world_view_portfolio_reader`."""
    fastapi_app.state.execution_startup_status = None
    yield
    fastapi_app.state.execution_startup_status = None


async def test_route_reports_unavailable_without_an_active_lifespan():
    # No `with TestClient(...)` / lifespan anywhere in this test — a direct
    # ASGI call against the real, unstarted app, the same technique
    # test_world_view_portfolio.py's own route test already uses for this
    # exact "no active lifespan" shape.
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/execution-startup")

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "reason_code": None, "discrepancy_count": None}


def test_successful_startup_reports_ready():
    with TestClient(fastapi_app) as client:
        response = client.get("/health/execution-startup")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "reason_code": None, "discrepancy_count": None}

    # Shutdown state, covered here too: once the lifespan context exits,
    # the app-owned status is cleared the same way world_view_portfolio_
    # reader/position_monitor already are — a route hit afterward would
    # see "unavailable" again (proven directly by the first test above).
    assert fastapi_app.state.execution_startup_status is None


def test_reconciliation_discrepancy_reports_blocked_with_a_safe_count(monkeypatch):
    from app.portfolio_state.reconciliation import ReconciliationReport

    async def discrepant_reconciliation(*args):
        return ReconciliationReport(discrepancies=["mismatch A", "mismatch B", "mismatch C"])

    monkeypatch.setattr("app.portfolio_state.reconciliation.reconcile_with_venue", discrepant_reconciliation)

    with TestClient(fastapi_app) as client:
        response = client.get("/health/execution-startup")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "reconciliation_blocked",
        "reason_code": "reconciliation_discrepancy",
        "discrepancy_count": 3,
    }
    # The raw discrepancy text (which could name a specific symbol, order,
    # or ledger row) never reaches this route — only a plain count of it.
    assert "mismatch" not in response.text
    assert fastapi_app.state.execution_startup_status is None


def test_injected_partial_startup_failure_reports_startup_failed_after_rollback(monkeypatch):
    from app.position_monitor.engine import PositionMonitor

    # Same fault-injection shape test_main_execution_pipeline.py's own
    # test_partial_startup_rolls_back_before_serving_requests uses (fail
    # after the authorizer and execution engine have already started, so
    # rollback has real workers to stop) — narrowed here to just this
    # route's own reported status, not the full rollback state.
    monkeypatch.setattr(MarketClock, "is_regular_session", lambda self, ts=None: True)
    monkeypatch.setattr(
        governor_engine_module, "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots(market_state={"trend_score": 1.0}, context={"news": []}),
    )

    original_start = PositionMonitor.start

    def fail_after_start(self):
        original_start(self)
        raise RuntimeError("injected after authorizer and execution engine start")

    monkeypatch.setattr(PositionMonitor, "start", fail_after_start)

    with TestClient(fastapi_app) as client:
        response = client.get("/health/execution-startup")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "startup_failed",
        "reason_code": "startup_exception",
        "discrepancy_count": None,
    }
    # The route must never leak the caught exception's own text.
    assert "RuntimeError" not in response.text
    assert "injected after" not in response.text
    assert fastapi_app.state.execution_startup_status is None
