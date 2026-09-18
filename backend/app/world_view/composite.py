"""Read-only World View facade (confirmed decision #7; ``world-view-v1``).

``WorldView`` assembles the public snapshots owned by Market State and
Context with the two existing Performance Intelligence aggregates.  It
does not persist, cache, subscribe, schedule, or mutate any source.

Portfolio State has no application implementation in v1.  Its final schema
slot is nevertheless present as ``portfolio=None``: JSON ``null`` means the
source is unavailable, not that the account has an empty portfolio, zero
buying power, or no positions.

The optional ``symbol`` scopes Market State and Context only.  Performance
Intelligence's public query contracts have no symbol or recency filter, so
both populations below are system-wide, all-matching-history aggregates.
Live and backtest rows are queried separately and can never be blended.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from app.context_engine.engine import get_context_engine
from app.market_state_engine.engine import get_market_state_engine
from app.trading_intelligence.performance_queries import (
    get_expectancy_by_session_type,
    get_win_rate_by_hour,
)

__all__ = ["WorldView", "WorldViewSnapshot"]


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
    portfolio: dict[str, Any] | None


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
            portfolio=None,
        )
