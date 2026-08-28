"""
Historical Feature series — Stage 1 of the chart migration (confirmed
decision #54).

FeatureEngine (engine.py) is a LIVE, event-driven, stateful process — by
design it only ever knows the CURRENT/latest value per (symbol, timeframe)
(see its `_latest` dict). That's the right shape for the live engine, and
for GET /intelligence/state's snapshot use case (the Feature Engine panel,
which only ever shows "right now"). But it means a CHART — which needs a
value for every visible candle to draw a continuous line, not just the
newest one — can't be served by that snapshot at all. This module is what
closes that gap.

Deliberately NOT a method on FeatureEngine, and deliberately stateless:
this walks a batch of already-fetched candles once, per request, and holds
no memory between calls — a different shape from the live engine's
persistent, restart-safe, event-driven state entirely on purpose. It reuses
the EXACT same pure functions the live path uses — sma(), ema(),
typical_price(), vwap_from_accumulator() — so the two computations cannot
silently diverge from each other; only the ORCHESTRATION differs (a single
forward walk over history here, vs. an incremental per-event update there).

Warm-up behavior matches the existing frontend indicator files
(frontend/src/indicators/sma.ts et al.) on purpose, not by coincidence: the
first `period - 1` (SMA) / `period * seed_multiplier - 1` (EMA) candles in
whatever range is requested simply have no point in the returned series,
the same way sma.ts's own loop starts at `i = period - 1` rather than
padding the front with nulls. No extra lookback is fetched beyond the
requested range to pre-warm the very first point — a possible future
enhancement (flagged, not built), not a gap introduced here that the
existing frontend didn't already have.
"""
from __future__ import annotations

from app.core.market_clock import get_market_clock
from app.feature_engine.indicators import ema, ema_slope, sma, sma_slope, typical_price, vwap_from_accumulator
from app.services.candle_aggregator import Candle


def compute_series(
    candles: list[Candle],
    sma_periods: list[int],
    ema_periods: list[int],
    ema_seed_multiplier: int,
) -> dict[str, list[dict]]:
    """
    `candles` must be chronological (ascending candle_ts) — the same order
    candle_store.get_recorded_candles()/candle_aggregator.aggregate_from_recorded()
    already return, so callers don't need to sort before passing in.

    Returns {"sma_9": [{"candle_ts": iso, "value": ...}, ...], "ema_20": [...],
    "vwap": [...]} — the same per-key convention FeatureSet.features already
    uses (decision #50's D1: flat, not nested), so the frontend's
    lookup-by-key doesn't need a second convention to learn.

    `sma_{period}_slope`/`_r2`/`_slope_pct`/`_slope_angle` and the `ema_`
    equivalents (confirmed decision #83) ride the SAME forward walk as
    `sma_{period}`/`ema_{period}` above, reusing `sma_slope()`/`ema_slope()`
    verbatim at every step — same "reuse the exact same pure functions the
    live path uses" guarantee this module's own docstring already makes
    for `sma`/`ema`/`typical_price`/`vwap_from_accumulator`, just extended
    to the two newer functions rather than a second, parallel history walk.
    """
    series: dict[str, list[dict]] = {}

    closes_so_far: list[float] = []
    sma_points: dict[int, list[dict]] = {p: [] for p in sma_periods}
    ema_points: dict[int, list[dict]] = {p: [] for p in ema_periods}
    # One points-list PER slope output key (not just per period) — slope,
    # r2, slope_pct, and slope_angle each warm up together (sma_slope()/
    # ema_slope() return all four keys at once, or none), but keeping them
    # as separate series-dict entries matches how the live engine already
    # publishes them as separate FeatureSet.features keys, so the chart's
    # per-key lookup doesn't need a second, nested shape just for this
    # family.
    sma_slope_points: dict[str, list[dict]] = {}
    ema_slope_points: dict[str, list[dict]] = {}
    for c in candles:
        closes_so_far.append(c.close)
        for period in sma_periods:
            value = sma(closes_so_far, period)
            if value is not None:
                sma_points[period].append({"candle_ts": c.candle_ts.isoformat(), "value": round(value, 6)})
            for key, slope_value in sma_slope(closes_so_far, period).items():
                sma_slope_points.setdefault(key, []).append({"candle_ts": c.candle_ts.isoformat(), "value": slope_value})
        for period in ema_periods:
            value = ema(closes_so_far, period, ema_seed_multiplier)
            if value is not None:
                ema_points[period].append({"candle_ts": c.candle_ts.isoformat(), "value": round(value, 6)})
            for key, slope_value in ema_slope(closes_so_far, period, ema_seed_multiplier).items():
                ema_slope_points.setdefault(key, []).append({"candle_ts": c.candle_ts.isoformat(), "value": slope_value})
    for period, points in sma_points.items():
        series[f"sma_{period}"] = points
    for period, points in ema_points.items():
        series[f"ema_{period}"] = points
    series.update(sma_slope_points)
    series.update(ema_slope_points)

    # VWAP: session-reset accumulator, walked forward once — mirrors
    # engine.py's _update_vwap() live logic exactly (same regular-session
    # gating, same reset-on-new-session-start detection), just orchestrated
    # as a single loop here instead of a per-event callback carrying state
    # across calls.
    vwap_points: list[dict] = []
    clock = get_market_clock()
    session_start = None
    cumulative_pv = 0.0
    cumulative_volume = 0
    for c in candles:
        if not clock.is_regular_session(c.candle_ts):
            continue
        bounds = clock.session_bounds(c.candle_ts)
        if bounds is None:  # pragma: no cover — is_regular_session() true implies bounds exist
            continue
        this_session_start, _session_end = bounds
        if session_start is None or this_session_start != session_start:
            session_start = this_session_start
            cumulative_pv = 0.0
            cumulative_volume = 0
        cumulative_pv += typical_price(c.high, c.low, c.close) * c.volume
        cumulative_volume += c.volume
        value = vwap_from_accumulator(cumulative_pv, cumulative_volume)
        if value is not None:
            vwap_points.append({"candle_ts": c.candle_ts.isoformat(), "value": round(value, 6)})
    series["vwap"] = vwap_points

    return series
