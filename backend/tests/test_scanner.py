"""
ActivityScorer unit tests — pure function, hand-built FeatureSet
fixtures, no DB/event bus involved. Same style
tests/test_feature_engine.py already uses for indicators/atr.py and
indicators/rvol.py's own pure-math tests. Live-pipeline behavior against
a real running server is scripts/test_scanner_pipeline.py's job, not
this file's — see that script's docstring for why the two are
deliberately separate.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.scanner.scorer import score_symbol
from app.schemas.events.features import FeatureSet

_WEIGHTS = dict(weight_rvol=1.0, weight_gap=1.0, weight_session_change=1.0)


def _feature_set(features: dict[str, float]) -> FeatureSet:
    return FeatureSet(timeframe="1m", candle_ts=datetime(2026, 8, 27, tzinfo=timezone.utc), close=100.0, features=features)


def test_score_normalizes_gap_and_session_change_by_atr_pct_when_available():
    fs = _feature_set({"rvol": 2.0, "gap_pct": 4.0, "session_pct_change": -2.0, "atr_14_pct": 2.0})
    result = score_symbol("AAPL", fs, **_WEIGHTS)

    # rvol contributes raw (2.0); gap and session_change each get divided
    # by atr_14_pct (2.0) before being summed — 2.0 + (4.0/2.0) + (2.0/2.0) = 5.0
    assert result.score == 5.0
    assert result.inputs_available == 3


def test_score_falls_back_to_raw_value_when_atr_pct_missing():
    fs = _feature_set({"rvol": 1.0, "gap_pct": 3.0, "session_pct_change": 1.0})
    result = score_symbol("AAPL", fs, **_WEIGHTS)

    # No atr_14_pct at all (cold start) — gap/session_change used raw, not divided.
    assert result.score == 5.0
    assert result.inputs_available == 3


def test_score_skips_missing_rvol_rather_than_treating_it_as_zero():
    fs = _feature_set({"gap_pct": 2.0, "session_pct_change": 2.0, "atr_14_pct": 2.0})
    result = score_symbol("AAPL", fs, **_WEIGHTS)

    assert result.score == 2.0  # (2.0/2.0) + (2.0/2.0), no rvol term at all
    assert result.inputs_available == 2


def test_score_is_zero_with_no_inputs_available_when_nothing_has_computed_yet():
    fs = _feature_set({})
    result = score_symbol("AAPL", fs, **_WEIGHTS)

    assert result.score == 0.0
    assert result.inputs_available == 0  # caller's job to treat this as "exclude," not "flat"


def test_score_applies_weights_per_input_independently():
    fs = _feature_set({"rvol": 3.0, "gap_pct": 4.0, "atr_14_pct": 2.0})
    result = score_symbol("AAPL", fs, weight_rvol=2.0, weight_gap=0.5, weight_session_change=1.0)

    # (2.0 * 3.0) + (0.5 * (4.0/2.0)) = 6.0 + 1.0 = 7.0
    assert result.score == 7.0


def test_score_uses_absolute_value_for_gap_and_session_change_not_signed():
    fs = _feature_set({"gap_pct": -6.0, "session_pct_change": -4.0, "atr_14_pct": 2.0})
    result = score_symbol("AAPL", fs, **_WEIGHTS)

    # A large NEGATIVE gap/move is still activity worth surfacing —
    # (|-6|/2) + (|-4|/2) = 3.0 + 2.0 = 5.0, not a negative or near-zero score.
    assert result.score == 5.0
