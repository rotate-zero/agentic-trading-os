"""
VWAP's pure-function pieces — NOT the whole computation. Kept together in
one file rather than split further (unlike sma.py/ema.py's 1-function-per-
file split) because both functions here are small, tightly coupled parts
of the same indicator's math, mirroring how frontend/src/indicators/vwap.ts
is also one file, not several. See vwap_from_accumulator()'s own docstring
for what deliberately ISN'T here: the accumulation, session-reset
detection, and regular-hours gating all live in engine.py
(FeatureEngine._update_vwap) for the live path and feature_engine/
historical.py (compute_series) for the batch/chart path — neither of those
is a pure function of a single bar, so neither belongs in this package.
"""
from __future__ import annotations


def typical_price(high: float, low: float, close: float) -> float:
    """(high + low + close) / 3 — the standard VWAP weighting price for a
    bar, matching frontend/src/indicators/vwap.ts's own definition exactly
    (confirmed decision #53)."""
    return (high + low + close) / 3


def vwap_from_accumulator(cumulative_pv: float, cumulative_volume: int) -> float | None:
    """
    Divides a running (price*volume, volume) accumulator into a VWAP
    value. Deliberately NOT the whole VWAP computation — engine.py and
    historical.py each own the accumulation itself (session-reset
    detection, cold-start backfill, regular-hours gating), since none of
    that is a pure function of a single bar the way sma()/ema() are. This
    is just the one piece of VWAP's math that IS pure, split out for its
    own direct test.

    Returns None for zero cumulative volume — defensive against a
    zero-volume bar being the very first bar of a session, which would
    otherwise raise a ZeroDivisionError rather than "not ready yet."
    """
    if cumulative_volume <= 0:
        return None
    return cumulative_pv / cumulative_volume
