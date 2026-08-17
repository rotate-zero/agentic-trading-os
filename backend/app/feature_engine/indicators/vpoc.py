"""
Volume Point of Control (previous trading day) — pure math only, matching
frontend/src/indicators/vpoc.ts's algorithm exactly. Resolves D5
(feature-engine-chart-migration.md) more simply than originally
anticipated there: D5 worried about VPOC needing a live, continuously-
growing volume-at-price histogram that wouldn't fit FeatureSet's flat
dict[str, float] shape. It doesn't need one — the frontend's own VPOC is
explicitly scoped to the PREVIOUS trading day only ("VPOC (Prev Day)" in
types/workspace.ts's HORIZONTAL_LEVEL_LABELS), the exact same bounded,
already-fully-elapsed dataset PDH/PDL/PDC (previous_day.py) and Camarilla
already use. engine.py computes it alongside those, from the SAME
already-fetched rows — no separate DB query, no live accumulator, no
schema change: just one more scalar in the same flat dict.
"""
from __future__ import annotations

from app.feature_engine.indicators.vwap import typical_price
from app.services.candle_aggregator import Candle

BUCKET_COUNT = 24  # matches vpoc.ts's own hardcoded constant — not made configurable here since it isn't there either


def volume_point_of_control(rows: list[Candle], bucket_count: int = BUCKET_COUNT) -> float | None:
    """
    Buckets `rows` (a single day's worth of candles — any order, high/low
    are computed fresh from the rows themselves) into `bucket_count`
    fixed-width price bins keyed by each candle's typical price, sums
    volume per bin, and returns the midpoint of whichever bin has the
    most volume.

    A coarse-but-honest approximation, not a true tick-level volume
    profile — matching vpoc.ts's own documented caveat exactly: real VPOC
    needs individual trade prices within each candle, which this system
    only has as OHLCV bars. Bucketing by whole-candle typical price is the
    standard fallback when only candle data is available.

    Returns None for an empty list (no previous day yet — same "honest
    gap" as PDH/PDL/PDC/Camarilla's own absence in that case).
    """
    if not rows:
        return None
    high = max(r.high for r in rows)
    low = min(r.low for r in rows)
    if high == low:
        return high  # degenerate range guard — a single flat price, matching vpoc.ts exactly

    bucket_size = (high - low) / bucket_count
    volume_by_bucket = [0] * bucket_count
    for r in rows:
        tp = typical_price(r.high, r.low, r.close)
        bucket = int((tp - low) / bucket_size)
        bucket = min(bucket_count - 1, max(0, bucket))
        volume_by_bucket[bucket] += r.volume

    max_bucket = max(range(bucket_count), key=lambda i: volume_by_bucket[i])
    return low + bucket_size * (max_bucket + 0.5)
