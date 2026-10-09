"""Pure candidate and completed-evaluation-batch contract (C1, §19.2).

Values and identities only: no clock, I/O, logging, randomness, event bus,
database, Governor or authorization dependency. Every timestamp is explicit
input and is normalised to UTC; naive datetimes are rejected rather than
assumed to be UTC.

Candidate identity is deliberately distinct from accepted-trade identity: the
IDs below are versioned, prefixed hash strings, never trade UUIDs.

* ``evaluation_id`` identifies one strategy evaluation of one source candle
  and EXCLUDES direction.
* ``candidate_id`` identifies one directional candidate and INCLUDES it.
* Confidence, receive time, completion time and evidence never affect either.
  A changed confidence under the same identity is a content conflict, which
  the reducer reports explicitly.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

IDENTITY_ENCODING_VERSION = 1
EVALUATION_ID_PREFIX = f"evl{IDENTITY_ENCODING_VERSION}:"
CANDIDATE_ID_PREFIX = f"cnd{IDENTITY_ENCODING_VERSION}:"

Direction = Literal["long", "short"]
DispositionKind = Literal["opportunity", "no_opportunity", "gated", "error"]
OpportunityStatus = Literal["potential", "waiting", "actionable", "expired"]
ExecutionMode = Literal["simulated", "paper", "live", "backtest"]

DIRECTIONS: tuple[str, ...] = ("long", "short")
DISPOSITION_KINDS: tuple[str, ...] = ("opportunity", "no_opportunity", "gated", "error")
OPPORTUNITY_STATUSES: tuple[str, ...] = ("potential", "waiting", "actionable", "expired")
EXECUTION_MODES: tuple[str, ...] = ("simulated", "paper", "live", "backtest")


class CandidateContractError(ValueError):
    """A candidate/batch value violates the documented contract."""


# --- Normalisation helpers ------------------------------------------------


def to_utc(value: datetime, field: str) -> datetime:
    """Return ``value`` as an aware UTC datetime; naive values are rejected."""
    if not isinstance(value, datetime):
        raise CandidateContractError(f"{field} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise CandidateContractError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def canonical_timestamp(value: datetime) -> str:
    """Fixed-width UTC text (microseconds, ``Z``) — equal instants, equal text."""
    return to_utc(value, "timestamp").strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CandidateContractError(f"{field} must be a non-empty string without surrounding whitespace")
    return value


def _require_finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CandidateContractError(f"{field} must be a finite number")
    return float(value)


def canonical_json(value: Any, field: str) -> str:
    """Canonical JSON text; only JSON types with finite numbers are accepted."""
    _check_json(value, field)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _check_json(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CandidateContractError(f"{path} contains a non-finite number")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_json(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CandidateContractError(f"{path} has a non-string key")
            _check_json(item, f"{path}.{key}")
        return
    raise CandidateContractError(f"{path} holds unsupported type {type(value).__name__}; serialise it first")


# --- Identity -------------------------------------------------------------


def canonical_identity_payload(
    kind: Literal["evaluation", "candidate"],
    symbol: str,
    strategy: str,
    strategy_version: str,
    trigger_timeframe: str,
    source_candle_ts: datetime,
    direction: str | None = None,
) -> str:
    """Documented canonical text hashed into an ID (encoding version 1).

    A JSON array — ``[domain, encoding_version, symbol, strategy, version,
    timeframe, source_candle_ts_utc[, direction]]`` — with no whitespace and
    ASCII escaping. The domain tag keeps evaluation and candidate hashes
    disjoint. Timestamps are fixed-width UTC, so the same instant in any
    offset encodes identically.
    """
    parts: list[Any] = [
        f"tios.{kind}",
        IDENTITY_ENCODING_VERSION,
        _require_text(symbol, "symbol"),
        _require_text(strategy, "strategy"),
        _require_text(strategy_version, "strategy_version"),
        _require_text(trigger_timeframe, "trigger_timeframe"),
        canonical_timestamp(source_candle_ts),
    ]
    if kind == "candidate":
        if direction not in DIRECTIONS:
            raise CandidateContractError("direction must be 'long' or 'short'")
        parts.append(direction)
    elif direction is not None:
        raise CandidateContractError("evaluation identity excludes direction")
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluation_id(
    symbol: str, strategy: str, strategy_version: str, trigger_timeframe: str, source_candle_ts: datetime
) -> str:
    payload = canonical_identity_payload(
        "evaluation", symbol, strategy, strategy_version, trigger_timeframe, source_candle_ts
    )
    return EVALUATION_ID_PREFIX + _digest(payload)


def candidate_id(
    symbol: str,
    strategy: str,
    strategy_version: str,
    trigger_timeframe: str,
    source_candle_ts: datetime,
    direction: str,
) -> str:
    payload = canonical_identity_payload(
        "candidate", symbol, strategy, strategy_version, trigger_timeframe, source_candle_ts, direction
    )
    return CANDIDATE_ID_PREFIX + _digest(payload)


# --- Values ---------------------------------------------------------------


@dataclass(frozen=True)
class OpportunityContent:
    """Immutable, detached copy of the strategy's opportunity fields.

    ``evidence`` is held as canonical JSON text, so the caller's mapping is
    never referenced and every :meth:`evidence_copy` is a fresh object.
    ``expected_horizon_minutes`` stays descriptive; no expiry is derived from
    it and ``wait_expires_at`` is deliberately not carried (§19.2).
    """

    direction: Direction
    confidence: float
    structural_invalidation: float
    structural_target: float
    status: OpportunityStatus
    expected_horizon_minutes: int | None
    evidence_json: str

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise CandidateContractError("direction must be 'long' or 'short'")
        if self.status not in OPPORTUNITY_STATUSES:
            raise CandidateContractError(f"unknown opportunity status {self.status!r}")
        for name in ("confidence", "structural_invalidation", "structural_target"):
            object.__setattr__(self, name, _require_finite(getattr(self, name), name))
        horizon = self.expected_horizon_minutes
        if horizon is not None and (isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0):
            raise CandidateContractError("expected_horizon_minutes must be a positive integer or None")
        parsed = json.loads(self.evidence_json)
        if not isinstance(parsed, dict) or canonical_json(parsed, "evidence") != self.evidence_json:
            raise CandidateContractError("evidence_json must be canonical JSON of an object")

    @classmethod
    def create(
        cls,
        *,
        direction: Direction,
        confidence: float,
        structural_invalidation: float,
        structural_target: float,
        evidence: Mapping[str, Any],
        status: OpportunityStatus = "actionable",
        expected_horizon_minutes: int | None = None,
    ) -> "OpportunityContent":
        if not isinstance(evidence, Mapping):
            raise CandidateContractError("evidence must be a mapping")
        return cls(
            direction=direction,
            confidence=confidence,
            structural_invalidation=structural_invalidation,
            structural_target=structural_target,
            status=status,
            expected_horizon_minutes=expected_horizon_minutes,
            evidence_json=canonical_json(evidence, "evidence"),
        )

    def evidence_copy(self) -> dict[str, Any]:
        return json.loads(self.evidence_json)


@dataclass(frozen=True)
class StrategyDisposition:
    """Terminal result of one strategy version for one source candle."""

    strategy: str
    strategy_version: str
    kind: DispositionKind
    opportunity: OpportunityContent | None = None
    reason: str | None = None  # machine-readable gate/error code

    def __post_init__(self) -> None:
        _require_text(self.strategy, "strategy")
        _require_text(self.strategy_version, "strategy_version")
        if self.kind not in DISPOSITION_KINDS:
            raise CandidateContractError(f"unknown disposition kind {self.kind!r}")
        if self.kind == "opportunity":
            if not isinstance(self.opportunity, OpportunityContent):
                raise CandidateContractError("an opportunity disposition requires OpportunityContent")
        elif self.opportunity is not None:
            raise CandidateContractError(f"a {self.kind} disposition cannot carry an opportunity")
        if self.kind in ("gated", "error"):
            _require_text(self.reason, "reason")
        elif self.reason is not None:
            _require_text(self.reason, "reason")


@dataclass(frozen=True)
class UnavailablePrerequisite:
    """An input the batch could not obtain coherently (e.g. a candle mismatch)."""

    name: str
    reason: str

    def __post_init__(self) -> None:
        _require_text(self.name, "prerequisite name")
        _require_text(self.reason, "prerequisite reason")


@dataclass(frozen=True)
class EvaluationBatch:
    """Complete result of one trigger: one symbol, timeframe and source candle.

    ``source_candle_ts`` is the identity input in the producer's own
    convention (the built producers stamp the interval's open time). The
    interval start/close are explicit rather than derived as
    ``candle_ts + nominal timeframe width`` because the producer owns the
    candle convention and a session-trailing bucket can be shorter than its
    nominal width (e.g. the final 30 minutes of a regular session at 1h).
    Age and reset-boundary checks use the interval, never delivery time.

    ``completed_at`` is the producer's local completion time. Receive time is
    supplied to the reducer separately. Neither affects identity.

    A batch with unavailable prerequisites carries no dispositions: no
    strategy evaluated, and an untriggered strategy is never recorded as having
    evaluated. A batch without them must carry at least one disposition.
    """

    symbol: str
    trigger_timeframe: str
    mode: ExecutionMode
    source_candle_ts: datetime
    source_interval_start: datetime
    source_interval_close: datetime
    completed_at: datetime
    dispositions: tuple[StrategyDisposition, ...] = ()
    unavailable_prerequisites: tuple[UnavailablePrerequisite, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.trigger_timeframe, "trigger_timeframe")
        if self.mode not in EXECUTION_MODES:
            raise CandidateContractError(f"unknown execution mode {self.mode!r}")
        for name in ("source_candle_ts", "source_interval_start", "source_interval_close", "completed_at"):
            object.__setattr__(self, name, to_utc(getattr(self, name), name))
        if not (self.source_interval_start <= self.source_candle_ts < self.source_interval_close):
            raise CandidateContractError("source_candle_ts must lie within [source_interval_start, source_interval_close)")
        dispositions = tuple(self.dispositions)
        unavailable = tuple(self.unavailable_prerequisites)
        if any(not isinstance(item, StrategyDisposition) for item in dispositions):
            raise CandidateContractError("dispositions must be StrategyDisposition values")
        if any(not isinstance(item, UnavailablePrerequisite) for item in unavailable):
            raise CandidateContractError("unavailable_prerequisites must be UnavailablePrerequisite values")
        keys = [(item.strategy, item.strategy_version) for item in dispositions]
        if len(set(keys)) != len(keys):
            raise CandidateContractError("a strategy version may have only one disposition per batch")
        names = [item.name for item in unavailable]
        if len(set(names)) != len(names):
            raise CandidateContractError("unavailable prerequisites must be unique by name")
        if unavailable and dispositions:
            raise CandidateContractError("a batch with unavailable prerequisites carries no dispositions")
        if not unavailable and not dispositions:
            raise CandidateContractError("a batch needs at least one disposition")
        object.__setattr__(self, "dispositions", tuple(sorted(dispositions, key=lambda d: (d.strategy, d.strategy_version))))
        object.__setattr__(self, "unavailable_prerequisites", tuple(sorted(unavailable, key=lambda p: p.name)))

    @property
    def selectable(self) -> bool:
        return not self.unavailable_prerequisites

    def evaluation_id_for(self, disposition: StrategyDisposition) -> str:
        return evaluation_id(
            self.symbol,
            disposition.strategy,
            disposition.strategy_version,
            self.trigger_timeframe,
            self.source_candle_ts,
        )

    def candidate_id_for(self, disposition: StrategyDisposition) -> str | None:
        if disposition.kind != "opportunity" or disposition.opportunity is None:
            return None
        return candidate_id(
            self.symbol,
            disposition.strategy,
            disposition.strategy_version,
            self.trigger_timeframe,
            self.source_candle_ts,
            disposition.opportunity.direction,
        )
