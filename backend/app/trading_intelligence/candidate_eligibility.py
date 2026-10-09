"""Pure eligibility assessment over candidate state (C1, §19.2).

``assess_candidates`` reads an immutable :class:`CandidateState` with an
explicit ``as_of`` instant and an explicit freshness policy. It has no clock
and invents no maximum age: with no policy (or none for the candidate's
timeframe) a candidate is reported ``freshness_policy_unconfigured`` and is
NOT eligible. Eligibility here is evidence for a later Decision stage; it
authorizes nothing.

Age is measured from the source interval's CLOSE to ``as_of`` (never from
delivery time). A candidate is fresh while ``age <= max_age`` and expired once
strictly older. ``as_of`` before the interval close is a negative/unknown age
and is unavailable (clock anomaly), never fresh (§19.8).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from app.trading_intelligence.candidate_contract import (
    CandidateContractError,
    Direction,
    DispositionKind,
    ExecutionMode,
    OpportunityContent,
    to_utc,
)
from app.trading_intelligence.candidate_state import CandidateState, EvaluationRecord

REASON_POLICY_UNCONFIGURED = "freshness_policy_unconfigured"
REASON_SOURCE_AGE_UNAVAILABLE = "source_age_unavailable"
REASON_EXPIRED = "expired"
REASON_NOT_ACTIONABLE = "not_actionable"
REASON_INVALIDATED = "invalidated"


@dataclass(frozen=True)
class CandidateFreshnessPolicy:
    """Explicit maximum candidate ages in seconds, keyed by trigger timeframe.

    The caller supplies every number; this module defines no default. Build
    with :meth:`create`; the mapping is copied and sorted.
    """

    max_age_seconds: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        items = tuple(self.max_age_seconds)
        seen: set[str] = set()
        for timeframe, seconds in items:
            if not isinstance(timeframe, str) or not timeframe:
                raise CandidateContractError("policy timeframe must be a non-empty string")
            if timeframe in seen:
                raise CandidateContractError(f"duplicate policy timeframe {timeframe!r}")
            seen.add(timeframe)
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
                raise CandidateContractError(f"max age for {timeframe!r} must be a finite positive number of seconds")
        object.__setattr__(self, "max_age_seconds", tuple(sorted((tf, float(sec)) for tf, sec in items)))

    @classmethod
    def create(cls, max_age_seconds_by_timeframe: Mapping[str, float]) -> "CandidateFreshnessPolicy":
        return cls(tuple(max_age_seconds_by_timeframe.items()))

    def max_age_for(self, timeframe: str) -> float | None:
        for key, seconds in self.max_age_seconds:
            if key == timeframe:
                return seconds
        return None


@dataclass(frozen=True)
class CandidateAssessment:
    """One slot's eligibility with deterministic reasons and separate times."""

    symbol: str
    strategy: str
    strategy_version: str
    trigger_timeframe: str
    kind: DispositionKind
    disposition_reason: str | None
    evaluation_id: str
    candidate_id: str | None
    direction: Direction | None
    eligible: bool
    reasons: tuple[str, ...]
    source_candle_ts: datetime
    source_interval_start: datetime
    source_interval_close: datetime
    completed_at: datetime
    received_at: datetime
    age_seconds: float | None
    max_age_seconds: float | None
    invalidation_reason: str | None
    opportunity: OpportunityContent | None


@dataclass(frozen=True)
class EligibilitySnapshot:
    mode: ExecutionMode
    as_of: datetime
    reset_boundary: datetime | None
    reset_count: int
    assessments: tuple[CandidateAssessment, ...]  # sorted by symbol, strategy, version

    @property
    def eligible(self) -> tuple[CandidateAssessment, ...]:
        return tuple(item for item in self.assessments if item.eligible)


def _assess(record: EvaluationRecord, as_of: datetime, policy: CandidateFreshnessPolicy | None) -> CandidateAssessment:
    age = (as_of - record.source_interval_close).total_seconds()
    age_seconds = age if age >= 0 else None
    max_age = policy.max_age_for(record.trigger_timeframe) if policy is not None else None

    reasons: list[str] = []
    if record.kind != "opportunity":
        reasons.append(record.kind)
    if record.invalidation is not None:
        reasons.append(f"{REASON_INVALIDATED}:{record.invalidation.reason}")
    if record.kind == "opportunity":
        assert record.opportunity is not None
        if record.opportunity.status != "actionable":
            reasons.append(REASON_NOT_ACTIONABLE)
        if max_age is None:
            reasons.append(REASON_POLICY_UNCONFIGURED)
        elif age_seconds is None:
            reasons.append(REASON_SOURCE_AGE_UNAVAILABLE)
        elif age_seconds > max_age:
            reasons.append(REASON_EXPIRED)

    return CandidateAssessment(
        symbol=record.symbol,
        strategy=record.strategy,
        strategy_version=record.strategy_version,
        trigger_timeframe=record.trigger_timeframe,
        kind=record.kind,
        disposition_reason=record.disposition_reason,
        evaluation_id=record.evaluation_id,
        candidate_id=record.candidate_id,
        direction=record.opportunity.direction if record.opportunity is not None else None,
        eligible=record.kind == "opportunity" and not reasons,
        reasons=tuple(reasons),
        source_candle_ts=record.source_candle_ts,
        source_interval_start=record.source_interval_start,
        source_interval_close=record.source_interval_close,
        completed_at=record.completed_at,
        received_at=record.received_at,
        age_seconds=age_seconds,
        max_age_seconds=max_age,
        invalidation_reason=record.invalidation.reason if record.invalidation is not None else None,
        opportunity=record.opportunity,
    )


def assess_candidates(
    state: CandidateState, as_of: datetime, policy: CandidateFreshnessPolicy | None = None
) -> EligibilitySnapshot:
    """Return a detached, deterministically ordered eligibility snapshot."""
    as_of = to_utc(as_of, "as_of")
    records = sorted(state.slots.values(), key=lambda r: (r.symbol, r.strategy, r.strategy_version))
    return EligibilitySnapshot(
        mode=state.mode,
        as_of=as_of,
        reset_boundary=state.reset_boundary,
        reset_count=state.reset_count,
        assessments=tuple(_assess(record, as_of, policy) for record in records),
    )
