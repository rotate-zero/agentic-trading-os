"""
Pure scoring functions for Market State Engine's per-symbol dimensions
(trading-intelligence-architecture.md §4, decision #91's dimension list;
decision #93 for this build's formulas and its two deviations from that
list — see engine.py's module docstring for both, and the full reasoning
in confirmed-decisions.md #93) and its cross-symbol composite (decision
#91's `CrossSymbolState`, this build #97 — see the "Cross-symbol" section
below).

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


# --- Cross-symbol (CrossSymbolState, decision #91 §4, this build #97) -------
#
# All four formulas below take SPY/QQQ/IWM's own trend_score (already
# 0-100, directional — 50 neutral) as input, not raw price/volume — the
# per-symbol Trend dimension already did the real normalization work, so
# cross-symbol synthesis is comparisons between three already-comparable
# numbers, nothing new to calibrate against price itself. `spy_direction_
# score`/`qqq_direction_score`/`iwm_direction_score` (CrossSymbolState's
# other three fields) are a straight passthrough of that same trend_score
# with no function needed — engine.py assigns them directly.
#
# Calibration constants (60, 30) are v1 defaults, explicitly unvalidated
# against real distributions — same posture as every constant above.

TREND_ALIGNMENT_SPREAD_CAP = 60.0  # spread (max-min) across the three at/beyond this -> fully misaligned (0)
CROSS_SYMBOL_DIFF_CAP = 30.0       # points of difference that saturates risk_on/qqq_leadership
IWM_CONFIRMATION_DEVIATION_CAP = 30.0  # |iwm - broad-market avg| at/beyond this -> fully diverged (0)


def trend_alignment_score(spy_direction_score: float, qqq_direction_score: float, iwm_direction_score: float) -> float:
    """How closely SPY/QQQ/IWM's direction scores agree. 100 = identical
    readings across all three; falls toward 0 as they spread apart,
    saturating at `TREND_ALIGNMENT_SPREAD_CAP` points of spread — a
    genuine cross-symbol disagreement (e.g. one bullish, one bearish),
    not just ordinary noise between three independently-computed
    scores."""
    scores = (spy_direction_score, qqq_direction_score, iwm_direction_score)
    spread = max(scores) - min(scores)
    return _clamp(100.0 - spread / TREND_ALIGNMENT_SPREAD_CAP * 100.0)


def risk_on_score(spy_direction_score: float, qqq_direction_score: float, iwm_direction_score: float) -> float:
    """QQQ/IWM (growth, small-cap) strength relative to SPY (the broad
    market). Above 50 = risk-on (growth/small-cap leading); below 50 =
    risk-off (growth/small-cap lagging the broad tape) — the same
    directional convention as Trend itself, applied one level up."""
    growth_avg = (qqq_direction_score + iwm_direction_score) / 2.0
    return _clamp(50.0 + (growth_avg - spy_direction_score) * (50.0 / CROSS_SYMBOL_DIFF_CAP))


def qqq_leadership_score(spy_direction_score: float, qqq_direction_score: float) -> float:
    """Is tech (QQQ) leading or lagging the broader tape (SPY) —
    deliberately narrower than risk_on_score (SPY vs. QQQ only, no IWM),
    since tech leadership specifically is a distinct, commonly-asked
    question from growth-vs-value broadly."""
    return _clamp(50.0 + (qqq_direction_score - spy_direction_score) * (50.0 / CROSS_SYMBOL_DIFF_CAP))


def iwm_confirmation_score(spy_direction_score: float, qqq_direction_score: float, iwm_direction_score: float) -> float:
    """Does small-cap (IWM) confirm or diverge from SPY/QQQ's own
    average read. 100 = IWM matches the SPY/QQQ average exactly; falls
    toward 0 as IWM diverges, saturating at `IWM_CONFIRMATION_DEVIATION_
    CAP` points away — small-caps trading against the broad market's
    signal is the classic confirmation/divergence question this
    dimension answers."""
    broad_avg = (spy_direction_score + qqq_direction_score) / 2.0
    deviation = abs(iwm_direction_score - broad_avg)
    return _clamp(100.0 - deviation / IWM_CONFIRMATION_DEVIATION_CAP * 100.0)
