"""
Relative Volume (RVOL) — pure math only. A day-trading agent's proxy for
"is today unusually busy for this symbol, right now" — today's
regular-session volume so far, divided by what a NORMAL day's volume
would be by this same point in the session.

Not part of the original five-family design brief
(docs/architecture/feature-engine-indicator-expansion.md) — added
separately per direct request, average-daily-volume lookback resolved as
5 trading days (not the 7 initially mentioned). See confirmed decision
#71 for the full reasoning, including the two real judgment calls this
module embodies.
"""
from __future__ import annotations


def rvol(session_volume: float, avg_daily_volume: float, elapsed_minutes: int, total_session_minutes: int) -> dict[str, float]:
    """
    RVOL = session_volume / (avg_daily_volume * elapsed_fraction), where
    elapsed_fraction = elapsed_minutes / total_session_minutes.

    This is a TIME-OF-DAY-NORMALIZED proxy, not the naive
    session_volume / avg_daily_volume (which would read misleadingly low
    for most of the day on a perfectly normal session, since regular
    volume accrues roughly proportionally to elapsed time — not the
    "true" RVOL some scanners compute from a full per-minute historical
    volume PROFILE averaged across days, which needs much more history
    than a handful of daily totals to build. This proxy needs only
    `avg_daily_volume` (a single number per day, already available from
    the same shared daily-candle cache ATR/Daily Levels use) rather than
    an intraday profile, at the cost of assuming volume accrues roughly
    linearly through the session — good enough for a rough "busier or
    quieter than normal" read, not a claim of scanner-grade precision. A
    real, deliberate scope choice, not an oversight.

    Returns {} when `avg_daily_volume <= 0`, `total_session_minutes <= 0`,
    or `elapsed_minutes <= 0` — all should be impossible in practice given
    how the caller derives them, but an honest gap costs nothing and a
    silent division by zero costs a crashed worker loop iteration.
    """
    if avg_daily_volume <= 0 or total_session_minutes <= 0 or elapsed_minutes <= 0:
        return {}

    elapsed_fraction = elapsed_minutes / total_session_minutes
    expected_volume_by_now = avg_daily_volume * elapsed_fraction
    return {"rvol": round(session_volume / expected_volume_by_now, 6)}
