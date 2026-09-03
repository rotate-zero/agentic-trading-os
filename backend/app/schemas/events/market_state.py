"""Payload schemas for MarketStateChanged — both row shapes (M3, decision
#91: "no new EventType needed — envelope.symbol distinguishes the two
shapes"). See system-design.md §10.3 and trading-intelligence-
architecture.md §4.

`MarketState`: decision #91 for the score-first shape; decision #93 for
this build's scoring formulas and its two deviations from #91's original
six-dimension list — `session_type_score` dropped, `acceleration_score`
scoped to Trend only.

`CrossSymbolState`: decision #91's 7-field composite (this build, #97).
Published with `envelope.symbol == "__MARKET__"` instead of a real
ticker — same sentinel used for the persisted row (models/market_state.py).

`symbol` deliberately isn't a field on either schema — same convention as
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


class CrossSymbolState(BaseModel):
    """SPY/QQQ/IWM synthesized composite (decision #91 §4, this build
    #97). Carries `timeframe`/`candle_ts` the same way `MarketState`
    does — set by the engine to whichever of the three symbols has the
    most recent `candle_ts` at synthesis time, since they don't
    necessarily recompute in lockstep.

    The 7 score fields are required — this is only ever constructed once
    all three of SPY/QQQ/IWM have reported at least one per-symbol
    `trend_score`; `MarketStateEngine._compute_cross_symbol` returns None
    rather than a partially-filled instance until that's true (honest
    state over fabricated state)."""

    timeframe: str
    candle_ts: datetime

    spy_direction_score: float  # SPY's own trend_score, passthrough
    qqq_direction_score: float
    iwm_direction_score: float
    trend_alignment_score: float
    risk_on_score: float
    qqq_leadership_score: float
    iwm_confirmation_score: float
