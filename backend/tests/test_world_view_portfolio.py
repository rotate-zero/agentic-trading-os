"""World View's read-only Portfolio State projection and route contract."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

import app.world_view.composite as composite
from app.main import app
from app.portfolio_state.accounting import PositionState
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.ports import InFlightOrder, LedgerState
from app.world_view import WorldView

AS_OF = datetime(2026, 9, 25, 12, 34, 56, tzinfo=timezone.utc)
POSITION_ID = UUID("12345678-1234-5678-1234-567812345678")


class _Source:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def get_snapshot(self):
        self.calls += 1
        return self.snapshot


class _ScopedSource:
    def __init__(self, name):
        self.name = name

    def get_snapshot(self, symbol=None):
        return {self.name: symbol}


@pytest.fixture(autouse=True)
def sources(monkeypatch):
    monkeypatch.setattr(composite, "get_market_state_engine", lambda: _ScopedSource("market"))
    monkeypatch.setattr(composite, "get_context_engine", lambda: _ScopedSource("context"))
    monkeypatch.setattr(composite, "_read_performance", lambda: {"live": {"hourly_win_rates": [], "session_expectancy": []}, "backtest": {"hourly_win_rates": [], "session_expectancy": []}})
    monkeypatch.setattr(app.state, "world_view_portfolio_reader", None, raising=False)


def _restored_portfolio(*, position=False):
    positions = ()
    orders = ()
    if position:
        positions = (PositionState(
            position_id=POSITION_ID, trade_id=UUID("12345678-1234-5678-1234-567812345679"),
            execution_mode="simulated", execution_venue="simulated", symbol="AAPL", side="BUY",
            qty=3, avg_price=Decimal("101.2300"), opened_at=AS_OF,
            stop=Decimal("98.005"), target=Decimal("110.5000"),
        ),)
        orders = (InFlightOrder("exit-1", "AAPL", "SELL", 3, "close", "simulated"),)
    state = LedgerState("simulated", 1, AS_OF, positions=positions, orders=orders)
    portfolio = PortfolioState("simulated")
    portfolio._install_state(state)  # restore the real read cache without a database dependency
    return portfolio


@pytest.mark.asyncio
async def test_unavailable_reader_and_unavailable_snapshot_remain_null():
    assert (await WorldView().snapshot()).portfolio is None
    source = _Source(None)  # startup has not restored, or read cache is stale
    result = await WorldView(source).snapshot()
    assert result.portfolio is None
    assert source.calls == 1


@pytest.mark.asyncio
async def test_restored_empty_portfolio_is_non_null_and_preserves_other_fields():
    result = await WorldView(_restored_portfolio()).snapshot("AAPL")
    assert result.portfolio is not None
    assert result.portfolio.positions == []
    assert result.portfolio.in_flight_order_count == 0
    assert result.portfolio.execution_mode == "simulated"
    assert result.portfolio.snapshot_time == AS_OF
    assert result.symbol == "AAPL"
    assert result.market_state == {"market": "AAPL"}
    assert result.context == {"context": "AAPL"}
    assert result.performance == {"live": {"hourly_win_rates": [], "session_expectancy": []}, "backtest": {"hourly_win_rates": [], "session_expectancy": []}}


@pytest.mark.asyncio
async def test_open_position_serializes_exact_decimal_prices_and_is_system_wide():
    source = _restored_portfolio(position=True)
    app.state.world_view_portfolio_reader = source
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        all_response = await client.get("/intelligence/world-view")
        scoped_response = await client.get("/intelligence/world-view", params={"symbol": "MSFT"})

    assert all_response.status_code == scoped_response.status_code == 200
    all_body = all_response.json()
    scoped_body = scoped_response.json()
    assert all_body["portfolio"] == scoped_body["portfolio"] == {
        "execution_mode": "simulated",
        "snapshot_time": "2026-09-25T12:34:56+00:00",
        "positions": [{
            "position_id": str(POSITION_ID), "symbol": "AAPL", "side": "BUY",
            "remaining_quantity": 3, "average_entry": "101.2300",
            "stop": "98.005", "target": "110.5000",
        }],
        "in_flight_order_count": 1,
    }
    assert all_body["symbol"] is None and scoped_body["symbol"] == "MSFT"
    assert all_body["market_state"] == {"market": None}
    assert scoped_body["market_state"] == {"market": "MSFT"}
    assert scoped_body["context"] == {"context": "MSFT"}
    assert all_body["performance"] == scoped_body["performance"]
    assert set(scoped_body) == {"symbol", "market_state", "context", "performance", "portfolio"}
