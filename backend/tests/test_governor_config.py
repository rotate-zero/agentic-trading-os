"""
Tests for the three execution-authorizer limits appended to Settings
(decision #171, scope item 5) — each must be
validated positive at startup (AC #16).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_execution_limits_have_the_documented_defaults() -> None:
    settings = Settings()
    assert settings.execution_max_concurrent_positions == 1
    assert settings.execution_fixed_notional_usd == 1000.0
    assert settings.execution_daily_loss_cap_usd == 100.0


@pytest.mark.parametrize(
    "field",
    ["execution_max_concurrent_positions", "execution_fixed_notional_usd", "execution_daily_loss_cap_usd"],
)
@pytest.mark.parametrize("bad_value", [0, -1, -0.01])
def test_execution_limits_reject_non_positive_values(field: str, bad_value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: bad_value})


def test_execution_limits_accept_a_positive_override() -> None:
    settings = Settings(
        execution_max_concurrent_positions=3,
        execution_fixed_notional_usd=500.0,
        execution_daily_loss_cap_usd=250.0,
    )
    assert settings.execution_max_concurrent_positions == 3
    assert settings.execution_fixed_notional_usd == 500.0
    assert settings.execution_daily_loss_cap_usd == 250.0
