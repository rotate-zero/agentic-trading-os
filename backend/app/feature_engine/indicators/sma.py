"""
Simple Moving Average — pure math only, same posture as every file in this
package (see indicators/__init__.py's own docstring for why it's a
package, not one file).
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
