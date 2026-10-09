"""Pure reducer for eligible-candidate state (C1, §19.2).

``apply_batch``, ``apply_reset`` and ``retire_strategy_versions`` are
functions from an immutable :class:`CandidateState` to a new one. There is no
clock (receive and boundary times are explicit arguments), no I/O, no logging,
no Event Bus and no authorization: this module cannot select, plan, reserve or
emit an order event, and it is not subscribed to anything.

Reduction rules
---------------
* Only the latest complete evaluation per ``(symbol, strategy, version)`` is
  retained; a newer disposition of any kind replaces an earlier candidate.
  An older source candle can never replace a newer one (no resurrection).
* Redelivery of the same ``evaluation_id`` with identical content is a no-op;
  differing content is an explicit conflict. Conflicts are atomic: nothing in
  the batch is applied.
* A strategy absent from a batch was not triggered; its earlier record is
  untouched (it is not falsely marked as evaluated).
* A batch with unavailable prerequisites carries no dispositions. It cannot
  contribute candidates, and it invalidates earlier opportunity records of
  that symbol/timeframe from older candles (a conservative C1 rule: a newer
  incoherent candle cannot confirm them). Older or same-candle available
  batches arriving afterwards cannot revive them.
* Session, provider and restart resets clear eligibility and set a reset
  boundary. Any batch whose source interval starts before the boundary is
  rejected, so delayed pre-reset events cannot repopulate the state, while the
  first batch whose interval starts at or after the boundary is admitted. This
  does not claim old-source events already queued were retracted.
* Retired strategy versions lose their records and are refused afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

from app.trading_intelligence.candidate_contract import (
    CandidateContractError,
    DispositionKind,
    EvaluationBatch,
    ExecutionMode,
    OpportunityContent,
    StrategyDisposition,
    UnavailablePrerequisite,
    EXECUTION_MODES,
    to_utc,
)

ResetKind = Literal["session_change", "provider_change", "restart"]
RESET_KINDS: tuple[str, ...] = ("session_change", "provider_change", "restart")

BatchStatus = Literal["applied", "duplicate", "stale", "ignored", "rejected", "conflict"]
DispositionResultKind = Literal["applied", "duplicate", "stale", "rejected", "conflict", "withheld"]

SlotKey = tuple[str, str, str]  # (symbol, strategy, strategy_version)
FrameKey = tuple[str, str]  # (symbol, trigger_timeframe)
VersionKey = tuple[str, str]  # (strategy, strategy_version)

# Deterministic reason codes.
REASON_MODE_MISMATCH = "mode_mismatch"
REASON_PRE_RESET = "pre_reset_boundary"
REASON_VERSION_RETIRED = "version_retired"
REASON_TIMEFRAME_MISMATCH = "slot_timeframe_mismatch"
REASON_SUPERSEDED = "superseded_by_newer_evaluation"
REASON_UNAVAILABLE_FLOOR = "superseded_by_unavailable_input"
REASON_UNAVAILABLE_NEWER = "superseded_by_newer_unavailable_input"
REASON_CONTENT_CONFLICT = "evaluation_content_conflict"
REASON_UNAVAILABLE_CANDLE = "unavailable_input_recorded_for_candle"
REASON_UNAVAILABLE_CONFLICT = "unavailable_input_conflict"
REASON_EVALUATION_CANDLE = "evaluation_recorded_for_candle"
REASON_PREREQUISITE_UNAVAILABLE = "prerequisite_unavailable"


@dataclass(frozen=True)
class Invalidation:
    reason: str
    source_candle_ts: datetime  # candle whose batch caused the invalidation
    invalidated_at: datetime  # local receive time of the invalidating input


@dataclass(frozen=True)
class EvaluationRecord:
    """Latest complete evaluation of one strategy version for one symbol."""

    evaluation_id: str
    candidate_id: str | None
    symbol: str
    strategy: str
    strategy_version: str
    trigger_timeframe: str
    mode: ExecutionMode
    source_candle_ts: datetime
    source_interval_start: datetime
    source_interval_close: datetime
    completed_at: datetime  # producer completion time
    received_at: datetime  # local receive time
    kind: DispositionKind
    disposition_reason: str | None
    opportunity: OpportunityContent | None
    invalidation: Invalidation | None = None


@dataclass(frozen=True)
class UnavailableRecord:
    symbol: str
    trigger_timeframe: str
    source_candle_ts: datetime
    source_interval_start: datetime
    source_interval_close: datetime
    completed_at: datetime
    received_at: datetime
    prerequisites: tuple[UnavailablePrerequisite, ...]


@dataclass(frozen=True)
class ResetRecord:
    kind: ResetKind
    requested_boundary: datetime
    effective_boundary: datetime  # never earlier than a previous boundary
    reason: str | None
    epoch: int


@dataclass(frozen=True)
class RetiredVersion:
    reason: str
    retired_at: datetime


@dataclass(frozen=True)
class CandidateState:
    """Immutable reducer state. Construct with :func:`initial_state`."""

    mode: ExecutionMode
    slots: Mapping[SlotKey, EvaluationRecord]
    unavailable: Mapping[FrameKey, UnavailableRecord]
    retired_versions: Mapping[VersionKey, RetiredVersion]
    reset_boundary: datetime | None = None
    reset_count: int = 0
    last_reset: ResetRecord | None = None

    def __post_init__(self) -> None:
        if self.mode not in EXECUTION_MODES:
            raise CandidateContractError(f"unknown execution mode {self.mode!r}")
        # Own read-only copies: the caller's dicts are never retained.
        object.__setattr__(self, "slots", MappingProxyType(dict(self.slots)))
        object.__setattr__(self, "unavailable", MappingProxyType(dict(self.unavailable)))
        object.__setattr__(self, "retired_versions", MappingProxyType(dict(self.retired_versions)))


def initial_state(mode: ExecutionMode) -> CandidateState:
    """Empty state bound to one execution mode (backtest never shares a store).

    After a process restart or provider change, follow with
    ``apply_reset(state, "restart" | "provider_change", boundary)`` so that only
    fully post-boundary candles are admitted.
    """
    return CandidateState(mode=mode, slots={}, unavailable={}, retired_versions={})


@dataclass(frozen=True)
class InvalidatedCandidate:
    evaluation_id: str
    candidate_id: str | None
    strategy: str
    strategy_version: str
    reason: str


@dataclass(frozen=True)
class DispositionResult:
    strategy: str
    strategy_version: str
    evaluation_id: str
    candidate_id: str | None
    result: DispositionResultKind
    reason: str | None


@dataclass(frozen=True)
class ReductionResult:
    state: CandidateState
    status: BatchStatus
    reason: str | None
    dispositions: tuple[DispositionResult, ...]
    invalidated: tuple[InvalidatedCandidate, ...]


# --- Internals ------------------------------------------------------------


def _record_from(batch: EvaluationBatch, disposition: StrategyDisposition, received_at: datetime) -> EvaluationRecord:
    return EvaluationRecord(
        evaluation_id=batch.evaluation_id_for(disposition),
        candidate_id=batch.candidate_id_for(disposition),
        symbol=batch.symbol,
        strategy=disposition.strategy,
        strategy_version=disposition.strategy_version,
        trigger_timeframe=batch.trigger_timeframe,
        mode=batch.mode,
        source_candle_ts=batch.source_candle_ts,
        source_interval_start=batch.source_interval_start,
        source_interval_close=batch.source_interval_close,
        completed_at=batch.completed_at,
        received_at=received_at,
        kind=disposition.kind,
        disposition_reason=disposition.reason,
        opportunity=disposition.opportunity,
    )


def _same_evaluation(left: EvaluationRecord, right: EvaluationRecord) -> bool:
    """Content equality ignoring local receive time and later invalidation."""
    return replace(left, received_at=right.received_at, invalidation=None) == replace(right, invalidation=None)


def _unavailable_from(batch: EvaluationBatch, received_at: datetime) -> UnavailableRecord:
    return UnavailableRecord(
        symbol=batch.symbol,
        trigger_timeframe=batch.trigger_timeframe,
        source_candle_ts=batch.source_candle_ts,
        source_interval_start=batch.source_interval_start,
        source_interval_close=batch.source_interval_close,
        completed_at=batch.completed_at,
        received_at=received_at,
        prerequisites=batch.unavailable_prerequisites,
    )


def _same_unavailable(left: UnavailableRecord, right: UnavailableRecord) -> bool:
    return replace(left, received_at=right.received_at) == right


def _unchanged(state: CandidateState, status: BatchStatus, reason: str | None) -> ReductionResult:
    return ReductionResult(state=state, status=status, reason=reason, dispositions=(), invalidated=())


def _supersession_reason(new_kind: str) -> str:
    return "superseded_by_newer_candidate" if new_kind == "opportunity" else f"superseded_by_{new_kind}"


# --- Reducer entry points -------------------------------------------------


def apply_batch(state: CandidateState, batch: EvaluationBatch, received_at: datetime) -> ReductionResult:
    """Reduce one completed batch. ``state`` and ``batch`` are never mutated."""
    received_at = to_utc(received_at, "received_at")

    if batch.mode != state.mode:
        return _unchanged(state, "rejected", REASON_MODE_MISMATCH)
    if state.reset_boundary is not None and batch.source_interval_start < state.reset_boundary:
        return _unchanged(state, "rejected", REASON_PRE_RESET)

    if not batch.selectable:
        return _apply_unavailable(state, batch, received_at)
    return _apply_available(state, batch, received_at)


def _apply_unavailable(state: CandidateState, batch: EvaluationBatch, received_at: datetime) -> ReductionResult:
    frame: FrameKey = (batch.symbol, batch.trigger_timeframe)
    new_record = _unavailable_from(batch, received_at)
    existing = state.unavailable.get(frame)
    if existing is not None:
        if batch.source_candle_ts < existing.source_candle_ts:
            return _unchanged(state, "stale", REASON_UNAVAILABLE_NEWER)
        if batch.source_candle_ts == existing.source_candle_ts:
            if _same_unavailable(existing, new_record):
                return _unchanged(state, "duplicate", None)
            return _unchanged(state, "conflict", REASON_UNAVAILABLE_CONFLICT)

    frame_slots = [slot for slot in state.slots.values() if (slot.symbol, slot.trigger_timeframe) == frame]
    if any(slot.source_candle_ts == batch.source_candle_ts for slot in frame_slots):
        return _unchanged(state, "conflict", REASON_EVALUATION_CANDLE)

    slots = dict(state.slots)
    invalidated: list[InvalidatedCandidate] = []
    for slot in sorted(frame_slots, key=lambda s: (s.strategy, s.strategy_version)):
        if slot.kind != "opportunity" or slot.invalidation is not None or slot.source_candle_ts >= batch.source_candle_ts:
            continue
        slots[(slot.symbol, slot.strategy, slot.strategy_version)] = replace(
            slot,
            invalidation=Invalidation(REASON_PREREQUISITE_UNAVAILABLE, batch.source_candle_ts, received_at),
        )
        invalidated.append(
            InvalidatedCandidate(
                slot.evaluation_id, slot.candidate_id, slot.strategy, slot.strategy_version, REASON_PREREQUISITE_UNAVAILABLE
            )
        )

    unavailable = dict(state.unavailable)
    unavailable[frame] = new_record
    new_state = replace(state, slots=slots, unavailable=unavailable)
    return ReductionResult(
        state=new_state,
        status="applied",
        reason=REASON_PREREQUISITE_UNAVAILABLE,
        dispositions=(),
        invalidated=tuple(invalidated),
    )


def _apply_available(state: CandidateState, batch: EvaluationBatch, received_at: datetime) -> ReductionResult:
    floor = state.unavailable.get((batch.symbol, batch.trigger_timeframe))
    if floor is not None:
        if batch.source_candle_ts < floor.source_candle_ts:
            return _unchanged(state, "stale", REASON_UNAVAILABLE_FLOOR)
        if batch.source_candle_ts == floor.source_candle_ts:
            return _unchanged(state, "conflict", REASON_UNAVAILABLE_CANDLE)

    results: list[DispositionResult] = []
    pending: dict[SlotKey, EvaluationRecord] = {}
    invalidated: list[InvalidatedCandidate] = []
    any_conflict = False

    for disposition in batch.dispositions:  # already sorted by (strategy, version)
        record = _record_from(batch, disposition, received_at)

        def outcome(result: DispositionResultKind, reason: str | None) -> DispositionResult:
            return DispositionResult(
                disposition.strategy, disposition.strategy_version, record.evaluation_id, record.candidate_id, result, reason
            )

        if (disposition.strategy, disposition.strategy_version) in state.retired_versions:
            results.append(outcome("rejected", REASON_VERSION_RETIRED))
            continue
        slot_key: SlotKey = (batch.symbol, disposition.strategy, disposition.strategy_version)
        slot = state.slots.get(slot_key)
        if slot is None:
            pending[slot_key] = record
            results.append(outcome("applied", None))
            continue
        if slot.trigger_timeframe != batch.trigger_timeframe:
            results.append(outcome("rejected", REASON_TIMEFRAME_MISMATCH))
        elif batch.source_candle_ts < slot.source_candle_ts:
            results.append(outcome("stale", REASON_SUPERSEDED))
        elif batch.source_candle_ts == slot.source_candle_ts:
            if _same_evaluation(slot, record):
                results.append(outcome("duplicate", None))
            else:
                any_conflict = True
                results.append(outcome("conflict", REASON_CONTENT_CONFLICT))
        else:
            pending[slot_key] = record
            results.append(outcome("applied", None))
            if slot.kind == "opportunity" and slot.invalidation is None:
                invalidated.append(
                    InvalidatedCandidate(
                        slot.evaluation_id,
                        slot.candidate_id,
                        slot.strategy,
                        slot.strategy_version,
                        _supersession_reason(disposition.kind),
                    )
                )

    if any_conflict:
        # Atomic: a self-inconsistent redelivery changes nothing.
        held = tuple(replace(item, result="withheld") if item.result == "applied" else item for item in results)
        return ReductionResult(state=state, status="conflict", reason=REASON_CONTENT_CONFLICT, dispositions=held, invalidated=())

    kinds = {item.result for item in results}
    if pending:
        status: BatchStatus = "applied"
    elif kinds == {"duplicate"}:
        status = "duplicate"
    elif kinds == {"stale"}:
        status = "stale"
    else:
        status = "ignored"

    if not pending:
        return ReductionResult(state=state, status=status, reason=None, dispositions=tuple(results), invalidated=())

    slots = dict(state.slots)
    slots.update(pending)
    return ReductionResult(
        state=replace(state, slots=slots),
        status=status,
        reason=None,
        dispositions=tuple(results),
        invalidated=tuple(invalidated),
    )


def apply_reset(state: CandidateState, kind: ResetKind, boundary: datetime, reason: str | None = None) -> CandidateState:
    """Clear all eligibility and set the admission boundary.

    The effective boundary never moves earlier. Retired versions persist
    (they are configuration, not observation).
    """
    if kind not in RESET_KINDS:
        raise CandidateContractError(f"unknown reset kind {kind!r}")
    requested = to_utc(boundary, "boundary")
    effective = requested if state.reset_boundary is None else max(state.reset_boundary, requested)
    epoch = state.reset_count + 1
    return CandidateState(
        mode=state.mode,
        slots={},
        unavailable={},
        retired_versions=state.retired_versions,
        reset_boundary=effective,
        reset_count=epoch,
        last_reset=ResetRecord(kind=kind, requested_boundary=requested, effective_boundary=effective, reason=reason, epoch=epoch),
    )


def retire_strategy_versions(
    state: CandidateState, versions: Iterable[VersionKey], reason: str, retired_at: datetime
) -> CandidateState:
    """Remove records of disabled strategy versions and refuse them afterwards."""
    if not isinstance(reason, str) or not reason.strip():
        raise CandidateContractError("reason must be a non-empty string")
    retired_at = to_utc(retired_at, "retired_at")
    keys = {(strategy, version) for strategy, version in versions}
    for strategy, version in keys:
        if not strategy or not version:
            raise CandidateContractError("strategy and version must be non-empty")
    retired = dict(state.retired_versions)
    for key in keys:
        retired.setdefault(key, RetiredVersion(reason=reason, retired_at=retired_at))
    slots = {key: slot for key, slot in state.slots.items() if (slot.strategy, slot.strategy_version) not in keys}
    return replace(state, slots=slots, retired_versions=retired)
