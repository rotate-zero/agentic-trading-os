"""C1 pure eligibility: explicit freshness policy, source-age boundaries, reasons."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.trading_intelligence.candidate_contract import (
    CandidateContractError,
    EvaluationBatch,
    StrategyDisposition,
    UnavailablePrerequisite,
)
from app.trading_intelligence.candidate_eligibility import CandidateFreshnessPolicy, assess_candidates
from app.trading_intelligence.candidate_state import apply_batch, initial_state
from tests.test_opportunity_candidate_contract import (
    ONE_MIN,
    T0,
    make_batch,
    make_opportunity,
    opportunity_disposition,
)

CLOSE = T0 + ONE_MIN  # source interval close of the default 1m batch
MAX_AGE = 120.0
POLICY = CandidateFreshnessPolicy.create({"1m": MAX_AGE})
EPS = timedelta(microseconds=1)


def state_with(batch, received_at: datetime = CLOSE + timedelta(seconds=2)):
    return apply_batch(initial_state("simulated"), batch, received_at).state


def only(state, as_of, policy=POLICY):
    (assessment,) = assess_candidates(state, as_of, policy).assessments
    return assessment


# --- Missing / partial policy ----------------------------------------------


def test_missing_policy_is_unconfigured_never_eligible() -> None:
    state = state_with(make_batch())
    for policy in (None, CandidateFreshnessPolicy.create({}), CandidateFreshnessPolicy.create({"5m": 600.0})):
        snapshot = assess_candidates(state, CLOSE + timedelta(seconds=1), policy)
        assert snapshot.eligible == ()
        assert snapshot.assessments[0].reasons == ("freshness_policy_unconfigured",)
        assert snapshot.assessments[0].max_age_seconds is None
        assert snapshot.assessments[0].age_seconds == 1.0  # age is still reported


def test_policy_is_only_what_the_caller_supplies() -> None:
    assert CandidateFreshnessPolicy.create({}).max_age_for("1m") is None
    assert POLICY.max_age_for("1m") == MAX_AGE and POLICY.max_age_for("5m") is None
    assert candidate_defaults_absent()


def candidate_defaults_absent() -> bool:
    import app.trading_intelligence.candidate_eligibility as module

    # No numeric module-level constant could act as a hidden maximum age.
    return not any(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for name, value in vars(module).items()
        if not name.startswith("__")
    )


@pytest.mark.parametrize("bad", [0, -1, -0.0, float("nan"), float("inf"), True, "60", None])
def test_policy_rejects_non_positive_non_finite_or_non_numeric_ages(bad) -> None:
    with pytest.raises(CandidateContractError):
        CandidateFreshnessPolicy.create({"1m": bad})


def test_policy_rejects_bad_or_duplicate_timeframes_and_copies_input() -> None:
    with pytest.raises(CandidateContractError):
        CandidateFreshnessPolicy.create({"": 60.0})
    with pytest.raises(CandidateContractError):
        CandidateFreshnessPolicy((("1m", 60.0), ("1m", 90.0)))
    source = {"1m": 60.0}
    policy = CandidateFreshnessPolicy.create(source)
    source["1m"] = 1.0
    source["5m"] = 2.0
    assert policy.max_age_for("1m") == 60.0 and policy.max_age_for("5m") is None


# --- Source-age boundaries (measured from interval close) -------------------


def test_age_boundaries_around_the_maximum() -> None:
    state = state_with(make_batch())
    assert only(state, CLOSE + timedelta(seconds=MAX_AGE) - EPS).eligible
    at_max = only(state, CLOSE + timedelta(seconds=MAX_AGE))
    assert at_max.eligible and at_max.age_seconds == MAX_AGE  # inclusive maximum
    over = only(state, CLOSE + timedelta(seconds=MAX_AGE) + EPS)
    assert not over.eligible and over.reasons == ("expired",)


def test_age_zero_at_the_interval_close_is_fresh_and_before_it_is_unavailable() -> None:
    state = state_with(make_batch())
    at_close = only(state, CLOSE)
    assert at_close.eligible and at_close.age_seconds == 0.0
    early = only(state, CLOSE - EPS)  # negative age: clock anomaly or still-open interval
    assert not early.eligible
    assert early.reasons == ("source_age_unavailable",) and early.age_seconds is None


def test_age_derives_from_interval_close_not_from_delivery_time() -> None:
    on_time = state_with(make_batch(), CLOSE + timedelta(seconds=1))
    very_late = state_with(make_batch(), CLOSE + timedelta(hours=3))
    as_of = CLOSE + timedelta(seconds=30)
    assert only(on_time, as_of).age_seconds == only(very_late, as_of).age_seconds == 30.0
    assert only(very_late, as_of).eligible  # a late receive does not make a candle look stale or new
    assert only(very_late, as_of).received_at == CLOSE + timedelta(hours=3)


def test_session_trailing_stub_interval_uses_its_shorter_explicit_close() -> None:
    # A 1h bucket cut short by the session end (30 minutes): +nominal width would overstate its close.
    stub = EvaluationBatch(
        symbol="AAPL",
        trigger_timeframe="1h",
        mode="simulated",
        source_candle_ts=T0,
        source_interval_start=T0,
        source_interval_close=T0 + timedelta(minutes=30),
        completed_at=T0 + timedelta(minutes=30, seconds=1),
        dispositions=(opportunity_disposition(),),
    )
    state = state_with(stub, T0 + timedelta(minutes=30, seconds=2))
    policy = CandidateFreshnessPolicy.create({"1h": 60.0})
    assert only(state, T0 + timedelta(minutes=31), policy).eligible  # 60s after the real close
    assert only(state, T0 + timedelta(minutes=31, microseconds=1), policy).reasons == ("expired",)


def test_age_uses_the_explicit_close_even_when_the_candle_stamp_is_inside_the_interval() -> None:
    # The contract only requires start <= candle < close; age comes from the explicit close.
    five = EvaluationBatch(
        symbol="AAPL",
        trigger_timeframe="5m",
        mode="simulated",
        source_candle_ts=T0 + timedelta(minutes=4),
        source_interval_start=T0,
        source_interval_close=T0 + timedelta(minutes=5),
        completed_at=T0 + timedelta(minutes=5, seconds=1),
        dispositions=(opportunity_disposition(),),
    )
    state = state_with(five, T0 + timedelta(minutes=5, seconds=2))
    policy = CandidateFreshnessPolicy.create({"5m": 60.0})
    assert only(state, T0 + timedelta(minutes=5, seconds=60), policy).eligible
    assert only(state, T0 + timedelta(minutes=5, seconds=61), policy).reasons == ("expired",)


def test_timeframes_use_their_own_maximum() -> None:
    state = state_with(make_batch())
    policy = CandidateFreshnessPolicy.create({"1m": 30.0, "5m": 9000.0})
    assert only(state, CLOSE + timedelta(seconds=31), policy).reasons == ("expired",)


def test_expected_horizon_is_descriptive_and_never_an_expiry() -> None:
    state = state_with(make_batch(dispositions=[opportunity_disposition(expected_horizon_minutes=1)]))
    assessment = only(state, CLOSE + timedelta(seconds=100))
    assert assessment.eligible
    assert assessment.opportunity.expected_horizon_minutes == 1


# --- Reasons and ordering ---------------------------------------------------


def test_non_actionable_opportunity_is_not_eligible() -> None:
    waiting = StrategyDisposition("orb", "1", "opportunity", make_opportunity(status="waiting"))
    state = state_with(make_batch(dispositions=[waiting]))
    assert only(state, CLOSE).reasons == ("not_actionable",)
    assert only(state, CLOSE, None).reasons == ("not_actionable", "freshness_policy_unconfigured")


def test_reason_order_is_fixed_when_several_apply() -> None:
    base = state_with(make_batch(T0, [opportunity_disposition("orb")]))
    invalidated = apply_batch(
        base, make_batch(T0 + ONE_MIN, unavailable=[UnavailablePrerequisite("features", "missing")]), CLOSE + ONE_MIN
    ).state
    assessment = only(invalidated, CLOSE + timedelta(seconds=MAX_AGE, microseconds=1))
    assert assessment.reasons == ("invalidated:prerequisite_unavailable", "expired")
    assert not assessment.eligible


@pytest.mark.parametrize(
    "disposition",
    [
        StrategyDisposition("orb", "1", "no_opportunity"),
        StrategyDisposition("orb", "1", "gated", reason="volume_gate"),
        StrategyDisposition("orb", "1", "error", reason="ValueError"),
    ],
    ids=["no_opportunity", "gated", "error"],
)
def test_terminal_non_opportunities_are_never_eligible_and_carry_no_candidate(disposition) -> None:
    assessment = only(state_with(make_batch(dispositions=[disposition])), CLOSE, None)
    assert not assessment.eligible
    assert assessment.reasons == (disposition.kind,)
    assert assessment.candidate_id is None and assessment.direction is None and assessment.opportunity is None
    assert assessment.disposition_reason == disposition.reason


def test_long_and_short_candidates_are_distinct_entries() -> None:
    state = state_with(make_batch(dispositions=[opportunity_disposition("orb", direction="long"), opportunity_disposition("gap", direction="short")]))
    snapshot = assess_candidates(state, CLOSE, POLICY)
    by_strategy = {a.strategy: a for a in snapshot.eligible}
    assert by_strategy["orb"].direction == "long" and by_strategy["gap"].direction == "short"
    assert by_strategy["orb"].candidate_id != by_strategy["gap"].candidate_id
    assert by_strategy["orb"].evaluation_id != by_strategy["gap"].evaluation_id


def test_assessments_are_deterministically_ordered_regardless_of_arrival_order() -> None:
    def build(order):
        state = initial_state("simulated")
        for symbol in order:
            state = apply_batch(state, make_batch(symbol=symbol, dispositions=[opportunity_disposition("orb"), opportunity_disposition("gap")]), CLOSE).state
        return assess_candidates(state, CLOSE, POLICY)

    forward, backward = build(["AAPL", "MSFT", "TSLA"]), build(["TSLA", "MSFT", "AAPL"])
    assert [(a.symbol, a.strategy) for a in forward.assessments] == [
        (s, st) for s in ("AAPL", "MSFT", "TSLA") for st in ("gap", "orb")
    ]
    assert forward.assessments == backward.assessments


def test_snapshot_carries_boundary_mode_and_normalised_as_of() -> None:
    from app.trading_intelligence.candidate_state import apply_reset

    state = apply_reset(initial_state("simulated"), "restart", T0, reason="boot")
    dhaka = timezone(timedelta(hours=6))
    snapshot = assess_candidates(state, CLOSE.astimezone(dhaka), POLICY)
    assert snapshot.mode == "simulated" and snapshot.reset_boundary == T0 and snapshot.reset_count == 1
    assert snapshot.as_of == CLOSE and snapshot.as_of.utcoffset() == timedelta(0)
    assert snapshot.assessments == () and snapshot.eligible == ()


def test_as_of_is_required_aware_and_explicit() -> None:
    with pytest.raises(CandidateContractError):
        assess_candidates(initial_state("simulated"), datetime(2026, 10, 9, 14, 1), POLICY)
    with pytest.raises(TypeError):
        assess_candidates(initial_state("simulated"))  # type: ignore[call-arg]  # no hidden wall clock


def test_ages_are_finite_numbers() -> None:
    assessment = only(state_with(make_batch()), CLOSE + timedelta(seconds=7.5))
    assert math.isfinite(assessment.age_seconds) and assessment.age_seconds == 7.5
