"""
Pure scoring functions for Market State Engine's per-symbol dimensions
(trading-intelligence-architecture.md §4, decision #91's dimension list;
decision #93 for this build's formulas and its two deviations from that
list — see engine.py's module docstring for both, and the full reasoning
in confirmed-decisions.md #93).

Every score is 0-100 (decision #91): 50 is neutral for the directional
dimensions (Trend, VWAP relationship, Acceleration); there's no neutral
point for the magnitude-only dimensions (Volatility regime, Volume
regime) — 0 is "quiet," 100 is "extreme," nothing bearish/bullish about
either end.

Calibration constants below are v1 defaults, explicitly NOT validated
against real score distributions yet — decision #91 makes the identical
point about band boundaries generally ("a guess until real score
distributions from real market data exist to set them from evidence").
Reasonable starting points, flagged as adjustable, not claimed correct —
Volatility regime's especially: "normal" ATR% varies a lot by symbol
character (a biotech vs. a utility), and there's no per-symbol percentile
history to calibrate against yet.

Pure functions, no I/O, no state — deliberately, so these are testable
without any of engine.py's async/DB/EventBus machinery. engine.py owns
the only state these need (the previous trend_score, for Acceleration).
"""
from __future__ import annotations


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# --- Trend ------------------------------------------------------------------

TREND_ANGLE_CAP_DEGREES = 20.0  # |sma_20_slope_angle| beyond this saturates the score


def trend_score(sma_20_slope_angle: float) -> float:
    """Driven by `sma_20_slope_angle` — decision #83's arctan-normalized
    slope angle, already comparable across symbols, timeframes, and
    price levels. Chosen over a multi-SMA blend or `regression_slope_norm`
    for v1: one already-normalized, already-tested feature beats
    reinventing normalization on a second one or combining several
    without a stated reason to."""
    return _clamp(50.0 + sma_20_slope_angle * (50.0 / TREND_ANGLE_CAP_DEGREES))


# --- Volatility regime --------------------------------------------------------

VOLATILITY_PCT_FLOOR = 0.3   # atr_14_pct at/below this -> 0
VOLATILITY_PCT_CEILING = 4.0  # atr_14_pct at/above this -> 100


def volatility_regime_score(atr_14_pct: float) -> float:
    """Driven by `atr_14_pct`, against a fixed v1 calibration band —
    see module docstring re: this being the weakest of the four
    formulas here."""
    span = VOLATILITY_PCT_CEILING - VOLATILITY_PCT_FLOOR
    return _clamp((atr_14_pct - VOLATILITY_PCT_FLOOR) / span * 100.0)


# --- Volume regime -------------------------------------------------------------

VOLUME_RVOL_CEILING = 3.0  # rvol at/above this -> 100


def volume_regime_score(rvol: float) -> float:
    """Driven by `rvol` — already relative by construction (1.0 = this
    symbol's own average for this time of day), so unlike Volatility
    this needs no separate baseline, just a ceiling."""
    return _clamp(rvol / VOLUME_RVOL_CEILING * 100.0)


# --- VWAP relationship -----------------------------------------------------------

VWAP_PCT_CAP = 1.5  # |close - vwap| / vwap, as a %, beyond this saturates the score


def vwap_relationship_score(close: float, vwap: float) -> float:
    """Driven by `(close - vwap) / vwap`. 50 = sitting right at VWAP."""
    if vwap == 0:
        return 50.0
    pct = (close - vwap) / vwap * 100.0
    return _clamp(50.0 + pct * (50.0 / VWAP_PCT_CAP))


# --- Acceleration ------------------------------------------------------------------

ACCELERATION_RATE_CAP = 100.0 / 60.0  # trend_score points/second that saturates the score
# i.e. trend_score swinging its full 0-100 range in ~60 seconds saturates
# Acceleration — a fast, but not absurd, regime shift for a 1m-timeframe
# symbol. Adjustable; not derived from real data yet, same caveat as
# every other constant in this file.


def acceleration_score(trend_score_now: float, trend_score_prev: float, elapsed_seconds: float) -> float | None:
    """Trend's own rate of change over the rolling window (decision #93:
    just Trend, not a value per dimension, and not an adaptive pick of
    whichever dimension moved most). 50 = trend_score holding steady;
    above/below reads as gaining/losing bullish (or bearish) momentum,
    mirroring Trend's own directional shape.

    Returns None when `elapsed_seconds` isn't positive — the caller
    (engine.py) is expected to only pass a genuine prior observation for
    this symbol, never a fabricated one; a symbol's first-ever recompute
    has no rate to report yet, and that absence should stay visible, not
    be papered over with a neutral 50."""
    if elapsed_seconds <= 0:
        return None
    rate = (trend_score_now - trend_score_prev) / elapsed_seconds
    return _clamp(50.0 + rate * (50.0 / ACCELERATION_RATE_CAP))
