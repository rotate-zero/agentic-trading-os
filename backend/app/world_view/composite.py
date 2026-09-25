"""Read-only World View facade (confirmed decision #7; #150).

``WorldView`` assembles the public snapshots owned by Market State and
Context with the two existing Performance Intelligence aggregates.  It
does not persist, cache, subscribe, schedule, or mutate any source.

The portfolio slot reads the running, restored Portfolio State when the
lifespan supplies it. JSON ``null`` means that source or its snapshot is
unavailable; a restored flat account has an empty positions list.

The optional ``symbol`` scopes Market State and Context only.  Performance
Intelligence's public query contracts have no symbol or recency filter, so
both populations below are system-wide, all-matching-history aggregates.
Live and backtest rows are queried separately and can never be blended.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from app.context_engine.engine import get_context_engine
from app.market_state_engine.engine import get_market_state_engine
from app.portfolio_state.snapshot import PortfolioSnapshot
from app.trading_intelligence.performance_queries import (
    get_expectancy_by_session_type,
    get_win_rate_by_hour,
)

__all__ = ["WorldView", "WorldViewSnapshot"]


class PortfolioSnapshotReader(Protocol):
    """Only the restored Portfolio State read method needed by World View."""

    def get_snapshot(self) -> PortfolioSnapshot | None: ...


@dataclass(frozen=True)
class WorldViewPosition:
    position_id: str
    symbol: str
    side: str
    remaining_quantity: int
    average_entry: str
    stop: str | None
    target: str | None


@dataclass(frozen=True)
class WorldViewPortfolio:
    execution_mode: str
    snapshot_time: datetime
    positions: list[WorldViewPosition]
    in_flight_order_count: int


def _read_portfolio(reader: PortfolioSnapshotReader | None) -> WorldViewPortfolio | None:
    if reader is None:
        return None
    snapshot = reader.get_snapshot()  # system-wide; symbol scopes only Market State and Context
    if snapshot is None:
        return None
    return WorldViewPortfolio(
        execution_mode=snapshot.execution_mode,
        snapshot_time=snapshot.as_of,
        positions=[
            WorldViewPosition(
                position_id=str(position.position_id),
                symbol=position.symbol,
                side=position.side,
                remaining_quantity=position.qty,
                average_entry=str(position.avg_price),
                stop=str(position.stop) if position.stop is not None else None,
                target=str(position.target) if position.target is not None else None,
            )
            for position in snapshot.positions.values()
        ],
        in_flight_order_count=snapshot.in_flight_count,
    )


@dataclass(frozen=True)
class WorldViewSnapshot:
    """One point-in-time composite of the four World View domain slots.

    ``market_state`` and ``context`` retain their owning engines' complete
    public envelopes without flattening or synthetic defaults.
    """

    symbol: str | None
    market_state: dict[str, Any]
    context: dict[str, Any]
    performance: dict[str, dict[str, list[dict[str, Any]]]]
    portfolio: WorldViewPortfolio | None


def _read_performance() -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Run all synchronous Performance Intelligence reads off-loop.

    The query functions own their synchronous SQLAlchemy session lifecycles.
    This helper only selects each population explicitly and converts their
    existing dataclass rows; it defines no parallel metrics or SQL.
    """

    return {
        "live": {
            "hourly_win_rates": [asdict(row) for row in get_win_rate_by_hour(is_backtest=False)],
            "session_expectancy": [
                asdict(row) for row in get_expectancy_by_session_type(is_backtest=False)
            ],
        },
        "backtest": {
            "hourly_win_rates": [asdict(row) for row in get_win_rate_by_hour(is_backtest=True)],
            "session_expectancy": [
                asdict(row) for row in get_expectancy_by_session_type(is_backtest=True)
            ],
        },
    }


class WorldView:
    """Stateless read facade over independently owned source domains."""

    def __init__(self, portfolio_reader: PortfolioSnapshotReader | None = None) -> None:
        self._portfolio_reader = portfolio_reader

    async def snapshot(self, symbol: str | None = None) -> WorldViewSnapshot:
        """Return the current composite without writing or owning state.

        ``symbol`` is echoed exactly and passed unchanged to Market State and
        Context.  Their public response envelopes are returned unchanged.
        Performance remains system-wide/all-history in v1 because its two
        public query functions do not accept a symbol or recency filter.
        """

        market_state = get_market_state_engine().get_snapshot(symbol)
        context = get_context_engine().get_snapshot(symbol)
        performance = await asyncio.to_thread(_read_performance)
        return WorldViewSnapshot(
            symbol=symbol,
            market_state=market_state,
            context=context,
            performance=performance,
            portfolio=_read_portfolio(self._portfolio_reader),
        )
