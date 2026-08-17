"""
Premarket High/Low — the only "math" beyond max()/min() themselves is
folding one more bar into a running range, which is what fold_range()
below does. Session-reset detection, cold-start backfill, and the
"stays frozen after premarket ends" behavior are all genuinely stateful,
DB-touching concerns that live in engine.py
(FeatureEngine._update_premarket), not here — same split previous_day.py
and camarilla.py both keep (pure math here, orchestration there).

A dedicated file for something this small is mainly for consistency with
sma.py/ema.py/vwap.py's own per-indicator-file split (confirmed decision
#56) and to give this indicator a stable, directly-testable seam — worth
having if PMH/PML ever needs to become more than a plain running max/min
(e.g. volume-weighted, outlier-aware) later, not because plain max()/min()
needed hiding behind a function on their own merits.
"""
from __future__ import annotations


def fold_range(
    current_high: float | None, current_low: float | None, new_high: float, new_low: float
) -> tuple[float, float]:
    """Folds one more bar's high/low into a running range. `current_high`/
    `current_low` are None for the very first bar of a session — handled
    here rather than pushing a sentinel/-inf convention onto every caller."""
    high = new_high if current_high is None else max(current_high, new_high)
    low = new_low if current_low is None else min(current_low, new_low)
    return high, low
