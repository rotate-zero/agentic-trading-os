"""Payload schema for StrategyEvaluationCompleted (C2, trading-intelligence-
architecture.md §19.2).

One event is one COMPLETED strategy pass: one symbol, one timeframe, one source
candle, and every registered strategy version that candle triggered. It carries
the full terminal disposition of each strategy — complete opportunity contents
included — so a consumer never has to reconstruct eligibility from separately
delivered ``OpportunityCreated`` events. The Strategy Scheduler is the sole
producer; it rides the normal dispatch lane.

``symbol`` is deliberately not a field (same convention as ``FeatureSet`` and
``MarketState``): it lives on the ``EventEnvelope``.

The three timestamps are distinct facts and are never substituted for one
another:

* ``source_candle_ts`` — the source candle's own timestamp in the producer's
  convention (the built producers stamp the interval OPEN);
* ``source_interval_start`` / ``source_interval_close`` — the explicit source
  interval, which can be shorter than the nominal width for a session-trailing
  bucket;
* ``completed_at`` — the Scheduler's local completion time. Receive time is the
  consumer's own and is never carried here.

A batch with ``unavailable_prerequisites`` carries no dispositions: no strategy
evaluated, and an untriggered strategy is never recorded as having evaluated.
The model mirrors ``trading_intelligence/candidate_contract.py`` field for
field; that module remains the validating contract, this one is the wire shape.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvaluatedOpportunity(BaseModel):
    """Detached copy of the opportunity fields a candidate needs.

    ``direction`` uses the candidate vocabulary (``long``/``short``), not the
    legacy ``Opportunity`` ``BUY``/``SELL``. ``expected_horizon_minutes`` is
    descriptive only; ``wait_expires_at`` is deliberately not carried.
    """

    direction: Literal["long", "short"]
    confidence: float = Field(allow_inf_nan=False)
    structural_invalidation: float = Field(allow_inf_nan=False)
    structural_target: float = Field(allow_inf_nan=False)
    status: Literal["potential", "waiting", "actionable", "expired"]
    expected_horizon_minutes: int | None = None
    evidence: dict[str, Any]


class StrategyDispositionPayload(BaseModel):
    strategy: str
    strategy_version: str
    kind: Literal["opportunity", "no_opportunity", "gated", "error"]
    reason: str | None = None  # machine-readable code for gated/error; never exception text
    opportunity: EvaluatedOpportunity | None = None


class UnavailablePrerequisitePayload(BaseModel):
    name: str
    reason: str


class StrategyEvaluationCompleted(BaseModel):
    timeframe: str
    mode: Literal["simulated", "paper", "live", "backtest"]
    source_candle_ts: datetime
    source_interval_start: datetime
    source_interval_close: datetime
    completed_at: datetime
    dispositions: list[StrategyDispositionPayload] = Field(default_factory=list)
    unavailable_prerequisites: list[UnavailablePrerequisitePayload] = Field(default_factory=list)
