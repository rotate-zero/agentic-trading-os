"""
The `execution` registry role — AC #5: set_execution_venue() refuses a
venue whose supported_modes lacks the configured execution_mode.
No database needed.
"""
from __future__ import annotations

import pytest

import app.services.broker_registry as registry
from app.broker_adapters.order_venue import (
    ExecutionMode,
    OrderAck,
    OrderInstruction,
    OrderStatusReport,
    OrderUpdateCallback,
    OrderVenue,
    VenuePosition,
)
from app.broker_adapters.simulated_venue import SimulatedVenue


class _PaperOnlyVenue(OrderVenue):
    """A minimal stub whose only purpose is NOT supporting 'simulated'
    — exercises the refusal path without needing a real paper venue."""

    @property
    def venue_id(self) -> str:
        return "stub-paper"

    @property
    def supported_modes(self) -> frozenset[ExecutionMode]:
        return frozenset({"paper"})

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def is_connected(self) -> bool: return True
    async def place_order(self, instruction: OrderInstruction) -> OrderAck:
        raise NotImplementedError
    async def cancel_order(self, client_order_id: str) -> None:
        raise NotImplementedError
    async def get_order(self, client_order_id: str) -> OrderStatusReport | None:
        raise NotImplementedError
    async def list_open_orders(self) -> list[OrderStatusReport]:
        raise NotImplementedError
    async def get_fills(self, client_order_id: str) -> list:
        raise NotImplementedError
    async def get_positions(self) -> list[VenuePosition]:
        raise NotImplementedError
    def on_order_update(self, callback: OrderUpdateCallback) -> None:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _clear_registry():
    registry.clear_all()
    yield
    registry.clear_all()


def test_set_execution_venue_accepts_matching_mode() -> None:
    venue = SimulatedVenue()
    registry.set_execution_venue(venue)  # config default execution_mode == "simulated"
    assert registry.get_execution_venue() is venue


def test_set_execution_venue_refuses_unsupported_mode() -> None:
    with pytest.raises(registry.UnsupportedExecutionModeError):
        registry.set_execution_venue(_PaperOnlyVenue())
    assert registry.get_execution_venue() is None  # refused registration leaves the slot empty


def test_clear_all_clears_execution_venue_too() -> None:
    registry.set_execution_venue(SimulatedVenue())
    assert registry.get_execution_venue() is not None
    registry.clear_all()
    assert registry.get_execution_venue() is None


def test_execution_venue_slot_is_independent_of_streaming_historical() -> None:
    """An OrderVenue is never stored in, nor confused with, the
    streaming/historical MarketDataProvider slots (design doc §6.4)."""
    registry.set_execution_venue(SimulatedVenue())
    assert registry.get_streaming_provider() is None
    assert registry.get_historical_provider() is None
