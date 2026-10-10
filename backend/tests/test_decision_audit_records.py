"""D2 pure record contract: validation, detachment, shadow-only D1 mapping, non-shadow input."""
from __future__ import annotations

import ast
import json
from datetime import timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.decision_audit import records as records_module, shadow as shadow_module
from app.decision_audit.records import (
    EVIDENCE_KEYS, NonShadowSelectionInput, SelectionAttemptRecord, SelectionAuditError, check_acceptance_support,
)
from app.decision_audit.shadow import shadow_selection_record
from tests.decision_audit_support import CREATED, d1, non_shadow_input, non_shadow_record, shadow_record
from tests.decision_test_support import evidence_record, make_batch, opportunity_disposition


BASE = non_shadow_record("orb")


def fresh_evidence():
    return BASE.evidence


def make(**changes):
    base = BASE
    fields = dict(selection_id=base.selection_id, created_at=CREATED, execution_mode="simulated", shadow=False,
                  policy_version=base.policy_version, result="selected",
                  selected_candidate_id=base.selected_candidate_id, evidence=base.evidence)
    fields.update(changes)
    return SelectionAttemptRecord.create(**fields)


def test_real_d1_selected_result_is_journalled_shadow_only():
    selection, ranking = d1("orb", evidence=(evidence_record(strategy="orb"),))
    assert selection.status == "selected" and selection.shadow and not selection.authorizes_trade
    record = shadow_selection_record(selection, ranking, created_at=CREATED)
    assert record.shadow is True and record.result == "selected"
    assert record.selected_candidate_id == selection.selected_candidate_id
    assert str(record.selection_id) == selection.audit_id and record.policy_version == "unique_candidate_v1"
    evidence = record.evidence
    assert set(evidence) == EVIDENCE_KEYS
    assert evidence["shadow"] is True and evidence["authorizes_trade"] is False
    assert evidence["considered_candidate_ids"] == [selection.selected_candidate_id]
    assert evidence["cutoff"] == selection.cutoff.to_audit_record()
    assert evidence["portfolio_captured_at"] == selection.to_audit_record()["portfolio_captured_at"]
    prov = evidence["ranking_evidence"]["candidate_evidence"][0]
    assert prov["candidate_id"] == selection.selected_candidate_id and prov["attributed"][0]["evidence_ref"] == "ev-1"
    assert evidence["selected_candidate"]["source_candle_ts"] and evidence["selected_candidate"]["received_at"]


def test_real_d1_abstention_is_journalled_with_reasons_and_exclusions():
    rival = make_batch(symbol="MSFT", dispositions=[opportunity_disposition("orb")])
    selection, ranking = d1("orb", extra=[rival])
    assert selection.status == "abstained"
    record = shadow_selection_record(selection, ranking, created_at=CREATED)
    assert record.result == "abstained" and record.selected_candidate_id is None
    assert "multiple_surviving_candidates" in record.evidence["abstention_reasons"]
    assert len(record.evidence["considered_candidate_ids"]) == 2
    assert record.evidence["conflict_groups"]


def test_no_featureset_is_duplicated_in_evidence():
    text = shadow_record("orb").evidence_json
    for forbidden in ("feature_set", "FeatureSet", "features\""):
        assert forbidden not in text


def test_shadow_adapter_has_no_way_to_label_non_shadow_and_rejects_mismatched_ranking():
    import inspect

    assert "shadow" not in inspect.signature(shadow_selection_record).parameters
    selection, _ = d1("orb")
    _, other_ranking = d1("orb", minute=3)
    with pytest.raises(SelectionAuditError, match="ranking result"):
        shadow_selection_record(selection, other_ranking, created_at=CREATED)


def test_shadow_adapter_requires_canonical_uuid_audit_id():
    from tests.decision_test_support import ranking_from, run_select, snapshot_from, make_batch

    ranking = ranking_from(snapshot_from([make_batch()]))
    selection = run_select(ranking, audit_id="audit-0001")
    with pytest.raises(SelectionAuditError, match="UUID"):
        shadow_selection_record(selection, ranking, created_at=CREATED)


def test_d1_audit_cannot_be_relabelled_non_shadow():
    base = shadow_record("orb")
    with pytest.raises(SelectionAuditError, match="shadow flag"):
        SelectionAttemptRecord.create(
            selection_id=base.selection_id, created_at=CREATED, execution_mode="simulated", shadow=False,
            policy_version=base.policy_version, result="selected",
            selected_candidate_id=base.selected_candidate_id, evidence=base.evidence)  # evidence says shadow true
    assert not hasattr(NonShadowSelectionInput, "from_selection_result")


def test_non_shadow_input_requires_non_shadow_evidence_and_never_authorizes():
    item = non_shadow_input("orb")
    record = item.to_record()
    assert record.shadow is False and record.evidence["shadow"] is False and record.evidence["authorizes_trade"] is False
    evidence = item.evidence
    evidence = dict(evidence)
    evidence["authorizes_trade"] = True
    with pytest.raises(SelectionAuditError, match="authorizes"):
        NonShadowSelectionInput(item.selection_id, item.created_at, item.execution_mode, item.policy_version,
                                item.result, item.selected_candidate_id, evidence).to_record()


@pytest.mark.parametrize("mutate,match", [
    (lambda e: e.pop("cutoff"), "keys differ"),
    (lambda e: e.update(extra=1), "keys differ"),
    (lambda e: e.update(status="abstained"), "status contradicts"),
    (lambda e: e.update(execution_mode="paper"), "execution_mode contradicts"),
    (lambda e: e.update(audit_id=str(uuid4())), "audit_id contradicts"),
    (lambda e: e.update(selected_candidate_id="cnd1:other"), "contradicts"),
    (lambda e: e.update(considered_candidate_ids=[]), "not among"),
    (lambda e: e.update(abstention_reasons=["x"]), "no abstention reasons"),
    (lambda e: e.update(portfolio=None, portfolio_captured_at=None), "ledger-synchronized|present together"),
    (lambda e: e.update(ranking_evidence={}), "provenance"),
    (lambda e: e.update(schema_version=0), "schema_version"),
    (lambda e: e.update(cutoff={"arrival_sequence": -1, "cutoff_at": "x"}), "cutoff"),
])
def test_contradictory_or_incomplete_evidence_is_refused(mutate, match):
    evidence = fresh_evidence()
    mutate(evidence)
    with pytest.raises(SelectionAuditError, match=match):
        make(evidence=evidence)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), object(), (1, 2), {1: "x"}, b"x", {1, 2}])
def test_strict_finite_plain_json_only(bad):
    evidence = fresh_evidence()
    evidence["slots_available"] = bad
    with pytest.raises(SelectionAuditError, match="plain finite JSON"):
        make(evidence=evidence)


def test_non_dict_evidence_and_bad_columns_are_refused():
    with pytest.raises(SelectionAuditError, match="dict"):
        make(evidence=[1])
    for changes, match in [
        ({"execution_mode": "staging"}, "execution mode"), ({"result": "maybe"}, "result"),
        ({"result": "abstained"}, "required if and only if"), ({"selected_candidate_id": None}, "required if and only if"),
        ({"schema_version": 2}, "schema_version"), ({"schema_version": True}, "schema_version"),
        ({"policy_version": ""}, "policy_version"), ({"policy_version": "x" * 65}, "64"),
        ({"shadow": 1}, "shadow"), ({"selection_id": "not-a-uuid"}, "UUID"),
        ({"selection_id": str(uuid4()).upper()}, "canonical"),
    ]:
        with pytest.raises(SelectionAuditError, match=match):
            make(**changes)
    import datetime as dt

    with pytest.raises(SelectionAuditError, match="timezone-aware"):
        make(created_at=dt.datetime(2026, 1, 1))


def test_caller_mapping_is_detached_both_ways():
    evidence = fresh_evidence()
    record = make(evidence=evidence)
    before = record.evidence_json
    evidence["considered_candidate_ids"].append("mutated")
    evidence["portfolio"]["available"] = False
    assert record.evidence_json == before
    out = record.evidence
    out["considered_candidate_ids"].clear()
    out["cutoff"]["arrival_sequence"] = 999
    assert record.evidence_json == before and record.evidence != out
    assert json.loads(before)["considered_candidate_ids"]


def test_created_at_is_normalised_to_utc_and_equivalence_is_complete():
    from datetime import datetime, timedelta, timezone as tz

    a = make()
    plus = datetime(2026, 1, 1, 12, tzinfo=tz(timedelta(hours=6)))
    b = make(created_at=plus)
    assert b.created_at.utcoffset() == timedelta(0)
    assert a.equivalent(make())
    assert not a.equivalent(b)
    evidence = a.evidence
    evidence["slots_available"] = 5
    assert not a.equivalent(make(evidence=evidence))
    assert not a.equivalent(object())


def test_acceptance_support_rules():
    record = non_shadow_record("orb")
    args = dict(execution_mode="simulated", candidate_id=record.selected_candidate_id, symbol="AAPL",
                strategy="orb", strategy_version="1", direction="BUY")
    check_acceptance_support(record, **args)
    for changes, match in [
        ({"execution_mode": "paper"}, "another execution mode"), ({"candidate_id": "cnd1:zzz"}, "different candidate"),
        ({"symbol": "MSFT"}, "does not match"), ({"strategy": "gap"}, "does not match"),
        ({"strategy_version": "2"}, "does not match"), ({"direction": "SELL"}, "does not match"),
    ]:
        with pytest.raises(SelectionAuditError, match=match):
            check_acceptance_support(record, **{**args, **changes})
    with pytest.raises(SelectionAuditError, match="does not exist"):
        check_acceptance_support(None, **args)
    with pytest.raises(SelectionAuditError, match="shadow"):
        check_acceptance_support(shadow_record("orb"), **args)
    rival = make_batch(symbol="MSFT", dispositions=[opportunity_disposition("orb")])
    selection, ranking = d1("orb", extra=[rival])
    abstained = shadow_selection_record(selection, ranking, created_at=CREATED)
    ev = abstained.evidence
    ev["shadow"] = False
    non_shadow_abstained = SelectionAttemptRecord.create(
        selection_id=abstained.selection_id, created_at=CREATED, execution_mode="simulated", shadow=False,
        policy_version=abstained.policy_version, result="abstained", selected_candidate_id=None, evidence=ev)
    with pytest.raises(SelectionAuditError, match="abstained"):
        check_acceptance_support(non_shadow_abstained, **args)


def test_pure_modules_import_no_database_or_wiring():
    banned = ("sqlalchemy", "app.event_bus", "app.execution_engine", "app.portfolio_state", "app.db")
    for module in (records_module, shadow_module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert not name.startswith(banned), f"{module.__name__} imports {name}"
            if isinstance(node, ast.Call):
                func = node.func
                assert (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)) not in {
                    "now", "utcnow", "uuid4", "sleep", "open", "print"}
