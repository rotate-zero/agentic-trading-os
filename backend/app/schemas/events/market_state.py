"""Payload schema for MarketStateChanged. See system-design.md §10.3 and
trading-intelligence-architecture.md §4 (decision #91 for the score-first
shape; decision #93 for this build's scoring formulas and its two
deviations from #91's original six-dimension list — `session_type_score`
dropped, `acceleration_score` scoped to Trend only).

`symbol` deliberately isn't a field here — same convention as
`FeatureSet` (schemas/events/features.py): it already lives on the
EventEnvelope.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MarketState(BaseModel):
    timeframe: str  # matches FeatureSet.timeframe — which candle stream this was computed from
    candle_ts: datetime  # which candle close this state was computed from, same convention as FeatureSet

    trend_score: float
    volatility_regime_score: float
    volume_regime_score: float
    vwap_relationship_score: float
    # None only for a symbol's first-ever recompute — no prior
    # trend_score yet to derive a rate of change from (decision #93).
    acceleration_score: float | None = None
