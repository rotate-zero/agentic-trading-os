"""
Builds 5m/15m/1h candles from self-recorded 1m data (candle_store.py /
CandleRecorder) — the recorder only ever writes "1m" rows (TickIngestBridge's
fixed bucket size; see its own module docstring), so every coarser intraday
timeframe has to be derived on read rather than fetched directly. GET
/market/candles (app/api/routes/market.py) calls aggregate_from_recorded()
after an exact-match self-recorded lookup comes back empty, and before
falling through to an external provider — see that route for the full
fallback chain.

Built hierarchically (1m -> 5m -> 15m -> 1h), each level from the nearest
coarser level already computed, rather than every timeframe re-aggregating
raw 1m independently — one bucketing/OHLCV function to get right instead of
three slightly-different ones, and no repeated work when a single request
needs to walk through several levels to reach "1h".

Session-local, not a continuous 24h clock (confirmed-decisions.md — session-
local aggregation): premarket / regular / after-hours are aggregated as
three SEPARATE domains, each anchored to its own session's start time via
MarketClock.session_bounds(), so a bucket never straddles a session
boundary — e.g. a naive clock-aligned 1h bucket anchored to midnight would
blend 15:30-16:00 regular-session trading with 16:00-16:30 after-hours
trading into one misleading bar. Regular session (open/lunch/power-hour) is
treated as ONE continuous domain here — only the session TYPE changes the
anchor, not MarketClock's finer sub-labels, which exist for other callers.

None of the three target widths divides evenly into every session length:
regular (6.5h) and premarket (5.5h) both leave a 30-minute stub bucket at
1h; after-hours (4h) is the only one of the three that's clean at every
width. The trailing bucket of any session is returned regardless — it's
inherently partial until real time (or the next session) catches up to it,
same as the "current candle" on any live chart.

Known limitation, not currently handled: a bucket that ISN'T the trailing
one is trusted as complete. If CandleRecorder had a gap mid-session (a
restart, a dropped connection), the 1m rows it's missing are simply absent
from what gets aggregated — the resulting bar's high/low can silently be
narrower than what actually happened, with no flag indicating that.
Revisit if/when that's worth the complexity (a completeness check would
need to know how many 1m candles a bucket SHOULD have, which itself needs
a real trading-calendar source per the Phase 2 scope note in
market_clock.py, not just wall-clock minutes).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from app.core.market_clock import get_market_clock
from app.schemas.events.market_data import CandleClosed as Candle
from app.services import candle_store

# width in minutes -> the resulting candle's timeframe label. Public
# (confirmed decision #51) — feature_engine/engine.py reads this directly
# to know which widths to check for live boundary completion, rather than
# hardcoding a parallel {5, 15, 60} list that could drift from this one.
WIDTH_TO_LABEL: dict[int, str] = {5: "5m", 15: "15m", 60: "1h"}

AGGREGATABLE_TIMEFRAMES: frozenset[str] = frozenset(WIDTH_TO_LABEL.values())


def aggregate_from_recorded(symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
    """
    Returns [] — not an error — when there's no self-recorded 1m data to
    build from at all, same "empty means fall through to an external
    provider" contract candle_store.get_recorded_candles already uses, so
    the route doesn't need to treat this call any differently from a plain
    cache miss. Raises ValueError for a timeframe this module doesn't
    handle — that's a caller bug (checking AGGREGATABLE_TIMEFRAMES first is
    the caller's job), not a runtime "nothing found" case.

    Synchronous, same reasoning as candle_store itself — wrap in
    asyncio.to_thread at the call site, don't await this directly.
    """
    if timeframe not in AGGREGATABLE_TIMEFRAMES:
        raise ValueError(f"{timeframe!r} is not an aggregatable timeframe (supported: {sorted(AGGREGATABLE_TIMEFRAMES)})")

    one_min = candle_store.get_recorded_candles(symbol, "1m", start, end)
    if not one_min:
        return []

    five_min = _bucket_and_aggregate(one_min, 5)
    if timeframe == "5m":
        return five_min

    fifteen_min = _bucket_and_aggregate(five_min, 15)
    if timeframe == "15m":
        return fifteen_min

    return _bucket_and_aggregate(fifteen_min, 60)  # "1h" — the only remaining case


def bucket_start_for(candle_ts: datetime, session_start: datetime, width_minutes: int) -> datetime:
    """
    The session-local bucket a given timestamp falls into, for a bucket
    width of `width_minutes` anchored at `session_start`. Pulled out as its
    own public function (confirmed decision #51) so a second module —
    feature_engine/engine.py's live 5m/15m/1h boundary detection — uses the
    literal same formula this module already had correct and tested,
    instead of a parallel reimplementation that could quietly drift from
    it. `_bucket_and_aggregate` below is just this function's original
    caller, unchanged in behavior.
    """
    width = timedelta(minutes=width_minutes)
    bucket_index = (candle_ts - session_start) // width
    return session_start + bucket_index * width


def completes_bucket(candle_ts: datetime, session_start: datetime, width_minutes: int) -> bool:
    """
    True when a 1m candle at `candle_ts` is the LAST member of its
    session-local bucket of `width_minutes` — i.e. the very next minute
    would start a new bucket. This is the live-boundary-crossing check
    feature_engine/engine.py uses (confirmed decision #51) to know when a
    5m/15m/1h bar has just closed, without re-deriving the bucketing math
    this module already owns.
    """
    this_bucket = bucket_start_for(candle_ts, session_start, width_minutes)
    next_bucket = bucket_start_for(candle_ts + timedelta(minutes=1), session_start, width_minutes)
    return next_bucket != this_bucket


def _bucket_and_aggregate(candles: list[Candle], width_minutes: int) -> list[Candle]:
    """
    Groups `candles` (any single timeframe — 1m, or an already-aggregated
    5m/15m batch) into session-local buckets of `width_minutes`, and folds
    each bucket down to one OHLCV candle: open = first member's open,
    high/low = max/min across ALL members (not just the first and last —
    that would miss any spike or dip that happened mid-bucket), close =
    last member's close, volume = sum across all members.
    """
    clock = get_market_clock()
    buckets: dict[datetime, list[Candle]] = defaultdict(list)

    for c in candles:
        bounds = clock.session_bounds(c.candle_ts)
        if bounds is None:
            # No known session contains this timestamp — shouldn't happen
            # for data the recorder itself produced live during a real
            # session, but defensive against stale/backfilled rows, or a
            # later correction to MarketClock's boundaries (see its own
            # scope note) disagreeing with what was true when this candle
            # was recorded. Dropped rather than mis-bucketed.
            continue
        session_start, _session_end = bounds
        bucket_start = bucket_start_for(c.candle_ts, session_start, width_minutes)
        buckets[bucket_start].append(c)

    label = WIDTH_TO_LABEL[width_minutes]
    result: list[Candle] = []
    for bucket_start in sorted(buckets):
        members = sorted(buckets[bucket_start], key=lambda c: c.candle_ts)
        result.append(
            Candle(
                timeframe=label,
                open=members[0].open,
                high=max(m.high for m in members),
                low=min(m.low for m in members),
                close=members[-1].close,
                volume=sum(m.volume for m in members),
                candle_ts=bucket_start,
            )
        )
    return result
