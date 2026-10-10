"""Read-only API projection of the C2 observation snapshot (§19.2).

``project_candidate_observation`` turns ONE already-captured
:class:`ObservationSnapshot` into plain JSON-ready containers for
``GET /intelligence/candidate-observation``. It is a serializer and nothing
else: no clock, I/O, logging, event bus, database, strategy evaluation,
ranking, selection, Planning or Governor dependency, and it never decides or
changes eligibility — it copies what C1's ``assess_candidates`` already
concluded.

Guarantees
----------
* Detached: every list/dict is freshly built. Tuples, read-only mappings and
  frozen dataclasses from the snapshot are never returned as-is, so mutating
  a response (or the JSON decoded from it) cannot reach reader state.
* Strict JSON-safe: timestamps are fixed-width UTC ISO text (``Z``); floats
  that are not finite become ``null`` rather than ``NaN``/``Infinity``.
* Deterministic: candidates are ordered eligible first, then by
  ``(symbol, strategy, strategy_version)``; unavailable inputs by
  ``(symbol, timeframe)``; counters by key.
* Bounded and honest: rows are capped at ``MAX_CANDIDATE_ROWS`` and
  unavailable-input frames at ``MAX_UNAVAILABLE_FRAMES``; counts always cover
  the FULL population, and a ``truncation`` object states whether a list is
  partial. A partial list is never presented as complete.
* No raw evidence: ``OpportunityContent.evidence_json`` is not exposed, nor
  are structural levels. Only the opportunity's status, descriptive
  confidence and expected horizon are surfaced.

``confidence`` is the strategy's own descriptive score; it is not a win
probability and plays no part in eligibility here.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.trading_intelligence.candidate_eligibility import CandidateAssessment
from app.trading_intelligence.candidate_observation import ObservationSnapshot, ObservedProblem
from app.trading_intelligence.candidate_state import UnavailableRecord

MAX_CANDIDATE_ROWS = 200
MAX_UNAVAILABLE_FRAMES = 50
MAX_TEXT_CHARS = 200

SOURCE_ORIGIN = "c2_candidate_observation_reader"

REASON_READER_NOT_INSTALLED = "reader_not_installed"
REASON_READER_NOT_STARTED = "reader_not_started"
REASON_READER_STOPPED = "reader_stopped"

_UNAVAILABLE_TEXT = {
    REASON_READER_NOT_INSTALLED: "No candidate observation reader is installed in this backend process.",
    REASON_READER_NOT_STARTED: "The candidate observation reader has not been started, so it has observed nothing.",
    REASON_READER_STOPPED: (
        "The candidate observation reader is stopped and no longer receives evaluations, so its "
        "last retained state is not reported as current."
    ),
}


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value) if math.isfinite(value) else None


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    return value if len(value) <= MAX_TEXT_CHARS else value[:MAX_TEXT_CHARS] + "…"


def unavailable_payload(reason: str, *, reader_status: str | None = None, mode: str | None = None) -> dict[str, Any]:
    """An explicit unavailable state — never an apparently healthy empty universe."""
    return {
        "status": "unavailable",
        "reason": reason,
        "reason_text": _UNAVAILABLE_TEXT.get(reason, "Candidate observation is unavailable."),
        "observation_only": True,
        "source": SOURCE_ORIGIN,
        "reader": {"status": reader_status, "execution_mode": mode},
        "snapshot": None,
    }


def _project_candidate(item: CandidateAssessment) -> dict[str, Any]:
    opportunity = item.opportunity
    return {
        "candidate_id": item.candidate_id,
        "evaluation_id": item.evaluation_id,
        "symbol": item.symbol,
        "strategy": item.strategy,
        "strategy_version": item.strategy_version,
        "timeframe": item.trigger_timeframe,
        "disposition": item.kind,
        "disposition_reason": _clip(item.disposition_reason),
        "direction": item.direction,
        "eligible": bool(item.eligible),
        "reasons": [_clip(reason) for reason in item.reasons],
        "invalidation_reason": _clip(item.invalidation_reason),
        "opportunity": None
        if opportunity is None
        else {
            "status": opportunity.status,
            "confidence": _finite_or_none(opportunity.confidence),
            "expected_horizon_minutes": opportunity.expected_horizon_minutes,
        },
        "source_candle_ts": _utc_iso(item.source_candle_ts),
        "source_interval_start": _utc_iso(item.source_interval_start),
        "source_interval_close": _utc_iso(item.source_interval_close),
        "completed_at": _utc_iso(item.completed_at),
        "received_at": _utc_iso(item.received_at),
        "age_seconds": _finite_or_none(item.age_seconds),
        "max_age_seconds": _finite_or_none(item.max_age_seconds),
    }


def _project_unavailable(record: UnavailableRecord) -> dict[str, Any]:
    return {
        "symbol": record.symbol,
        "timeframe": record.trigger_timeframe,
        "source_candle_ts": _utc_iso(record.source_candle_ts),
        "source_interval_start": _utc_iso(record.source_interval_start),
        "source_interval_close": _utc_iso(record.source_interval_close),
        "completed_at": _utc_iso(record.completed_at),
        "received_at": _utc_iso(record.received_at),
        "prerequisites": [
            {"name": _clip(item.name), "reason": _clip(item.reason)} for item in record.prerequisites
        ],
    }


def _project_problem(problem: ObservedProblem) -> dict[str, Any]:
    return {
        "arrival_sequence": problem.arrival_sequence,
        "status": problem.status,
        "reason": _clip(problem.reason),
        "symbol": problem.symbol,
        "timeframe": problem.timeframe,
        "source_candle_ts": _utc_iso(problem.source_candle_ts),
        "received_at": _utc_iso(problem.received_at),
    }


def _counter(pairs: tuple[tuple[str, int], ...]) -> dict[str, int]:
    return {key: int(count) for key, count in sorted(pairs)}


def _truncation(limit: int, returned: int, total: int) -> dict[str, Any]:
    return {"limit": limit, "returned": returned, "total": total, "truncated": returned < total}


def project_candidate_observation(snapshot: ObservationSnapshot) -> dict[str, Any]:
    """Serialize one captured snapshot. See the module docstring for guarantees."""
    if snapshot.status != "running":
        reason = REASON_READER_NOT_STARTED if snapshot.status == "not_started" else REASON_READER_STOPPED
        return unavailable_payload(reason, reader_status=snapshot.status, mode=snapshot.mode)

    assessments = snapshot.eligibility.assessments
    # eligible first, then the snapshot's own (symbol, strategy, version) order
    ordered = sorted(
        assessments,
        key=lambda item: (not item.eligible, item.symbol, item.strategy, item.strategy_version),
    )
    eligible_total = sum(1 for item in assessments if item.eligible)
    by_disposition: dict[str, int] = {}
    for item in assessments:
        by_disposition[item.kind] = by_disposition.get(item.kind, 0) + 1

    rows = [_project_candidate(item) for item in ordered[:MAX_CANDIDATE_ROWS]]
    frames = sorted(snapshot.unavailable_frames, key=lambda rec: (rec.symbol, rec.trigger_timeframe))
    frame_rows = [_project_unavailable(rec) for rec in frames[:MAX_UNAVAILABLE_FRAMES]]
    diagnostics = snapshot.diagnostics

    return {
        "status": "available",
        "reason": None,
        "reason_text": None,
        "observation_only": True,
        "source": SOURCE_ORIGIN,
        "reader": {"status": snapshot.status, "execution_mode": snapshot.mode},
        "snapshot": {
            "as_of": _utc_iso(snapshot.as_of),
            "arrival_sequence": int(snapshot.arrival_sequence),
            "freshness": {
                "status": snapshot.freshness_status,
                "configured": snapshot.freshness_status == "freshness_policy_configured",
            },
            "reset": {
                "boundary": _utc_iso(snapshot.eligibility.reset_boundary),
                "count": int(snapshot.eligibility.reset_count),
            },
            "counts": {
                "evaluations": len(assessments),
                "eligible": eligible_total,
                "ineligible": len(assessments) - eligible_total,
                "by_disposition": dict(sorted(by_disposition.items())),
                "unavailable_inputs": len(frames),
            },
            "candidates": rows,
            "candidates_truncation": _truncation(MAX_CANDIDATE_ROWS, len(rows), len(assessments)),
            "unavailable_inputs": frame_rows,
            "unavailable_inputs_truncation": _truncation(MAX_UNAVAILABLE_FRAMES, len(frame_rows), len(frames)),
            "diagnostics": {
                "deliveries": int(diagnostics.deliveries),
                "by_status": _counter(diagnostics.by_status),
                "by_reason": _counter(diagnostics.by_reason),
                "resets": _counter(diagnostics.resets),
                "recent_problems": [_project_problem(item) for item in diagnostics.recent_problems],
            },
        },
    }
