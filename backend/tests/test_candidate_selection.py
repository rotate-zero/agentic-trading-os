"""D1 `unique_candidate_v1` over REAL C1 snapshots: unique-or-abstain, never a tie-break."""

from __future__ import annotations

import itertools
import json
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from app.trading_intelligence.candidate_ranking import RankingPolicyRef
from app.trading_intelligence.candidate_selection import (
    MAX_ATTEMPTS_PER_CANDIDATE,
    PortfolioExposureRef,
    PortfolioInput,
    SlotPolicy,
    normalize_attempted,
    record_attempt,
    select,
)
from app.trading_intelligence.decision_evidence import DecisionContractError
from tests.decision_test_support import (
    AS_OF,
    AUDIT_ID,
    CUTOFF,
    RECEIVED,
    UNIQUE,
    evidence_record,
    make_batch,
    opportunity_disposition,
    portfolio_snapshot,
    ranking_from,
    run_select,
    snapshot_from,
    synced_portfolio,
)


def ranking_of(*batches, **kw):
    return ranking_from(snapshot_from(list(batches), **kw))


def batch_for(symbol, *specs):
    """specs: (strategy, direction[, confidence])"""
    return make_batch(
        symbol=symbol,
        dispositions=[opportunity_disposition(s[0], direction=s[1], **({"confidence": s[2]} if len(s) > 2 else {})) for s in specs],
    )


# --- Zero / one / many -------------------------------------------------------


def test_zero_candidates_abstains() -> None:
    result = run_select(ranking_of())
    assert result.status == "abstained" and result.selected_candidate_id is None
    assert result.abstention_reasons == ("no_eligible_candidates",)
    assert result.considered_candidate_ids == ()


def test_exactly_one_candidate_is_selected() -> None:
    ranking = ranking_of(make_batch())
    result = run_select(ranking)
    assert result.status == "selected" and result.abstention_reasons == ()
    assert result.selected_candidate_id == ranking.candidate_ids[0]
    assert result.selected_candidate == ranking.candidates[0]
    assert result.considered_candidate_ids == ranking.candidate_ids
    assert result.slots_available == 1 and result.exclusions == ()


def test_selection_is_shadow_and_authorizes_nothing() -> None:
    result = run_select(ranking_of(make_batch()))
    assert result.shadow is True and result.authorizes_trade is False
    audit = result.to_audit_record()
    assert audit["shadow"] is True and audit["authorizes_trade"] is False
    assert not hasattr(result, "trade_id") and not hasattr(result, "order")


def test_one_candidate_with_a_ranking_status_other_than_unranked_still_selects_by_policy_only() -> None:
    ranking = ranking_from(snapshot_from([make_batch()]), ranking_policy=RankingPolicyRef("future", "1"))
    result = run_select(ranking)
    assert ranking.status == "unavailable" and result.status == "selected"
    assert result.ranking_status == "unavailable" and result.ranking_policy_id == "future"


# --- Competition: always abstain -------------------------------------------------


def test_same_direction_strategies_on_one_symbol_are_competitors() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "long")))
    result = run_select(ranking)
    assert result.status == "abstained" and result.selected_candidate_id is None
    assert result.abstention_reasons == ("multiple_surviving_candidates", "conflict:same_symbol_same_direction")
    (group,) = result.conflict_groups
    assert (group.kind, group.symbol, group.directions) == ("same_symbol_same_direction", "AAPL", ("long",))
    assert set(group.candidate_ids) == set(ranking.candidate_ids)


def test_opposite_directions_on_one_symbol_abstain() -> None:
    result = run_select(ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "short"))))
    assert result.status == "abstained"
    assert result.abstention_reasons == (
        "multiple_surviving_candidates", "opposite_directions", "conflict:same_symbol_opposite_direction",
    )
    assert result.conflict_groups[0].directions == ("long", "short")


def test_multiple_symbols_abstain_even_when_all_agree() -> None:
    result = run_select(ranking_of(batch_for("AAPL", ("orb", "long")), batch_for("MSFT", ("orb", "long"))), slots=5)
    assert result.status == "abstained"
    assert result.abstention_reasons == ("multiple_surviving_candidates", "conflict:multiple_symbols")
    (group,) = result.conflict_groups
    assert group.kind == "multiple_symbols" and group.symbol is None and len(group.candidate_ids) == 2


def test_opposite_directions_across_symbols_abstain() -> None:
    result = run_select(ranking_of(batch_for("AAPL", ("orb", "long")), batch_for("MSFT", ("orb", "short"))), slots=5)
    assert result.status == "abstained" and "opposite_directions" in result.abstention_reasons


def test_mixed_conflicts_report_every_group() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "short")), batch_for("MSFT", ("orb", "long")))
    result = run_select(ranking, slots=5)
    assert {g.kind for g in result.conflict_groups} == {"same_symbol_opposite_direction", "multiple_symbols"}
    assert len(result.considered_candidate_ids) == 3


# --- No hidden tie-break -------------------------------------------------------------


@pytest.mark.parametrize("confidences", [(0.1, 0.9), (0.9, 0.1), (0.5, 0.5), (0.0, 1.0)])
def test_confidence_never_breaks_a_tie(confidences) -> None:
    low, high = confidences
    result = run_select(ranking_of(batch_for("AAPL", ("orb", "long", low), ("gap", "long", high))))
    assert result.status == "abstained" and result.selected_candidate_id is None


def test_arrival_order_never_breaks_a_tie() -> None:
    for first, second in itertools.permutations(["AAA", "BBB"]):
        snapshot = snapshot_from([
            (batch_for(first, ("orb", "long")), RECEIVED),
            (batch_for(second, ("orb", "long")), RECEIVED + timedelta(seconds=5)),
        ])
        result = run_select(ranking_from(snapshot), slots=5)
        assert result.status == "abstained" and result.selected_candidate_id is None


def test_candidate_id_or_lexical_order_never_breaks_a_tie() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "long"), ("momentum", "long")))
    ids = ranking.candidate_ids
    assert ids == tuple(sorted(ids))  # the listing IS id-sorted ...
    result = run_select(ranking)
    assert result.selected_candidate_id is None  # ... and still no winner
    assert result.considered_candidate_ids == ids


def test_evidence_cannot_pick_a_winner() -> None:
    snapshot = snapshot_from([batch_for("AAPL", ("orb", "long"), ("gap", "long"))])
    strong = evidence_record("strong", strategy="orb", metrics={"win_rate": 0.99}, sample_count=10_000)
    result = run_select(ranking_from(snapshot, strong))
    assert result.status == "abstained" and result.selected_candidate_id is None


def test_evidence_does_not_change_a_unique_selection() -> None:
    snapshot = snapshot_from([make_batch()])
    assert run_select(ranking_from(snapshot)).selected_candidate_id == run_select(
        ranking_from(snapshot, evidence_record("a"), evidence_record("b", available_at=AS_OF + timedelta(days=1)))
    ).selected_candidate_id


# --- Existing exposure -------------------------------------------------------------


def test_open_position_removes_that_symbol_and_leaves_a_unique_winner() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long")), batch_for("MSFT", ("orb", "long")))
    result = run_select(ranking, synced_portfolio(("AAPL", False)), slots=2)
    assert result.status == "selected" and result.selected_candidate.symbol == "MSFT"
    (excluded,) = result.exclusions
    assert (excluded.symbol, excluded.stage, excluded.reasons) == ("AAPL", "exposure", ("symbol_busy:open_position",))
    assert excluded.candidate_id in ranking.candidate_ids


def test_in_flight_entry_removes_that_symbol() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long")), batch_for("MSFT", ("orb", "long")))
    result = run_select(ranking, synced_portfolio(("MSFT", True)), slots=2)
    assert result.status == "selected" and result.selected_candidate.symbol == "AAPL"
    assert result.exclusions[0].reasons == ("symbol_busy:in_flight_order",)


def test_symbol_with_both_position_and_in_flight_reports_both_reasons() -> None:
    result = run_select(ranking_of(make_batch()), synced_portfolio(("AAPL", False), ("AAPL", True)), slots=5)
    assert result.status == "abstained"
    assert result.exclusions[0].reasons == ("symbol_busy:open_position", "symbol_busy:in_flight_order")
    assert result.abstention_reasons == ("all_candidates_excluded",)


def test_every_candidate_blocked_by_exposure_abstains() -> None:
    result = run_select(ranking_of(make_batch()), synced_portfolio(("AAPL", False)), slots=3)
    assert result.status == "abstained" and result.abstention_reasons == ("all_candidates_excluded",)
    assert result.considered_candidate_ids  # still recorded as considered


def test_exposure_can_remove_an_opposite_direction_conflict_but_never_forces_ambiguity_away() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "short")), batch_for("MSFT", ("orb", "long")))
    # AAPL busy -> only MSFT survives -> unique
    assert run_select(ranking, synced_portfolio(("AAPL", False)), slots=3).selected_candidate.symbol == "MSFT"
    # nothing busy -> three candidates -> abstain
    assert run_select(ranking, slots=3).status == "abstained"


# --- Slots ----------------------------------------------------------------------------


def test_exhausted_slots_abstain_and_record_exclusions() -> None:
    ranking = ranking_of(make_batch(symbol="AAPL"))
    result = run_select(ranking, synced_portfolio(("MSFT", False)), slots=1)
    assert result.status == "abstained" and result.slots_available == 0
    assert result.abstention_reasons == ("no_available_slots", "all_candidates_excluded")
    assert result.exclusions[0].stage == "slots" and result.exclusions[0].reasons == ("no_available_slot",)


def test_slot_headroom_allows_selection() -> None:
    result = run_select(ranking_of(make_batch(symbol="AAPL")), synced_portfolio(("MSFT", False)), slots=2)
    assert result.status == "selected" and result.slots_available == 1


def test_in_flight_entries_consume_slots_like_open_positions() -> None:
    assert run_select(ranking_of(make_batch()), synced_portfolio(("MSFT", True)), slots=1).abstention_reasons[0] == "no_available_slots"


def test_over_subscribed_slots_stay_exhausted() -> None:
    result = run_select(ranking_of(make_batch()), synced_portfolio(("X", False), ("Y", False)), slots=1)
    assert result.slots_available == -1 and result.status == "abstained"


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, None, "1"])
def test_slot_policy_must_be_a_positive_integer(bad) -> None:
    with pytest.raises(DecisionContractError):
        SlotPolicy(bad)  # type: ignore[arg-type]


def test_missing_slot_policy_abstains_without_inventing_a_default() -> None:
    result = run_select(ranking_of(make_batch()), slots=None)
    assert result.status == "abstained" and result.abstention_reasons == ("slot_policy_unconfigured",)
    assert result.slot_policy is None and result.slots_available is None


# --- Portfolio availability / readiness ------------------------------------------------


def test_missing_portfolio_state_abstains() -> None:
    result = select(ranking_of(make_batch()), None, (), selection_policy=UNIQUE, slot_policy=SlotPolicy(1), audit_id=AUDIT_ID)
    assert result.status == "abstained" and result.abstention_reasons == ("portfolio_state_missing",)
    assert result.portfolio_captured_at is None and result.portfolio is None
    assert result.considered_candidate_ids  # the captured set is still recorded


def test_unavailable_portfolio_state_abstains() -> None:
    portfolio = PortfolioInput.unavailable("simulated", AS_OF, "ledger_load_failed")
    result = run_select(ranking_of(make_batch()), portfolio)
    assert result.abstention_reasons == ("portfolio_state_unavailable",)
    assert result.to_audit_record()["portfolio"]["unavailable_reason"] == "ledger_load_failed"


def test_fresh_as_of_does_not_prove_ledger_synchronization() -> None:
    portfolio = PortfolioInput("simulated", AS_OF + timedelta(seconds=30), True, False, ())  # brand-new stamp, not synchronized
    result = run_select(ranking_of(make_batch()), portfolio)
    assert result.status == "abstained" and result.abstention_reasons == ("portfolio_ledger_not_synchronized",)
    assert result.portfolio_captured_at == AS_OF + timedelta(seconds=30)


def test_portfolio_for_another_execution_mode_is_a_mismatch() -> None:
    result = run_select(ranking_of(make_batch()), synced_portfolio(mode="paper"))
    assert result.status == "abstained" and result.abstention_reasons == ("portfolio_mode_mismatch",)


def test_every_global_problem_is_reported_together() -> None:
    portfolio = PortfolioInput("paper", AS_OF, True, False, ())
    result = run_select(ranking_of(make_batch()), portfolio, slots=None)
    assert result.abstention_reasons == (
        "portfolio_ledger_not_synchronized", "portfolio_mode_mismatch", "slot_policy_unconfigured",
    )


def test_unavailable_portfolio_cannot_carry_exposures_or_readiness() -> None:
    with pytest.raises(DecisionContractError):
        PortfolioInput("simulated", AS_OF, False, False, (PortfolioExposureRef("AAPL", False),))
    with pytest.raises(DecisionContractError):
        PortfolioInput("simulated", AS_OF, False, True, ())
    with pytest.raises(DecisionContractError):
        PortfolioInput("simulated", AS_OF.replace(tzinfo=None), True, True, ())
    with pytest.raises(DecisionContractError):
        PortfolioExposureRef("AAPL", "yes")  # type: ignore[arg-type]


def test_no_portfolio_freshness_rule_is_invented() -> None:
    """A very old capture is accepted as long as readiness is stated; age is not a rule here."""
    old = PortfolioInput("simulated", AS_OF - timedelta(days=30), True, True, ())
    assert run_select(ranking_of(make_batch()), old).status == "selected"


def test_portfolio_input_from_a_real_portfolio_state_snapshot() -> None:
    snapshot = portfolio_snapshot(positions=("AAPL",), orders=("MSFT",))
    portfolio = PortfolioInput.from_snapshot(snapshot, ledger_synchronized=True)
    assert portfolio.execution_mode == "simulated" and portfolio.captured_at == snapshot.as_of
    assert {(e.symbol, e.is_in_flight) for e in portfolio.exposures} == {("AAPL", False), ("MSFT", True)}
    assert len(portfolio.exposures) == len(snapshot.exposures)
    ranking = ranking_of(batch_for("AAPL", ("orb", "long")), batch_for("MSFT", ("orb", "long")), batch_for("NVDA", ("orb", "long")))
    result = run_select(ranking, portfolio, slots=3)
    assert result.status == "selected" and result.selected_candidate.symbol == "NVDA"
    assert result.slots_available == 1


def test_from_snapshot_requires_explicit_readiness_and_marks_unsynchronized() -> None:
    snapshot = portfolio_snapshot()
    with pytest.raises(TypeError):
        PortfolioInput.from_snapshot(snapshot)  # type: ignore[call-arg]
    stale = PortfolioInput.from_snapshot(snapshot, ledger_synchronized=False)
    assert run_select(ranking_of(make_batch()), stale).abstention_reasons == ("portfolio_ledger_not_synchronized",)


def test_flat_real_snapshot_is_known_flat_not_missing() -> None:
    portfolio = PortfolioInput.from_snapshot(portfolio_snapshot(), ledger_synchronized=True)
    assert portfolio.exposures == () and run_select(ranking_of(make_batch()), portfolio).status == "selected"


# --- C1 eligibility is honoured, never recomputed -----------------------------------------


def test_unconfigured_freshness_leaves_nothing_selectable() -> None:
    result = run_select(ranking_of(make_batch(), policy=None))
    assert result.status == "abstained" and result.abstention_reasons == ("no_eligible_candidates",)
    (excluded,) = result.exclusions
    assert excluded.stage == "eligibility" and excluded.reasons == ("freshness_policy_unconfigured",)


def test_expired_gated_error_and_no_opportunity_are_never_selectable() -> None:
    from app.trading_intelligence.candidate_contract import StrategyDisposition

    dispositions = [
        StrategyDisposition("gate", "1", "gated", reason="x"),
        StrategyDisposition("boom", "1", "error", reason="y"),
        StrategyDisposition("quiet", "1", "no_opportunity"),
    ]
    result = run_select(ranking_of(make_batch(dispositions=dispositions)))
    assert result.status == "abstained" and len(result.exclusions) == 3


def test_a_later_arrival_is_not_pulled_into_the_set() -> None:
    snapshot = snapshot_from([(make_batch(symbol="AAA"), RECEIVED),
                              (make_batch(symbol="BBB"), CUTOFF.cutoff_at + timedelta(seconds=1))])
    result = run_select(ranking_from(snapshot), slots=5)
    assert result.status == "selected" and result.selected_candidate.symbol == "AAA"
    assert [e.symbol for e in result.exclusions] == ["BBB"] and result.exclusions[0].stage == "capture_cutoff"


# --- Attempts: bounded feedback ----------------------------------------------------------------


def test_a_lone_attempted_candidate_is_not_proposed_again() -> None:
    ranking = ranking_of(make_batch())
    first = run_select(ranking)
    again = run_select(ranking, attempted=(first.selected_candidate_id,))
    assert first.status == "selected" and again.status == "abstained"
    assert again.abstention_reasons == ("candidate_already_attempted",)
    assert again.attempted_candidate_ids == (first.selected_candidate_id,)
    assert again.exclusions[0].stage == "attempted"


def test_an_attempt_cannot_force_a_winner_from_an_ambiguous_set() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "long")))
    one, other = ranking.candidate_ids
    for attempted in ((one,), (other,), (one, other)):
        result = run_select(ranking, attempted=attempted)
        assert result.status == "abstained" and result.selected_candidate_id is None
        assert "attempted_candidates_do_not_resolve_ambiguity" in result.abstention_reasons


def test_attempts_on_candidates_outside_the_captured_set_are_recorded_and_ignored() -> None:
    ranking = ranking_of(make_batch())
    result = run_select(ranking, attempted=("cnd1:" + "0" * 64,))
    assert result.status == "selected"
    assert result.attempted_not_in_captured_set == ("cnd1:" + "0" * 64,)


def test_attempted_ids_are_normalised_and_validated() -> None:
    assert normalize_attempted(["b", "a", "a"]) == ("a", "b")
    for bad in ("abc", b"abc", [""], [" x "], [1], [None]):
        with pytest.raises(DecisionContractError):
            normalize_attempted(bad)  # type: ignore[arg-type]


def test_record_attempt_allows_exactly_one_attempt_per_candidate() -> None:
    assert MAX_ATTEMPTS_PER_CANDIDATE == 1
    first = record_attempt((), "cnd1:aaa")
    assert first == ("cnd1:aaa",)
    assert record_attempt(first, "cnd1:bbb") == ("cnd1:aaa", "cnd1:bbb")
    with pytest.raises(DecisionContractError):
        record_attempt(first, "cnd1:aaa")
    with pytest.raises(DecisionContractError):
        record_attempt((), "")


def test_new_arrivals_need_a_new_capture_not_a_retry() -> None:
    """The retry contract runs on the SAME RankingResult; a new arrival is a new cycle."""
    captured = ranking_of(make_batch(symbol="AAA"))
    first = run_select(captured)
    attempted = record_attempt((), first.selected_candidate_id)
    later = snapshot_from([(make_batch(symbol="AAA"), RECEIVED), (make_batch(symbol="BBB"), RECEIVED + timedelta(seconds=2))])
    same_capture = run_select(captured, attempted=attempted)
    assert same_capture.status == "abstained" and same_capture.considered_candidate_ids == captured.candidate_ids
    next_cycle = run_select(ranking_from(later), attempted=attempted, slots=5)
    assert next_cycle.status == "abstained"  # two survivors now: still no forced winner


# --- Determinism and detachment ------------------------------------------------------------------


def test_same_captured_inputs_give_identical_results_in_any_input_order() -> None:
    batches = [batch_for("AAPL", ("orb", "long"), ("gap", "short")), batch_for("MSFT", ("orb", "long")), batch_for("TSLA", ("orb", "short"))]
    results = []
    for ordering in itertools.permutations(batches):
        result = run_select(ranking_of(*ordering), synced_portfolio(("TSLA", True)), slots=4)
        results.append(result)
    assert all(r == results[0] for r in results)
    assert all(r.to_audit_record() == results[0].to_audit_record() for r in results)


def test_evaluation_is_repeatable_and_pure() -> None:
    ranking = ranking_of(make_batch())
    portfolio = synced_portfolio()
    a = select(ranking, portfolio, (), selection_policy=UNIQUE, slot_policy=SlotPolicy(1), audit_id=AUDIT_ID)
    b = select(ranking, portfolio, (), selection_policy=UNIQUE, slot_policy=SlotPolicy(1), audit_id=AUDIT_ID)
    assert a == b and a is not b


def test_selection_policy_must_be_chosen_explicitly_and_exist() -> None:
    ranking = ranking_of(make_batch())
    with pytest.raises(TypeError):
        select(ranking, synced_portfolio(), (), slot_policy=SlotPolicy(1), audit_id=AUDIT_ID)  # type: ignore[call-arg]
    for bad in ("", "unique_candidate_v2", "ranked_top_1", None):
        with pytest.raises(DecisionContractError):
            select(ranking, synced_portfolio(), (), selection_policy=bad, slot_policy=SlotPolicy(1), audit_id=AUDIT_ID)  # type: ignore[arg-type]


def test_audit_identity_is_explicit_and_recorded() -> None:
    ranking = ranking_of(make_batch())
    assert run_select(ranking, audit_id="sel-attempt-7").audit_id == "sel-attempt-7"
    for bad in ("", " x ", None, 7):
        with pytest.raises(DecisionContractError):
            run_select(ranking, audit_id=bad)  # type: ignore[arg-type]


def test_wrong_input_types_are_refused() -> None:
    ranking = ranking_of(make_batch())
    with pytest.raises(DecisionContractError):
        select(object(), synced_portfolio(), (), selection_policy=UNIQUE, slot_policy=SlotPolicy(1), audit_id=AUDIT_ID)  # type: ignore[arg-type]
    with pytest.raises(DecisionContractError):
        select(ranking, object(), (), selection_policy=UNIQUE, slot_policy=SlotPolicy(1), audit_id=AUDIT_ID)  # type: ignore[arg-type]
    with pytest.raises(DecisionContractError):
        select(ranking, synced_portfolio(), (), selection_policy=UNIQUE, slot_policy=1, audit_id=AUDIT_ID)  # type: ignore[arg-type]


def test_result_retains_policy_versions_times_cutoff_and_audit_identity() -> None:
    result = run_select(ranking_of(make_batch()), audit_id="sel-9")
    assert result.selection_policy == UNIQUE and result.schema_version == 1
    assert result.ranking_status == "unranked" and result.ranking_policy_id is None
    assert result.ranking_adapter_version == "unranked_adapter_v1"
    assert result.eligibility_as_of == AS_OF and result.evidence_as_of == AS_OF
    assert result.cutoff == CUTOFF and result.portfolio_captured_at == AS_OF
    assert result.execution_mode == "simulated" and result.reset_boundary is None
    assert result.slot_policy == SlotPolicy(1)


def test_result_is_immutable_json_safe_and_detached() -> None:
    result = run_select(ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "short")), batch_for("MSFT", ("orb", "long"))), slots=4)
    with pytest.raises(FrozenInstanceError):
        result.status = "selected"  # type: ignore[misc]
    audit = result.to_audit_record()
    assert json.loads(json.dumps(audit, allow_nan=False, sort_keys=True)) == audit
    audit["considered_candidate_ids"].clear()
    audit["conflict_groups"].clear()
    audit["portfolio"]["exposures"].append("x")
    fresh = result.to_audit_record()
    assert len(fresh["considered_candidate_ids"]) == 3 and fresh["conflict_groups"] and fresh["portfolio"]["exposures"] == []
    assert fresh["cutoff"] == {"arrival_sequence": 41, "cutoff_at": "2026-10-09T14:01:10.000000Z"}
    assert fresh["audit_id"] == AUDIT_ID and fresh["selection_policy"] == UNIQUE


def test_select_does_not_mutate_the_ranking_or_portfolio() -> None:
    ranking = ranking_of(batch_for("AAPL", ("orb", "long"), ("gap", "long")))
    portfolio = synced_portfolio(("MSFT", False))
    before = (ranking.to_audit_record(), portfolio.to_audit_record())
    run_select(ranking, portfolio, attempted=(ranking.candidate_ids[0],), slots=3)
    assert (ranking.to_audit_record(), portfolio.to_audit_record()) == before
