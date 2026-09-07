"""VolumeSpikeStrategy tests, in two tiers:

1. Pure functions (match_direction, score_confidence,
   spike_strength_fraction) — no DB, no event loop, no MarketClock.
2. End-to-end evaluate() across a simulated trading day, using real
   MarketClock-anchored ET timestamps (same `_et()` convention as
   test_orb_strategy.py/test_gap_strategy.py) — proves the GATE-stage
   rolling-baseline warm-up, cooldown, absolute-volume floor,
   body-ratio check, and day-rollover state machine, not just the pure
   MATCH/SCORE math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.volume_spike_strategy import (
    DEFAULT_LOOKBACK_BARS,
    DEFAULT_MIN_ABSOLUTE_VOLUME,
    DEFAULT_MIN_BODY_RATIO,
    DEFAULT_SPIKE_RATIO_THRESHOLD,
    DEFAULT_TREND_SCORE_THRESHOLD,
    DEFAULT_VOLUME_REGIME_THRESHOLD,
    VolumeSpikeStrategy,
    default_config,
    match_direction,
    score_confidence,
    spike_strength_fraction,
)


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


# --- Tier 1: pure functions -------------------------------------------------
# candle_high/candle_low chosen so body_ratio comfortably clears
# DEFAULT_MIN_BODY_RATIO (0.3) unless a test is specifically checking that
# gate; candle_volume comfortably clears DEFAULT_MIN_ABSOLUTE_VOLUME (500)
# unless a test is specifically checking that gate.


def test_match_direction_buy_on_bullish_spike_candle():
    assert (
        match_direction(
            volume_ratio=4.0, candle_open=100.0, candle_high=101.2, candle_low=99.8,
            candle_close=101.0, candle_volume=5000,
            trend_score=70.0, volume_regime_score=60.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )
        == "BUY"
    )


def test_match_direction_sell_on_bearish_spike_candle():
    assert (
        match_direction(
            volume_ratio=4.0, candle_open=100.0, candle_high=100.2, candle_low=98.8,
            candle_close=99.0, candle_volume=5000,
            trend_score=30.0, volume_regime_score=60.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )
        == "SELL"
    )


def test_match_direction_none_on_doji_no_net_direction():
    assert (
        match_direction(
            volume_ratio=4.0, candle_open=100.0, candle_high=100.5, candle_low=99.5,
            candle_close=100.0, candle_volume=5000,
            trend_score=90.0, volume_regime_score=90.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )
        is None
    )


def test_match_direction_none_when_not_actually_a_spike():
    assert (
        match_direction(
            volume_ratio=1.2, candle_open=100.0, candle_high=101.2, candle_low=99.8,  # below DEFAULT_SPIKE_RATIO_THRESHOLD (3.0)
            candle_close=101.0, candle_volume=5000,
            trend_score=90.0, volume_regime_score=90.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )
        is None
    )


def test_match_direction_none_when_volume_floor_fails():
    """Participation floor (Market State's volume_regime_score) is
    checked once, direction-agnostic — same convention orb_strategy.py's/
    gap_strategy.py's own volume floor uses. Distinct from the new
    absolute-volume-in-shares floor below."""
    assert (
        match_direction(
            volume_ratio=4.0, candle_open=100.0, candle_high=101.2, candle_low=99.8,
            candle_close=101.0, candle_volume=5000,
            trend_score=90.0, volume_regime_score=0.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )
        is None
    )


def test_match_direction_none_when_absolute_volume_floor_fails():
    """Decision #111, design review §3 — a ratio can look like a spike
    on a thin baseline even when the absolute size is economically
    meaningless. candle_volume below DEFAULT_MIN_ABSOLUTE_VOLUME (500)
    refuses regardless of how strong the ratio is."""
    assert (
        match_direction(
            volume_ratio=10.0, candle_open=100.0, candle_high=101.2, candle_low=99.8,
            candle_close=101.0, candle_volume=200,  # below DEFAULT_MIN_ABSOLUTE_VOLUME (500)
            trend_score=90.0, volume_regime_score=90.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )
        is None
    )


def test_match_direction_none_when_body_ratio_too_thin():
    """Decision #111, design review §3 — a huge-volume, razor-thin-body
    candle (net displacement a tiny fraction of its own range) is closer
    to indecision than conviction, even though close != open technically
    holds. body_ratio = 0.05 / 6.0 ≈ 0.0083, well below
    DEFAULT_MIN_BODY_RATIO (0.3)."""
    assert (
        match_direction(
            volume_ratio=5.0, candle_open=100.0, candle_high=103.0, candle_low=97.0,
            candle_close=100.05, candle_volume=5000,
            trend_score=90.0, volume_regime_score=90.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=DEFAULT_TREND_SCORE_THRESHOLD,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )
        is None
    )


def test_match_direction_rejects_threshold_at_or_below_50():
    """Same guard as orb_strategy.py's/gap_strategy.py's match_direction()
    — decision #99's fix, now via the shared
    scoring_utils.validate_mirror_threshold()."""
    with pytest.raises(ValueError):
        match_direction(
            volume_ratio=4.0, candle_open=100.0, candle_high=100.2, candle_low=98.8,
            candle_close=99.0, candle_volume=5000,
            trend_score=55.0, volume_regime_score=90.0,
            spike_ratio_threshold=DEFAULT_SPIKE_RATIO_THRESHOLD,
            trend_score_threshold=40.0,
            volume_regime_threshold=DEFAULT_VOLUME_REGIME_THRESHOLD,
            min_absolute_volume=DEFAULT_MIN_ABSOLUTE_VOLUME,
            min_body_ratio=DEFAULT_MIN_BODY_RATIO,
        )


def test_spike_strength_fraction_zero_at_threshold():
    assert spike_strength_fraction(3.0, spike_ratio_threshold=3.0) == 0.0


def test_spike_strength_fraction_scales_with_excess():
    assert spike_strength_fraction(6.0, spike_ratio_threshold=3.0) == pytest.approx(1.0)


def test_spike_strength_fraction_never_negative_below_threshold():
    assert spike_strength_fraction(1.0, spike_ratio_threshold=3.0) == 0.0


def test_spike_strength_fraction_honest_zero_on_nonpositive_threshold():
    assert spike_strength_fraction(6.0, spike_ratio_threshold=0.0) == 0.0


def test_score_confidence_clamped_between_0_and_100():
    assert 0.0 <= score_confidence(trend_score=100.0, volume_regime_score=100.0, spike_strength=50.0) <= 100.0
    assert 0.0 <= score_confidence(trend_score=0.0, volume_regime_score=0.0, spike_strength=0.0) <= 100.0


# --- Tier 2: end-to-end evaluate() -------------------------------------------


def _make_market_state(candle_ts: datetime, **overrides) -> MarketState:
    base = dict(
        timeframe="1m",
        candle_ts=candle_ts,
        trend_score=70.0,
        volatility_regime_score=50.0,
        volume_regime_score=60.0,
        vwap_relationship_score=55.0,
        acceleration_score=60.0,
    )
    base.update(overrides)
    return MarketState(**base)


def _make_features(candle_ts: datetime, close: float, open_: float, volume: int, **overrides) -> FeatureSet:
    base = dict(
        timeframe="1m",
        candle_ts=candle_ts,
        close=close,
        open=open_,
        high=max(open_, close) + 0.1,
        low=min(open_, close) - 0.1,
        volume=volume,
        features={},
    )
    base.update(overrides)
    return FeatureSet(**base)


async def _run_warmup(strategy: VolumeSpikeStrategy, symbol: str, session_open, lookback_bars: int = DEFAULT_LOOKBACK_BARS, baseline_volume: int = 1000):
    """Feeds `lookback_bars` flat, average-volume 1m candles starting at
    `session_open`, returns the list of evaluate() results (should all
    be None — still warming up the rolling baseline)."""
    results = []
    for i in range(lookback_bars):
        ts = session_open + timedelta(minutes=i)
        fs = _make_features(ts, close=100.1, open_=100.0, volume=baseline_volume)
        ms = _make_market_state(ts)
        results.append(await strategy.evaluate(symbol, ms, fs, ContextChanged()))
    return results


@pytest.mark.asyncio
async def test_warmup_never_fires():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)  # a real Monday
    results = await _run_warmup(strategy, "TEST", session_open)
    assert all(r is None for r in results)


@pytest.mark.asyncio
async def test_spike_after_warmup_fires_buy_then_respects_cooldown():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", session_open, baseline_volume=1000)

    spike_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(spike_ts, close=101.0, open_=100.0, volume=5000)  # 5x the 1000 baseline
    opp = await strategy.evaluate("TEST", _make_market_state(spike_ts), fs, ContextChanged())

    assert opp is not None
    assert opp.strategy == "Volume Spike"
    assert opp.direction == "BUY"
    assert opp.structural_invalidation == pytest.approx(99.9)  # this candle's own low
    assert opp.structural_target == pytest.approx(101.0 + 2.0 * (101.0 - 99.9))
    assert opp.expected_horizon_minutes == 15  # decision #111 default
    assert opp.evidence["conditions"]["volume_ratio"] == pytest.approx(5.0)

    # Next candle, still elevated volume, same direction: cooldown withholds it.
    next_ts = spike_ts + timedelta(minutes=1)
    fs2 = _make_features(next_ts, close=102.0, open_=101.0, volume=4500)
    opp2 = await strategy.evaluate("TEST", _make_market_state(next_ts), fs2, ContextChanged())
    assert opp2 is None


@pytest.mark.asyncio
async def test_second_independent_spike_fires_again_after_cooldown_elapses():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", session_open, baseline_volume=1000)

    spike_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(spike_ts, close=101.0, open_=100.0, volume=5000)
    first_opp = await strategy.evaluate("TEST", _make_market_state(spike_ts), fs, ContextChanged())
    assert first_opp is not None

    # A few quiet candles pass (cooldown default is 5 minutes) ...
    for i in range(1, 6):
        quiet_ts = spike_ts + timedelta(minutes=i)
        quiet_fs = _make_features(quiet_ts, close=101.0, open_=101.0, volume=900)
        await strategy.evaluate("TEST", _make_market_state(quiet_ts), quiet_fs, ContextChanged())

    later_spike_ts = spike_ts + timedelta(minutes=6)
    later_fs = _make_features(later_spike_ts, close=103.0, open_=101.5, volume=4800)
    second_opp = await strategy.evaluate("TEST", _make_market_state(later_spike_ts), later_fs, ContextChanged())
    assert second_opp is not None  # cooldown elapsed — a genuinely later, independent spike fires


@pytest.mark.asyncio
async def test_outside_regular_session_never_fires():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    premarket_ts = _et(2026, 8, 17, 8, 0)
    fs = _make_features(premarket_ts, close=101.0, open_=100.0, volume=50000)
    opp = await strategy.evaluate("TEST", _make_market_state(premarket_ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_missing_volume_returns_none_honest_absence():
    """A FeatureSet without volume (pre-decision-#99 shape, or an
    aggregated-timeframe FeatureSet that slipped past the timeframe
    check) — can't test a spike without real per-candle volume."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    ts = _et(2026, 8, 17, 9, 30)
    fs = FeatureSet(timeframe="1m", candle_ts=ts, close=100.0, features={})  # no open/high/low/volume
    opp = await strategy.evaluate("TEST", _make_market_state(ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_thin_illiquid_baseline_never_fires_despite_strong_ratio():
    """Decision #111, design review §3 — the review's central worked
    example: an illiquid symbol/quiet stretch where the rolling baseline
    itself is tiny (50 shares/minute), so even a small absolute order
    (200 shares) computes as a strong ratio (4x). Without the absolute
    floor this would previously have fired; with it, it correctly
    doesn't."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", session_open, baseline_volume=50)

    spike_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(spike_ts, close=101.0, open_=100.0, volume=200)  # ratio=4.0, but only 200 shares absolute
    opp = await strategy.evaluate("TEST", _make_market_state(spike_ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_wide_range_thin_body_large_volume_never_fires():
    """Decision #111, design review §3 — a huge-volume candle with a
    razor-thin net body (most of the range is wick, not net
    displacement) is closer to indecision than conviction. Without the
    body-ratio check this would previously have fired on
    close != open alone; with it, it correctly doesn't."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)
    await _run_warmup(strategy, "TEST", session_open, baseline_volume=1000)

    spike_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    fs = _make_features(
        spike_ts, close=100.05, open_=100.0, volume=5000,  # ratio=5.0, but body_ratio ~ 0.008
        high=103.0, low=97.0,
    )
    opp = await strategy.evaluate("TEST", _make_market_state(spike_ts), fs, ContextChanged())
    assert opp is None


@pytest.mark.asyncio
async def test_new_trading_day_resets_baseline_and_cooldown():
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)

    day1_open = _et(2026, 8, 17, 9, 30)  # Monday
    await _run_warmup(strategy, "TEST", day1_open, baseline_volume=1000)
    day1_spike_ts = day1_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    day1_opp = await strategy.evaluate(
        "TEST", _make_market_state(day1_spike_ts),
        _make_features(day1_spike_ts, close=101.0, open_=100.0, volume=5000),
        ContextChanged(),
    )
    assert day1_opp is not None

    day2_open = _et(2026, 8, 18, 9, 30)  # Tuesday — new trading_day, fresh baseline
    day2_results = await _run_warmup(strategy, "TEST", day2_open, baseline_volume=2000)
    assert all(r is None for r in day2_results)  # warming up fresh, not carrying day1's baseline/cooldown

    day2_spike_ts = day2_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    day2_opp = await strategy.evaluate(
        "TEST", _make_market_state(day2_spike_ts),
        _make_features(day2_spike_ts, close=205.0, open_=202.0, volume=10000),  # 5x the 2000 baseline
        ContextChanged(),
    )
    assert day2_opp is not None  # fires again — day1's cooldown/baseline didn't leak across days


@pytest.mark.asyncio
async def test_different_symbols_track_independent_state():
    """One VolumeSpikeStrategy instance serves the whole symbol universe
    (same singleton-with-internal-keying shape as FeatureEngine/
    MarketStateEngine/orb_strategy.py) — a spike on one symbol must not
    affect another's independently-forming baseline."""
    config = default_config(active_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    strategy = VolumeSpikeStrategy(config)
    session_open = _et(2026, 8, 17, 9, 30)

    await _run_warmup(strategy, "AAA", session_open, baseline_volume=1000)
    # BBB has only seen a couple of candles — still warming up.
    for i in range(2):
        ts = session_open + timedelta(minutes=i)
        await strategy.evaluate(
            "BBB", _make_market_state(ts),
            _make_features(ts, close=50.0, open_=50.0, volume=500),
            ContextChanged(),
        )

    spike_ts = session_open + timedelta(minutes=DEFAULT_LOOKBACK_BARS)
    aaa_opp = await strategy.evaluate(
        "AAA", _make_market_state(spike_ts),
        _make_features(spike_ts, close=101.0, open_=100.0, volume=5000),
        ContextChanged(),
    )
    assert aaa_opp is not None  # AAA's baseline fully warmed — spike test applies normally

    bbb_opp = await strategy.evaluate(
        "BBB", _make_market_state(spike_ts),
        _make_features(spike_ts, close=52.0, open_=50.0, volume=5000),
        ContextChanged(),
    )
    assert bbb_opp is None  # BBB never finished warming up (only 2 of 20 bars seen) — honest absence
