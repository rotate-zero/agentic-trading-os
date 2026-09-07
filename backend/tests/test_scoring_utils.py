"""Tests for scoring_utils.py — the shared clamp/trend_magnitude/
threshold-guard extracted from ORB/Gap/Volume Spike (and, once
discovered duplicated there too during this same change, First
Pullback/Reversal). Decision #107/#111.

Deliberately thin: each function is a few lines of domain-free
arithmetic, already exercised indirectly through every strategy's own
match_direction()/score_confidence() tests. These tests exist so the
shared module has its own, direct coverage independent of any one
strategy's behavior.
"""
from __future__ import annotations

import pytest

from app.strategy_engine.scoring_utils import (
    ESTABLISHED_TREND_SCORE_THRESHOLD,
    clamp,
    trend_established_side,
    trend_magnitude,
    validate_mirror_threshold,
)


def test_clamp_passes_through_in_range_values():
    assert clamp(50.0) == 50.0


def test_clamp_bounds_below_zero():
    assert clamp(-10.0) == 0.0


def test_clamp_bounds_above_hundred():
    assert clamp(150.0) == 100.0


def test_clamp_respects_custom_bounds():
    assert clamp(5.0, lo=1.0, hi=3.0) == 3.0


def test_trend_magnitude_zero_at_neutral():
    assert trend_magnitude(50.0) == 0.0


def test_trend_magnitude_maxes_at_extremes():
    assert trend_magnitude(100.0) == 100.0
    assert trend_magnitude(0.0) == 100.0


def test_trend_magnitude_direction_agnostic():
    """Equidistant above/below neutral produce the same magnitude —
    the whole point of this helper (direction lives in match_direction's
    own branching, not in this shared component)."""
    assert trend_magnitude(70.0) == trend_magnitude(30.0)


def test_validate_mirror_threshold_accepts_above_50():
    validate_mirror_threshold(60.0)  # does not raise


def test_validate_mirror_threshold_rejects_exactly_50():
    with pytest.raises(ValueError):
        validate_mirror_threshold(50.0)


def test_validate_mirror_threshold_rejects_below_50():
    with pytest.raises(ValueError):
        validate_mirror_threshold(40.0)


def test_validate_mirror_threshold_message_includes_param_name():
    """Custom param_name (a strategy may call this on a differently-named
    threshold) shows up in the error, not a hardcoded generic name."""
    with pytest.raises(ValueError, match="my_custom_threshold"):
        validate_mirror_threshold(40.0, param_name="my_custom_threshold")


# --- trend_established_side (decision #113) ---------------------------------


def test_trend_established_side_bullish_at_and_above_threshold():
    assert trend_established_side(60.0, threshold=60.0) == "bullish"
    assert trend_established_side(85.0, threshold=60.0) == "bullish"


def test_trend_established_side_bearish_at_and_below_mirror():
    assert trend_established_side(40.0, threshold=60.0) == "bearish"
    assert trend_established_side(15.0, threshold=60.0) == "bearish"


def test_trend_established_side_neutral_strictly_between():
    assert trend_established_side(50.0, threshold=60.0) is None
    assert trend_established_side(40.1, threshold=60.0) is None
    assert trend_established_side(59.9, threshold=60.0) is None


def test_trend_established_side_uses_default_threshold():
    assert trend_established_side(ESTABLISHED_TREND_SCORE_THRESHOLD) == "bullish"


def test_trend_established_side_rejects_invalid_threshold():
    with pytest.raises(ValueError):
        trend_established_side(70.0, threshold=40.0)


def test_established_trend_score_threshold_is_a_valid_mirror_threshold():
    """The shared constant itself must satisfy its own guard — a
    regression here would silently break both reversal_strategy.py and
    vwap_strategy.py's defaults at once."""
    validate_mirror_threshold(ESTABLISHED_TREND_SCORE_THRESHOLD)  # does not raise
