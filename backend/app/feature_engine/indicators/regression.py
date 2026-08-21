"""
Linear Regression (OLS) — pure math only. Confirmed decisions #67/#68
(docs/architecture/feature-engine-indicator-expansion.md §5). Describes
short-term trend behavior over a fixed trailing window of closes — slope,
a locally-normalized slope, deviation of the current price from the
fitted line, and R² (fit quality) — NOT a signal in itself; the
intelligence layer (currently on hold) interprets these, this module only
measures them, same framing the original design brief itself used.
"""
from __future__ import annotations

import statistics


def regression(closes: list[float], period: int) -> dict[str, float]:
    """
    OLS fit of the trailing `period` closes against an equally-spaced bar
    index (0 = oldest, period-1 = newest/current). Returns {} when fewer
    than `period` closes are available, or `period < 2` (a single point
    has no defined slope) — an honest gap, same convention throughout
    this engine.

    `value` is the fitted price AT the most recent bar (x = period-1),
    not the raw close itself — the two differ whenever the fit doesn't
    pass exactly through the last point, the normal case. `deviation` is
    close - value: how far the actual price has strayed from the fitted
    trend line, in dollars.

    `slope_norm` divides the raw $/bar slope by the standard deviation of
    the SAME window's closes — resolved (design doc §5) as local
    intraday volatility rather than the daily ATR family, avoiding an
    unstated cross-timeframe unit conversion (a 1-minute slope against a
    whole-day range). Omitted specifically (not the whole dict) when that
    stdev is exactly 0 (a perfectly flat window) — `value`/`slope`/
    `deviation`/`r2` are all still well-defined there (slope is exactly
    0), only the normalization's denominator is degenerate.

    `r2` uses the population definition (1 - SS_res/SS_tot). In the same
    flat-window case where SS_tot is also 0, r2 is defined as 1.0 — a
    judgment call, not the only defensible one: a constant window is
    trivially, perfectly "explained" by its own mean (residuals are all
    exactly 0 too), so 1.0 reads as the more honest degenerate value here
    than omitting it — the OPPOSITE convention from KAMA's Efficiency
    Ratio (kama.py), where a 0/0 denominator maps to the WORST value (0,
    "no efficiency to measure") rather than the best. Different
    quantities, different natural degenerate answers; both spelled out
    rather than picked silently.
    """
    if period < 2 or len(closes) < period:
        return {}

    window = closes[-period:]
    xs = list(range(period))
    mean_x = (period - 1) / 2
    mean_y = sum(window) / period

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window))
    denominator = sum((x - mean_x) ** 2 for x in xs)  # nonzero: period >= 2 guaranteed above
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x

    fitted = [slope * x + intercept for x in xs]
    ss_res = sum((y - f) ** 2 for y, f in zip(window, fitted))
    ss_tot = sum((y - mean_y) ** 2 for y in window)
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot

    value = slope * (period - 1) + intercept
    deviation = window[-1] - value

    result = {
        f"regression_{period}_value": round(value, 6),
        f"regression_{period}_slope": round(slope, 6),
        f"regression_{period}_deviation": round(deviation, 6),
        f"regression_{period}_r2": round(r2, 6),
    }

    stdev = statistics.pstdev(window)
    if stdev != 0:
        result[f"regression_{period}_slope_norm"] = round(slope / stdev, 6)

    return result
