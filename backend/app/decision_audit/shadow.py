"""Journal record for a D1 shadow ``SelectionResult`` (D2, §19.4).

The only way to turn a D1 result into a journal record. The record is ALWAYS
``shadow = true``: there is no parameter that changes it, and D1's own audit
says ``shadow: true`` / ``authorizes_trade: false``. A shadow record can never
support a candidate acceptance (the claim table's foreign key excludes it).

The D1 selection audit has no per-candidate evidence provenance; that lives on
the :class:`RankingResult` the selection was computed from. The caller passes
that ranking result too and only its provenance sections (attributed evidence,
excluded evidence, gaps, status/policy) are copied, after the two are checked
to describe the same captured set. No FeatureSet is ever included.
"""
from __future__ import annotations

from datetime import datetime

from app.decision_audit.records import SelectionAttemptRecord, SelectionAuditError, _canonical_uuid
from app.trading_intelligence.candidate_ranking import RankingResult
from app.trading_intelligence.candidate_selection import SelectionResult


def shadow_selection_record(
    selection: SelectionResult, ranking: RankingResult, *, created_at: datetime
) -> SelectionAttemptRecord:
    """The shadow journal record for ``selection`` (computed from ``ranking``).

    ``selection.audit_id`` must be the canonical UUID text that becomes the
    ``selection_id``. ``created_at`` is the writer's explicit journal time; the
    pure core reads no clock, so a retry must reuse the same value to replay
    identically.
    """
    if not isinstance(selection, SelectionResult) or not isinstance(ranking, RankingResult):
        raise SelectionAuditError("a SelectionResult and the RankingResult it was computed from are required")
    mismatches = [
        name
        for name, same in (
            ("execution_mode", selection.execution_mode == ranking.execution_mode),
            ("candidate_ids", selection.considered_candidate_ids == ranking.candidate_ids),
            ("cutoff", selection.cutoff == ranking.cutoff),
            ("ranking_status", selection.ranking_status == ranking.status),
            ("ranking_policy_id", selection.ranking_policy_id == ranking.ranking_policy_id),
            ("ranking_policy_version", selection.ranking_policy_version == ranking.ranking_policy_version),
            ("ranking_adapter_version", selection.ranking_adapter_version == ranking.adapter_version),
            ("eligibility_as_of", selection.eligibility_as_of == ranking.eligibility_as_of),
            ("evidence_as_of", selection.evidence_as_of == ranking.evidence_as_of),
        )
        if not same
    ]
    if mismatches:
        raise SelectionAuditError(f"the ranking result is not the one this selection used: {mismatches}")
    selection_id = _canonical_uuid(selection.audit_id, "selection audit_id")
    ranking_audit = ranking.to_audit_record()
    evidence = selection.to_audit_record()
    evidence["ranking_evidence"] = {
        "status": ranking_audit["status"],
        "reasons": ranking_audit["reasons"],
        "ranking_policy_id": ranking_audit["ranking_policy_id"],
        "ranking_policy_version": ranking_audit["ranking_policy_version"],
        "adapter_version": ranking_audit["adapter_version"],
        "reset_count": ranking_audit["reset_count"],
        "candidate_evidence": ranking_audit["candidate_evidence"],
        "unattributed_evidence": ranking_audit["unattributed_evidence"],
    }
    return SelectionAttemptRecord.create(
        selection_id=selection_id,
        created_at=created_at,
        execution_mode=selection.execution_mode,
        shadow=True,
        policy_version=selection.selection_policy,
        result=selection.status,
        selected_candidate_id=selection.selected_candidate_id,
        evidence=evidence,
    )
