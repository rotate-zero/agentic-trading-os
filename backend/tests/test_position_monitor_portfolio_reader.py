"""Position Monitor's read boundary against real Portfolio State snapshots."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.portfolio_state.accounting import LedgerFill, apply_fill
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.ports import InFlightOrder, LedgerState
from app.position_monitor.portfolio_state_reader import (
    PortfolioStatePositionReader, PositionSnapshotUnavailable,
)
from app.position_monitor.ports import PositionView

TS = datetime(2026, 1, 5, 15, tzinfo=timezone.utc)


def restored(*, positions=(), orders=(), problems=()):
    state = PortfolioState("simulated")
    state._install_state(LedgerState("simulated", 0, TS, positions, orders, problems=problems))
    return PortfolioStatePositionReader(state)


def opened_position():
    fill = LedgerFill(
        ledger_seq=1, venue_fill_id="entry-fill", client_order_id="entry",
        trade_id=uuid4(), execution_mode="simulated", execution_venue="simulated",
        symbol="AAPL", side="BUY", position_effect="open", qty=10,
        price=Decimal("100"), venue_ts=TS, stop=Decimal("90.25"),
        target=Decimal("120.50"),
    )
    return apply_fill(None, fill, new_position_id=uuid4()).position, fill


def test_restored_empty_portfolio_returns_empty_tuple():
    assert restored().get_open_positions() == ()


def test_open_position_maps_identity_quantity_prices_and_time():
    position, _ = opened_position()
    reader = restored(positions=(position,))
    assert reader.get_open_positions() == (
        PositionView(position.position_id, "AAPL", "BUY", 10, 90.25, 120.5, TS),
    )
    assert isinstance(reader.get_open_positions()[0].stop, float)
    assert isinstance(reader.get_open_positions()[0].target, float)


def test_partial_reduction_keeps_closing_position_with_remaining_quantity():
    position, entry = opened_position()
    exit_fill = replace(entry, ledger_seq=2, venue_fill_id="exit-fill", client_order_id="exit",
                        side="SELL", position_effect="close", qty=4, price=Decimal("110"))
    reduced = apply_fill(position, exit_fill).position
    assert reduced.status == "closing" and reduced.qty == 6
    assert restored(positions=(reduced,)).get_open_positions() == (
        PositionView(position.position_id, "AAPL", "BUY", 6, 90.25, 120.5, TS),
    )


def test_in_flight_entry_order_without_position_is_excluded():
    order = InFlightOrder("entry", "AAPL", "BUY", 10, "open", "simulated")
    assert restored(orders=(order,)).get_open_positions() == ()


def test_unrestored_or_blocked_snapshot_raises_instead_of_reporting_flat():
    with pytest.raises(PositionSnapshotUnavailable, match="snapshot unavailable"):
        PortfolioStatePositionReader(PortfolioState("simulated")).get_open_positions()
    with pytest.raises(PositionSnapshotUnavailable, match="snapshot unavailable"):
        restored(problems=("unresolved ledger anomaly",)).get_open_positions()
