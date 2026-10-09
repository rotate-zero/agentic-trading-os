"""C1 pure reducer: duplicates, conflicts, supersession, ordering, resets, retirement."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from app.trading_intelligence.candidate_contract import (
    CandidateContractError,
    StrategyDisposition,
    UnavailablePrerequisite,
)
from app.trading_intelligence.candidate_eligibility import CandidateFreshnessPolicy, assess_candidates
from app.trading_intelligence.candidate_state import (
    CandidateState,
    apply_batch,
    apply_reset,
    initial_state,
    retire_strategy_versions,
)
from tests.test_opportunity_candidate_contract import (
    ONE_MIN,
    T0,
    make_batch,
    make_opportunity,
    opportunity_disposition,
)

RX = T0 + timedelta(minutes=1, seconds=2)  # a plausible local receive time
POLICY = CandidateFreshnessPolicy.create({"1m": 120.0, "5m": 600.0})


def minute(n: int) -> datetime:
    return T0 + n * ONE_MIN


def reduce(state: CandidateState, batch, received_at: datetime = RX):
    return apply_batch(state, batch, received_at)


def eligible_ids(state: CandidateState, as_of: datetime, policy=POLICY) -> list[str]:
    return [a.candidate_id for a in assess_candidates(state, as_of, policy).eligible]


def slot(state: CandidateState, strategy: str = "orb", version: str = "1", symbol: str = "AAPL"):
    return state.slots[(symbol, strategy, version)]


# --- Basic admission, duplicates, conflicts -------------------------------


def test_first_batch_creates_an_eligible_candidate() -> None:
    result = reduce(initial_state("simulated"), make_batch())
    assert result.status == "applied"
    assert [d.result for d in result.dispositions] == ["applied"]
    assert len(eligible_ids(result.state, minute(1) + timedelta(seconds=30))) == 1


def test_identical_redelivery_is_a_no_op_even_with_a_later_receive_time() -> None:
    first = reduce(initial_state("simulated"), make_batch())
    again = reduce(first.state, make_batch(), RX + timedelta(minutes=30))
    assert again.status == "duplicate"
    assert again.state is first.state  # nothing changed, original receive time preserved
    assert slot(again.state).received_at == RX


def test_changed_confidence_under_the_same_identity_is_an_explicit_conflict() -> None:
    first = reduce(initial_state("simulated"), make_batch())
    changed = make_batch(dispositions=[opportunity_disposition(confidence=0.95)])
    result = reduce(first.state, changed)
    assert result.status == "conflict"
    assert result.reason == "evaluation_content_conflict"
    assert result.state is first.state
    assert result.dispositions[0].result == "conflict"
    assert slot(result.state).opportunity.confidence == 0.7  # never last-write-wins


def test_changed_evidence_stop_or_target_each_conflict() -> None:
    first = reduce(initial_state("simulated"), make_batch())
    for override in (
        {"evidence": {"other": 1}},
        {"structural_invalidation": 98.5},
        {"structural_target": 103.0},
        {"expected_horizon_minutes": 30},
        {"status": "waiting"},
    ):
        result = reduce(first.state, make_batch(dispositions=[opportunity_disposition(**override)]))
        assert result.status == "conflict", override


def test_same_evaluation_with_opposite_direction_conflicts() -> None:
    first = reduce(initial_state("simulated"), make_batch())
    flipped = make_batch(dispositions=[opportunity_disposition(direction="short")])
    result = reduce(first.state, flipped)
    assert result.status == "conflict"
    assert slot(result.state).opportunity.direction == "long"


def test_different_completion_time_for_the_same_evaluation_conflicts() -> None:
    first = reduce(initial_state("simulated"), make_batch())
    result = reduce(first.state, make_batch(completed_delay=timedelta(seconds=9)))
    assert result.status == "conflict"


def test_conflict_is_atomic_across_the_batch() -> None:
    base = make_batch(dispositions=[opportunity_disposition("orb"), opportunity_disposition("gap")])
    first = reduce(initial_state("simulated"), base)
    mixed = make_batch(
        dispositions=[
            opportunity_disposition("orb", confidence=0.1),  # conflicts
            opportunity_disposition("vwap"),  # would be new
        ]
    )
    result = reduce(first.state, mixed)
    assert result.status == "conflict"
    assert {d.strategy: d.result for d in result.dispositions} == {"orb": "conflict", "vwap": "withheld"}
    assert result.state is first.state
    assert ("AAPL", "vwap", "1") not in result.state.slots


# --- Newer invalidation and out-of-order -----------------------------------


@pytest.mark.parametrize(
    "newer",
    [
        StrategyDisposition("orb", "1", "no_opportunity"),
        StrategyDisposition("orb", "1", "gated", reason="volume_gate"),
        StrategyDisposition("orb", "1", "error", reason="ZeroDivisionError"),
    ],
    ids=["no_opportunity", "gated", "error"],
)
def test_newer_complete_non_opportunity_removes_earlier_eligibility(newer) -> None:
    first = reduce(initial_state("simulated"), make_batch(minute(0)))
    as_of = minute(2)
    assert len(eligible_ids(first.state, as_of)) == 1
    second = reduce(first.state, make_batch(minute(1), [newer]), minute(2))
    assert second.status == "applied"
    assert eligible_ids(second.state, as_of) == []
    assert [i.reason for i in second.invalidated] == [f"superseded_by_{newer.kind}"]
    assert second.invalidated[0].candidate_id == first.state.slots[("AAPL", "orb", "1")].candidate_id
    assessment = assess_candidates(second.state, as_of, POLICY).assessments[0]
    assert assessment.reasons == (newer.kind,)
    assert assessment.disposition_reason == newer.reason


def test_older_batch_cannot_resurrect_a_superseded_candidate() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(1), [StrategyDisposition("orb", "1", "no_opportunity")])).state
    late = reduce(state, make_batch(minute(0)), minute(2))  # opportunity from the older candle
    assert late.status == "stale"
    assert late.dispositions[0].reason == "superseded_by_newer_evaluation"
    assert late.state is state
    assert eligible_ids(late.state, minute(2)) == []


def test_out_of_order_arrival_converges_to_the_newest_evaluation() -> None:
    older, newer = make_batch(minute(0)), make_batch(minute(1), [StrategyDisposition("orb", "1", "gated", reason="g")])
    in_order = reduce(reduce(initial_state("simulated"), older).state, newer, minute(2)).state
    reverse = reduce(reduce(initial_state("simulated"), newer, minute(2)).state, older, minute(3)).state
    assert slot(in_order).evaluation_id == slot(reverse).evaluation_id
    assert slot(reverse).kind == "gated"
    assert eligible_ids(reverse, minute(3)) == []


def test_newer_opportunity_replaces_an_older_one_and_reports_supersession() -> None:
    first = reduce(initial_state("simulated"), make_batch(minute(0)))
    second = reduce(first.state, make_batch(minute(1), [opportunity_disposition(direction="short")]), minute(2))
    assert [i.reason for i in second.invalidated] == ["superseded_by_newer_candidate"]
    (current,) = eligible_ids(second.state, minute(2))
    assert current == slot(second.state).candidate_id != first.state.slots[("AAPL", "orb", "1")].candidate_id


def test_untriggered_strategy_keeps_its_record_and_is_not_marked_evaluated() -> None:
    both = make_batch(minute(0), [opportunity_disposition("orb"), opportunity_disposition("gap")])
    state = reduce(initial_state("simulated"), both).state
    only_gap = make_batch(minute(1), [StrategyDisposition("gap", "1", "no_opportunity")])
    result = reduce(state, only_gap, minute(2))
    assert slot(result.state, "orb").source_candle_ts == minute(0)  # untouched
    assert slot(result.state, "gap").kind == "no_opportunity"
    assert [a.strategy for a in assess_candidates(result.state, minute(2), POLICY).eligible] == ["orb"]


def test_symbols_and_versions_are_independent_slots() -> None:
    state = initial_state("simulated")
    state = reduce(state, make_batch(minute(0), symbol="AAPL")).state
    state = reduce(state, make_batch(minute(0), symbol="MSFT")).state
    state = reduce(state, make_batch(minute(0), [opportunity_disposition("orb", "2")])).state
    state = reduce(state, make_batch(minute(1), [StrategyDisposition("orb", "1", "no_opportunity")]), minute(2)).state
    assert {a.symbol + a.strategy_version for a in assess_candidates(state, minute(2), POLICY).eligible} == {"MSFT1", "AAPL2"}


def test_a_slot_is_bound_to_its_first_timeframe() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(0))).state
    other = reduce(state, make_batch(minute(5), timeframe="5m", interval=timedelta(minutes=5)), minute(11))
    assert other.status == "ignored"
    assert other.dispositions[0].reason == "slot_timeframe_mismatch"
    assert other.state is state


# --- Unavailable prerequisites ---------------------------------------------

BAD = [UnavailablePrerequisite("market_state", "candle_ts_mismatch")]


def test_unavailable_batch_contributes_nothing_and_invalidates_older_candidates() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(0), [opportunity_disposition("orb"), opportunity_disposition("gap")])).state
    result = reduce(state, make_batch(minute(1), unavailable=BAD), minute(2))
    assert result.status == "applied" and result.reason == "prerequisite_unavailable"
    assert {i.strategy for i in result.invalidated} == {"orb", "gap"}
    assert eligible_ids(result.state, minute(2)) == []
    assessment = assess_candidates(result.state, minute(2), POLICY).assessments[0]
    assert assessment.reasons == ("invalidated:prerequisite_unavailable",)
    assert assessment.invalidation_reason == "prerequisite_unavailable"
    assert slot(result.state).invalidation.source_candle_ts == minute(1)
    assert slot(result.state).invalidation.invalidated_at == minute(2)


def test_unavailable_batch_leaves_other_symbols_and_timeframes_alone() -> None:
    state = initial_state("simulated")
    state = reduce(state, make_batch(minute(0), symbol="AAPL")).state
    state = reduce(state, make_batch(minute(0), symbol="MSFT")).state
    state = reduce(state, make_batch(minute(1), symbol="AAPL", unavailable=BAD), minute(2)).state
    assert [a.symbol for a in assess_candidates(state, minute(2), POLICY).eligible] == ["MSFT"]


def test_older_available_batch_cannot_revive_after_an_unavailable_candle() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(2), unavailable=BAD), minute(3)).state
    late = reduce(state, make_batch(minute(1), [opportunity_disposition("gap")]), minute(4))
    assert late.status == "stale" and late.reason == "superseded_by_unavailable_input"
    assert late.state is state


def test_available_batch_for_the_unavailable_candle_is_an_explicit_conflict() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(1), unavailable=BAD), minute(2)).state
    result = reduce(state, make_batch(minute(1)), minute(3))
    assert result.status == "conflict" and result.reason == "unavailable_input_recorded_for_candle"


def test_unavailable_batch_for_an_already_evaluated_candle_conflicts() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(1))).state
    result = reduce(state, make_batch(minute(1), unavailable=BAD), minute(3))
    assert result.status == "conflict" and result.reason == "evaluation_recorded_for_candle"
    assert result.state is state


def test_unavailable_redelivery_duplicate_conflict_and_stale() -> None:
    first = reduce(initial_state("simulated"), make_batch(minute(2), unavailable=BAD), minute(3))
    assert reduce(first.state, make_batch(minute(2), unavailable=BAD), minute(9)).status == "duplicate"
    other = [UnavailablePrerequisite("features", "missing")]
    assert reduce(first.state, make_batch(minute(2), unavailable=other)).status == "conflict"
    assert reduce(first.state, make_batch(minute(1), unavailable=BAD)).status == "stale"


def test_newer_available_batch_after_unavailable_is_admitted() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(1), unavailable=BAD), minute(2)).state
    result = reduce(state, make_batch(minute(2)), minute(3))
    assert result.status == "applied"
    assert len(eligible_ids(result.state, minute(3))) == 1


# --- Reset boundaries ------------------------------------------------------


@pytest.mark.parametrize("kind", ["session_change", "provider_change", "restart"])
def test_reset_clears_eligibility_and_records_the_boundary(kind) -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(0))).state
    reset = apply_reset(state, kind, minute(5), reason="test")
    assert reset.slots == {} and reset.unavailable == {}
    assert reset.reset_boundary == minute(5) and reset.reset_count == 1
    assert (reset.last_reset.kind, reset.last_reset.reason, reset.last_reset.epoch) == (kind, "test", 1)
    assert eligible_ids(reset, minute(5)) == []
    assert state.slots  # the previous state is untouched


@pytest.mark.parametrize("kind", ["session_change", "provider_change", "restart"])
def test_delayed_pre_reset_batch_is_rejected_and_cannot_repopulate(kind) -> None:
    state = apply_reset(reduce(initial_state("simulated"), make_batch(minute(0))).state, kind, minute(5))
    delayed = reduce(state, make_batch(minute(4)), minute(6))  # candle that began before the boundary
    assert delayed.status == "rejected" and delayed.reason == "pre_reset_boundary"
    assert delayed.state is state and state.slots == {}


def test_candle_straddling_the_boundary_is_rejected_until_a_fully_post_reset_candle() -> None:
    state = apply_reset(initial_state("simulated"), "provider_change", minute(5) + timedelta(seconds=30))
    straddling = reduce(state, make_batch(minute(5)), minute(7))  # interval began before the boundary
    assert straddling.status == "rejected"
    first_full = reduce(state, make_batch(minute(6)), minute(8))
    assert first_full.status == "applied"


def test_candle_starting_exactly_at_the_boundary_is_admitted() -> None:
    state = apply_reset(initial_state("simulated"), "restart", minute(5))
    assert reduce(state, make_batch(minute(5)), minute(7)).status == "applied"
    assert reduce(state, make_batch(minute(5) - timedelta(microseconds=1)), minute(7)).status == "rejected"


def test_unavailable_batches_respect_the_boundary_too() -> None:
    state = apply_reset(initial_state("simulated"), "session_change", minute(5))
    assert reduce(state, make_batch(minute(4), unavailable=BAD), minute(7)).status == "rejected"


def test_boundary_never_moves_earlier_and_epochs_increase() -> None:
    state = apply_reset(initial_state("simulated"), "restart", minute(5))
    again = apply_reset(state, "provider_change", minute(3))
    assert again.reset_boundary == minute(5)
    assert again.last_reset.requested_boundary == minute(3) and again.last_reset.effective_boundary == minute(5)
    assert again.reset_count == 2
    later = apply_reset(again, "session_change", minute(9))
    assert later.reset_boundary == minute(9) and later.reset_count == 3


def test_reset_clears_unavailable_floor_but_keeps_retirement() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(1), unavailable=BAD), minute(2)).state
    state = retire_strategy_versions(state, [("orb", "1")], "disabled", minute(2))
    reset = apply_reset(state, "session_change", minute(1))
    assert reset.unavailable == {}
    assert ("orb", "1") in reset.retired_versions
    assert reduce(reset, make_batch(minute(2)), minute(3)).dispositions[0].reason == "version_retired"


def test_reset_rejects_unknown_kind_and_naive_boundary() -> None:
    with pytest.raises(CandidateContractError):
        apply_reset(initial_state("simulated"), "reboot", minute(1))  # type: ignore[arg-type]
    with pytest.raises(CandidateContractError):
        apply_reset(initial_state("simulated"), "restart", datetime(2026, 10, 9, 14, 5))


# --- Mode isolation and version retirement ---------------------------------


def test_batch_for_another_mode_is_rejected() -> None:
    live_store = initial_state("simulated")
    result = reduce(live_store, make_batch(mode="backtest"))
    assert result.status == "rejected" and result.reason == "mode_mismatch"
    assert result.state is live_store


def test_retirement_removes_eligibility_and_refuses_later_batches() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(0), [opportunity_disposition("orb"), opportunity_disposition("gap")])).state
    retired = retire_strategy_versions(state, [("orb", "1")], "disabled_in_config", minute(1))
    assert [a.strategy for a in assess_candidates(retired, minute(1), POLICY).eligible] == ["gap"]
    assert retired.retired_versions[("orb", "1")].reason == "disabled_in_config"
    late = reduce(retired, make_batch(minute(1), [opportunity_disposition("orb"), opportunity_disposition("gap")]), minute(2))
    results = {d.strategy: d for d in late.dispositions}
    assert results["orb"].result == "rejected" and results["orb"].reason == "version_retired"
    assert results["gap"].result == "applied"
    assert ("AAPL", "orb", "1") not in late.state.slots
    assert state.slots[("AAPL", "orb", "1")]  # earlier state untouched


def test_retiring_one_version_leaves_a_sibling_version_active() -> None:
    state = reduce(initial_state("simulated"), make_batch(minute(0), [opportunity_disposition("orb", "1"), opportunity_disposition("orb", "2")])).state
    retired = retire_strategy_versions(state, [("orb", "1")], "superseded", minute(1))
    assert [a.strategy_version for a in assess_candidates(retired, minute(1), POLICY).eligible] == ["2"]


def test_retirement_is_idempotent_and_keeps_the_first_reason() -> None:
    state = retire_strategy_versions(initial_state("simulated"), [("orb", "1")], "first", minute(1))
    again = retire_strategy_versions(state, [("orb", "1")], "second", minute(2))
    assert again.retired_versions[("orb", "1")].reason == "first"


def test_retirement_input_validation() -> None:
    with pytest.raises(CandidateContractError):
        retire_strategy_versions(initial_state("simulated"), [("orb", "1")], "", minute(1))
    with pytest.raises(CandidateContractError):
        retire_strategy_versions(initial_state("simulated"), [("", "1")], "x", minute(1))


# --- Separate times, immutability, determinism -----------------------------


def test_source_completion_and_receive_times_stay_separate() -> None:
    result = reduce(initial_state("simulated"), make_batch(completed_delay=timedelta(seconds=3)), RX + timedelta(seconds=10))
    record = slot(result.state)
    assert record.source_candle_ts == T0
    assert record.source_interval_close == T0 + ONE_MIN
    assert record.completed_at == T0 + ONE_MIN + timedelta(seconds=3)
    assert record.received_at == RX + timedelta(seconds=10)
    assert len({record.source_interval_close, record.completed_at, record.received_at}) == 3


def test_received_at_must_be_timezone_aware_and_is_normalised() -> None:
    with pytest.raises(CandidateContractError):
        reduce(initial_state("simulated"), make_batch(), datetime(2026, 10, 9, 14, 1))
    dhaka = timezone(timedelta(hours=6))
    record = slot(reduce(initial_state("simulated"), make_batch(), RX.astimezone(dhaka)).state)
    assert record.received_at == RX and record.received_at.utcoffset() == timedelta(0)


def test_reducer_never_mutates_state_batch_or_caller_payloads() -> None:
    evidence = {"conditions": {"levels": [1, 2, 3]}}
    snapshot = copy.deepcopy(evidence)
    batch = make_batch(dispositions=[
        StrategyDisposition("orb", "1", "opportunity", make_opportunity(evidence=evidence))
    ])
    state = initial_state("simulated")
    result = reduce(state, batch)
    assert evidence == snapshot
    assert state.slots == {} and result.state is not state
    evidence["conditions"]["levels"].append(4)  # post-hoc caller mutation
    assert slot(result.state).opportunity.evidence_copy() == snapshot


def test_state_and_snapshots_are_read_only_and_detached() -> None:
    state = reduce(initial_state("simulated"), make_batch()).state
    with pytest.raises(TypeError):
        state.slots[("AAPL", "x", "1")] = slot(state)  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        state.reset_count = 9  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        slot(state).kind = "gated"  # type: ignore[misc]
    external = dict(state.slots)
    rebuilt = CandidateState(mode="simulated", slots=external, unavailable={}, retired_versions={})
    external.clear()
    assert len(rebuilt.slots) == 1  # the caller's dict is not retained
    snap = assess_candidates(state, minute(1), POLICY)
    with pytest.raises(FrozenInstanceError):
        snap.assessments[0].eligible = False  # type: ignore[misc]
    assert isinstance(snap.assessments, tuple)
    again = assess_candidates(state, minute(1), POLICY)
    assert snap == again and snap is not again


def test_reduction_is_deterministic_for_a_fixed_input_sequence() -> None:
    sequence = [
        (make_batch(minute(0), [opportunity_disposition("orb"), opportunity_disposition("gap")]), minute(1)),
        (make_batch(minute(1), [StrategyDisposition("gap", "1", "gated", reason="g")]), minute(2)),
        (make_batch(minute(2), unavailable=BAD), minute(3)),
        (make_batch(minute(3), [opportunity_disposition("orb", direction="short")]), minute(4)),
    ]

    def run():
        state = initial_state("simulated")
        outcomes = []
        for batch, rx in sequence:
            result = apply_batch(state, batch, rx)
            state = result.state
            outcomes.append((result.status, result.reason, result.dispositions, result.invalidated))
        return state, outcomes, assess_candidates(state, minute(4), POLICY)

    assert run() == run()
