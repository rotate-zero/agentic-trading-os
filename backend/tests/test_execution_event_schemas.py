"""
Tests for this task's additive schema changes: OrderApproved.position_effect
(EX-14), the new TradePlanned model (R2), and the new OrderStatusChanged
model + EventType member (EX-9). Also proves OrderStatusChanged can be
wrapped in an envelope, dispatched through the critical lane, and is
distinguishable from the pre-venue TradePlanned event (per fork 2's own
instruction).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.event_bus.events import make_envelope
from app.schemas.events.envelope import CRITICAL_EVENT_TYPES, EventType
from app.schemas.events.execution import OrderApproved, OrderStatusChanged, TradePlanned


# --- OrderApproved.position_effect (EX-14) ----------------------------------


def test_order_approved_requires_position_effect() -> None:
    with pytest.raises(ValidationError):
        OrderApproved(order_id="T1:entry", symbol="AAPL", side="BUY", qty=10)


@pytest.mark.parametrize("value", ["open", "close"])
def test_order_approved_accepts_open_or_close(value: str) -> None:
    order = OrderApproved(order_id="T1:entry", symbol="AAPL", side="BUY", qty=10, position_effect=value)
    assert order.position_effect == value


def test_order_approved_rejects_an_invalid_position_effect() -> None:
    with pytest.raises(ValidationError):
        OrderApproved(order_id="T1:entry", symbol="AAPL", side="BUY", qty=10, position_effect="both")


# --- TradePlanned (R2) -------------------------------------------------------


def test_trade_planned_requires_direction_entry_stop_size() -> None:
    with pytest.raises(ValidationError):
        TradePlanned()


def test_trade_planned_defaults_match_this_delivery_scope() -> None:
    plan = TradePlanned(direction="long", entry=100.0, stop=95.0, size=10)
    assert plan.origin == "auto"
    assert plan.corroboration == []
    assert plan.max_hold_seconds is None
    assert plan.scaling_plan is None
    assert plan.trailing_stop_rule is None
    assert plan.target is None
    assert plan.r_multiple is None


def test_trade_planned_rejects_direction_outside_long_short() -> None:
    with pytest.raises(ValidationError):
        TradePlanned(direction="BUY", entry=100.0, stop=95.0, size=10)  # order-layer vocabulary, not planning-layer


def test_trade_planned_has_no_symbol_field() -> None:
    """Symbol lives on the envelope only — same convention every other
    payload in this file (and OpportunityCreated/FeaturesUpdated/
    MarketStateChanged) already follows."""
    plan = TradePlanned(direction="long", entry=100.0, stop=95.0, size=10)
    assert "symbol" not in plan.model_dump()


# --- OrderStatusChanged (EX-9) -----------------------------------------------


def test_order_status_changed_requires_order_id_status_reason() -> None:
    with pytest.raises(ValidationError):
        OrderStatusChanged()


def test_order_status_changed_only_accepts_rejected_in_this_delivery() -> None:
    with pytest.raises(ValidationError):
        OrderStatusChanged(order_id="T1:entry", status="submitted", reason="x")

    status_changed = OrderStatusChanged(order_id="T1:entry", status="rejected", reason="mode_not_supported")
    assert status_changed.status == "rejected"


def test_order_status_changed_execution_venue_defaults_to_none() -> None:
    status_changed = OrderStatusChanged(order_id="T1:entry", status="rejected", reason="x")
    assert status_changed.execution_venue is None


# --- EventType / CRITICAL_EVENT_TYPES (EX-9, fork 2) -------------------------


def test_order_status_changed_event_type_exists_and_is_critical() -> None:
    assert EventType.ORDER_STATUS_CHANGED == "OrderStatusChanged"
    assert EventType.ORDER_STATUS_CHANGED in CRITICAL_EVENT_TYPES


def test_trade_planned_event_type_is_not_critical() -> None:
    """Unchanged by this delivery — TradePlanned rides the normal lane
    (system-design.md §4.4's table), distinct from the venue-level
    OrderStatusChanged."""
    assert EventType.TRADE_PLANNED not in CRITICAL_EVENT_TYPES


def test_order_status_changed_round_trips_through_an_envelope_on_the_critical_lane() -> None:
    payload = OrderStatusChanged(order_id="T1:entry", status="rejected", reason="mode_not_supported")
    envelope = make_envelope(EventType.ORDER_STATUS_CHANGED, payload, symbol="AAPL")
    assert envelope.is_critical is True
    assert envelope.payload["order_id"] == "T1:entry"
    assert envelope.event_type == EventType.ORDER_STATUS_CHANGED


def test_order_status_changed_is_distinguishable_from_trade_planned() -> None:
    """Both can carry the same symbol/moment in a pipeline, but are
    different event types with different payload shapes and different
    lane membership — no overlap in required fields."""
    trade_planned = make_envelope(
        EventType.TRADE_PLANNED, TradePlanned(direction="long", entry=100.0, stop=95.0, size=10), symbol="AAPL"
    )
    order_status_changed = make_envelope(
        EventType.ORDER_STATUS_CHANGED,
        OrderStatusChanged(order_id="T1:entry", status="rejected", reason="mode_not_supported"),
        symbol="AAPL",
    )
    assert trade_planned.event_type != order_status_changed.event_type
    assert trade_planned.is_critical is False
    assert order_status_changed.is_critical is True
    assert "order_id" not in trade_planned.payload
    assert "entry" not in order_status_changed.payload
