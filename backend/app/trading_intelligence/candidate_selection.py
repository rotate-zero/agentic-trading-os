"""Pure provisional selection: ``unique_candidate_v1`` (D1, §19.4).

``select`` consumes a captured :class:`RankingResult`, an explicit
:class:`PortfolioInput`, an explicit :class:`SlotPolicy`, the already attempted
candidate IDs and an explicit audit identity, and returns a detached
:class:`SelectionResult`: ``selected(candidate_id)`` or ``abstained(reasons)``.
It reads no clock, queries no database, publishes no event and authorizes
nothing: a selection is shadow evidence, not an approval, reservation or order.

``unique_candidate_v1`` -- after hard eligibility (already settled by C1 and
the capture cutoff), existing-symbol exposure and slot checks, select only when
exactly one candidate remains globally. Everything else abstains explicitly:

* several candidates -- including several strategies agreeing on one symbol and
  direction, opposite directions, or several symbols;
* Portfolio State missing, unavailable, not ledger-synchronized or for another
  execution mode, or no slot policy supplied.

No confidence, arrival order, ``received_at``, candidate-ID order, ranking
status or evidence is ever a tie-break; they are not read for the decision.

Attempts: ambiguity is judged on the captured set *before* attempted IDs are
removed, so a rejected candidate can never turn an ambiguous set into a forced
winner, and an already attempted lone survivor is never proposed again (at most
:data:`MAX_ATTEMPTS_PER_CANDIDATE` per candidate). Later arrivals never enter
the set because the captured :class:`RankingResult` is the only candidate
source. No retry worker exists here.

Slots follow the existing Governor reading (rule 4): every open position and
in-flight entry counts, and a cycle may propose a candidate only while that
count is below the explicit maximum. The maximum is an input; this module has
no default, and no buying-power, correlation, risk or portfolio-freshness rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Literal, Protocol, Sequence

from app.trading_intelligence.candidate_contract import canonical_timestamp
from app.trading_intelligence.candidate_ranking import CandidateRef, ExcludedAssessment, RankingResult, SnapshotCutoff
from app.trading_intelligence.decision_evidence import DecisionContractError, require_text, utc

SELECTION_POLICY_UNIQUE_CANDIDATE_V1 = "unique_candidate_v1"
SELECTION_RESULT_SCHEMA_VERSION = 1
MAX_ATTEMPTS_PER_CANDIDATE = 1

SelectionStatus = Literal["selected", "abstained"]

# Global abstention reasons.
REASON_PORTFOLIO_MISSING = "portfolio_state_missing"
REASON_PORTFOLIO_UNAVAILABLE = "portfolio_state_unavailable"
REASON_PORTFOLIO_NOT_SYNCHRONIZED = "portfolio_ledger_not_synchronized"
REASON_PORTFOLIO_MODE_MISMATCH = "portfolio_mode_mismatch"
REASON_SLOT_POLICY_UNCONFIGURED = "slot_policy_unconfigured"
REASON_NO_ELIGIBLE = "no_eligible_candidates"
REASON_ALL_EXCLUDED = "all_candidates_excluded"
REASON_NO_SLOTS = "no_available_slots"
REASON_MULTIPLE = "multiple_surviving_candidates"
REASON_OPPOSITE_DIRECTIONS = "opposite_directions"
REASON_ATTEMPTS_DO_NOT_RESOLVE = "attempted_candidates_do_not_resolve_ambiguity"
REASON_ALREADY_ATTEMPTED = "candidate_already_attempted"

# Per-candidate exclusion stages and reasons added by selection.
STAGE_EXPOSURE = "exposure"
STAGE_SLOTS = "slots"
STAGE_ATTEMPTED = "attempted"
REASON_SYMBOL_OPEN_POSITION = "symbol_busy:open_position"
REASON_SYMBOL_IN_FLIGHT = "symbol_busy:in_flight_order"
REASON_NO_SLOT = "no_available_slot"
REASON_ATTEMPTED = "already_attempted"

CONFLICT_SAME_SYMBOL_SAME_DIRECTION = "same_symbol_same_direction"
CONFLICT_SAME_SYMBOL_OPPOSITE_DIRECTION = "same_symbol_opposite_direction"
CONFLICT_MULTIPLE_SYMBOLS = "multiple_symbols"


# --- Inputs ---------------------------------------------------------------


@dataclass(frozen=True)
class SlotPolicy:
    """Explicit maximum of concurrent open positions plus in-flight entries."""

    max_concurrent_positions: int

    def __post_init__(self) -> None:
        value = self.max_concurrent_positions
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise DecisionContractError("max_concurrent_positions must be a positive integer")


@dataclass(frozen=True)
class PortfolioExposureRef:
    """One counted exposure: an open position or an in-flight entry for ``symbol``."""

    symbol: str
    is_in_flight: bool

    def __post_init__(self) -> None:
        require_text(self.symbol, "exposure symbol")
        if not isinstance(self.is_in_flight, bool):
            raise DecisionContractError("is_in_flight must be a bool")


class _SnapshotExposure(Protocol):
    symbol: str
    is_in_flight: bool


class _PortfolioSnapshotLike(Protocol):
    """The slice of ``portfolio_state.snapshot.PortfolioSnapshot`` this module reads.

    Structural on purpose: the pure core imports no Portfolio State module.
    """

    execution_mode: str
    as_of: datetime
    exposures: Sequence[_SnapshotExposure]


@dataclass(frozen=True)
class PortfolioInput:
    """An explicit, detached Portfolio State capture.

    ``captured_at`` is the snapshot's own ``as_of`` and proves nothing about
    ledger synchronization: ``ledger_synchronized`` is a separate, explicit
    statement by the caller that the ledger catch-up was complete when this was
    read. An unavailable capture carries no exposures.
    """

    execution_mode: str
    captured_at: datetime
    available: bool
    ledger_synchronized: bool
    exposures: tuple[PortfolioExposureRef, ...] = ()
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        require_text(self.execution_mode, "portfolio execution_mode")
        object.__setattr__(self, "captured_at", utc(self.captured_at, "portfolio captured_at"))
        if not isinstance(self.available, bool) or not isinstance(self.ledger_synchronized, bool):
            raise DecisionContractError("available and ledger_synchronized must be bool")
        exposures = tuple(self.exposures)
        if any(not isinstance(item, PortfolioExposureRef) for item in exposures):
            raise DecisionContractError("exposures must be PortfolioExposureRef values")
        if not self.available and (exposures or self.ledger_synchronized):
            raise DecisionContractError("an unavailable portfolio carries no exposures and is never synchronized")
        if self.unavailable_reason is not None:
            require_text(self.unavailable_reason, "unavailable_reason")
        object.__setattr__(
            self, "exposures", tuple(sorted(exposures, key=lambda item: (item.symbol, item.is_in_flight)))
        )

    @classmethod
    def unavailable(cls, execution_mode: str, captured_at: datetime, reason: str) -> "PortfolioInput":
        return cls(execution_mode, captured_at, False, False, (), reason)

    @classmethod
    def from_snapshot(cls, snapshot: _PortfolioSnapshotLike, *, ledger_synchronized: bool) -> "PortfolioInput":
        """Detached copy of a Portfolio State snapshot's counted exposures.

        ``ledger_synchronized`` has no default: a freshly stamped ``as_of`` does
        not prove the ledger caught up, so the caller must state readiness.
        """
        exposures = tuple(PortfolioExposureRef(item.symbol, bool(item.is_in_flight)) for item in snapshot.exposures)
        return cls(snapshot.execution_mode, snapshot.as_of, True, ledger_synchronized, exposures)

    def to_audit_record(self) -> dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "captured_at": canonical_timestamp(self.captured_at),
            "available": self.available,
            "ledger_synchronized": self.ledger_synchronized,
            "unavailable_reason": self.unavailable_reason,
            "exposures": [{"symbol": item.symbol, "is_in_flight": item.is_in_flight} for item in self.exposures],
        }


# --- Outputs --------------------------------------------------------------


@dataclass(frozen=True)
class SelectionExclusion:
    """A captured assessment or candidate that did not survive, with stage and reasons."""

    evaluation_id: str
    candidate_id: str | None
    symbol: str
    strategy: str
    strategy_version: str
    stage: str
    reasons: tuple[str, ...]

    def to_audit_record(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "stage": self.stage,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ConflictGroup:
    kind: str
    symbol: str | None
    directions: tuple[str, ...]
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class SelectionResult:
    schema_version: int
    status: SelectionStatus
    selected_candidate_id: str | None
    selected_candidate: CandidateRef | None
    abstention_reasons: tuple[str, ...]
    selection_policy: str
    ranking_status: str
    ranking_policy_id: str | None
    ranking_policy_version: str | None
    ranking_adapter_version: str
    execution_mode: str
    audit_id: str
    eligibility_as_of: datetime
    evidence_as_of: datetime
    cutoff: SnapshotCutoff
    reset_boundary: datetime | None
    portfolio_captured_at: datetime | None
    portfolio: PortfolioInput | None
    slot_policy: SlotPolicy | None
    slots_available: int | None
    considered_candidate_ids: tuple[str, ...]  # sorted by ID for reproducibility only
    exclusions: tuple[SelectionExclusion, ...]
    conflict_groups: tuple[ConflictGroup, ...]
    attempted_candidate_ids: tuple[str, ...]
    attempted_not_in_captured_set: tuple[str, ...]

    @property
    def shadow(self) -> bool:
        """Always True: D1 output is evidence, never an entry decision."""
        return True

    @property
    def authorizes_trade(self) -> bool:
        """Always False: no approval, reservation, accepted-trade ID or order exists."""
        return False

    def to_audit_record(self) -> dict[str, Any]:
        """A fresh, JSON-safe mapping of the complete result."""
        return {
            "schema_version": self.schema_version,
            "shadow": self.shadow,
            "authorizes_trade": self.authorizes_trade,
            "status": self.status,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_candidate": None if self.selected_candidate is None else self.selected_candidate.to_audit_record(),
            "abstention_reasons": list(self.abstention_reasons),
            "selection_policy": self.selection_policy,
            "ranking_status": self.ranking_status,
            "ranking_policy_id": self.ranking_policy_id,
            "ranking_policy_version": self.ranking_policy_version,
            "ranking_adapter_version": self.ranking_adapter_version,
            "execution_mode": self.execution_mode,
            "audit_id": self.audit_id,
            "eligibility_as_of": canonical_timestamp(self.eligibility_as_of),
            "evidence_as_of": canonical_timestamp(self.evidence_as_of),
            "cutoff": self.cutoff.to_audit_record(),
            "reset_boundary": None if self.reset_boundary is None else canonical_timestamp(self.reset_boundary),
            "portfolio_captured_at": (
                None if self.portfolio_captured_at is None else canonical_timestamp(self.portfolio_captured_at)
            ),
            "portfolio": None if self.portfolio is None else self.portfolio.to_audit_record(),
            "slot_policy": (
                None
                if self.slot_policy is None
                else {"max_concurrent_positions": self.slot_policy.max_concurrent_positions}
            ),
            "slots_available": self.slots_available,
            "considered_candidate_ids": list(self.considered_candidate_ids),
            "exclusions": [item.to_audit_record() for item in self.exclusions],
            "conflict_groups": [
                {
                    "kind": group.kind,
                    "symbol": group.symbol,
                    "directions": list(group.directions),
                    "candidate_ids": list(group.candidate_ids),
                }
                for group in self.conflict_groups
            ],
            "attempted_candidate_ids": list(self.attempted_candidate_ids),
            "attempted_not_in_captured_set": list(self.attempted_not_in_captured_set),
        }


# --- Bounded feedback contract --------------------------------------------


def normalize_attempted(attempted_candidate_ids: Iterable[str]) -> tuple[str, ...]:
    """Validated, de-duplicated, sorted attempted IDs (sorting is for reproducibility)."""
    if isinstance(attempted_candidate_ids, (str, bytes)):
        raise DecisionContractError("attempted_candidate_ids must be an iterable of candidate IDs")
    ids = [require_text(item, "attempted candidate id") for item in attempted_candidate_ids]
    return tuple(sorted(set(ids)))


def record_attempt(attempted_candidate_ids: Iterable[str], candidate_id: str) -> tuple[str, ...]:
    """The attempted set after one more attempt; a second attempt is refused.

    This is the whole feedback contract: the coordinator (a later task) records
    a Planning failure or ordinary Governor rejection here and passes the result
    to the next ``select`` on the SAME captured set. At most one attempt per
    candidate; no retry loop lives in this module.
    """
    current = normalize_attempted(attempted_candidate_ids)
    require_text(candidate_id, "candidate_id")
    if candidate_id in current:
        raise DecisionContractError(
            f"candidate {candidate_id!r} was already attempted (limit {MAX_ATTEMPTS_PER_CANDIDATE} per candidate)"
        )
    return tuple(sorted(current + (candidate_id,)))


# --- Selection ------------------------------------------------------------


def _from_ranking_exclusion(item: ExcludedAssessment) -> SelectionExclusion:
    return SelectionExclusion(
        evaluation_id=item.evaluation_id,
        candidate_id=item.candidate_id,
        symbol=item.symbol,
        strategy=item.strategy,
        strategy_version=item.strategy_version,
        stage=item.stage,
        reasons=item.reasons,
    )


def _exclusion(candidate: CandidateRef, stage: str, reasons: tuple[str, ...]) -> SelectionExclusion:
    return SelectionExclusion(
        evaluation_id=candidate.evaluation_id,
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        strategy=candidate.strategy,
        strategy_version=candidate.strategy_version,
        stage=stage,
        reasons=reasons,
    )


def _conflict_groups(survivors: Sequence[CandidateRef]) -> tuple[ConflictGroup, ...]:
    groups: list[ConflictGroup] = []
    for symbol in sorted({item.symbol for item in survivors}):
        members = [item for item in survivors if item.symbol == symbol]
        if len(members) < 2:
            continue
        directions = tuple(sorted({item.direction for item in members}))
        kind = CONFLICT_SAME_SYMBOL_SAME_DIRECTION if len(directions) == 1 else CONFLICT_SAME_SYMBOL_OPPOSITE_DIRECTION
        groups.append(ConflictGroup(kind, symbol, directions, tuple(sorted(item.candidate_id for item in members))))
    if len({item.symbol for item in survivors}) > 1:
        groups.append(
            ConflictGroup(
                CONFLICT_MULTIPLE_SYMBOLS,
                None,
                tuple(sorted({item.direction for item in survivors})),
                tuple(sorted(item.candidate_id for item in survivors)),
            )
        )
    return tuple(groups)


def select(
    ranking_result: RankingResult,
    portfolio: PortfolioInput | None,
    attempted_candidate_ids: Iterable[str] = (),
    *,
    selection_policy: str,
    slot_policy: SlotPolicy | None,
    audit_id: str,
) -> SelectionResult:
    """Provisionally select at most one candidate, or abstain, deterministically.

    ``selection_policy`` has no default: the policy must be chosen explicitly and
    only ``"unique_candidate_v1"`` exists. ``portfolio=None`` and
    ``slot_policy=None`` are valid inputs that cause explicit abstention.
    """
    if selection_policy != SELECTION_POLICY_UNIQUE_CANDIDATE_V1:
        raise DecisionContractError(f"unsupported selection policy {selection_policy!r}")
    if not isinstance(ranking_result, RankingResult):
        raise DecisionContractError("ranking_result must be a RankingResult")
    if portfolio is not None and not isinstance(portfolio, PortfolioInput):
        raise DecisionContractError("portfolio must be a PortfolioInput or None")
    if slot_policy is not None and not isinstance(slot_policy, SlotPolicy):
        raise DecisionContractError("slot_policy must be a SlotPolicy or None")
    audit_id = require_text(audit_id, "audit_id")
    attempted = normalize_attempted(attempted_candidate_ids)

    captured = ranking_result.candidates
    captured_ids = ranking_result.candidate_ids
    attempted_outside = tuple(item for item in attempted if item not in captured_ids)
    exclusions: list[SelectionExclusion] = [_from_ranking_exclusion(item) for item in ranking_result.excluded]
    reasons: list[str] = []
    conflicts: tuple[ConflictGroup, ...] = ()
    selected: CandidateRef | None = None
    slots_available: int | None = None

    # Global preconditions: without usable Portfolio State and a slot policy the
    # exposure/slot checks cannot be made, so the cycle abstains.
    if portfolio is None:
        reasons.append(REASON_PORTFOLIO_MISSING)
    else:
        if not portfolio.available:
            reasons.append(REASON_PORTFOLIO_UNAVAILABLE)
        elif not portfolio.ledger_synchronized:
            reasons.append(REASON_PORTFOLIO_NOT_SYNCHRONIZED)
        if portfolio.execution_mode != ranking_result.execution_mode:
            reasons.append(REASON_PORTFOLIO_MODE_MISMATCH)
    if slot_policy is None:
        reasons.append(REASON_SLOT_POLICY_UNCONFIGURED)

    if not reasons:
        assert portfolio is not None and slot_policy is not None
        open_symbols = {item.symbol for item in portfolio.exposures if not item.is_in_flight}
        in_flight_symbols = {item.symbol for item in portfolio.exposures if item.is_in_flight}
        slots_available = slot_policy.max_concurrent_positions - len(portfolio.exposures)

        survivors: list[CandidateRef] = []
        for candidate in captured:
            busy: list[str] = []
            if candidate.symbol in open_symbols:
                busy.append(REASON_SYMBOL_OPEN_POSITION)
            if candidate.symbol in in_flight_symbols:
                busy.append(REASON_SYMBOL_IN_FLIGHT)
            if busy:
                exclusions.append(_exclusion(candidate, STAGE_EXPOSURE, tuple(busy)))
            else:
                survivors.append(candidate)

        if survivors and slots_available <= 0:
            for candidate in survivors:
                exclusions.append(_exclusion(candidate, STAGE_SLOTS, (REASON_NO_SLOT,)))
            survivors = []
            reasons.append(REASON_NO_SLOTS)

        if not survivors:
            reasons.append(REASON_NO_ELIGIBLE if not captured else REASON_ALL_EXCLUDED)
        elif len(survivors) > 1:
            conflicts = _conflict_groups(survivors)
            reasons.append(REASON_MULTIPLE)
            if len({item.direction for item in survivors}) > 1:
                reasons.append(REASON_OPPOSITE_DIRECTIONS)
            reasons.extend(f"conflict:{group.kind}" for group in conflicts)
            if any(item.candidate_id in attempted for item in survivors):
                reasons.append(REASON_ATTEMPTS_DO_NOT_RESOLVE)
        else:
            (only,) = survivors
            if only.candidate_id in attempted:
                exclusions.append(_exclusion(only, STAGE_ATTEMPTED, (REASON_ATTEMPTED,)))
                reasons.append(REASON_ALREADY_ATTEMPTED)
            else:
                selected = only

    exclusions_sorted = tuple(
        sorted(exclusions, key=lambda item: (item.stage, item.symbol, item.strategy, item.strategy_version, item.evaluation_id))
    )
    return SelectionResult(
        schema_version=SELECTION_RESULT_SCHEMA_VERSION,
        status="selected" if selected is not None else "abstained",
        selected_candidate_id=None if selected is None else selected.candidate_id,
        selected_candidate=selected,
        abstention_reasons=tuple(reasons) if selected is None else (),
        selection_policy=selection_policy,
        ranking_status=ranking_result.status,
        ranking_policy_id=ranking_result.ranking_policy_id,
        ranking_policy_version=ranking_result.ranking_policy_version,
        ranking_adapter_version=ranking_result.adapter_version,
        execution_mode=ranking_result.execution_mode,
        audit_id=audit_id,
        eligibility_as_of=ranking_result.eligibility_as_of,
        evidence_as_of=ranking_result.evidence_as_of,
        cutoff=ranking_result.cutoff,
        reset_boundary=ranking_result.reset_boundary,
        portfolio_captured_at=None if portfolio is None else portfolio.captured_at,
        portfolio=portfolio,
        slot_policy=slot_policy,
        slots_available=slots_available,
        considered_candidate_ids=captured_ids,
        exclusions=exclusions_sorted,
        conflict_groups=conflicts,
        attempted_candidate_ids=attempted,
        attempted_not_in_captured_set=attempted_outside,
    )
