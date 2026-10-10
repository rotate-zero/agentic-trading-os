"""Builders for the D2 tests: REAL D1 results (C1 -> rank -> select), never hand-made.

Not collected by pytest (no ``test_`` prefix).
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from app.decision_audit.records import NonShadowSelectionInput, SelectionAttemptRecord
from app.decision_audit.shadow import shadow_selection_record
from app.trading_intelligence.candidate_ranking import SnapshotCutoff
from tests.decision_test_support import (
    ONE_MIN, T0, make_batch, opportunity_disposition, ranking_from, run_select, snapshot_from, evidence_record,
)

CREATED = T0 + timedelta(minutes=5)


def d1(strategy: str, *, symbol: str = "AAPL", direction: str = "long", minute: int = 0, evidence=(), extra=()):
    """(selection, ranking) from the real pipeline; one candidate unless ``extra`` adds rivals."""
    batches = [make_batch(T0 + minute * ONE_MIN, symbol=symbol,
                          dispositions=[opportunity_disposition(strategy, direction=direction)])]
    batches += list(extra)
    # A later candle needs its own consistent receive/as-of/cutoff times, or it is not eligible.
    close = T0 + (minute + 1) * ONE_MIN
    as_of = close + timedelta(seconds=10)
    snapshot = snapshot_from(batches, as_of=as_of, received_at=close + timedelta(seconds=2))
    ranking = ranking_from(snapshot, *evidence, cutoff=SnapshotCutoff(41 + minute, as_of))
    return run_select(ranking, audit_id=str(uuid4())), ranking


def shadow_record(strategy: str, **kw) -> SelectionAttemptRecord:
    selection, ranking = d1(strategy, **kw)
    return shadow_selection_record(selection, ranking, created_at=CREATED)


def non_shadow_input(strategy: str, **kw) -> NonShadowSelectionInput:
    """An explicit coordinator-style input derived from a real D1 audit, with its own shadow=false evidence."""
    base = shadow_record(strategy, **kw)
    evidence = base.evidence
    evidence["shadow"] = False
    return NonShadowSelectionInput(
        selection_id=str(base.selection_id), created_at=CREATED, execution_mode=base.execution_mode,
        policy_version=base.policy_version, result=base.result,
        selected_candidate_id=base.selected_candidate_id, evidence=evidence)


def non_shadow_record(strategy: str, **kw) -> SelectionAttemptRecord:
    return non_shadow_input(strategy, **kw).to_record()
