"""Tests for gate_conditions.py — the declarative StrategyConfig.gate_
conditions registry/check/validation (§2b, decision #117).

Deliberately thin and DB-free: `gate_conditions_satisfied()` and
`validate_gate_conditions()` are pure functions over a real MarketClock
(no mock — MarketClock itself is already clock-free/pure when given an
explicit `ts`, so there is no reason to stand in for it). Scheduler-level
integration (registration-time validation wired into __init__, the
per-strategy skip inside _on_market_state_changed) is covered separately
in test_strategy_scheduler.py.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.strategy_engine.gate_conditions import gate_conditions_satisfied, validate_gate_conditions

_ET = ZoneInfo("America/New_York")

# Monday 2026-08-10 — a real, non-holiday regular trading weekday (same
# calendar date test_strategy_scheduler.py's own _TS already uses).
_PRE_MARKET_ET = datetime(2026, 8, 10, 7, 0, tzinfo=_ET)  # 07:00 ET — pre-market
_REGULAR_ET = datetime(2026, 8, 10, 10, 0, tzinfo=_ET)  # 10:00 ET — regular session (OPEN)
_AFTER_HOURS_ET = datetime(2026, 8, 10, 17, 0, tzinfo=_ET)  # 17:00 ET — after-hours
_WEEKEND_ET = datetime(2026, 8, 8, 10, 0, tzinfo=_ET)  # Saturday — CLOSED


# --- gate_conditions_satisfied() ---------------------------------------


def test_empty_gate_conditions_always_satisfied_regardless_of_session():
    """StrategyConfig.gate_conditions defaults to {} (base_strategy.py
    §3) — empty means no restriction declared, never "block
    everything." True even at a timestamp no real session condition
    would pass."""
    assert gate_conditions_satisfied({}, _PRE_MARKET_ET) is True
    assert gate_conditions_satisfied({}, _AFTER_HOURS_ET) is True
    assert gate_conditions_satisfied({}, _WEEKEND_ET) is True


def test_session_regular_satisfied_during_regular_session():
    assert gate_conditions_satisfied({"session": "regular"}, _REGULAR_ET) is True


def test_session_regular_not_satisfied_pre_market():
    assert gate_conditions_satisfied({"session": "regular"}, _PRE_MARKET_ET) is False


def test_session_regular_not_satisfied_after_hours():
    assert gate_conditions_satisfied({"session": "regular"}, _AFTER_HOURS_ET) is False


def test_session_regular_not_satisfied_weekend_closed():
    assert gate_conditions_satisfied({"session": "regular"}, _WEEKEND_ET) is False


# --- validate_gate_conditions() ----------------------------------------


def test_validate_accepts_empty_gate_conditions():
    validate_gate_conditions("Stub", {})  # must not raise


def test_validate_accepts_the_only_recognized_condition():
    validate_gate_conditions("Stub", {"session": "regular"})  # must not raise


def test_validate_rejects_unrecognized_key():
    """The illustrative strategy-engine-design.md §3 schema comment
    shows "vix_min": 20 — confirmed (decision #117) to correspond to no
    real field anywhere in this codebase. Any strategy that ever
    declared it must fail loudly, not silently pass through."""
    with pytest.raises(ValueError, match="vix_min"):
        validate_gate_conditions("Stub", {"vix_min": 20})


def test_validate_rejects_unrecognized_value_for_a_recognized_key():
    """"session" is recognized; "pre_market" is a real Session member
    (market_clock.py) but not a value this build's "regular" check
    implements — must fail the same way an unrecognized key does, not
    silently pass through as if it were "regular" or always-true."""
    with pytest.raises(ValueError, match="pre_market"):
        validate_gate_conditions("Stub", {"session": "pre_market"})


def test_validate_error_names_the_strategy():
    with pytest.raises(ValueError, match="MyStrategy"):
        validate_gate_conditions("MyStrategy", {"vix_min": 20})
