"""
ATR(1D, 14) / ATR% — Wilder's classic Average True Range, pure math only.
Confirmed decisions #67/#68
(docs/architecture/feature-engine-indicator-expansion.md §1). Takes an
already-fetched, already-filtered list of COMPLETE prior daily candles
(strictly before today) — engine.py's shared `self._daily_candle_cache`,
populated once per (symbol, ET day) by `_maybe_refresh_daily_levels`, the
SAME fetch Daily Levels itself uses (decision #68, D1) — this function
never fetches anything itself, same "pure math here, orchestration in
engine.py" split every file in this package keeps.
"""
from __future__ import annotations

from typing import Any


def atr(prior_candles: list[Any], period: int) -> dict[str, float]:
    """
    Wilder ATR over exactly the LAST `period + 1` entries of
    `prior_candles` (each bar's True Range needs the PRIOR bar's close,
    so `period` True Ranges need `period + 1` candles) — the seed average
    IS the full result here, deliberately: this always recomputes from a
    fixed trailing window rather than smoothing forward through a longer
    incremental history, the same "recompute from window" choice already
    made for EMA/KAMA in this design (see
    feature-engine-indicator-expansion.md §1 for why). Returns {} when
    fewer than `period + 1` candles are available — an honest gap, not a
    fabricated partial-period average.

    `prior_candles` must already be sorted chronologically ascending and
    contain ONLY complete days strictly before today —
    `_maybe_refresh_daily_levels`'s own "Strictly-prior days only"
    filtering already guarantees both, so this function trusts that
    ordering rather than re-sorting/re-filtering it itself.

    ATR% uses the close of the LAST candle in this same window (the most
    recent complete daily bar's close) as its denominator — resolved
    (decision #68, D2) as a value frozen for the day alongside ATR
    itself, not the live intraday close, so ATR and ATR% move together as
    one stable daily reference pair rather than one static number paired
    with an intraday-reactive one.
    """
    if len(prior_candles) < period + 1:
        return {}

    window = prior_candles[-(period + 1):]
    true_ranges = [
        max(
            window[i].high - window[i].low,
            abs(window[i].high - window[i - 1].close),
            abs(window[i].low - window[i - 1].close),
        )
        for i in range(1, len(window))
    ]
    atr_value = sum(true_ranges) / period
    last_close = window[-1].close

    return {
        f"atr_{period}": round(atr_value, 6),
        f"atr_{period}_pct": round(atr_value / last_close * 100, 6),
    }
