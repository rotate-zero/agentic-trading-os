"""
Tests for feature_engine/historical.py — pure, stateless, no DB or Event
Bus needed at all: compute_series() takes a plain list of Candle objects
directly, so every test here just builds that list by hand.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.feature_engine.historical import compute_series
from app.services.candle_aggregator import Candle

_ET = ZoneInfo("America/New_York")


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


def _candle(ts: datetime, close: float, *, high: float | None = None, low: float | None = None, volume: int = 10) -> Candle:
    return Candle(
        timeframe="1m", open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=volume, candle_ts=ts,
    )


def test_sma_series_matches_pure_sma_at_each_valid_point():
    base = _et(2026, 8, 11, 9, 30)
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    candles = [_candle(base + timedelta(minutes=i), c) for i, c in enumerate(closes)]

    series = compute_series(candles, sma_periods=[3], ema_periods=[], ema_seed_multiplier=5)

    # period=3: no point for the first 2 candles, then mean of trailing 3.
    assert len(series["sma_3"]) == 3
    assert series["sma_3"][0]["value"] == 2.0  # mean(1,2,3)
    assert series["sma_3"][1]["value"] == 3.0  # mean(2,3,4)
    assert series["sma_3"][2]["value"] == 4.0  # mean(3,4,5)
    assert series["sma_3"][0]["candle_ts"] == candles[2].candle_ts.isoformat()  # aligned to the 3rd candle, not the 1st


def test_ema_series_warms_up_later_than_sma_at_the_same_period():
    base = _et(2026, 8, 11, 9, 30)
    closes = [float(i) for i in range(1, 11)]  # 10 candles
    candles = [_candle(base + timedelta(minutes=i), c) for i, c in enumerate(closes)]

    series = compute_series(candles, sma_periods=[2], ema_periods=[2], ema_seed_multiplier=3)

    assert len(series["sma_2"]) == 9  # ready from the 2nd candle
    assert len(series["ema_2"]) == 5  # needs period*multiplier=6 candles — ready from the 6th through 10th of 10
    # Hand-verified in test_feature_engine.py's own
    # test_ema_matches_hand_computed_recursion for the same window shape.
    assert series["ema_2"][0]["value"] == 5.5


def test_sma_slope_series_appears_alongside_sma_series_once_warmed_up():
    """
    Confirmed decision #83. period=3 -> sma_3 needs 3 closes, sma_3_slope
    needs 2*3-1=5 — same hand-computed shape as
    test_sma_slope_fits_ols_over_its_own_trailing_values in
    test_feature_engine.py: slope=1.0, r2=1.0, current value=4.0 (the 5th
    candle's sma_3), slope_pct=25.0.
    """
    base = _et(2026, 8, 11, 9, 30)
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    candles = [_candle(base + timedelta(minutes=i), c) for i, c in enumerate(closes)]

    series = compute_series(candles, sma_periods=[3], ema_periods=[], ema_seed_multiplier=5)

    assert len(series["sma_3"]) == 3  # unchanged from the pre-decision-#83 test above
    assert len(series["sma_3_slope"]) == 1  # only the 5th candle has enough history (needs 5)
    assert series["sma_3_slope"][0]["value"] == 1.0
    assert series["sma_3_r2"][0]["value"] == 1.0
    assert series["sma_3_slope_pct"][0]["value"] == 25.0
    assert series["sma_3_slope"][0]["candle_ts"] == candles[4].candle_ts.isoformat()  # aligned to the 5th candle


def test_ema_slope_series_matches_hand_computed_value():
    """period=2, seed_multiplier=3 -> ema_2_slope needs 2*3+2-1=7 closes — same
    hand-computed window as test_ema_slope_matches_hand_computed_series in
    test_feature_engine.py: slope=1.0, current value=5.5."""
    base = _et(2026, 8, 11, 9, 30)
    closes = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    candles = [_candle(base + timedelta(minutes=i), c) for i, c in enumerate(closes)]

    series = compute_series(candles, sma_periods=[], ema_periods=[2], ema_seed_multiplier=3)

    assert len(series["ema_2_slope"]) == 1  # only the 7th candle has enough history
    assert series["ema_2_slope"][0]["value"] == 1.0
    assert series["ema_2_slope_pct"][0]["value"] == pytest.approx(1.0 / 5.5 * 100)


def test_vwap_series_excludes_pre_market_candles():
    candles = [
        _candle(_et(2026, 8, 11, 8, 0), 50.0),  # pre-market
        _candle(_et(2026, 8, 11, 9, 30), 100.0),  # regular session open
    ]
    series = compute_series(candles, sma_periods=[], ema_periods=[], ema_seed_multiplier=5)
    assert len(series["vwap"]) == 1
    assert series["vwap"][0]["value"] == 100.0


def test_vwap_series_resets_across_a_session_boundary():
    candles = [
        _candle(_et(2026, 8, 11, 9, 30), 100.0),
        _candle(_et(2026, 8, 11, 9, 31), 200.0),
        _candle(_et(2026, 8, 12, 9, 30), 300.0),  # next day — a new session entirely
    ]
    series = compute_series(candles, sma_periods=[], ema_periods=[], ema_seed_multiplier=5)
    assert [p["value"] for p in series["vwap"]] == [100.0, 150.0, 300.0]  # NOT blended with day 1


def test_vwap_series_weights_by_typical_price_not_just_close():
    """A single bar with real intrabar range — proves the engine-facing
    tests (which all use flat open=high=low=close candles for hand-
    verifiability) aren't hiding a bug where H/L never actually gets used."""
    candles = [_candle(_et(2026, 8, 11, 9, 30), close=10.0, high=12.0, low=8.0, volume=100)]
    series = compute_series(candles, sma_periods=[], ema_periods=[], ema_seed_multiplier=5)
    assert series["vwap"][0]["value"] == 10.0  # typical_price(12,8,10) == 10, same as close here by construction

    candles2 = [_candle(_et(2026, 8, 11, 9, 30), close=8.0, high=14.0, low=8.0, volume=100)]
    series2 = compute_series(candles2, sma_periods=[], ema_periods=[], ema_seed_multiplier=5)
    assert series2["vwap"][0]["value"] == 10.0  # typical_price(14,8,8) == 10, genuinely != close (8.0)


def test_compute_series_returns_empty_list_not_missing_key_when_never_warmed_up():
    candles = [_candle(_et(2026, 8, 11, 9, 30), 100.0)]
    series = compute_series(candles, sma_periods=[50], ema_periods=[20], ema_seed_multiplier=5)
    assert series["sma_50"] == []
    assert series["ema_20"] == []
    assert series["vwap"] == [{"candle_ts": candles[0].candle_ts.isoformat(), "value": 100.0}]
