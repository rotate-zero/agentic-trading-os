"""D1 ranking boundary over REAL C1 snapshots: honest unranked, attribution, as-of handling."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from app.trading_intelligence.candidate_contract import StrategyDisposition
from app.trading_intelligence.candidate_ranking import (
    RankingPolicyRef,
    SnapshotCutoff,
    rank_candidates,
)
from app.trading_intelligence.decision_evidence import DecisionContractError
from tests.decision_test_support import (
    AS_OF,
    CLOSE,
    CUTOFF,
    POLICY,
    RECEIVED,
    T0,
    evidence_record,
    evidence_snapshot,
    make_batch,
    make_opportunity,
    opportunity_disposition,
    ranking_from,
    snapshot_from,
)


def two_strategy_batch(symbol="AAPL", **kw):
    return make_batch(
        dispositions=[opportunity_disposition("orb", direction="long"), opportunity_disposition("gap", direction="short")],
        symbol=symbol,
        **kw,
    )


# --- Honest non-ranking ----------------------------------------------------


def test_zero_candidates_is_unranked_and_says_why() -> None:
    result = ranking_from(snapshot_from([]))
    assert result.status == "unranked"
    assert result.reasons == ("no_eligible_candidates", "no_approved_ranking_policy")
    assert result.candidates == () and result.ranked_order == () and result.candidate_ids == ()


def test_one_candidate_is_still_unranked_with_no_policy() -> None:
    snapshot = snapshot_from([make_batch()])
    result = ranking_from(snapshot)
    assert result.status == "unranked"
    assert result.reasons == ("no_approved_ranking_policy",)
    assert result.ranking_policy_id is None and result.ranking_policy_version is None
    assert result.adapter_version == "unranked_adapter_v1"
    assert result.ranked_order == ()
    assert result.candidate_ids == (snapshot.eligible[0].candidate_id,)


def test_many_candidates_are_never_ordered_or_scored() -> None:
    low = opportunity_disposition("orb", confidence=0.10)
    high = opportunity_disposition("gap", confidence=0.99)
    result = ranking_from(snapshot_from([make_batch(dispositions=[low, high])]))
    assert result.status == "unranked" and result.ranked_order == ()
    ids = result.candidate_ids
    assert list(ids) == sorted(ids)  # reproducibility/display order only
    assert not hasattr(result, "scores") and not hasattr(result, "winner")


def test_confidence_is_descriptive_not_an_order() -> None:
    """Swapping confidences never changes the ID-sorted listing or the status."""
    a = ranking_from(snapshot_from([make_batch(dispositions=[
        opportunity_disposition("orb", confidence=0.1), opportunity_disposition("gap", confidence=0.9)])]))
    b = ranking_from(snapshot_from([make_batch(dispositions=[
        opportunity_disposition("orb", confidence=0.9), opportunity_disposition("gap", confidence=0.1)])]))
    assert a.candidate_ids == b.candidate_ids and a.ranked_order == b.ranked_order == ()
    assert {c.strategy: c.reported_confidence for c in a.candidates} == {"orb": 0.1, "gap": 0.9}


def test_naming_a_ranking_policy_is_unavailable_not_invented() -> None:
    result = ranking_from(snapshot_from([make_batch()]), ranking_policy=RankingPolicyRef("weighted_v1", "3"))
    assert result.status == "unavailable"
    assert result.reasons == ("ranking_policy_not_implemented:weighted_v1@3",)
    assert (result.ranking_policy_id, result.ranking_policy_version) == ("weighted_v1", "3")
    assert result.ranked_order == () and len(result.candidates) == 1


def test_rank_requires_the_c1_snapshot_evidence_and_cutoff_types() -> None:
    snapshot = snapshot_from([make_batch()])
    with pytest.raises(DecisionContractError):
        rank_candidates(object(), evidence_snapshot(), cutoff=CUTOFF)  # type: ignore[arg-type]
    with pytest.raises(DecisionContractError):
        rank_candidates(snapshot, object(), cutoff=CUTOFF)  # type: ignore[arg-type]
    with pytest.raises(DecisionContractError):
        rank_candidates(snapshot, evidence_snapshot(), cutoff=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        rank_candidates(snapshot, evidence_snapshot())  # type: ignore[call-arg]  # cutoff is explicit


# --- Captured set: C1 exclusions and cutoff --------------------------------


def test_c1_ineligible_assessments_are_excluded_with_their_reasons() -> None:
    gated = StrategyDisposition("gate", "1", "gated", reason="volume_gate")
    errored = StrategyDisposition("boom", "1", "error", reason="boom")
    none = StrategyDisposition("quiet", "1", "no_opportunity")
    waiting = StrategyDisposition("wait", "1", "opportunity", make_opportunity(status="waiting"))
    snapshot = snapshot_from([make_batch(dispositions=[opportunity_disposition("orb"), gated, errored, none, waiting])])
    result = ranking_from(snapshot)
    assert [c.strategy for c in result.candidates] == ["orb"]
    reasons = {item.strategy: item.reasons for item in result.excluded}
    assert reasons == {
        "gate": ("gated",), "boom": ("error",), "quiet": ("no_opportunity",), "wait": ("not_actionable",),
    }
    assert {item.stage for item in result.excluded} == {"eligibility"}
    assert all(item.candidate_id is None for item in result.excluded if item.kind != "opportunity")


def test_unconfigured_freshness_keeps_the_candidate_out_of_the_set() -> None:
    snapshot = snapshot_from([make_batch()], policy=None)
    result = ranking_from(snapshot)
    assert result.candidates == ()
    (excluded,) = result.excluded
    assert excluded.reasons == ("freshness_policy_unconfigured",)
    assert result.reasons[0] == "no_eligible_candidates"


def test_expired_candidate_is_excluded() -> None:
    snapshot = snapshot_from([make_batch()], as_of=CLOSE + timedelta(seconds=121))
    result = ranking_from(snapshot)
    assert result.candidates == () and result.excluded[0].reasons == ("expired",)


def test_later_arrival_is_not_pulled_into_the_captured_set() -> None:
    early = make_batch(symbol="AAA")
    late = make_batch(symbol="BBB")
    snapshot = snapshot_from([(early, RECEIVED), (late, CUTOFF.cutoff_at + timedelta(microseconds=1))])
    result = ranking_from(snapshot)
    assert [c.symbol for c in result.candidates] == ["AAA"]
    (late_item,) = result.excluded
    assert (late_item.symbol, late_item.stage, late_item.reasons) == ("BBB", "capture_cutoff", ("received_after_cutoff",))


def test_arrival_exactly_at_the_cutoff_is_included() -> None:
    snapshot = snapshot_from([(make_batch(), CUTOFF.cutoff_at)])
    assert len(ranking_from(snapshot).candidates) == 1


def test_cutoff_validation() -> None:
    for bad in (-1, True, 1.5, "1"):
        with pytest.raises(DecisionContractError):
            SnapshotCutoff(bad, AS_OF)  # type: ignore[arg-type]
    with pytest.raises(DecisionContractError):
        SnapshotCutoff(1, AS_OF.replace(tzinfo=None))


# --- Groups ----------------------------------------------------------------


def test_direction_symbol_and_comparability_groups() -> None:
    snapshot = snapshot_from([
        two_strategy_batch("AAPL"),
        make_batch(symbol="MSFT", dispositions=[opportunity_disposition("orb", direction="long"),
                                                 opportunity_disposition("momentum", direction="long")]),
        make_batch(symbol="TSLA", dispositions=[opportunity_disposition("orb", direction="short")]),
    ])
    result = ranking_from(snapshot)
    by_symbol = {g.symbol: g for g in result.symbol_groups}
    assert by_symbol["AAPL"].relation == "opposite_direction" and by_symbol["AAPL"].directions == ("long", "short")
    assert by_symbol["MSFT"].relation == "same_direction" and by_symbol["MSFT"].directions == ("long",)
    assert by_symbol["TSLA"].relation == "single"
    directions = {g.direction: g.candidate_ids for g in result.direction_groups}
    assert len(directions["long"]) == 3 and len(directions["short"]) == 2
    assert {c.direction for c in result.candidates if c.candidate_id in directions["long"]} == {"long"}
    (group,) = result.comparability_groups
    assert group.trigger_timeframe == "1m" and group.comparability == "unassessed"
    assert set(group.candidate_ids) == set(result.candidate_ids)


# --- Determinism ------------------------------------------------------------


def test_same_captured_inputs_give_equal_results_regardless_of_input_order() -> None:
    batches = [two_strategy_batch("AAPL"), make_batch(symbol="MSFT"), make_batch(symbol="TSLA")]
    forward = ranking_from(snapshot_from(batches))
    backward = ranking_from(snapshot_from(list(reversed(batches))))
    assert forward == backward
    assert forward.to_audit_record() == backward.to_audit_record()


def test_arrival_order_does_not_change_the_result() -> None:
    first = snapshot_from([(make_batch(symbol="AAA"), RECEIVED), (make_batch(symbol="BBB"), RECEIVED + timedelta(seconds=3))])
    second = snapshot_from([(make_batch(symbol="AAA"), RECEIVED + timedelta(seconds=3)), (make_batch(symbol="BBB"), RECEIVED)])
    assert ranking_from(first).candidate_ids == ranking_from(second).candidate_ids
    assert ranking_from(first).ranked_order == ranking_from(second).ranked_order == ()


# --- Evidence attribution and provenance ---------------------------------------


def test_no_evidence_is_an_explicit_gap_never_a_neutral_value() -> None:
    result = ranking_from(snapshot_from([make_batch()]))
    (view,) = result.candidate_evidence
    assert view.attributed == () and view.gaps == ("no_attributed_evidence",)
    assert result.unattributed_evidence == ()


def test_evidence_attaches_only_to_the_same_strategy_and_version() -> None:
    snapshot = snapshot_from([make_batch(dispositions=[opportunity_disposition("orb", version="1"),
                                                         opportunity_disposition("orb", version="2")])])
    result = ranking_from(snapshot, evidence_record("v1", strategy_version="1"), evidence_record("v3", strategy_version="3"),
                          evidence_record("other", strategy="gap"))
    attributed = {
        c.strategy_version: [r.evidence_ref for r in view.attributed]
        for c in result.candidates
        for view in result.candidate_evidence
        if view.candidate_id == c.candidate_id
    }
    assert attributed == {"1": ["v1"], "2": []}
    assert {(item.evidence_ref, item.reason) for item in result.unattributed_evidence} == {
        ("v3", "no_matching_candidate"), ("other", "no_matching_candidate"),
    }


def test_symbol_and_direction_narrow_attribution() -> None:
    snapshot = snapshot_from([two_strategy_batch("AAPL"), make_batch(symbol="MSFT", dispositions=[opportunity_disposition("orb")])])
    result = ranking_from(snapshot, evidence_record("aapl-long", symbol="AAPL", direction="long"),
                          evidence_record("short-only", strategy="gap", direction="long"))
    refs = {}
    for view in result.candidate_evidence:
        candidate = next(c for c in result.candidates if c.candidate_id == view.candidate_id)
        refs[(candidate.symbol, candidate.strategy)] = [r.evidence_ref for r in view.attributed]
    assert refs[("AAPL", "orb")] == ["aapl-long"]
    assert refs[("MSFT", "orb")] == []
    assert refs[("AAPL", "gap")] == []  # gap candidate is short; the record is long-only


def test_future_or_unknowable_evidence_never_influences_the_result() -> None:
    snapshot = snapshot_from([make_batch()])
    result = ranking_from(
        snapshot,
        evidence_record("ok"),
        evidence_record("future-availability", available_at=AS_OF + timedelta(microseconds=1)),
        evidence_record("future-sample", sample_period_end=AS_OF + timedelta(seconds=1)),
        evidence_record("unknown-availability", available_at=None),
    )
    (view,) = result.candidate_evidence
    assert [r.evidence_ref for r in view.attributed] == ["ok"]
    assert {(e.evidence_ref, e.reason) for e in view.excluded} == {
        ("future-availability", "unavailable_at_as_of"),
        ("future-sample", "sample_period_after_as_of"),
        ("unknown-availability", "availability_time_unknown"),
    }
    assert "ok:missing:available_at" not in view.gaps


def test_evidence_available_exactly_at_as_of_is_usable() -> None:
    result = ranking_from(snapshot_from([make_batch()]), evidence_record("edge", available_at=AS_OF, sample_period_end=AS_OF))
    assert [r.evidence_ref for r in result.candidate_evidence[0].attributed] == ["edge"]


def test_excluded_unmatched_future_evidence_reports_the_leakage_reason() -> None:
    result = ranking_from(snapshot_from([make_batch()]),
                          evidence_record("future-other", strategy="gap", available_at=AS_OF + timedelta(days=1)))
    assert [(e.evidence_ref, e.reason) for e in result.unattributed_evidence] == [("future-other", "unavailable_at_as_of")]


def test_adding_future_evidence_changes_nothing_but_the_exclusion_list() -> None:
    snapshot = snapshot_from([make_batch()])
    base = ranking_from(snapshot, evidence_record("ok"))
    with_future = ranking_from(snapshot, evidence_record("ok"), evidence_record("future", available_at=AS_OF + timedelta(days=1)))
    assert base.candidate_evidence[0].attributed == with_future.candidate_evidence[0].attributed
    assert base.candidate_evidence[0].gaps == with_future.candidate_evidence[0].gaps
    assert base.status == with_future.status and base.candidate_ids == with_future.candidate_ids


def test_gaps_name_missing_provenance_empty_samples_and_synthetic_evidence() -> None:
    sparse = evidence_record("sparse", configuration_ref=None, outcome_definition=None, sample_count=0)
    synthetic = evidence_record("synthetic", evidence_class="synthetic_mechanics")
    result = ranking_from(snapshot_from([make_batch()]), sparse, synthetic)
    gaps = set(result.candidate_evidence[0].gaps)
    assert {"sparse:missing:configuration_ref", "sparse:missing:outcome_definition", "sparse:empty_sample",
            "synthetic:synthetic_mechanics_not_calibration"} <= gaps
    assert "only_synthetic_mechanics_evidence" not in gaps  # a calibration record is present


def test_synthetic_only_evidence_is_flagged_as_mechanics_not_calibration() -> None:
    result = ranking_from(snapshot_from([make_batch()]), evidence_record("syn", evidence_class="synthetic_mechanics"))
    gaps = result.candidate_evidence[0].gaps
    assert "only_synthetic_mechanics_evidence" in gaps and "syn:synthetic_mechanics_not_calibration" in gaps
    assert result.status == "unranked"  # nothing here can satisfy a calibration gate


def test_populations_are_labelled_never_blended() -> None:
    result = ranking_from(
        snapshot_from([make_batch()]),
        evidence_record("sim", population="simulated"),
        evidence_record("bt", population="backtest", backtest_run_id="run-1", data_provenance="d", feature_provenance="f"),
    )
    view = result.candidate_evidence[0]
    assert {r.population for r in view.attributed} == {"simulated", "backtest"}
    assert "mixed_populations_not_blended" in view.gaps
    audit = result.to_audit_record()["candidate_evidence"][0]["attributed"]
    assert {item["population"] for item in audit} == {"simulated", "backtest"}


def test_attributed_evidence_retains_all_provenance() -> None:
    record = evidence_record("full", population="backtest", backtest_run_id="run-9", backtest_sweep_id="sw-2",
                             data_provenance="dataset-a", feature_provenance="fe-3", configuration_ref="cfg-x",
                             sample_count=57, outcome_definition="realized_r", execution_venue="simulated",
                             context_slice={"market_state": "trend_up"})
    result = ranking_from(snapshot_from([make_batch()]), record)
    (attributed,) = result.candidate_evidence[0].attributed
    assert attributed is record
    audit = result.to_audit_record()["candidate_evidence"][0]["attributed"][0]
    assert audit["backtest_run_id"] == "run-9" and audit["backtest_sweep_id"] == "sw-2"
    assert audit["context_slice"] == {"market_state": "trend_up"} and audit["sample_count"] == 57
    assert audit["missing_fields"] == []


# --- Detached, audit-ready values ----------------------------------------------


def test_result_is_immutable_json_safe_and_detached() -> None:
    result = ranking_from(snapshot_from([two_strategy_batch()]), evidence_record("e1"))
    with pytest.raises(FrozenInstanceError):
        result.status = "ranked"  # type: ignore[misc]
    audit = result.to_audit_record()
    text = json.dumps(audit, allow_nan=False, sort_keys=True)
    assert json.loads(text) == audit
    audit["candidates"].clear()
    audit["candidate_evidence"][0]["attributed"].clear()
    again = result.to_audit_record()
    assert len(again["candidates"]) == 2 and again["candidate_evidence"][0]["attributed"]
    assert result.to_audit_record() == again and result.to_audit_record() is not again
    assert audit["cutoff"] == {"arrival_sequence": 41, "cutoff_at": "2026-10-09T14:01:10.000000Z"}


def test_result_records_captured_times_and_reset_context() -> None:
    result = ranking_from(snapshot_from([make_batch()]))
    assert result.eligibility_as_of == AS_OF and result.evidence_as_of == AS_OF
    assert result.cutoff == CUTOFF and result.execution_mode == "simulated"
    assert result.reset_boundary is None and result.reset_count == 0
    candidate = result.candidates[0]
    assert candidate.source_candle_ts == T0 and candidate.received_at == RECEIVED
    assert candidate.age_seconds == 10.0 and candidate.max_age_seconds == POLICY.max_age_for("1m")


def test_ranking_does_not_mutate_its_inputs() -> None:
    snapshot = snapshot_from([two_strategy_batch()])
    before = repr(snapshot)
    evidence = evidence_snapshot(evidence_record("e1"))
    rank_candidates(snapshot, evidence, cutoff=CUTOFF)
    assert repr(snapshot) == before and len(evidence.records) == 1
