"""Shared builders for the D1 ranking/selection tests.

Every snapshot here is produced by the REAL landed C1 pipeline
(``apply_batch`` -> ``assess_candidates``); nothing fabricates an
``EligibilitySnapshot`` by hand. Not collected by pytest (no ``test_`` prefix).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.portfolio_state.accounting import PositionState
from app.portfolio_state.ports import InFlightOrder, LedgerState
from app.portfolio_state.snapshot import build_snapshot
from app.trading_intelligence.candidate_eligibility import CandidateFreshnessPolicy, EligibilitySnapshot, assess_candidates
from app.trading_intelligence.candidate_ranking import RankingResult, SnapshotCutoff, rank_candidates
from app.trading_intelligence.candidate_selection import PortfolioInput, SlotPolicy, select
from app.trading_intelligence.candidate_state import apply_batch, initial_state
from app.trading_intelligence.decision_evidence import EvidenceRecord, EvidenceSnapshot
from tests.test_opportunity_candidate_contract import (
    ONE_MIN,
    T0,
    make_batch,
    make_opportunity,
    opportunity_disposition,
)

CLOSE = T0 + ONE_MIN
RECEIVED = CLOSE + timedelta(seconds=2)
AS_OF = CLOSE + timedelta(seconds=10)
POLICY = CandidateFreshnessPolicy.create({"1m": 120.0})
CUTOFF = SnapshotCutoff(arrival_sequence=41, cutoff_at=AS_OF)
AUDIT_ID = "audit-0001"
UNIQUE = "unique_candidate_v1"

__all__ = [
    "AS_OF", "AUDIT_ID", "CLOSE", "CUTOFF", "ONE_MIN", "POLICY", "RECEIVED", "T0", "UNIQUE",
    "evidence_record", "evidence_snapshot", "make_batch", "make_opportunity", "opportunity_disposition",
    "portfolio_snapshot", "ranking_from", "run_select", "snapshot_from", "synced_portfolio",
]


def snapshot_from(
    batches,
    *,
    as_of: datetime = AS_OF,
    policy: CandidateFreshnessPolicy | None = POLICY,
    received_at: datetime = RECEIVED,
) -> EligibilitySnapshot:
    """Reduce ``batches`` (each a batch or ``(batch, received_at)``) then assess."""
    state = initial_state("simulated")
    for item in batches:
        batch, received = item if isinstance(item, tuple) else (item, received_at)
        state = apply_batch(state, batch, received).state
    return assess_candidates(state, as_of, policy)


def evidence_record(ref: str = "ev-1", **overrides) -> EvidenceRecord:
    fields = dict(
        evidence_ref=ref,
        strategy="orb",
        strategy_version="1",
        population="simulated",
        evidence_class="calibration",
        available_at=AS_OF - timedelta(days=1),
        configuration_ref="cfg-a",
        context_slice={"session": "regular"},
        sample_period_start=AS_OF - timedelta(days=30),
        sample_period_end=AS_OF - timedelta(days=1),
        sample_count=12,
        outcome_definition="realized_r",
        execution_venue="simulated",
        metrics={"win_rate": 0.5},
    )
    fields.update(overrides)
    return EvidenceRecord.create(**fields)


def evidence_snapshot(*records: EvidenceRecord, as_of: datetime = AS_OF) -> EvidenceSnapshot:
    return EvidenceSnapshot(as_of=as_of, records=tuple(records))


def ranking_from(snapshot: EligibilitySnapshot, *records: EvidenceRecord, cutoff: SnapshotCutoff = CUTOFF, **kw) -> RankingResult:
    return rank_candidates(snapshot, evidence_snapshot(*records), cutoff=cutoff, **kw)


def synced_portfolio(*exposures: tuple[str, bool], mode: str = "simulated") -> PortfolioInput:
    from app.trading_intelligence.candidate_selection import PortfolioExposureRef

    return PortfolioInput(
        mode, AS_OF, True, True, tuple(PortfolioExposureRef(symbol, in_flight) for symbol, in_flight in exposures)
    )


def run_select(ranking: RankingResult, portfolio=None, attempted=(), *, slots: int | None = 1, audit_id: str = AUDIT_ID):
    return select(
        ranking,
        synced_portfolio() if portfolio is None else portfolio,
        attempted,
        selection_policy=UNIQUE,
        slot_policy=None if slots is None else SlotPolicy(slots),
        audit_id=audit_id,
    )


def portfolio_snapshot(*, positions=(), orders=(), mode: str = "simulated"):
    """A REAL Portfolio State ``PortfolioSnapshot`` built by ``build_snapshot``."""
    day = AS_OF.date()
    position_states = tuple(
        PositionState(
            position_id=uuid4(), trade_id=uuid4(), execution_mode=mode, execution_venue="simulated",
            symbol=symbol, side="BUY", qty=10, avg_price=Decimal("100"), opened_at=AS_OF - timedelta(hours=1),
        )
        for symbol in positions
    )
    order_states = tuple(
        InFlightOrder(
            client_order_id=f"order-{symbol}", symbol=symbol, side="BUY", qty=10, position_effect="open",
            execution_mode=mode, status="approved", reference_price=Decimal("50"), stop=Decimal("49"),
        )
        for symbol in orders
    )
    state = LedgerState(execution_mode=mode, cursor=7, as_of=AS_OF, positions=position_states, orders=order_states)
    return build_snapshot(state, {}, day, None)
