"""D1 consumes the landed C2 reader's real snapshots (observation-only, no wiring)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.services import broker_registry
from app.trading_intelligence.candidate_ranking import SnapshotCutoff, rank_candidates
from app.trading_intelligence.decision_evidence import EvidenceSnapshot
from tests.decision_test_support import portfolio_snapshot, run_select
from app.trading_intelligence.candidate_selection import PortfolioInput
from tests.test_candidate_observation import deliver, make_reader, minute
from tests.test_opportunity_candidate_contract import make_batch, opportunity_disposition


@pytest.fixture(autouse=True)
def _clean_listeners():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


def capture(reader, clock):
    """What a later coordinator would do: freeze one reader snapshot and its sequence."""
    snapshot = reader.snapshot(clock.now)
    cutoff = SnapshotCutoff(snapshot.arrival_sequence, snapshot.as_of)
    return snapshot, rank_candidates(snapshot.eligibility, EvidenceSnapshot(snapshot.as_of), cutoff=cutoff)


async def test_one_reader_candidate_is_selected_in_shadow() -> None:
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    snapshot, ranking = capture(reader, clock)
    assert snapshot.arrival_sequence == 1 and ranking.cutoff.arrival_sequence == 1
    result = run_select(ranking, PortfolioInput.from_snapshot(portfolio_snapshot(), ledger_synchronized=True), slots=1)
    assert result.status == "selected" and result.shadow and not result.authorizes_trade
    assert result.selected_candidate_id == snapshot.eligibility.eligible[0].candidate_id


async def test_reader_batch_with_competing_strategies_abstains() -> None:
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition("orb"), opportunity_disposition("gap", direction="short")]))
    _, ranking = capture(reader, clock)
    result = run_select(ranking, slots=3)
    assert result.status == "abstained" and "opposite_directions" in result.abstention_reasons


async def test_unconfigured_reader_policy_leaves_nothing_selectable() -> None:
    reader, clock, _ = make_reader(policy=None)
    await deliver(reader, clock, make_batch(minute(0)))
    snapshot, ranking = capture(reader, clock)
    assert snapshot.freshness_status == "freshness_policy_unconfigured"
    assert ranking.candidates == () and run_select(ranking).abstention_reasons == ("no_eligible_candidates",)


async def test_batches_delivered_after_the_frozen_snapshot_are_not_in_the_captured_set() -> None:
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0), symbol="AAA"))
    _, frozen = capture(reader, clock)
    await deliver(reader, clock, make_batch(minute(0), symbol="BBB"), at=clock.now + timedelta(seconds=1))
    assert [c.symbol for c in frozen.candidates] == ["AAA"]
    assert run_select(frozen, slots=5).selected_candidate.symbol == "AAA"  # the frozen set is unchanged
    _, later = capture(reader, clock)
    assert {c.symbol for c in later.candidates} == {"AAA", "BBB"} and later.cutoff.arrival_sequence == 2
    assert run_select(later, slots=5).status == "abstained"


async def test_a_newer_no_result_removes_the_candidate_from_the_next_capture() -> None:
    from app.trading_intelligence.candidate_contract import StrategyDisposition

    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    await deliver(reader, clock, make_batch(minute(1), [StrategyDisposition("orb", "1", "no_opportunity")]))
    _, ranking = capture(reader, clock)
    assert ranking.candidates == () and run_select(ranking).status == "abstained"
