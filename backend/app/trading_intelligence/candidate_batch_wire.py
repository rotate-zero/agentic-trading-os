"""Wire helpers between the C1 batch contract and ``StrategyEvaluationCompleted``
(C2, trading-intelligence-architecture.md §19.2).

No Event Bus, database, Governor or order dependency; the only collaborator is
a ``MarketClock`` passed in for source-interval derivation.

Source interval convention
--------------------------
Built producers stamp ``candle_ts`` at the interval's OPEN: ``CandleClosed``
for 1m, and aggregated 5m/15m/1h ``FeatureSet`` via
``candle_aggregator.bucket_start_for(candle_ts, session_start, width)`` with
``session_start`` from ``MarketClock.session_bounds``. The interval is therefore
``[candle_ts, min(candle_ts + width, session_end))``. The ``min`` is what makes a
session-trailing bucket honest (the final 30 minutes of a regular session at 1h
closes at the session close, not 60 minutes after its open; a half-day session
ends at 13:00 ET). Anything the clock cannot place in a verified session, or an
aggregated candle that is not bucket-aligned, raises
:class:`SourceIntervalUnavailable` instead of guessing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.market_clock import MarketClock
from app.schemas.events.strategy_evaluation import (
    EvaluatedOpportunity,
    StrategyDispositionPayload,
    StrategyEvaluationCompleted,
    UnavailablePrerequisitePayload,
)
from app.services.candle_aggregator import WIDTH_TO_LABEL, bucket_start_for
from app.strategy_engine.base_strategy import Opportunity
from app.trading_intelligence.candidate_contract import (
    CandidateContractError,
    EvaluationBatch,
    OpportunityContent,
    StrategyDisposition,
    UnavailablePrerequisite,
)

_WIDTH_MINUTES: dict[str, int] = {"1m": 1, **{label: width for width, label in WIDTH_TO_LABEL.items()}}
_DIRECTION = {"BUY": "long", "SELL": "short"}


class SourceIntervalUnavailable(Exception):
    """The source interval cannot be derived honestly; ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def as_utc(value: datetime) -> datetime:
    """Aware UTC; a naive value is read as UTC (the convention FeatureEngine's
    own handlers apply to naive candle timestamps)."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def derive_source_interval(timeframe: str, candle_ts: datetime, clock: MarketClock) -> tuple[datetime, datetime]:
    """``(interval_start, interval_close)`` in UTC for a candle in the producer's convention."""
    width = _WIDTH_MINUTES.get(timeframe)
    if width is None:
        raise SourceIntervalUnavailable("unsupported_timeframe")
    candle_ts = as_utc(candle_ts)
    if not clock.has_calendar_for_year(clock.trading_day(candle_ts).year):
        raise SourceIntervalUnavailable("market_calendar_unverified")
    bounds = clock.session_bounds(candle_ts)
    if bounds is None:
        raise SourceIntervalUnavailable("no_session_bounds")
    session_start, session_end = bounds
    if not session_start <= candle_ts < session_end:
        raise SourceIntervalUnavailable("candle_outside_session")
    if width > 1 and bucket_start_for(candle_ts, session_start, width) != candle_ts:
        raise SourceIntervalUnavailable("candle_not_bucket_aligned")
    close = min(candle_ts + timedelta(minutes=width), session_end)
    return candle_ts, close.astimezone(timezone.utc)


def opportunity_content_from(opportunity: Opportunity, strategy: str, strategy_version: str) -> OpportunityContent:
    """Detached candidate content from a strategy ``Opportunity``.

    Raises :class:`CandidateContractError` for anything the contract refuses
    (identity mismatch, unknown direction, non-finite numbers, evidence that is
    not plain JSON). ``wait_expires_at`` and ``setup_detected_at`` are not
    carried; ``expected_horizon_minutes`` stays descriptive.
    """
    if opportunity.strategy != strategy or opportunity.version != strategy_version:
        raise CandidateContractError("opportunity identity does not match the evaluating strategy version")
    direction = _DIRECTION.get(opportunity.direction)
    if direction is None:
        raise CandidateContractError(f"unknown opportunity direction {opportunity.direction!r}")
    return OpportunityContent.create(
        direction=direction,  # type: ignore[arg-type]
        confidence=opportunity.confidence,
        structural_invalidation=opportunity.structural_invalidation,
        structural_target=opportunity.structural_target,
        evidence=opportunity.evidence,
        status=opportunity.status,
        expected_horizon_minutes=opportunity.expected_horizon_minutes,
    )


def batch_to_payload(batch: EvaluationBatch) -> StrategyEvaluationCompleted:
    dispositions = []
    for item in batch.dispositions:
        content = item.opportunity
        dispositions.append(
            StrategyDispositionPayload(
                strategy=item.strategy,
                strategy_version=item.strategy_version,
                kind=item.kind,
                reason=item.reason,
                opportunity=None
                if content is None
                else EvaluatedOpportunity(
                    direction=content.direction,
                    confidence=content.confidence,
                    structural_invalidation=content.structural_invalidation,
                    structural_target=content.structural_target,
                    status=content.status,
                    expected_horizon_minutes=content.expected_horizon_minutes,
                    evidence=content.evidence_copy(),
                ),
            )
        )
    return StrategyEvaluationCompleted(
        timeframe=batch.trigger_timeframe,
        mode=batch.mode,
        source_candle_ts=batch.source_candle_ts,
        source_interval_start=batch.source_interval_start,
        source_interval_close=batch.source_interval_close,
        completed_at=batch.completed_at,
        dispositions=dispositions,
        unavailable_prerequisites=[
            UnavailablePrerequisitePayload(name=p.name, reason=p.reason) for p in batch.unavailable_prerequisites
        ],
    )


def payload_to_batch(symbol: str, payload: StrategyEvaluationCompleted) -> EvaluationBatch:
    """Validate a payload through the C1 contract. Raises :class:`CandidateContractError`."""
    dispositions = []
    for item in payload.dispositions:
        content = None
        if item.opportunity is not None:
            opp = item.opportunity
            content = OpportunityContent.create(
                direction=opp.direction,
                confidence=opp.confidence,
                structural_invalidation=opp.structural_invalidation,
                structural_target=opp.structural_target,
                evidence=opp.evidence,
                status=opp.status,
                expected_horizon_minutes=opp.expected_horizon_minutes,
            )
        dispositions.append(
            StrategyDisposition(
                strategy=item.strategy,
                strategy_version=item.strategy_version,
                kind=item.kind,
                opportunity=content,
                reason=item.reason,
            )
        )
    return EvaluationBatch(
        symbol=symbol,
        trigger_timeframe=payload.timeframe,
        mode=payload.mode,
        source_candle_ts=payload.source_candle_ts,
        source_interval_start=payload.source_interval_start,
        source_interval_close=payload.source_interval_close,
        completed_at=payload.completed_at,
        dispositions=tuple(dispositions),
        unavailable_prerequisites=tuple(UnavailablePrerequisite(p.name, p.reason) for p in payload.unavailable_prerequisites),
    )
