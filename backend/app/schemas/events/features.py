"""Payload schema for FeaturesUpdated. See system-design.md §10.3 and §4.5.

`symbol` deliberately isn't a field here — same reasoning as CandleClosed
(schemas/events/market_data.py): it already lives on the EventEnvelope,
duplicating it into the payload would just be two places that could
disagree.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
    features: dict[str, float]  # e.g. {"sma_9": 231.4521, "ema_20": 229.881, "vwap": 230.1} — decisions #45, #52, #53

