"""base_strategy.py tests — the ScheduleTrigger factories, StrategyConfig/
Opportunity construction, and the Strategy ABC's contract. No DB, no
event loop; everything here is a plain object/pydantic-model test.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.strategy_engine.base_strategy import (
    Opportunity,
    ScheduleTrigger,
    Strategy,
    StrategyConfig,
    after_time,
    every_candle,
    on_event,
)


def test_every_candle_defaults_to_1m():
    trigger = every_candle()
    assert trigger == ScheduleTrigger(kind="every_candle", timeframe="1m")


def test_every_candle_accepts_explicit_timeframe():
    trigger = every_candle(timeframe="5m")
    assert trigger.timeframe == "5m"


def test_after_time_with_and_without_until():
    open_ended = after_time("09:35")
    assert open_ended.at == "09:35"
    assert open_ended.until is None

    windowed = after_time("09:30", until="09:45")
    assert windowed.at == "09:30"
    assert windowed.until == "09:45"


def test_on_event_carries_event_name():
    trigger = on_event("VolumeSpike")
    assert trigger.kind == "on_event"
    assert trigger.event_name == "VolumeSpike"


def test_schedule_trigger_is_frozen():
    trigger = every_candle()
    with pytest.raises(Exception):  # noqa: B017 — dataclasses.FrozenInstanceError, not worth importing just for this
        trigger.timeframe = "5m"  # type: ignore[misc]


def test_strategy_config_defaults():
    config = StrategyConfig(
        strategy_name="ORB",
        version="orb_v1",
        params={"or_minutes": 15},
        active_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert config.gate_conditions == {}
    assert config.allows_waiting is False
    assert config.active_to is None
    assert config.rationale == ""


def test_opportunity_has_no_symbol_field():
    """Deliberate — see base_strategy.py's Opportunity docstring. Symbol
    lives on the EventEnvelope once this is published, same convention
    FeatureSet/MarketState already use."""
    assert "symbol" not in Opportunity.model_fields


def test_opportunity_construction_round_trips():
    now = datetime(2026, 8, 17, 13, 45, tzinfo=timezone.utc)
    opp = Opportunity(
        strategy="ORB",
        version="orb_v1",
        direction="BUY",
        confidence=67.0,
        structural_invalidation=100.0,
        structural_target=106.0,
        evidence={"conditions": {}, "reason": "test", "basis": "closed"},
        setup_detected_at=now,
    )
    assert opp.status == "actionable"  # v1 default (§8)
    assert opp.wait_reason is None
    assert opp.confirmed_at is None


def test_strategy_is_abstract():
    with pytest.raises(TypeError):
        Strategy(StrategyConfig(  # type: ignore[abstract]
            strategy_name="X", version="x_v1", params={},
            active_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))
