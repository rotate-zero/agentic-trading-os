"""Market State scoring tests — pure functions, no DB, no event loop."""
from __future__ import annotations

import pytest

from app.market_state_engine.scoring import (
    ACCELERATION_RATE_CAP,
    CROSS_SYMBOL_DIFF_CAP,
    IWM_CONFIRMATION_DEVIATION_CAP,
    TREND_ALIGNMENT_SPREAD_CAP,
    TREND_ANGLE_CAP_DEGREES,
    VOLATILITY_PCT_CEILING,
    VOLATILITY_PCT_FLOOR,
    VOLUME_RVOL_CEILING,
    VWAP_PCT_CAP,
    acceleration_score,
    iwm_confirmation_score,
    qqq_leadership_score,
    risk_on_score,
    trend_alignment_score,
    trend_score,
    volatility_regime_score,
    volume_regime_score,
    vwap_relationship_score,
)


def test_trend_score_neutral_at_zero_slope():
    assert trend_score(0.0) == 50.0


def test_trend_score_saturates_at_cap():
    assert trend_score(TREND_ANGLE_CAP_DEGREES) == 100.0
    assert trend_score(-TREND_ANGLE_CAP_DEGREES) == 0.0


def test_trend_score_clamps_beyond_cap():
    assert trend_score(TREND_ANGLE_CAP_DEGREES * 5) == 100.0
    assert trend_score(-TREND_ANGLE_CAP_DEGREES * 5) == 0.0


def test_volatility_regime_score_floor_and_ceiling():
    assert volatility_regime_score(VOLATILITY_PCT_FLOOR) == 0.0
    assert volatility_regime_score(VOLATILITY_PCT_CEILING) == 100.0


def test_volatility_regime_score_clamps_below_floor():
    assert volatility_regime_score(0.0) == 0.0


def test_volume_regime_score_zero_and_ceiling():
    assert volume_regime_score(0.0) == 0.0
    assert volume_regime_score(VOLUME_RVOL_CEILING) == 100.0


def test_volume_regime_score_clamps_above_ceiling():
    assert volume_regime_score(VOLUME_RVOL_CEILING * 10) == 100.0


def test_vwap_relationship_score_neutral_at_vwap():
    assert vwap_relationship_score(100.0, 100.0) == 50.0


def test_vwap_relationship_score_guards_zero_vwap():
    assert vwap_relationship_score(100.0, 0.0) == 50.0


def test_vwap_relationship_score_saturates_at_cap():
    above = 100.0 * (1 + VWAP_PCT_CAP / 100.0)
    below = 100.0 * (1 - VWAP_PCT_CAP / 100.0)
    assert vwap_relationship_score(above, 100.0) == pytest.approx(100.0)
    assert vwap_relationship_score(below, 100.0) == pytest.approx(0.0)


def test_acceleration_score_none_without_elapsed_time():
    assert acceleration_score(80.0, 50.0, 0.0) is None
    assert acceleration_score(80.0, 50.0, -1.0) is None


def test_acceleration_score_neutral_when_trend_unchanged():
    assert acceleration_score(60.0, 60.0, 5.0) == 50.0


def test_acceleration_score_saturates_at_rate_cap():
    # trend_score swinging its full 0-100 range in exactly the calibrated
    # window saturates the score.
    elapsed = 1.0
    delta = ACCELERATION_RATE_CAP * elapsed
    assert acceleration_score(50.0 + delta, 50.0, elapsed) == pytest.approx(100.0)
    assert acceleration_score(50.0 - delta, 50.0, elapsed) == pytest.approx(0.0)


# --- Cross-symbol (CrossSymbolState, decision #91 §4, this build #97) -------


def test_trend_alignment_score_full_agreement():
    assert trend_alignment_score(70.0, 70.0, 70.0) == 100.0


def test_trend_alignment_score_saturates_at_spread_cap():
    lo = 50.0
    hi = lo + TREND_ALIGNMENT_SPREAD_CAP
    assert trend_alignment_score(hi, lo, lo) == pytest.approx(0.0)


def test_trend_alignment_score_clamps_beyond_spread_cap():
    assert trend_alignment_score(100.0, 0.0, 50.0) == 0.0


def test_trend_alignment_score_order_independent():
    # spread is max-min — which of the three holds which value shouldn't matter.
    assert trend_alignment_score(80.0, 60.0, 70.0) == trend_alignment_score(60.0, 80.0, 70.0)


def test_risk_on_score_neutral_when_growth_matches_broad_market():
    assert risk_on_score(spy_direction_score=60.0, qqq_direction_score=60.0, iwm_direction_score=60.0) == 50.0


def test_risk_on_score_above_neutral_when_growth_leads():
    # QQQ/IWM stronger than SPY -> risk-on -> above 50.
    assert risk_on_score(spy_direction_score=50.0, qqq_direction_score=70.0, iwm_direction_score=70.0) > 50.0


def test_risk_on_score_below_neutral_when_growth_lags():
    assert risk_on_score(spy_direction_score=70.0, qqq_direction_score=50.0, iwm_direction_score=50.0) < 50.0


def test_risk_on_score_saturates_at_diff_cap():
    spy = 50.0
    growth = spy + CROSS_SYMBOL_DIFF_CAP
    assert risk_on_score(spy_direction_score=spy, qqq_direction_score=growth, iwm_direction_score=growth) == pytest.approx(100.0)


def test_qqq_leadership_score_neutral_when_matched():
    assert qqq_leadership_score(spy_direction_score=55.0, qqq_direction_score=55.0) == 50.0


def test_qqq_leadership_score_saturates_at_diff_cap():
    spy = 50.0
    qqq = spy + CROSS_SYMBOL_DIFF_CAP
    assert qqq_leadership_score(spy_direction_score=spy, qqq_direction_score=qqq) == pytest.approx(100.0)
    qqq_lagging = spy - CROSS_SYMBOL_DIFF_CAP
    assert qqq_leadership_score(spy_direction_score=spy, qqq_direction_score=qqq_lagging) == pytest.approx(0.0)


def test_qqq_leadership_score_clamps_beyond_diff_cap():
    assert qqq_leadership_score(spy_direction_score=0.0, qqq_direction_score=100.0) == 100.0


def test_iwm_confirmation_score_full_confirmation():
    # IWM sitting exactly at the SPY/QQQ average -> full confirmation.
    assert iwm_confirmation_score(spy_direction_score=60.0, qqq_direction_score=40.0, iwm_direction_score=50.0) == 100.0


def test_iwm_confirmation_score_saturates_at_deviation_cap():
    broad_avg = 50.0
    iwm = broad_avg + IWM_CONFIRMATION_DEVIATION_CAP
    assert iwm_confirmation_score(spy_direction_score=50.0, qqq_direction_score=50.0, iwm_direction_score=iwm) == pytest.approx(0.0)


def test_iwm_confirmation_score_clamps_beyond_deviation_cap():
    assert iwm_confirmation_score(spy_direction_score=50.0, qqq_direction_score=50.0, iwm_direction_score=100.0) == 0.0
