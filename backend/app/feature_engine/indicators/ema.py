"""
Exponential Moving Average — pure math only, same posture as every file in
this package (see indicators/__init__.py's own docstring for why it's a
package, not one file).
"""
from __future__ import annotations

import math


def ema(closes: list[float], period: int, seed_multiplier: int) -> float | None:
    """
    Exponential Moving Average over the last `period * seed_multiplier`
    closes — resolves confirmed decision #52's D3 (full-window recompute vs.
    true incremental recursion).

    EMA is mathematically a recursion over ALL history back to inception —
    unlike SMA, there's no exact finite window. What this function does
    instead: seed with the SMA of the OLDEST `period` closes in a bounded
    window, then apply the standard recursion (`price*k + prev*(1-k)`,
    `k = 2/(period+1)`) forward through the rest of that window. This is
    the exact same algorithm frontend/src/indicators/ema.ts already uses
    (seed with SMA of the first `period` closes, then recurse) — the only
    difference is bound: the frontend recomputes from EVERY candle
    currently loaded on the chart (effectively unbounded, very converged);
    this recomputes from a fixed-size window sized at `seed_multiplier`
    times the period, restart-safe the same way SMA already is (no
    incrementally-carried EMA value surviving a process restart to drift
    or need reconstruction — see `engine.py`'s cold-start backfill).

    This IS an approximation, surfaced rather than hidden: the seed value
    isn't the "true" EMA from inception, so early values in the window
    carry a small residual bias that decays geometrically at rate
    `(1 - k)` per bar. At the default `seed_multiplier=5`, that bias is
    below `(0.8)^40 ≈ 0.00013` of its starting size for a 9-period EMA —
    negligible at any price this system trades — but it is not
    bit-identical to a party that has carried the recursion since
    inception, and callers needing that guarantee should know this isn't
    it.

    Returns None when there isn't yet `period * seed_multiplier` closes —
    a STRICTER warm-up than sma()'s, deliberately: publishing an
    under-converged EMA (seeded but with few bars to decay that seed's
    influence away) would be a materially biased number, not just a
    slightly-early one, and this module doesn't publish those silently.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if seed_multiplier <= 0:
        raise ValueError("seed_multiplier must be positive")
    needed = period * seed_multiplier
    if len(closes) < needed:
        return None
    window = closes[-needed:]
    k = 2 / (period + 1)
    value = sum(window[:period]) / period
    for price in window[period:]:
        value = price * k + value * (1 - k)
    return value


def ema_slope(closes: list[float], period: int, seed_multiplier: int) -> dict[str, float]:
    """
    Slope of the EMA(period) LINE itself — same "OLS fit over the
    average's own trailing `period` values, lookback tied 1:1 to the
    smoothing period" design as sma_slope() (indicators/sma.py), for the
    same reason: no second, independently-tuned window parameter.

    Needs `period * seed_multiplier + period - 1` closes: each of the
    `period` trailing EMA(period) values individually needs
    `period * seed_multiplier` closes to clear ema()'s own (stricter
    than SMA's) warm-up floor, staggered one bar apart — mirrors
    sma_slope()'s `2*period-1` shape with ema()'s own warm-up floor
    substituted for SMA's bare `period`. At this system's shipped
    defaults this stays comfortably under KAMA's own window-capacity
    requirement (`er_period + slow_period * seed_multiplier`), so it
    doesn't grow `FeatureEngine._window_capacity` in practice — see
    engine.py's own capacity comment for the current numbers; it's still
    counted explicitly there rather than assumed to stay dominated.

    Recomputes `ema()` `period` times over overlapping slices — O(period)
    calls, each O(period * seed_multiplier) — negligible at this
    system's period sizes (9-50), same "recompute from window, cost is
    fine at these sizes" reasoning ema() itself already uses relative to
    a true incremental recursion.

    Same slope_pct / slope_angle reasoning as sma_slope(): normalized by
    the CURRENT EMA value, not ATR (same cross-timeframe objection
    confirmed decision #67 already raised, unchanged by which moving
    average the slope is measured on), and slope_angle is arctan of the
    already-×100 slope_pct number — 45° = 1%-of-EMA-value per bar, the
    same explicit calibration anchor sma_slope() documents.
    """
    if period < 2 or seed_multiplier < 1:
        return {}
    per_point_needed = period * seed_multiplier
    total_needed = per_point_needed + period - 1
    if len(closes) < total_needed:
        return {}

    window = closes[-total_needed:]
    ema_series = [ema(window[i : i + per_point_needed], period, seed_multiplier) for i in range(period)]
    # Every element is guaranteed non-None: window has exactly
    # total_needed closes, and window[i:i+per_point_needed] has exactly
    # per_point_needed elements for every i in range(period) (largest i
    # = period-1, and (period-1)+per_point_needed == total_needed ==
    # len(window)) — never short of ema()'s own `needed` floor.

    xs = list(range(period))
    mean_x = (period - 1) / 2
    mean_y = sum(ema_series) / period

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ema_series))
    denominator = sum((x - mean_x) ** 2 for x in xs)  # nonzero: period >= 2 guaranteed above
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x

    fitted = [slope * x + intercept for x in xs]
    ss_res = sum((y - f) ** 2 for y, f in zip(ema_series, fitted))
    ss_tot = sum((y - mean_y) ** 2 for y in ema_series)
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot

    current_value = ema_series[-1]  # == ema(closes, period, seed_multiplier); same trailing window, by construction

    result = {
        f"ema_{period}_slope": round(slope, 6),
        f"ema_{period}_r2": round(r2, 6),
    }
    if current_value != 0:
        slope_pct = slope / current_value * 100
        result[f"ema_{period}_slope_pct"] = round(slope_pct, 6)
        result[f"ema_{period}_slope_angle"] = round(math.degrees(math.atan(slope_pct)), 6)

    return result
