"""Pure, validated selection-journal records (D2, §19.4).

This module defines the journal row as an immutable value and the checks that
make an attempt trustworthy BEFORE anything is persisted. It reads no clock,
touches no database and publishes nothing.

* :class:`SelectionAttemptRecord` is one append-only journal record. It owns a
  detached canonical-JSON copy of the evidence, so neither the caller's mapping
  nor a value returned from the journal can alias stored state.
* Evidence is strict, finite, plain JSON (the Governor's existing
  :func:`detach_evidence`) with a fixed key set: the D1 ``SelectionResult``
  audit keys plus ``ranking_evidence``. A missing key, an unknown key or a value
  that contradicts the record's own columns is refused, never repaired. Only
  candidate IDs, exclusions/reasons, attributed-evidence provenance, times, the
  snapshot cutoff and the portfolio capture appear; no FeatureSet is copied.
* ``shadow`` is part of the record and of its evidence. D1 output states
  ``shadow = true`` and ``authorizes_trade = false`` and is only ever journalled
  as shadow (see ``shadow.py``). :class:`NonShadowSelectionInput` is the
  explicit, separate input a future coordinator uses for an attempt that may
  support a candidate acceptance; it has no constructor from a D1 result, and a
  D1 audit mapping is refused there because its ``shadow`` is true.
* A selection never authorizes anything (``authorizes_trade`` is false in every
  record). Authorization is the Governor's later, separate commit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping
from uuid import UUID

from app.governor.evidence import EvidenceError, detach_evidence, evidence_equal
from app.trading_intelligence.candidate_contract import EXECUTION_MODES

JOURNAL_SCHEMA_VERSION = 1

SelectionResultKind = Literal["selected", "abstained"]
RESULTS: tuple[str, ...] = ("selected", "abstained")

# D1 ``SelectionResult.to_audit_record()`` keys, plus the ranking provenance.
EVIDENCE_KEYS: frozenset[str] = frozenset(
    {
        "schema_version", "shadow", "authorizes_trade", "status", "selected_candidate_id", "selected_candidate",
        "abstention_reasons", "selection_policy", "ranking_status", "ranking_policy_id", "ranking_policy_version",
        "ranking_adapter_version", "execution_mode", "audit_id", "eligibility_as_of", "evidence_as_of", "cutoff",
        "reset_boundary", "portfolio_captured_at", "portfolio", "slot_policy", "slots_available",
        "considered_candidate_ids", "exclusions", "conflict_groups", "attempted_candidate_ids",
        "attempted_not_in_captured_set", "ranking_evidence",
    }
)
RANKING_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {"status", "reasons", "ranking_policy_id", "ranking_policy_version", "adapter_version", "reset_count",
     "candidate_evidence", "unattributed_evidence"}
)
_DIRECTION_TO_SIDE = {"long": "BUY", "short": "SELL"}


class SelectionAuditError(ValueError):
    """A selection record or acceptance association violates the documented contract."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SelectionAuditError(f"{field} must be a non-empty string without surrounding whitespace")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_uuid(value: Any, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise SelectionAuditError(f"{field} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise SelectionAuditError(f"{field} must be a UUID string") from exc
    if str(parsed) != value:
        raise SelectionAuditError(f"{field} must be the canonical lowercase UUID text")
    return parsed


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SelectionAuditError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _validate_evidence(
    evidence: dict[str, Any], *, selection_id: UUID, execution_mode: str, shadow: bool, policy_version: str,
    result: str, selected_candidate_id: str | None,
) -> None:
    keys = set(evidence)
    if keys != EVIDENCE_KEYS:
        missing, unknown = sorted(EVIDENCE_KEYS - keys), sorted(keys - EVIDENCE_KEYS)
        raise SelectionAuditError(f"evidence keys differ from the journal contract: missing={missing} unknown={unknown}")
    if not _is_int(evidence["schema_version"]) or evidence["schema_version"] < 1:
        raise SelectionAuditError("evidence schema_version must be a positive integer")
    if not isinstance(evidence["shadow"], bool) or evidence["shadow"] is not shadow:
        raise SelectionAuditError("evidence shadow flag contradicts the record's shadow flag")
    if evidence["authorizes_trade"] is not False:
        raise SelectionAuditError("a selection never authorizes a trade: evidence authorizes_trade must be false")
    for key, expected in (
        ("status", result), ("selected_candidate_id", selected_candidate_id), ("execution_mode", execution_mode),
        ("audit_id", str(selection_id)), ("selection_policy", policy_version),
    ):
        if evidence[key] != expected or type(evidence[key]) is not type(expected):
            raise SelectionAuditError(f"evidence {key} contradicts the record")
    considered = evidence["considered_candidate_ids"]
    if not isinstance(considered, list) or any(not isinstance(item, str) or not item for item in considered):
        raise SelectionAuditError("evidence considered_candidate_ids must be a list of candidate IDs")
    if len(set(considered)) != len(considered):
        raise SelectionAuditError("evidence considered_candidate_ids must be unique")
    for key in ("exclusions", "conflict_groups", "attempted_candidate_ids", "attempted_not_in_captured_set",
                "abstention_reasons"):
        if not isinstance(evidence[key], list):
            raise SelectionAuditError(f"evidence {key} must be a list")
    if any(not isinstance(item, dict) for item in evidence["exclusions"]):
        raise SelectionAuditError("evidence exclusions must be mappings")
    cutoff = evidence["cutoff"]
    if (not isinstance(cutoff, dict) or set(cutoff) != {"arrival_sequence", "cutoff_at"}
            or not _is_int(cutoff["arrival_sequence"]) or cutoff["arrival_sequence"] < 0
            or not isinstance(cutoff["cutoff_at"], str) or not cutoff["cutoff_at"]):
        raise SelectionAuditError("evidence cutoff must hold arrival_sequence and cutoff_at")
    for key in ("eligibility_as_of", "evidence_as_of"):
        if not isinstance(evidence[key], str) or not evidence[key]:
            raise SelectionAuditError(f"evidence {key} must be a timestamp string")
    captured, portfolio = evidence["portfolio_captured_at"], evidence["portfolio"]
    if (captured is None) != (portfolio is None) or (captured is not None and not isinstance(captured, str)):
        raise SelectionAuditError("evidence portfolio and portfolio_captured_at must be present together")
    if portfolio is not None and (not isinstance(portfolio, dict) or portfolio.get("captured_at") != captured):
        raise SelectionAuditError("evidence portfolio capture time contradicts portfolio_captured_at")
    ranking = evidence["ranking_evidence"]
    if (not isinstance(ranking, dict) or set(ranking) != RANKING_EVIDENCE_KEYS
            or not isinstance(ranking["candidate_evidence"], list)
            or not isinstance(ranking["unattributed_evidence"], list)):
        raise SelectionAuditError("evidence ranking_evidence is missing its documented provenance")
    if result == "selected":
        selected = evidence["selected_candidate"]
        if selected_candidate_id not in considered:
            raise SelectionAuditError("the selected candidate is not among the captured candidate IDs")
        if (not isinstance(selected, dict) or selected.get("candidate_id") != selected_candidate_id
                or any(not isinstance(selected.get(k), str) or not selected[k]
                       for k in ("symbol", "strategy", "strategy_version"))
                or selected.get("direction") not in _DIRECTION_TO_SIDE):
            raise SelectionAuditError("evidence selected_candidate does not describe the selected candidate")
        if evidence["abstention_reasons"]:
            raise SelectionAuditError("a selected attempt carries no abstention reasons")
        if (not isinstance(portfolio, dict) or portfolio.get("available") is not True
                or portfolio.get("ledger_synchronized") is not True):
            raise SelectionAuditError("a selected attempt requires an available, ledger-synchronized portfolio capture")
    else:
        if evidence["selected_candidate"] is not None:
            raise SelectionAuditError("an abstained attempt names no selected candidate")
        if not evidence["abstention_reasons"] or any(not isinstance(r, str) or not r for r in evidence["abstention_reasons"]):
            raise SelectionAuditError("an abstained attempt requires its abstention reasons")


@dataclass(frozen=True)
class SelectionAttemptRecord:
    """One validated, immutable journal record. Build it with :meth:`create`.

    ``evidence_json`` is the canonical text of the detached evidence; read it
    through :attr:`evidence`, which returns a fresh mapping every time.
    """

    selection_id: UUID
    created_at: datetime
    execution_mode: str
    shadow: bool
    policy_version: str
    result: SelectionResultKind
    selected_candidate_id: str | None
    schema_version: int
    evidence_json: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "selection_id", _canonical_uuid(self.selection_id, "selection_id"))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.execution_mode not in EXECUTION_MODES:
            raise SelectionAuditError(f"unknown execution mode {self.execution_mode!r}")
        if not isinstance(self.shadow, bool):
            raise SelectionAuditError("shadow must be a bool")
        _text(self.policy_version, "policy_version")
        if len(self.policy_version) > 64:
            raise SelectionAuditError("policy_version is longer than 64 characters")
        if self.result not in RESULTS:
            raise SelectionAuditError(f"result must be one of {RESULTS}")
        if (self.result == "selected") != (self.selected_candidate_id is not None):
            raise SelectionAuditError("selected_candidate_id is required if and only if the result is selected")
        if self.selected_candidate_id is not None:
            _text(self.selected_candidate_id, "selected_candidate_id")
            if len(self.selected_candidate_id) > 128:
                raise SelectionAuditError("selected_candidate_id is longer than 128 characters")
        if not _is_int(self.schema_version) or self.schema_version != JOURNAL_SCHEMA_VERSION:
            raise SelectionAuditError(f"unsupported selection journal schema_version {self.schema_version!r}")
        if not isinstance(self.evidence_json, str):
            raise SelectionAuditError("evidence_json must be canonical JSON text")
        try:
            parsed = json.loads(self.evidence_json)
        except ValueError as exc:
            raise SelectionAuditError(f"evidence_json is not JSON: {exc}") from exc
        if not isinstance(parsed, dict) or _canonical_json(parsed) != self.evidence_json:
            raise SelectionAuditError("evidence_json must be the canonical JSON of an object")
        _validate_evidence(
            parsed, selection_id=self.selection_id, execution_mode=self.execution_mode, shadow=self.shadow,
            policy_version=self.policy_version, result=self.result, selected_candidate_id=self.selected_candidate_id,
        )

    @classmethod
    def create(
        cls, *, selection_id: UUID | str, created_at: datetime, execution_mode: str, shadow: bool,
        policy_version: str, result: str, selected_candidate_id: str | None, evidence: Mapping[str, Any],
        schema_version: int = JOURNAL_SCHEMA_VERSION,
    ) -> "SelectionAttemptRecord":
        """Validate and DETACH ``evidence`` (strict finite plain JSON), or raise ``SelectionAuditError``."""
        if not isinstance(evidence, dict):
            raise SelectionAuditError(f"evidence must be a dict, got {type(evidence).__name__}")
        try:
            detached = detach_evidence(evidence)
        except EvidenceError as exc:
            raise SelectionAuditError(f"evidence is not plain finite JSON: {exc}") from exc
        return cls(selection_id, created_at, execution_mode, shadow, policy_version, result, selected_candidate_id,
                   schema_version, _canonical_json(detached))

    @property
    def evidence(self) -> dict[str, Any]:
        """A fresh, detached copy; mutating it never affects the record."""
        return json.loads(self.evidence_json)

    @property
    def selected_candidate(self) -> dict[str, Any] | None:
        return self.evidence["selected_candidate"]

    def equivalent(self, other: "SelectionAttemptRecord") -> bool:
        """Complete equality for replay: every column and the evidence (True is never 1)."""
        return (
            isinstance(other, SelectionAttemptRecord)
            and (self.selection_id, self.created_at, self.execution_mode, self.shadow, self.policy_version,
                 self.result, self.selected_candidate_id, self.schema_version)
            == (other.selection_id, other.created_at, other.execution_mode, other.shadow, other.policy_version,
                other.result, other.selected_candidate_id, other.schema_version)
            and evidence_equal(self.evidence, other.evidence)
        )


@dataclass(frozen=True)
class NonShadowSelectionInput:
    """The explicit journal input for an attempt that may support a candidate acceptance.

    A future coordinator supplies this; D2 builds no coordinator and nothing
    calls it. It deliberately has no ``shadow`` field (the record is always
    non-shadow), no constructor from a D1 ``SelectionResult`` and no way to
    relabel one: a D1 audit states ``shadow = true`` and is refused here. The
    coordinator must provide its own evidence mapping (same documented key set)
    whose ``shadow`` is false and whose ``authorizes_trade`` is false. Being
    journalled as non-shadow only means the attempt is *eligible to support* a
    claim; the claim itself still needs the Governor's atomic approval.
    """

    selection_id: str
    created_at: datetime
    execution_mode: str
    policy_version: str
    result: str
    selected_candidate_id: str | None
    evidence: Mapping[str, Any]

    def to_record(self) -> SelectionAttemptRecord:
        return SelectionAttemptRecord.create(
            selection_id=self.selection_id, created_at=self.created_at, execution_mode=self.execution_mode,
            shadow=False, policy_version=self.policy_version, result=self.result,
            selected_candidate_id=self.selected_candidate_id, evidence=self.evidence,
        )


def check_acceptance_support(
    attempt: SelectionAttemptRecord | None, *, execution_mode: str, candidate_id: str, symbol: str,
    strategy: str, strategy_version: str, direction: str,
) -> None:
    """Raise ``SelectionAuditError`` unless ``attempt`` can support accepting this candidate.

    Shadow, abstained, missing, wrong-mode and wrong-candidate attempts never
    support a claim, and the approved trade must describe the selected candidate
    (same symbol, strategy, version and direction; ``long``/``short`` is
    ``BUY``/``SELL``).
    """
    if attempt is None:
        raise SelectionAuditError("the selection attempt does not exist")
    if attempt.shadow:
        raise SelectionAuditError("a shadow selection cannot support a candidate acceptance")
    if attempt.result != "selected":
        raise SelectionAuditError("an abstained selection cannot support a candidate acceptance")
    if attempt.execution_mode != execution_mode:
        raise SelectionAuditError("the selection attempt belongs to another execution mode")
    if attempt.selected_candidate_id != candidate_id:
        raise SelectionAuditError("the selection attempt selected a different candidate")
    selected = attempt.selected_candidate
    if selected is None or (selected["symbol"], selected["strategy"], selected["strategy_version"],
                            _DIRECTION_TO_SIDE[selected["direction"]]) != (symbol, strategy, strategy_version, direction):
        raise SelectionAuditError("the approved trade does not match the selected candidate")
