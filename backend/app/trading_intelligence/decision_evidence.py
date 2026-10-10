"""Pure, attributed evidence values for the ranking boundary (D1, §19.3).

Values only: no clock, I/O, logging, database or Performance Intelligence
query. The caller reads whatever evidence it chooses and supplies it here;
this module records provenance and states what is missing. It defines no
sufficiency rule (no sample minimum, no weight, no threshold): deciding what
evidence is *enough* is the open D4 task and is deliberately not guessed.

Rules encoded by the types:

* Provenance fields (strategy configuration, context slice, sample period and
  count, outcome definition, execution venue, backtest run/data/feature
  provenance) are kept, and an absent one stays ``None`` and is reported by
  :attr:`EvidenceRecord.missing_fields` -- never defaulted or inferred.
* ``population`` is the execution mode of the outcomes. Backtest, simulated,
  paper and live populations are never merged here; a backtest record must
  carry backtest provenance, any other record must not.
* ``evidence_class`` separates ``calibration`` evidence from
  ``synthetic_mechanics`` (acceptance outcomes that prove plumbing only).
  Synthetic mechanics evidence can never be calibration evidence.
* ``available_at`` is when the evidence became knowable. It is required to
  use a record at all; the ranking adapter excludes records whose availability
  or sample period is not provably at or before the evidence as-of instant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

from app.trading_intelligence.candidate_contract import (
    DIRECTIONS,
    EXECUTION_MODES,
    CandidateContractError,
    canonical_json,
    canonical_timestamp,
    to_utc,
)

EvidenceClass = Literal["calibration", "synthetic_mechanics"]
EVIDENCE_CLASSES: tuple[str, ...] = ("calibration", "synthetic_mechanics")

# Provenance an evidence record is expected to carry. Anything still ``None``
# is reported as missing; none of it is ever guessed.
_BASE_PROVENANCE: tuple[str, ...] = (
    "available_at",
    "configuration_ref",
    "context_slice_json",
    "sample_period_start",
    "sample_period_end",
    "sample_count",
    "outcome_definition",
    "execution_venue",
)
_BACKTEST_PROVENANCE: tuple[str, ...] = ("backtest_run_id", "data_provenance", "feature_provenance")


class DecisionContractError(CandidateContractError):
    """A ranking/selection value violates the documented contract."""


def utc(value: datetime, field: str) -> datetime:
    """C1's UTC normalisation, raising this package's error type."""
    try:
        return to_utc(value, field)
    except CandidateContractError as error:
        if isinstance(error, DecisionContractError):
            raise
        raise DecisionContractError(str(error)) from error


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DecisionContractError(f"{field} must be a non-empty string without surrounding whitespace")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    return None if value is None else require_text(value, field)


def _optional_utc(value: datetime | None, field: str) -> datetime | None:
    return None if value is None else utc(value, field)


def _json_object_text(text: str | None, field: str) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise DecisionContractError(f"{field} must be canonical JSON text")
    try:
        parsed = json.loads(text)
        canonical = canonical_json(parsed, field)
    except (ValueError, CandidateContractError) as error:
        raise DecisionContractError(f"{field} must be canonical JSON of an object: {error}") from error
    if not isinstance(parsed, dict) or canonical != text:
        raise DecisionContractError(f"{field} must be canonical JSON of an object")
    return text


@dataclass(frozen=True)
class EvidenceRecord:
    """One attributed piece of performance evidence, detached and immutable.

    ``strategy`` and ``strategy_version`` are mandatory attribution keys;
    evidence for another version is never attributed to a candidate (versions
    are never blended). ``symbol`` and ``direction`` optionally narrow the
    attribution. ``metrics_json`` is descriptive output the caller computed
    (for example a win rate); this package never combines or scores it.
    """

    evidence_ref: str
    strategy: str
    strategy_version: str
    population: str
    evidence_class: EvidenceClass
    available_at: datetime | None = None
    configuration_ref: str | None = None
    context_slice_json: str | None = None
    sample_period_start: datetime | None = None
    sample_period_end: datetime | None = None
    sample_count: int | None = None
    outcome_definition: str | None = None
    execution_venue: str | None = None
    symbol: str | None = None
    direction: str | None = None
    backtest_run_id: str | None = None
    backtest_sweep_id: str | None = None
    data_provenance: str | None = None
    feature_provenance: str | None = None
    metrics_json: str = "{}"

    def __post_init__(self) -> None:
        require_text(self.evidence_ref, "evidence_ref")
        require_text(self.strategy, "strategy")
        require_text(self.strategy_version, "strategy_version")
        if self.population not in EXECUTION_MODES:
            raise DecisionContractError(f"unknown evidence population {self.population!r}")
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise DecisionContractError(f"unknown evidence class {self.evidence_class!r}")
        for name in ("available_at", "sample_period_start", "sample_period_end"):
            object.__setattr__(self, name, _optional_utc(getattr(self, name), name))
        if (
            self.sample_period_start is not None
            and self.sample_period_end is not None
            and self.sample_period_start > self.sample_period_end
        ):
            raise DecisionContractError("sample_period_start must not be after sample_period_end")
        count = self.sample_count
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise DecisionContractError("sample_count must be a non-negative integer or None")
        for name in (
            "configuration_ref",
            "outcome_definition",
            "execution_venue",
            "symbol",
            "backtest_run_id",
            "backtest_sweep_id",
            "data_provenance",
            "feature_provenance",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        if self.direction is not None and self.direction not in DIRECTIONS:
            raise DecisionContractError("direction must be 'long', 'short' or None")
        object.__setattr__(self, "context_slice_json", _json_object_text(self.context_slice_json, "context_slice_json"))
        if _json_object_text(self.metrics_json, "metrics_json") is None:
            raise DecisionContractError("metrics_json must be canonical JSON of an object")
        backtest_fields = (self.backtest_run_id, self.backtest_sweep_id, self.data_provenance, self.feature_provenance)
        if self.population != "backtest" and any(value is not None for value in backtest_fields):
            raise DecisionContractError("backtest provenance belongs only to the backtest population")
        if self.evidence_class == "synthetic_mechanics" and self.population == "live":
            raise DecisionContractError("synthetic mechanics evidence cannot come from the live population")

    @classmethod
    def create(
        cls,
        *,
        evidence_ref: str,
        strategy: str,
        strategy_version: str,
        population: str,
        evidence_class: EvidenceClass,
        context_slice: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> "EvidenceRecord":
        """Build from plain mappings; the caller's objects are never referenced."""
        if context_slice is not None and not isinstance(context_slice, Mapping):
            raise DecisionContractError("context_slice must be a mapping or None")
        if metrics is not None and not isinstance(metrics, Mapping):
            raise DecisionContractError("metrics must be a mapping or None")
        try:
            context_json = None if context_slice is None else canonical_json(dict(context_slice), "context_slice")
            metrics_json = canonical_json(dict(metrics or {}), "metrics")
        except CandidateContractError as error:
            raise DecisionContractError(str(error)) from error
        return cls(
            evidence_ref=evidence_ref,
            strategy=strategy,
            strategy_version=strategy_version,
            population=population,
            evidence_class=evidence_class,
            context_slice_json=context_json,
            metrics_json=metrics_json,
            **fields,
        )

    @property
    def missing_fields(self) -> tuple[str, ...]:
        """Provenance still absent, in a fixed order. Never filled in."""
        names = _BASE_PROVENANCE + (_BACKTEST_PROVENANCE if self.population == "backtest" else ())
        return tuple(name for name in names if getattr(self, name) is None)

    @property
    def is_synthetic_mechanics(self) -> bool:
        return self.evidence_class == "synthetic_mechanics"

    @property
    def is_empty_sample(self) -> bool:
        return self.sample_count == 0

    def context_slice_copy(self) -> dict[str, Any] | None:
        return None if self.context_slice_json is None else json.loads(self.context_slice_json)

    def metrics_copy(self) -> dict[str, Any]:
        return json.loads(self.metrics_json)

    def to_audit_record(self) -> dict[str, Any]:
        """A fresh, JSON-safe mapping; mutating it never touches this record."""
        return {
            "evidence_ref": self.evidence_ref,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "population": self.population,
            "evidence_class": self.evidence_class,
            "available_at": _stamp(self.available_at),
            "configuration_ref": self.configuration_ref,
            "context_slice": self.context_slice_copy(),
            "sample_period_start": _stamp(self.sample_period_start),
            "sample_period_end": _stamp(self.sample_period_end),
            "sample_count": self.sample_count,
            "outcome_definition": self.outcome_definition,
            "execution_venue": self.execution_venue,
            "symbol": self.symbol,
            "direction": self.direction,
            "backtest_run_id": self.backtest_run_id,
            "backtest_sweep_id": self.backtest_sweep_id,
            "data_provenance": self.data_provenance,
            "feature_provenance": self.feature_provenance,
            "metrics": self.metrics_copy(),
            "missing_fields": list(self.missing_fields),
        }


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else canonical_timestamp(value)


@dataclass(frozen=True)
class EvidenceSnapshot:
    """The evidence supplied for one ranking call, with its explicit as-of.

    ``as_of`` is the instant the evidence set is meant to describe. Nothing
    here reads a clock. An empty snapshot is valid and means *no evidence
    supplied*, which downstream stays an explicit gap, never a neutral score.
    """

    as_of: datetime
    records: tuple[EvidenceRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", utc(self.as_of, "evidence as_of"))
        records = tuple(self.records)
        if any(not isinstance(item, EvidenceRecord) for item in records):
            raise DecisionContractError("records must be EvidenceRecord values")
        refs = [item.evidence_ref for item in records]
        if len(set(refs)) != len(refs):
            raise DecisionContractError("evidence_ref must be unique within one evidence snapshot")
        object.__setattr__(self, "records", tuple(sorted(records, key=lambda item: item.evidence_ref)))
