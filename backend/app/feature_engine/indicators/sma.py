"""
Simple Moving Average — pure math only, same posture as every file in this
package (see indicators/__init__.py's own docstring for why it's a
package, not one file).
"""
from __future__ import annotations

import math


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


def sma_slope(closes: list[float], period: int) -> dict[str, float]:
    """
    Slope of the SMA(period) LINE itself — an OLS fit over the SMA's OWN
    trailing `period` values, not a fit against raw closes (that's
    regression.py's job, a different question). Lookback is tied 1:1 to
    the smoothing period itself, deliberately not a second, independently
    -tuned window: a 9-period SMA fits its slope over its own last 9
    values, a 50-period SMA over its own last 50, so the slope's "memory"
    always scales with how smoothed the line already is.

    Needs `2 * period - 1` closes: `period` trailing SMA(period) values,
    each staggered one bar apart, share all but their first/last close.
    Returns {} when fewer are available, or `period < 2` — an honest gap,
    same convention throughout this engine. (`sma_{period}` itself may
    still be publishing from engine.py at this same call — the slope
    warms up strictly slower than the underlying average, same shape
    ema()'s warm-up already has relative to sma()'s.)

    `slope` is $/bar (raw OLS coefficient over the SMA series). `r2` is
    the SMA series' own fit quality — 1.0 on a perfectly flat window,
    same degenerate-value reasoning regression.py's r2 already uses (a
    constant window is trivially, perfectly explained by its own mean).

    `slope_pct` normalizes by the CURRENT SMA value — NOT ATR, and NOT
    the window's own stdev (regression/KAMA's choice, confirmed decision
    #67). ATR(1D,14) is a fixed DAILY range; this slope is measured over
    whichever timeframe the SMA itself runs on (1m/5m/15m/1h, decision
    #51's uniform fan-out) — dividing an intraday slope by a daily range
    is the exact unstated cross-timeframe conversion #67 already ruled
    out for regression/KAMA, unchanged by which moving average the slope
    is measured on. `slope_pct` is %/bar (already ×100, matching
    atr_pct/kama's dist_pct convention), omitted only when the current
    SMA value is exactly 0.

    `slope_angle` = arctan(slope_pct) in degrees — arctan of the
    ALREADY-×100 slope_pct number, not the raw fraction. This is a
    deliberate calibration choice, not a mathematical necessity: arctan
    of the raw fraction compresses nearly every real-world SMA slope
    into single-digit degrees, which defeats the point of an
    at-a-glance angle. Using slope_pct directly instead means 45°
    corresponds to exactly a 1%-of-SMA-value move per bar — an explicit,
    documented anchor, not an arbitrary one. Revisit this scaling if
    1%/bar turns out to be the wrong "steep" reference once this runs
    against real symbols/timeframes.
    """
    if period < 2:
        return {}
    needed = 2 * period - 1
    if len(closes) < needed:
        return {}

    window = closes[-needed:]
    sma_series = [sum(window[i : i + period]) / period for i in range(period)]

    xs = list(range(period))
    mean_x = (period - 1) / 2
    mean_y = sum(sma_series) / period

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, sma_series))
    denominator = sum((x - mean_x) ** 2 for x in xs)  # nonzero: period >= 2 guaranteed above
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x

    fitted = [slope * x + intercept for x in xs]
    ss_res = sum((y - f) ** 2 for y, f in zip(sma_series, fitted))
    ss_tot = sum((y - mean_y) ** 2 for y in sma_series)
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot

    current_value = sma_series[-1]  # == sma(closes, period); same trailing window, by construction

    result = {
        f"sma_{period}_slope": round(slope, 6),
        f"sma_{period}_r2": round(r2, 6),
    }
    if current_value != 0:
        slope_pct = slope / current_value * 100
        result[f"sma_{period}_slope_pct"] = round(slope_pct, 6)
        result[f"sma_{period}_slope_angle"] = round(math.degrees(math.atan(slope_pct)), 6)

    return result
