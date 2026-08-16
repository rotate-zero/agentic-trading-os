"""
Exponential Moving Average — pure math only, same posture as every file in
this package (see indicators/__init__.py's own docstring for why it's a
package, not one file).
"""
from __future__ import annotations


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
