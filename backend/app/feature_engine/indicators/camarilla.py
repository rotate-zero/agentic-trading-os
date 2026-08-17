"""
Camarilla pivot levels — pure math only, a function of the previous day's
High/Low/Close (previous_day.py) — nothing else. engine.py computes those
once (FeatureEngine._update_previous_day) and passes them straight in here
rather than re-deriving the previous day's range a second time; worth
noting frontend/src/indicators/camarillaPivots.ts does re-derive it
independently of previousDayLevels.ts (its own separate loop over
getPreviousTradingDayCandles()) — a minor, harmless duplication over there,
just not one repeated here since it's trivial to avoid backend-side.
"""
from __future__ import annotations


def camarilla_pivots(high: float, low: float, close: float) -> dict[str, float]:
    """
    Nine levels — a central pivot (pp) plus four resistance (r1-r4) and
    four support (s1-s4) levels, each progressively further from close.
    Matches frontend/src/indicators/camarillaPivots.ts's formula exactly:
    range = high - low; pp = mean(high, low, close); r_n/s_n = close +/-
    range * 1.1 / {12, 6, 4, 2} for n = 1..4.
    """
    range_ = high - low
    return {
        "pp": (high + low + close) / 3,
        "r1": close + (range_ * 1.1) / 12,
        "r2": close + (range_ * 1.1) / 6,
        "r3": close + (range_ * 1.1) / 4,
        "r4": close + (range_ * 1.1) / 2,
        "s1": close - (range_ * 1.1) / 12,
        "s2": close - (range_ * 1.1) / 6,
        "s3": close - (range_ * 1.1) / 4,
        "s4": close - (range_ * 1.1) / 2,
    }
