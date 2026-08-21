"""
Kaufman Adaptive Moving Average (KAMA) + Efficiency Ratio — pure math
only. Confirmed decisions #67/#68
(docs/architecture/feature-engine-indicator-expansion.md §6). Adaptive
short-term trend context: a moving average whose responsiveness scales
with how directional ("efficient") recent movement has been, plus the
Efficiency Ratio itself as a standalone measure of trendiness vs. noise.
"""
from __future__ import annotations

import statistics


def kama(closes: list[float], er_period: int, fast_period: int, slow_period: int, seed_multiplier: int) -> dict[str, float]:
    """
    Same "no exact finite-window recursion" shape ema() already documents
    (KAMA is mathematically a recursion over all history back to
    inception) — seeded with the SMA of the OLDEST `er_period` closes in
    a bounded window, then the standard KAMA recursion applied forward
    through the rest of that window, keeping only the FINAL value
    (burn-in discarded implicitly, same as ema()'s own `return value`
    after its loop). `seed_multiplier` applies to `slow_period`, not
    `er_period` — the parameter that drives the LONGEST memory in the
    recursion, per the design doc's own reasoning for why this warm-up is
    arguably harder to get right than EMA's.

    Needs `er_period + slow_period * seed_multiplier` closes total: the
    trailing `slow_period * seed_multiplier` is the recursion length
    itself (mirroring ema()'s `period * seed_multiplier`), plus a LEADING
    `er_period` closes so the Efficiency Ratio is computable at every
    step of the recursion, including its first. Returns {} when fewer
    are available, or on a non-positive parameter — an honest gap, same
    convention throughout this engine.

    `slope` is the 1-bar delta of the KAMA value itself (confirmed
    decision #68, D3) — regression's slope is an explicit OLS fit over a
    whole window; KAMA has no equivalent "fit" to lean on, so the natural
    analog is simply KAMA_t - KAMA_(t-1), a byproduct of recursing one
    bar further than strictly needed for `value` alone. `slope_norm`
    divides that by the standard deviation of the trailing `er_period`
    closes specifically — the most local, most directly-comparable
    segment available (the same span ER itself measures over), not the
    full (possibly much longer) warm-up window — same omit-only-that-key
    behavior as regression.py when that stdev is 0.

    `dist`/`dist_pct` are the current close's distance from the KAMA
    value, in dollars and percent respectively — both, per the original
    design brief's two separate bullets (not just one, unlike
    regression's single `deviation`).

    `er` is the CURRENT bar's own Efficiency Ratio (0..1), reported
    directly. Omitted specifically when its own denominator (sum of
    absolute bar-to-bar changes over the last `er_period` closes) is
    exactly 0 — genuinely 0/0, not a fabricated value, everything else in
    the result still returned. INTERNALLY, during the recursion's warm-up
    steps, a zero-denominator ER is instead treated as 0.0 (not skipped):
    a flat sub-segment has zero change AND zero volatility together, so
    "no directional efficiency" (ER=0, the least-adaptive value) is the
    well-defined, honest reading for what SC that step should use. This
    differs from the OUTPUT `er` key's omit-on-zero rule, which is about
    what gets REPORTED for the CURRENT bar specifically, not about how
    earlier bars drive the recursion forward — two different questions,
    two different answers, both written down rather than silently
    conflated.
    """
    if er_period < 1 or fast_period < 1 or slow_period < 1 or seed_multiplier < 1:
        return {}

    needed = er_period + slow_period * seed_multiplier
    if len(closes) < needed:
        return {}

    window = closes[-needed:]
    fast_sc = 2 / (fast_period + 1)
    slow_sc = 2 / (slow_period + 1)

    def efficiency_ratio(end: int) -> float:
        segment = window[end - er_period : end + 1]
        change = abs(segment[-1] - segment[0])
        volatility = sum(abs(segment[i] - segment[i - 1]) for i in range(1, len(segment)))
        return change / volatility if volatility != 0 else 0.0

    value = sum(window[:er_period]) / er_period  # seed: SMA of the oldest er_period closes
    previous_value = value
    for i in range(er_period, len(window)):
        er = efficiency_ratio(i)
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        previous_value = value
        value = value + sc * (window[i] - value)

    slope = value - previous_value
    close = window[-1]
    dist = close - value

    result = {
        f"kama_{er_period}": round(value, 6),
        f"kama_{er_period}_slope": round(slope, 6),
        f"kama_{er_period}_dist": round(dist, 6),
    }
    if value != 0:
        result[f"kama_{er_period}_dist_pct"] = round(dist / value * 100, 6)

    stdev = statistics.pstdev(window[-er_period:])
    if stdev != 0:
        result[f"kama_{er_period}_slope_norm"] = round(slope / stdev, 6)

    final_er_volatility = sum(abs(window[i] - window[i - 1]) for i in range(len(window) - er_period, len(window)))
    if final_er_volatility != 0:
        result[f"kama_{er_period}_er"] = round(efficiency_ratio(len(window) - 1), 6)

    return result
