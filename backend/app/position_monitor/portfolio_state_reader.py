"""Synchronous, read-only Portfolio State adapter for Position Monitor."""
from __future__ import annotations

from app.portfolio_state.engine import PortfolioState

from .ports import PositionView


class PositionSnapshotUnavailable(RuntimeError):
    """Portfolio State has not supplied a usable restored snapshot."""


class PortfolioStatePositionReader:
    """Translate the live Portfolio State instance into monitor position views."""

    def __init__(self, portfolio_state: PortfolioState) -> None:
        self._portfolio_state = portfolio_state

    def get_open_positions(self) -> tuple[PositionView, ...]:
        snapshot = self._portfolio_state.get_snapshot()
        if snapshot is None:
            raise PositionSnapshotUnavailable("Portfolio State snapshot unavailable")

        return tuple(
            PositionView(
                position_id=position.position_id,
                symbol=position.symbol,
                side=position.side,
                qty=position.qty,
                stop=None if position.stop is None else float(position.stop),
                target=None if position.target is None else float(position.target),
                opened_at=position.opened_at,
            )
            for position in snapshot.positions.values()
            if position.qty > 0
        )
