"""
Feature Engine indicator math — pure functions only. No I/O, no Event Bus
awareness, no database. app/feature_engine/engine.py is what wires these to
CandleClosed/FeaturesUpdated; keeping this module pure makes it trivially
unit-testable without a database, an event loop, or a running app at all
(see backend/tests/test_feature_engine.py::test_sma_*).
"""
from __future__ import annotations


def sma(closes: list[float], period: int) -> float | None:
    """
    Simple Moving Average over the last `period` closes.

    Full recompute from the window on every call, deliberately NOT an
    incremental running sum (subtract-oldest / add-newest). Incremental
    accumulates floating-point drift the longer a process runs — exactly
    the "two places compute the same SMA and they're slightly off" failure
    mode this needs to not have across a ~100-symbol, long-running process.
    A fresh mean every close is negligible cost at these periods (9-50
    candles) and removes drift as a possibility entirely rather than
    bounding or tolerating it.

    Returns None — not 0.0, not an exception — when there isn't yet enough
    history for this period ("warm-up"). Callers must treat "not ready yet"
    and "computed value of zero" as distinct states; returning 0.0 here
    would collapse that distinction.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


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


def typical_price(high: float, low: float, close: float) -> float:
    """(high + low + close) / 3 — the standard VWAP weighting price for a
    bar, matching frontend/src/indicators/vwap.ts's own definition exactly
    (confirmed decision #53)."""
    return (high + low + close) / 3


def vwap_from_accumulator(cumulative_pv: float, cumulative_volume: int) -> float | None:
    """
    Divides a running (price*volume, volume) accumulator into a VWAP
    value. Deliberately NOT the whole VWAP computation — engine.py owns
    the accumulation itself (session-reset detection, cold-start backfill,
    regular-hours gating), since none of that is a pure function of a
    single bar the way sma()/ema() are. This is just the one piece of
    VWAP's math that IS pure, split out for its own direct test.

    Returns None for zero cumulative volume — defensive against a
    zero-volume bar being the very first bar of a session, which would
    otherwise raise a ZeroDivisionError rather than "not ready yet."
    """
    if cumulative_volume <= 0:
        return None
    return cumulative_pv / cumulative_volume
