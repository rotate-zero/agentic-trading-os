"""Payload schema for FeaturesUpdated. See system-design.md §10.3 and §4.5.

`symbol` deliberately isn't a field here — same reasoning as CandleClosed
(schemas/events/market_data.py): it already lives on the EventEnvelope,
duplicating it into the payload would just be two places that could
disagree.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DailyLevel(BaseModel):
    """One clustered support/resistance zone — decision #59,
    docs/architecture/daily-levels-design.md §3. A variable-length,
    day-to-day-reshaping collection, which is exactly why this is its own
    field rather than more dict[str, float] entries on `features` below:
    forcing (price, strength) pairs into fake flat keys would leak
    strength values into LevelInteractionEngine's generic key-iteration
    as if they were levels themselves.

    `level_id` is the persistent cross-day identity design doc §4
    describes (proximity-reconciled, never rank-based) — Stage 1 (this
    field's first populated version) mints a fresh id every day rather
    than reconciling against yesterday's, since that reconciliation is
    explicitly Stage 2, not yet built. Do not treat a Stage-1-era
    `level_id` as stable across days yet — engine.py's docstring flags
    this same limitation at the point where ids are actually minted.
    """

    level_id: str
    price: float
    strength: int
    distinct_candle_count: int


class FeatureSet(BaseModel):
    timeframe: str  # "1m", "5m", "15m", or "1h" (decision #51) — see feature_engine/engine.py's module docstring
    candle_ts: datetime  # which candle close this FeatureSet was computed from
    close: float  # the raw close this FeatureSet was computed from (confirmed decision #46) —
    # makes FeaturesUpdated self-contained for any consumer that needs BOTH a level value
    # and the raw price it's being compared against (e.g. Level Interaction Engine's zone
    # classification). Deliberately not left to a consumer to separately correlate against
    # CandleClosed: FeatureEngine and any such consumer would each be decoupled subscribers to
    # DIFFERENT event types published at different times, with no ordering guarantee between
    # them — the same class of race feature_engine/engine.py's own module docstring already
    # designed around for CandleRecorder vs. FeatureEngine, applied here one hop further down
    # the chain instead of being reintroduced by a new consumer.
    #
    # open/high/low/volume — decision #99. Closes the schema gap
    # strategy-engine-design.md §8 flagged ("FeatureSet carries only
    # close — no open/high/low/volume, so a strategy can't compute a wick
    # ratio or body size today"). ORB's opening range is the first real
    # consumer: a true opening range needs the WICK high/low of each 1m
    # candle, not the closes, or it silently understates the range and
    # mis-derives structural_invalidation off it.
    #
    # Nullable, not required — honest state over fabricated state (§11).
    # Correctly populated for the 1m FeatureSet only, where a single
    # candle's own OHLCV is unambiguous and already available in
    # _compute_one() (feature_engine/engine.py). Left None for aggregated
    # 5m/15m/1h FeatureSets: a truthful aggregated open/high/low/volume
    # needs the WHOLE bucket's constituent 1m candles (open = first bar's
    # open, high/low = max/min across the bucket, volume = sum) — none of
    # that is tracked today, and passing through the last constituent 1m
    # candle's own OHLC would silently misrepresent the aggregated bar.
    # Real, separate follow-up work if a future aggregated-timeframe
    # strategy needs it — not assumed as a side effect of this change.
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    features: dict[str, float]  # e.g. {"sma_9": 231.4521, "sma_9_slope_angle": 12.4, "ema_20": 229.881, "vwap": 230.1} — decisions #45, #52, #53, #83
    daily_levels: list[DailyLevel] = Field(default_factory=list)  # decision #59 — see DailyLevel above

