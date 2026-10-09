"""Pure P1 planning contract; no Governor, Event Bus, or database involved."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

import pytest

from app.strategy_engine.base_strategy import Opportunity
from app.trade_planning import PlanningRefusal, ReferenceObservation, TradePlan, plan_entry


NOW = datetime(2026, 10, 9, 14, 32, tzinfo=timezone.utc)


def opportunity(direction: str = "BUY", stop: float = 99.5, target: float = 105.0) -> Opportunity:
    return Opportunity(
        strategy="test", version="1", direction=direction, confidence=0.8,
        structural_invalidation=stop, structural_target=target,
        evidence={}, setup_detected_at=NOW,
    )


def observation(price: float = 100.0, *, observed_at=NOW, exchange_ts=NOW) -> ReferenceObservation:
    return ReferenceObservation(price=price, observed_at=observed_at, exchange_ts=exchange_ts)


def test_long_plan_and_immutable_values() -> None:
    plan = plan_entry("AAPL", opportunity(), observation(), 1000.0, NOW)
    assert isinstance(plan, TradePlan)
    assert (plan.symbol, plan.direction, plan.entry, plan.stop, plan.target, plan.size) == (
        "AAPL", "long", 100.0, 99.5, 105.0, 10,
    )
    assert (plan.planned_risk_usd, plan.r_multiple, plan.target_on_profit_side) == (Decimal("5.0"), 10.0, True)
    assert (plan.origin, plan.max_hold_seconds, plan.corroboration) == ("auto", None, ())
    assert (plan.sizing.method, plan.sizing.fixed_notional_usd) == ("fixed_notional", 1000.0)
    with pytest.raises(FrozenInstanceError):
        plan.size = 11
    with pytest.raises(FrozenInstanceError):
        plan.sizing.fixed_notional_usd = 2000.0


def test_short_plan() -> None:
    plan = plan_entry("MSFT", opportunity("SELL", 101.0, 95.0), observation(), 1000.0, NOW)
    assert isinstance(plan, TradePlan)
    assert (plan.direction, plan.stop, plan.target, plan.size) == ("short", 101.0, 95.0, 10)
    assert (plan.planned_risk_usd, plan.r_multiple) == (Decimal("10.0"), 5.0)


@pytest.mark.parametrize("reference", [None, observation(0.0), observation(-1.0), observation(float("nan")), observation(float("inf")), observation(float("-inf"))])
def test_unavailable_reference_precedes_bad_geometry(reference: ReferenceObservation | None) -> None:
    assert plan_entry("AAPL", opportunity(stop=-1.0, target=-1.0), reference, 1000.0, NOW) == PlanningRefusal("no_reference_price")


@pytest.mark.parametrize("direction,stop,target", [
    ("BUY", 0.0, 90.0), ("BUY", -1.0, 90.0), ("BUY", 100.0, 90.0),
    ("BUY", 100.5, 90.0), ("BUY", float("nan"), 90.0), ("BUY", float("inf"), 90.0),
    ("SELL", 100.0, 110.0), ("SELL", 99.5, 110.0), ("SELL", float("-inf"), 110.0),
])
def test_invalid_stop_precedes_quantity_and_target(direction: str, stop: float, target: float) -> None:
    assert plan_entry("AAPL", opportunity(direction, stop, target), observation(100.0), 99.0, NOW) == PlanningRefusal("invalid_stop_geometry")


@pytest.mark.parametrize("direction,stop,target", [
    ("BUY", 99.0, 100.0), ("BUY", 99.0, 90.0), ("BUY", 99.0, 0.0),
    ("BUY", 99.0, float("nan")), ("BUY", 99.0, float("inf")),
    ("SELL", 101.0, 100.0), ("SELL", 101.0, 110.0), ("SELL", 101.0, -1.0),
    ("SELL", 101.0, float("-inf")),
])
def test_invalid_target_after_quantity(direction: str, stop: float, target: float) -> None:
    candidate = opportunity(direction, stop, target)
    assert plan_entry("AAPL", candidate, observation(), 99.0, NOW) == PlanningRefusal("notional_below_one_share")
    assert plan_entry("AAPL", candidate, observation(), 1000.0, NOW) == PlanningRefusal("invalid_target_geometry")


@pytest.mark.parametrize("price,stop,target,expected", [
    (333.33, 332.0, 340.0, 3), (500.0, 499.0, 510.0, 2),
    (1000.0, 999.0, 1010.0, 1), (1000.01, 999.0, 1010.0, None),
    (0.07, 0.06, 0.08, 14285),
])
def test_quantity_boundaries(price: float, stop: float, target: float, expected: int | None) -> None:
    result = plan_entry("AAPL", opportunity(stop=stop, target=target), observation(price), 1000.0, NOW)
    if expected is None:
        assert result == PlanningRefusal("notional_below_one_share")
    else:
        assert isinstance(result, TradePlan)
        assert result.size == expected
        assert result.size == math.floor(1000.0 / price)


def test_decimal_risk_and_r_after_valid_geometry() -> None:
    plan = plan_entry("AAPL", opportunity(stop=0.2, target=0.4), observation(0.3), 0.9, NOW)
    assert isinstance(plan, TradePlan)
    assert plan.size == 3
    assert plan.planned_risk_usd == Decimal("0.3")
    assert plan.r_multiple == 1.0


def test_separate_local_and_exchange_timestamps() -> None:
    local = NOW - timedelta(seconds=1)
    source = NOW - timedelta(minutes=20)
    plan = plan_entry("AAPL", opportunity(), observation(observed_at=local, exchange_ts=source), 1000.0, NOW)
    assert isinstance(plan, TradePlan)
    assert (plan.planned_at, plan.reference_observed_at, plan.reference_exchange_ts) == (NOW, local, source)
    unknown = plan_entry("AAPL", opportunity(), observation(observed_at=local, exchange_ts=None), 1000.0, NOW)
    assert isinstance(unknown, TradePlan)
    assert unknown.reference_exchange_ts is None


@pytest.mark.parametrize("notional", [0.0, -1.0, float("nan"), float("inf")])
def test_bad_configured_notional_is_technical_error(notional: float) -> None:
    with pytest.raises(ValueError, match="fixed_notional_usd"):
        plan_entry("AAPL", opportunity(), observation(), notional, NOW)


def test_valid_input_sizing_matches_legacy_rule_five_grid() -> None:
    # Test-local copy of the legacy Governor formula, across both directions.
    for direction in ("BUY", "SELL"):
        for notional in (10.0, 1000.0, 1234.56):
            for price in (0.07, 0.3, 1.01, 19.99, 100.0, 333.33, 500.0, 1000.0):
                stop = price * (0.9 if direction == "BUY" else 1.1)
                target = price * (1.1 if direction == "BUY" else 0.9)
                result = plan_entry("AAPL", opportunity(direction, stop, target), observation(price), notional, NOW)
                legacy_size = math.floor(notional / price)
                if legacy_size < 1:
                    assert result == PlanningRefusal("notional_below_one_share")
                else:
                    assert isinstance(result, TradePlan)
                    assert result.size == legacy_size
