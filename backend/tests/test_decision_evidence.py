"""D1 evidence values: provenance kept, missing stays explicit, populations never mixed."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from app.trading_intelligence.decision_evidence import DecisionContractError, EvidenceRecord, EvidenceSnapshot
from tests.decision_test_support import AS_OF, evidence_record, evidence_snapshot


def test_complete_record_has_no_missing_fields_and_keeps_provenance() -> None:
    record = evidence_record()
    assert record.missing_fields == ()
    assert record.context_slice_copy() == {"session": "regular"}
    assert record.metrics_copy() == {"win_rate": 0.5}
    audit = record.to_audit_record()
    assert audit["configuration_ref"] == "cfg-a"
    assert audit["sample_count"] == 12 and audit["outcome_definition"] == "realized_r"
    assert audit["population"] == "simulated" and audit["execution_venue"] == "simulated"
    assert audit["sample_period_end"].endswith("Z")


def test_missing_provenance_is_reported_not_filled() -> None:
    record = EvidenceRecord.create(
        evidence_ref="sparse", strategy="orb", strategy_version="1", population="simulated", evidence_class="calibration"
    )
    assert record.missing_fields == (
        "available_at", "configuration_ref", "context_slice_json", "sample_period_start",
        "sample_period_end", "sample_count", "outcome_definition", "execution_venue",
    )
    assert record.sample_count is None and record.configuration_ref is None  # nothing defaulted


def test_backtest_record_requires_and_retains_backtest_provenance() -> None:
    bare = evidence_record("bt", population="backtest")
    assert bare.missing_fields == ("backtest_run_id", "data_provenance", "feature_provenance")
    full = evidence_record(
        "bt2", population="backtest", backtest_run_id="run-1", backtest_sweep_id="sweep-1",
        data_provenance="polygon-2026-09", feature_provenance="feature-engine-v3",
    )
    assert full.missing_fields == ()
    audit = full.to_audit_record()
    assert (audit["backtest_run_id"], audit["backtest_sweep_id"]) == ("run-1", "sweep-1")
    assert audit["data_provenance"] == "polygon-2026-09" and audit["feature_provenance"] == "feature-engine-v3"


@pytest.mark.parametrize("field", ["backtest_run_id", "backtest_sweep_id", "data_provenance", "feature_provenance"])
def test_non_backtest_population_cannot_carry_backtest_provenance(field) -> None:
    with pytest.raises(DecisionContractError):
        evidence_record("x", population="simulated", **{field: "value"})


def test_synthetic_mechanics_is_distinct_and_never_live() -> None:
    record = evidence_record("syn", evidence_class="synthetic_mechanics")
    assert record.is_synthetic_mechanics
    with pytest.raises(DecisionContractError):
        evidence_record("syn-live", population="live", evidence_class="synthetic_mechanics")


def test_empty_sample_is_visible() -> None:
    assert evidence_record("empty", sample_count=0).is_empty_sample
    assert not evidence_record("full", sample_count=3).is_empty_sample


@pytest.mark.parametrize(
    "overrides",
    [
        {"population": "paper-ish"},
        {"evidence_class": "calibrated"},
        {"sample_count": -1},
        {"sample_count": True},
        {"sample_count": 1.5},
        {"direction": "up"},
        {"configuration_ref": " padded "},
        {"strategy": ""},
        {"available_at": datetime(2026, 10, 9, 14, 0)},  # naive
        {"sample_period_start": AS_OF, "sample_period_end": AS_OF - timedelta(seconds=1)},
        {"metrics": {"x": float("nan")}},
        {"context_slice": {"x": float("inf")}},
    ],
)
def test_record_validation_rejects_bad_values(overrides) -> None:
    with pytest.raises(DecisionContractError):
        evidence_record("bad", **overrides)


def test_timestamps_normalise_to_utc() -> None:
    plus_six = timezone(timedelta(hours=6))
    record = evidence_record("tz", available_at=datetime(2026, 10, 9, 20, 0, tzinfo=plus_six))
    assert record.available_at == datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
    assert record.available_at.utcoffset() == timedelta(0)


def test_record_is_detached_and_immutable() -> None:
    context = {"session": "regular", "nested": {"a": 1}}
    metrics = {"win_rate": 0.4}
    record = EvidenceRecord.create(
        evidence_ref="d", strategy="orb", strategy_version="1", population="simulated",
        evidence_class="calibration", context_slice=context, metrics=metrics,
    )
    context["nested"]["a"] = 99
    metrics["win_rate"] = 0.99
    assert record.context_slice_copy() == {"session": "regular", "nested": {"a": 1}}
    assert record.metrics_copy() == {"win_rate": 0.4}
    record.metrics_copy()["win_rate"] = 5
    assert record.metrics_copy() == {"win_rate": 0.4}
    audit = record.to_audit_record()
    audit["metrics"]["win_rate"] = 5
    assert record.to_audit_record()["metrics"]["win_rate"] == 0.4
    with pytest.raises(FrozenInstanceError):
        record.strategy = "other"  # type: ignore[misc]


def test_snapshot_sorts_by_ref_requires_unique_refs_and_aware_as_of() -> None:
    snapshot = evidence_snapshot(evidence_record("b"), evidence_record("a"))
    assert [r.evidence_ref for r in snapshot.records] == ["a", "b"]
    with pytest.raises(DecisionContractError):
        evidence_snapshot(evidence_record("a"), evidence_record("a"))
    with pytest.raises(DecisionContractError):
        EvidenceSnapshot(as_of=datetime(2026, 10, 9, 14, 0))
    with pytest.raises(DecisionContractError):
        EvidenceSnapshot(as_of=AS_OF, records=("not-a-record",))  # type: ignore[arg-type]
    assert EvidenceSnapshot(as_of=AS_OF).records == ()  # no evidence supplied is representable
