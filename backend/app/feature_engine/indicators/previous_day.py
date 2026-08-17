"""
Previous-day High/Low/Close — pure aggregation only. engine.py owns
finding WHICH day is "previous" and fetching its rows (a genuinely
stateful, DB-touching concern — see FeatureEngine._update_previous_day's
own docstring); this file only does the math once that day's rows are
already in hand.
"""
from __future__ import annotations

from app.services.candle_aggregator import Candle


def aggregate_day(rows: list[Candle]) -> tuple[float, float, float] | None:
    """
    (high, low, close) across a full day's worth of already-fetched
    candles. `close` is the LAST row's close — `rows` must be chronological
    (ascending candle_ts), the same order candle_store.get_recorded_candles()
    already returns, so callers don't need to sort first.

    Matches frontend/src/indicators/previousDayLevels.ts's own definition
    exactly: high/low span the WHOLE calendar day (pre-market and
    after-hours included, not just regular session) — a deliberate choice
    already made there, not reconsidered here. Returns None for an empty
    list rather than raising, so callers can treat "no previous-day data
    yet" as a normal state (a fresh deployment, or fewer than 2 distinct
    trading days of history so far) exactly like PDH/PDL/PDC's absence
    from a chart with too little history already is on the frontend.
    """
    if not rows:
        return None
    high = max(r.high for r in rows)
    low = min(r.low for r in rows)
    close = rows[-1].close
    return high, low, close
