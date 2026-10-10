"""Pure ranking boundary and the honest unranked adapter (D1, §19.3).

``rank_candidates`` consumes an explicit, already-captured C1
:class:`EligibilitySnapshot`, an explicit :class:`EvidenceSnapshot` and an
explicit capture cutoff, and returns a detached :class:`RankingResult`. It
recalculates no strategy signal, reads no historical Opportunity Cache, queries
no database and reads no clock.

D4 (the ranking policy) is open, so the only adapter is the *unranked* one:
it never orders candidates, invents no score, weight, confidence threshold,
sample minimum, agreement bonus or Kelly estimate, and reports
``status="unranked"``. Candidates are listed sorted by ``candidate_id`` purely
so the same captured set always serialises identically; that order is neither
a ranking nor a tie-break and ``ranked_order`` stays empty. Asking for any
named ranking policy yields ``status="unavailable"`` because no such policy is
approved or implemented.

Evidence is attributed, never blended: only records for the same strategy
*and* version (and, when the record names them, symbol and direction) attach to
a candidate. Records that are not provably knowable at the evidence as-of
instant are excluded from every candidate and reported, so future outcomes
cannot leak into a result. Missing provenance, empty samples, synthetic
mechanics evidence and mixed populations are reported as explicit gaps.

The result authorizes nothing and is not an order, trade or accepted-trade ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.trading_intelligence.candidate_contract import Direction, canonical_timestamp
from app.trading_intelligence.candidate_eligibility import CandidateAssessment, EligibilitySnapshot
from app.trading_intelligence.decision_evidence import (
    DecisionContractError,
    EvidenceRecord,
    EvidenceSnapshot,
    require_text,
    utc,
)

RANKING_RESULT_SCHEMA_VERSION = 1
UNRANKED_ADAPTER_VERSION = "unranked_adapter_v1"

RankingStatus = Literal["ranked", "unranked", "unavailable"]

REASON_NO_APPROVED_POLICY = "no_approved_ranking_policy"
REASON_NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
REASON_POLICY_NOT_IMPLEMENTED = "ranking_policy_not_implemented"

STAGE_ELIGIBILITY = "eligibility"
STAGE_CAPTURE_CUTOFF = "capture_cutoff"
REASON_RECEIVED_AFTER_CUTOFF = "received_after_cutoff"

EVIDENCE_AVAILABILITY_UNKNOWN = "availability_time_unknown"
EVIDENCE_AFTER_AS_OF = "unavailable_at_as_of"
EVIDENCE_SAMPLE_AFTER_AS_OF = "sample_period_after_as_of"
EVIDENCE_NO_MATCH = "no_matching_candidate"


@dataclass(frozen=True)
class SnapshotCutoff:
    """The recorded cutoff that froze the captured set (§19.4).

    ``arrival_sequence`` is the coordinator's arrival-sequence number at the
    freeze and ``cutoff_at`` its instant; both are explicit inputs. A
    candidate received after ``cutoff_at`` is a later arrival and is never
    pulled into the captured set.
    """

    arrival_sequence: int
    cutoff_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.arrival_sequence, bool) or not isinstance(self.arrival_sequence, int) or self.arrival_sequence < 0:
            raise DecisionContractError("arrival_sequence must be a non-negative integer")
        object.__setattr__(self, "cutoff_at", utc(self.cutoff_at, "cutoff_at"))

    def to_audit_record(self) -> dict[str, Any]:
        return {"arrival_sequence": self.arrival_sequence, "cutoff_at": canonical_timestamp(self.cutoff_at)}


@dataclass(frozen=True)
class RankingPolicyRef:
    """A *requested* ranking policy. None is approved, so none is implemented."""

    policy_id: str
    version: str

    def __post_init__(self) -> None:
        require_text(self.policy_id, "ranking policy id")
        require_text(self.version, "ranking policy version")


@dataclass(frozen=True)
class CandidateRef:
    """Detached description of one captured eligible candidate (no OpportunityContent)."""

    candidate_id: str
    evaluation_id: str
    symbol: str
    strategy: str
    strategy_version: str
    trigger_timeframe: str
    direction: Direction
    source_candle_ts: datetime
    source_interval_start: datetime
    source_interval_close: datetime
    completed_at: datetime
    received_at: datetime
    age_seconds: float | None
    max_age_seconds: float | None
    reported_confidence: float  # descriptive only; nothing in D1 reads it for a decision

    def to_audit_record(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "evaluation_id": self.evaluation_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "trigger_timeframe": self.trigger_timeframe,
            "direction": self.direction,
            "source_candle_ts": canonical_timestamp(self.source_candle_ts),
            "source_interval_start": canonical_timestamp(self.source_interval_start),
            "source_interval_close": canonical_timestamp(self.source_interval_close),
            "completed_at": canonical_timestamp(self.completed_at),
            "received_at": canonical_timestamp(self.received_at),
            "age_seconds": self.age_seconds,
            "max_age_seconds": self.max_age_seconds,
            "reported_confidence": self.reported_confidence,
        }


@dataclass(frozen=True)
class ExcludedAssessment:
    """A captured assessment that is not a candidate, with its stage and reasons."""

    evaluation_id: str
    candidate_id: str | None
    symbol: str
    strategy: str
    strategy_version: str
    kind: str
    direction: str | None
    stage: str
    reasons: tuple[str, ...]

    def to_audit_record(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "kind": self.kind,
            "direction": self.direction,
            "stage": self.stage,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class DirectionGroup:
    direction: Direction
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class SymbolGroup:
    """Candidates sharing a symbol; ``relation`` states whether they agree in direction."""

    symbol: str
    candidate_ids: tuple[str, ...]
    directions: tuple[str, ...]
    relation: Literal["single", "same_direction", "opposite_direction"]


@dataclass(frozen=True)
class ComparabilityGroup:
    """Descriptive grouping by trigger timeframe only.

    No approved policy defines comparability, so every group is ``unassessed``;
    this is a label for audit, never a claim that members can be compared.
    """

    trigger_timeframe: str
    candidate_ids: tuple[str, ...]
    comparability: Literal["unassessed"] = "unassessed"


@dataclass(frozen=True)
class ExcludedEvidence:
    evidence_ref: str
    reason: str


@dataclass(frozen=True)
class CandidateEvidence:
    """Evidence attributed to one candidate, what was excluded, and the gaps."""

    candidate_id: str
    attributed: tuple[EvidenceRecord, ...]
    excluded: tuple[ExcludedEvidence, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class RankingResult:
    schema_version: int
    status: RankingStatus
    reasons: tuple[str, ...]
    ranking_policy_id: str | None
    ranking_policy_version: str | None
    adapter_version: str
    execution_mode: str
    eligibility_as_of: datetime
    evidence_as_of: datetime
    cutoff: SnapshotCutoff
    reset_boundary: datetime | None
    reset_count: int
    candidates: tuple[CandidateRef, ...]  # sorted by candidate_id: reproducibility/display only
    excluded: tuple[ExcludedAssessment, ...]
    ranked_order: tuple[str, ...]  # empty unless a future approved policy ranks
    direction_groups: tuple[DirectionGroup, ...]
    symbol_groups: tuple[SymbolGroup, ...]
    comparability_groups: tuple[ComparabilityGroup, ...]
    candidate_evidence: tuple[CandidateEvidence, ...]
    unattributed_evidence: tuple[ExcludedEvidence, ...]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.candidates)

    def to_audit_record(self) -> dict[str, Any]:
        """A fresh, JSON-safe mapping of the complete result."""
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reasons": list(self.reasons),
            "ranking_policy_id": self.ranking_policy_id,
            "ranking_policy_version": self.ranking_policy_version,
            "adapter_version": self.adapter_version,
            "execution_mode": self.execution_mode,
            "eligibility_as_of": canonical_timestamp(self.eligibility_as_of),
            "evidence_as_of": canonical_timestamp(self.evidence_as_of),
            "cutoff": self.cutoff.to_audit_record(),
            "reset_boundary": None if self.reset_boundary is None else canonical_timestamp(self.reset_boundary),
            "reset_count": self.reset_count,
            "candidates": [item.to_audit_record() for item in self.candidates],
            "excluded": [item.to_audit_record() for item in self.excluded],
            "ranked_order": list(self.ranked_order),
            "direction_groups": [
                {"direction": group.direction, "candidate_ids": list(group.candidate_ids)}
                for group in self.direction_groups
            ],
            "symbol_groups": [
                {
                    "symbol": group.symbol,
                    "candidate_ids": list(group.candidate_ids),
                    "directions": list(group.directions),
                    "relation": group.relation,
                }
                for group in self.symbol_groups
            ],
            "comparability_groups": [
                {
                    "trigger_timeframe": group.trigger_timeframe,
                    "candidate_ids": list(group.candidate_ids),
                    "comparability": group.comparability,
                }
                for group in self.comparability_groups
            ],
            "candidate_evidence": [
                {
                    "candidate_id": item.candidate_id,
                    "attributed": [record.to_audit_record() for record in item.attributed],
                    "excluded": [{"evidence_ref": e.evidence_ref, "reason": e.reason} for e in item.excluded],
                    "gaps": list(item.gaps),
                }
                for item in self.candidate_evidence
            ],
            "unattributed_evidence": [
                {"evidence_ref": item.evidence_ref, "reason": item.reason} for item in self.unattributed_evidence
            ],
        }


# --- Internals -------------------------------------------------------------


def _candidate_ref(assessment: CandidateAssessment) -> CandidateRef:
    assert assessment.candidate_id is not None and assessment.opportunity is not None
    assert assessment.direction is not None
    return CandidateRef(
        candidate_id=assessment.candidate_id,
        evaluation_id=assessment.evaluation_id,
        symbol=assessment.symbol,
        strategy=assessment.strategy,
        strategy_version=assessment.strategy_version,
        trigger_timeframe=assessment.trigger_timeframe,
        direction=assessment.direction,
        source_candle_ts=assessment.source_candle_ts,
        source_interval_start=assessment.source_interval_start,
        source_interval_close=assessment.source_interval_close,
        completed_at=assessment.completed_at,
        received_at=assessment.received_at,
        age_seconds=assessment.age_seconds,
        max_age_seconds=assessment.max_age_seconds,
        reported_confidence=assessment.opportunity.confidence,
    )


def _excluded(assessment: CandidateAssessment, stage: str, reasons: tuple[str, ...]) -> ExcludedAssessment:
    return ExcludedAssessment(
        evaluation_id=assessment.evaluation_id,
        candidate_id=assessment.candidate_id,
        symbol=assessment.symbol,
        strategy=assessment.strategy,
        strategy_version=assessment.strategy_version,
        kind=assessment.kind,
        direction=assessment.direction,
        stage=stage,
        reasons=reasons,
    )


def _leakage_reason(record: EvidenceRecord, as_of: datetime) -> str | None:
    """Why this record may not influence a result at ``as_of`` (None = usable)."""
    if record.available_at is None:
        return EVIDENCE_AVAILABILITY_UNKNOWN
    if record.available_at > as_of:
        return EVIDENCE_AFTER_AS_OF
    if record.sample_period_end is not None and record.sample_period_end > as_of:
        return EVIDENCE_SAMPLE_AFTER_AS_OF
    return None


def _matches(record: EvidenceRecord, candidate: CandidateRef) -> bool:
    return (
        record.strategy == candidate.strategy
        and record.strategy_version == candidate.strategy_version
        and (record.symbol is None or record.symbol == candidate.symbol)
        and (record.direction is None or record.direction == candidate.direction)
    )


def _record_gaps(record: EvidenceRecord) -> list[str]:
    gaps = [f"{record.evidence_ref}:missing:{name}" for name in record.missing_fields]
    if record.is_empty_sample:
        gaps.append(f"{record.evidence_ref}:empty_sample")
    if record.is_synthetic_mechanics:
        gaps.append(f"{record.evidence_ref}:synthetic_mechanics_not_calibration")
    return gaps


def _attribute(
    candidates: tuple[CandidateRef, ...], evidence: EvidenceSnapshot
) -> tuple[tuple[CandidateEvidence, ...], tuple[ExcludedEvidence, ...]]:
    leakage = {record.evidence_ref: _leakage_reason(record, evidence.as_of) for record in evidence.records}
    attached: set[str] = set()
    views: list[CandidateEvidence] = []
    for candidate in candidates:
        attributed: list[EvidenceRecord] = []
        excluded: list[ExcludedEvidence] = []
        for record in evidence.records:  # sorted by evidence_ref
            if not _matches(record, candidate):
                continue
            attached.add(record.evidence_ref)
            reason = leakage[record.evidence_ref]
            if reason is None:
                attributed.append(record)
            else:
                excluded.append(ExcludedEvidence(record.evidence_ref, reason))
        gaps: list[str] = []
        if not attributed:
            gaps.append("no_attributed_evidence")
        else:
            if all(record.is_synthetic_mechanics for record in attributed):
                gaps.append("only_synthetic_mechanics_evidence")
            if len({record.population for record in attributed}) > 1:
                gaps.append("mixed_populations_not_blended")
            for record in attributed:
                gaps.extend(_record_gaps(record))
        views.append(CandidateEvidence(candidate.candidate_id, tuple(attributed), tuple(excluded), tuple(gaps)))
    unattributed = tuple(
        ExcludedEvidence(record.evidence_ref, leakage[record.evidence_ref] or EVIDENCE_NO_MATCH)
        for record in evidence.records
        if record.evidence_ref not in attached
    )
    return tuple(views), unattributed


def _groups(
    candidates: tuple[CandidateRef, ...],
) -> tuple[tuple[DirectionGroup, ...], tuple[SymbolGroup, ...], tuple[ComparabilityGroup, ...]]:
    direction_groups = tuple(
        DirectionGroup(direction, tuple(c.candidate_id for c in candidates if c.direction == direction))
        for direction in ("long", "short")
        if any(c.direction == direction for c in candidates)
    )
    symbol_groups: list[SymbolGroup] = []
    for symbol in sorted({c.symbol for c in candidates}):
        members = [c for c in candidates if c.symbol == symbol]
        directions = tuple(sorted({c.direction for c in members}))
        if len(members) == 1:
            relation: Literal["single", "same_direction", "opposite_direction"] = "single"
        elif len(directions) == 1:
            relation = "same_direction"
        else:
            relation = "opposite_direction"
        symbol_groups.append(SymbolGroup(symbol, tuple(c.candidate_id for c in members), directions, relation))
    comparability = tuple(
        ComparabilityGroup(timeframe, tuple(c.candidate_id for c in candidates if c.trigger_timeframe == timeframe))
        for timeframe in sorted({c.trigger_timeframe for c in candidates})
    )
    return direction_groups, tuple(symbol_groups), comparability


# --- Public boundary ---------------------------------------------------------


def rank_candidates(
    snapshot: EligibilitySnapshot,
    evidence: EvidenceSnapshot,
    *,
    cutoff: SnapshotCutoff,
    ranking_policy: RankingPolicyRef | None = None,
) -> RankingResult:
    """Capture the candidate set and describe it; never order or score it.

    Eligible candidates received after ``cutoff.cutoff_at`` are later arrivals
    and are excluded with ``received_after_cutoff``; every other assessment
    keeps its C1 reasons. With ``ranking_policy=None`` the status is
    ``unranked``; naming any policy gives ``unavailable`` because none is
    approved or implemented (D4).
    """
    if not isinstance(snapshot, EligibilitySnapshot):
        raise DecisionContractError("snapshot must be a C1 EligibilitySnapshot")
    if not isinstance(evidence, EvidenceSnapshot):
        raise DecisionContractError("evidence must be an EvidenceSnapshot")
    if not isinstance(cutoff, SnapshotCutoff):
        raise DecisionContractError("cutoff must be a SnapshotCutoff")
    if ranking_policy is not None and not isinstance(ranking_policy, RankingPolicyRef):
        raise DecisionContractError("ranking_policy must be a RankingPolicyRef or None")

    candidates: list[CandidateRef] = []
    excluded: list[ExcludedAssessment] = []
    for assessment in snapshot.assessments:
        if not assessment.eligible:
            excluded.append(_excluded(assessment, STAGE_ELIGIBILITY, assessment.reasons))
        elif assessment.received_at > cutoff.cutoff_at:
            excluded.append(_excluded(assessment, STAGE_CAPTURE_CUTOFF, (REASON_RECEIVED_AFTER_CUTOFF,)))
        else:
            candidates.append(_candidate_ref(assessment))
    captured = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    excluded_sorted = tuple(
        sorted(excluded, key=lambda item: (item.symbol, item.strategy, item.strategy_version, item.evaluation_id))
    )

    direction_groups, symbol_groups, comparability_groups = _groups(captured)
    candidate_evidence, unattributed = _attribute(captured, evidence)

    reasons: list[str] = []
    if not captured:
        reasons.append(REASON_NO_ELIGIBLE_CANDIDATES)
    if ranking_policy is None:
        status: RankingStatus = "unranked"
        reasons.append(REASON_NO_APPROVED_POLICY)
    else:
        status = "unavailable"
        reasons.append(f"{REASON_POLICY_NOT_IMPLEMENTED}:{ranking_policy.policy_id}@{ranking_policy.version}")

    return RankingResult(
        schema_version=RANKING_RESULT_SCHEMA_VERSION,
        status=status,
        reasons=tuple(reasons),
        ranking_policy_id=None if ranking_policy is None else ranking_policy.policy_id,
        ranking_policy_version=None if ranking_policy is None else ranking_policy.version,
        adapter_version=UNRANKED_ADAPTER_VERSION,
        execution_mode=snapshot.mode,
        eligibility_as_of=snapshot.as_of,
        evidence_as_of=evidence.as_of,
        cutoff=cutoff,
        reset_boundary=snapshot.reset_boundary,
        reset_count=snapshot.reset_count,
        candidates=captured,
        excluded=excluded_sorted,
        ranked_order=(),
        direction_groups=direction_groups,
        symbol_groups=symbol_groups,
        comparability_groups=comparability_groups,
        candidate_evidence=candidate_evidence,
        unattributed_evidence=unattributed,
    )
