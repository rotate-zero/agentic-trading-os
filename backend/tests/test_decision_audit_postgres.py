"""D2 on real PostgreSQL: selection journal, candidate acceptance claims, atomicity, concurrency.

No skip-on-unavailable fallback. Rows are tagged with NAME (strategy) / tracked IDs and cleaned here.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.session import SessionLocal
from app.decision_audit.ports import SelectionJournalConflict, SelectionJournalError
from app.decision_audit.postgres import PostgresSelectionJournal
from app.decision_audit.records import SelectionAttemptRecord
from app.governor.ports import CandidateAcceptanceContext, LedgerCommitError, TradeDecisionRecord
from app.governor.postgres import PostgresTradeLedger
from app.models.decision_audit import CandidateAcceptance, SelectionAttempt
from app.models.execution_ledger import Order, Trade, TradeReservation
from app.schemas.events.envelope import EventType
from tests.decision_audit_support import CREATED, non_shadow_record, shadow_record
from tests.decision_test_support import ONE_MIN, make_batch, opportunity_disposition
from tests.test_governor_engine import _build_and_start
from tests.test_governor_evidence_postgres import _publish, decision
from tests.test_position_ledger_postgres import NAME, TS, clean

IDS: set[UUID] = set()


def _wipe():
    with SessionLocal.begin() as s:
        trades = select(Trade.trade_id).where(Trade.strategy_name == NAME)
        s.query(CandidateAcceptance).filter(CandidateAcceptance.trade_id.in_(trades)).delete(synchronize_session=False)
        s.query(CandidateAcceptance).filter(CandidateAcceptance.selection_id.in_(IDS)).delete(synchronize_session=False)
        s.query(SelectionAttempt).filter(SelectionAttempt.selection_id.in_(IDS)).delete(synchronize_session=False)
    clean()
    IDS.clear()


@pytest.fixture(autouse=True)
def database():
    _wipe()
    yield
    _wipe()


@pytest.fixture
def journal():
    return PostgresSelectionJournal(SessionLocal)


@pytest.fixture
def ledger():
    return PostgresTradeLedger(SessionLocal)


def add(record, journal=None):
    IDS.add(record.selection_id)
    return (journal or PostgresSelectionJournal(SessionLocal)).append(record)


def attempt(**kw):
    """A non-shadow SELECTED attempt for strategy NAME, stored."""
    record = non_shadow_record(NAME, **kw)
    assert record.result == "selected" and record.selected_candidate_id
    add(record)
    return record


def claim_decision(record, **changes):
    ctx = CandidateAcceptanceContext(record.selected_candidate_id, str(record.selection_id))
    return replace(decision(evidence=None), acceptance=ctx, **changes)


def counts():
    with SessionLocal() as s:
        return tuple(s.scalar(select(func.count()).select_from(t)) for t in (Trade, TradeReservation, CandidateAcceptance))


# --- journal ---------------------------------------------------------------------------------------------------


def test_real_d1_shadow_result_round_trips(journal):
    record = shadow_record(NAME)
    assert add(record, journal).inserted is True
    stored = journal.get(record.selection_id)
    assert stored.equivalent(record) and stored.shadow is True
    assert stored.evidence == record.evidence and stored.created_at == record.created_at
    with SessionLocal() as s:
        row = s.get(SelectionAttempt, record.selection_id)
        assert (row.execution_mode, row.shadow, row.policy_version, row.result, row.schema_version) == (
            "simulated", True, "unique_candidate_v1", "selected", 1)
        assert row.evidence["authorizes_trade"] is False and row.selected_candidate_id == record.selected_candidate_id


def test_abstained_record_round_trips_with_null_candidate(journal):
    from app.decision_audit.shadow import shadow_selection_record
    from tests.decision_audit_support import d1

    rival = make_batch(symbol="MSFT", dispositions=[opportunity_disposition(NAME)])
    selection, ranking = d1(NAME, extra=[rival])
    record = shadow_selection_record(selection, ranking, created_at=CREATED)
    add(record, journal)
    stored = journal.get(record.selection_id)
    assert stored.result == "abstained" and stored.selected_candidate_id is None and stored.equivalent(record)


def test_identical_replay_is_a_no_op_and_conflict_changes_nothing(journal):
    record = shadow_record(NAME)
    assert add(record, journal).inserted and not journal.append(record).inserted
    changed = record.evidence
    changed["slots_available"] = 99
    other = SelectionAttemptRecord.create(
        selection_id=record.selection_id, created_at=record.created_at, execution_mode="simulated", shadow=True,
        policy_version=record.policy_version, result="selected", selected_candidate_id=record.selected_candidate_id,
        evidence=changed)
    with pytest.raises(SelectionJournalConflict):
        journal.append(other)
    for kw in ({"created_at": record.created_at.replace(year=2030)},):
        with pytest.raises(SelectionJournalConflict):
            journal.append(SelectionAttemptRecord.create(
                selection_id=record.selection_id, execution_mode="simulated", shadow=True,
                policy_version=record.policy_version, result="selected",
                selected_candidate_id=record.selected_candidate_id, evidence=record.evidence, **kw))
    assert journal.get(record.selection_id).equivalent(record)
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(SelectionAttempt).where(
            SelectionAttempt.selection_id == record.selection_id)) == 1


def test_concurrent_same_id_appends_one_insert(journal):
    record = shadow_record(NAME)
    IDS.add(record.selection_id)
    barrier = Barrier(4)

    def go():
        barrier.wait(timeout=5)
        return PostgresSelectionJournal(SessionLocal).append(record)

    with ThreadPoolExecutor(4) as pool:
        results = [f.result(timeout=10) for f in [pool.submit(go) for _ in range(4)]]
    assert sorted(r.inserted for r in results) == [False, False, False, True]


def test_failed_persistence_is_explicit_not_silent():
    def broken():
        raise RuntimeError("never reached")

    class Boom:
        def __call__(self):
            from sqlalchemy.exc import OperationalError
            raise OperationalError("select", {}, Exception("db down"))

    with pytest.raises(SelectionJournalError, match="append failed"):
        PostgresSelectionJournal(Boom()).append(shadow_record(NAME))
    with pytest.raises(SelectionJournalError):
        PostgresSelectionJournal(SessionLocal).append({"not": "a record"})
    with pytest.raises(SelectionJournalError, match="read failed"):
        PostgresSelectionJournal(Boom()).get(uuid4())


def test_historical_attempts_cannot_be_updated_even_by_sql(journal):
    record = shadow_record(NAME)
    add(record, journal)
    with pytest.raises(DBAPIError, match="append-only"):
        with SessionLocal.begin() as s:
            s.execute(text("UPDATE selection_attempts SET policy_version = 'x' WHERE selection_id = :i"),
                      {"i": record.selection_id})
    assert not hasattr(journal, "update") and not hasattr(journal, "delete")
    assert journal.get(record.selection_id).equivalent(record)


@pytest.mark.parametrize("values", [
    {"execution_mode": "staging"}, {"result": "maybe"}, {"result": "abstained"},  # abstained + candidate id
    {"selected_candidate_id": None}, {"schema_version": 0}, {"policy_version": ""},
    {"selected_candidate_id": ""}, {"evidence": "[]"},
])
def test_journal_table_constraints(values):
    base = dict(selection_id=str(uuid4()), created_at=CREATED, execution_mode="simulated", shadow=True,
                policy_version="p", result="selected", schema_version=1, evidence="{}", selected_candidate_id="c1")
    base.update(values)
    IDS.add(UUID(base["selection_id"]))
    with pytest.raises(DBAPIError):
        with SessionLocal.begin() as s:
            s.execute(text(
                "INSERT INTO selection_attempts (selection_id, created_at, execution_mode, shadow, policy_version, "
                "result, selected_candidate_id, schema_version, evidence) VALUES (:selection_id, :created_at, "
                ":execution_mode, :shadow, :policy_version, :result, :selected_candidate_id, :schema_version, "
                "CAST(:evidence AS jsonb))"), base)


# --- acceptance -----------------------------------------------------------------------------------------------


def test_approval_with_claim_commits_trade_reservation_and_claim_together(ledger):
    record = attempt()
    d = claim_decision(record)
    ledger.commit_decision(d)
    assert counts() == (1, 1, 1)
    with SessionLocal() as s:
        claim = s.scalar(select(CandidateAcceptance))
        assert (claim.execution_mode, claim.candidate_id, str(claim.trade_id), str(claim.selection_id)) == (
            "simulated", record.selected_candidate_id, d.opportunity_id, str(record.selection_id))
        assert claim.selection_shadow is False
        assert "acceptance" not in s.get(Trade, UUID(d.opportunity_id)).decision_record


@pytest.mark.parametrize("make_attempt,changes,match", [
    ("missing", {}, "does not exist"),
    ("shadow", {}, "shadow"),
    ("non_shadow", {"symbol": "MSFT"}, "does not match"),
    ("non_shadow", {"direction": "SELL"}, "does not match"),
    ("non_shadow", {"strategy_version": "2"}, "does not match"),
    ("non_shadow", {"strategy": "other"}, "does not match"),
])
def test_invalid_associations_are_refused_and_leave_nothing(ledger, make_attempt, changes, match):
    if make_attempt == "shadow":
        record = shadow_record(NAME)
        add(record)
    elif make_attempt == "missing":
        record = non_shadow_record(NAME)  # never stored
    else:
        record = attempt()
    d = claim_decision(record, **changes)
    with pytest.raises(LedgerCommitError, match=match):
        ledger.commit_decision(d)
    assert counts() == (0, 0, 0)


def test_abstained_and_wrong_mode_attempts_cannot_support_claims(ledger):
    from app.decision_audit.shadow import shadow_selection_record
    from tests.decision_audit_support import d1

    rival = make_batch(symbol="MSFT", dispositions=[opportunity_disposition(NAME)])
    selection, ranking = d1(NAME, extra=[rival])
    ev = shadow_selection_record(selection, ranking, created_at=CREATED).evidence
    ev["shadow"] = False
    abstained = SelectionAttemptRecord.create(
        selection_id=selection.audit_id, created_at=CREATED, execution_mode="simulated", shadow=False,
        policy_version="unique_candidate_v1", result="abstained", selected_candidate_id=None, evidence=ev)
    add(abstained)
    d = replace(decision(evidence=None), acceptance=CandidateAcceptanceContext(
        selection.considered_candidate_ids[0], selection.audit_id))
    with pytest.raises(LedgerCommitError, match="abstained"):
        ledger.commit_decision(d)
    # wrong mode: a paper-mode selected attempt cannot back a simulated approval
    paper = non_shadow_record(NAME)
    ev = paper.evidence
    ev["execution_mode"] = "paper"
    paper = SelectionAttemptRecord.create(
        selection_id=paper.selection_id, created_at=CREATED, execution_mode="paper", shadow=False,
        policy_version=paper.policy_version, result="selected", selected_candidate_id=paper.selected_candidate_id,
        evidence=ev)
    add(paper)
    with pytest.raises(LedgerCommitError, match="another execution mode"):
        ledger.commit_decision(claim_decision(paper))
    assert counts() == (0, 0, 0)


@pytest.mark.parametrize("ctx", [
    CandidateAcceptanceContext("", str(uuid4())), CandidateAcceptanceContext(" c ", str(uuid4())),
    CandidateAcceptanceContext("c" * 129, str(uuid4())), CandidateAcceptanceContext("c", "nope"),
    CandidateAcceptanceContext("c", str(uuid4()).upper()), CandidateAcceptanceContext("c", None), "ctx",
])
def test_malformed_context_is_refused_before_locking(ledger, ctx):
    with pytest.raises(LedgerCommitError):
        ledger.commit_decision(replace(decision(evidence=None), acceptance=ctx))
    assert counts() == (0, 0, 0)


def test_rejection_cannot_carry_a_claim(ledger):
    record = attempt()
    rej = TradeDecisionRecord("AAPL", NAME, "1", "BUY", "rejected", ["not_regular_session"], {}, "simulated",
                              True, True, 98.0, 110.0, 0.8, TS, TS,
                              acceptance=CandidateAcceptanceContext(record.selected_candidate_id, str(record.selection_id)))
    with pytest.raises(LedgerCommitError, match="only an approved"):
        ledger.commit_decision(rej)
    assert counts() == (0, 0, 0)


def test_identical_replay_verifies_complete_equality_and_is_idempotent(ledger):
    record = attempt()
    d = claim_decision(record)
    first = ledger.commit_decision(d)
    again = ledger.commit_decision(d)
    assert first.opportunity_id == again.opportunity_id and counts() == (1, 1, 1)
    other = attempt(minute=2)
    for ctx in (CandidateAcceptanceContext(other.selected_candidate_id, str(other.selection_id)),
                CandidateAcceptanceContext(record.selected_candidate_id, str(other.selection_id)),
                None):
        with pytest.raises(LedgerCommitError, match="acceptance"):
            ledger.commit_decision(replace(d, acceptance=ctx))
    assert counts() == (1, 1, 1)


def test_second_trade_for_the_same_candidate_fails_without_approval_or_reservation(ledger):
    record = attempt()
    ledger.commit_decision(claim_decision(record))
    loser = claim_decision(record)
    with pytest.raises(LedgerCommitError, match="already accepted"):
        ledger.commit_decision(loser)
    assert counts() == (1, 1, 1)
    with SessionLocal() as s:
        assert s.get(Trade, UUID(loser.opportunity_id)) is None
        assert s.get(TradeReservation, UUID(loser.opportunity_id)) is None


def test_two_concurrent_claims_for_one_candidate_commit_exactly_one(ledger):
    record = attempt()
    decisions = [claim_decision(record) for _ in range(2)]
    barrier = Barrier(2)

    def go(d):
        barrier.wait(timeout=5)
        try:
            return PostgresTradeLedger(SessionLocal).commit_decision(d)
        except LedgerCommitError as exc:
            return exc

    with ThreadPoolExecutor(2) as pool:
        results = [f.result(timeout=15) for f in [pool.submit(go, d) for d in decisions]]
    assert sum(isinstance(r, LedgerCommitError) for r in results) == 1
    assert counts() == (1, 1, 1)
    with SessionLocal() as s:
        winner = s.scalar(select(CandidateAcceptance)).trade_id
        assert s.scalars(select(Trade.trade_id)).all() == [winner]
        assert s.scalars(select(TradeReservation.trade_id)).all() == [winner]


def test_failure_after_trade_and_reservation_rolls_back_everything(ledger):
    record = attempt()

    def boom(mapper, connection, target):
        from sqlalchemy.exc import OperationalError
        raise OperationalError("INSERT candidate_acceptances", {}, Exception("claim insert failed"))

    event.listen(CandidateAcceptance, "before_insert", boom)
    try:
        with pytest.raises(LedgerCommitError, match="claim insert failed"):
            ledger.commit_decision(claim_decision(record))
    finally:
        event.remove(CandidateAcceptance, "before_insert", boom)
    assert counts() == (0, 0, 0)
    ledger.commit_decision(claim_decision(record))  # the candidate was NOT consumed by the rolled-back attempt
    assert counts() == (1, 1, 1)


def test_database_constraints_refuse_shadow_abstained_and_cross_mode_claims():
    shadow = shadow_record(NAME)
    add(shadow)
    ok = attempt()
    with SessionLocal.begin() as s:
        trade = Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=NAME,
                      strategy_version="1", symbol="AAPL", direction="BUY", decision="approved", thesis={},
                      status="open")
        s.add(trade)
        s.flush()
        trade_id = trade.trade_id
    for sel, cand, mode in [(shadow.selection_id, shadow.selected_candidate_id, "simulated"),
                            (ok.selection_id, "cnd1:other", "simulated"),
                            (ok.selection_id, ok.selected_candidate_id, "paper")]:
        with pytest.raises(DBAPIError):
            with SessionLocal.begin() as s:
                s.add(CandidateAcceptance(execution_mode=mode, candidate_id=cand, trade_id=trade_id, selection_id=sel))
    with pytest.raises(DBAPIError):  # a shadow flag on the claim is refused outright
        with SessionLocal.begin() as s:
            s.add(CandidateAcceptance(execution_mode="simulated", candidate_id=shadow.selected_candidate_id,
                                      trade_id=trade_id, selection_id=shadow.selection_id, selection_shadow=True))
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(CandidateAcceptance)) == 0


def test_trade_id_is_unique_per_claim_and_claims_are_immutable():
    a, b = attempt(), attempt(minute=2)
    ledger_ = PostgresTradeLedger(SessionLocal)
    d = claim_decision(a)
    ledger_.commit_decision(d)
    with pytest.raises(DBAPIError):
        with SessionLocal.begin() as s:
            s.add(CandidateAcceptance(execution_mode="simulated", candidate_id=b.selected_candidate_id,
                                      trade_id=UUID(d.opportunity_id), selection_id=b.selection_id))
    with pytest.raises(DBAPIError, match="append-only"):
        with SessionLocal.begin() as s:
            s.execute(text("UPDATE candidate_acceptances SET selection_id = :i"), {"i": b.selection_id})


def test_claims_survive_closure_and_cancellation_and_newer_candidate_is_independent(ledger):
    first = attempt()
    d = claim_decision(first)
    ledger.commit_decision(d)
    trade_id = UUID(d.opportunity_id)
    with SessionLocal.begin() as s:  # proven-unsent cancellation, then closure
        s.add(Order(client_order_id=d.client_order_id, trade_id=trade_id, execution_mode="simulated",
                    execution_venue="simulated", symbol="AAPL", side="BUY", qty=10, position_effect="open",
                    status="cancelled", reject_reason="approval_not_dispatched_on_restart"))
        s.get(Trade, trade_id).status = "closed"
    with SessionLocal() as s:
        assert s.get(CandidateAcceptance, ("simulated", first.selected_candidate_id)) is not None
    with pytest.raises(LedgerCommitError, match="already accepted"):
        ledger.commit_decision(claim_decision(first))
    newer = attempt(minute=3)  # a later source candle is a different candidate ID
    assert newer.selected_candidate_id != first.selected_candidate_id
    ledger.commit_decision(claim_decision(newer))
    assert counts() == (2, 2, 2)


def test_legacy_approvals_commit_and_replay_without_claims_and_are_never_backfilled(ledger):
    legacy = decision(evidence=None)
    assert legacy.acceptance is None
    ledger.commit_decision(legacy)
    ledger.commit_decision(legacy)
    assert counts() == (1, 1, 0)
    record = attempt()
    with pytest.raises(LedgerCommitError, match="conflicting candidate acceptance"):
        ledger.commit_decision(replace(legacy, acceptance=CandidateAcceptanceContext(
            record.selected_candidate_id, str(record.selection_id))))
    assert counts() == (1, 1, 0)


def test_legacy_rejections_and_other_modes_never_touch_the_claim_table(ledger):
    rej = TradeDecisionRecord("AAPL", NAME, "1", "BUY", "rejected", ["not_regular_session"], {}, "simulated",
                              True, True, 98.0, 110.0, 0.8, TS, TS)
    ledger.commit_decision(rej)
    assert counts() == (1, 0, 0)


@pytest.mark.asyncio
async def test_authorizer_stub_does_not_mint_identities_or_claim(monkeypatch):
    bus, authorizer, published, _ = await _build_and_start(monkeypatch, trade_ledger=PostgresTradeLedger(SessionLocal))
    try:
        await _publish(bus, {"conditions": {}})
        await asyncio.sleep(0.4)
        assert EventType.ORDER_APPROVED in [e.event_type for e in published]
        assert counts() == (1, 1, 0)
        with SessionLocal() as s:
            assert s.scalar(select(func.count()).select_from(SelectionAttempt).where(
                SelectionAttempt.selection_id.in_(IDS))) == 0
    finally:
        await authorizer.stop()
        await bus.stop()


def test_new_capabilities_are_not_wired_into_the_entry_consumer():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    engine = (root / "governor" / "engine.py").read_text()
    assert "CandidateAcceptance" not in engine and "decision_audit" not in engine and "selection_id" not in engine
    for path in root.rglob("*.py"):
        # The package itself, its ORM models, the Alembic registration in db/base.py and the Governor ledger
        # adapter/ports (the explicit acceptance context) are the only intended references.
        if "decision_audit" in path.parts or path.name == "decision_audit.py" or path.as_posix().endswith(
                ("db/base.py", "governor/postgres.py", "governor/ports.py")):
            continue
        assert "decision_audit" not in path.read_text(), f"{path} references the D2 package"


def test_lock_order_prefix_is_unchanged_and_claim_lock_follows_it():
    import inspect
    from app.db import ledger_transaction as lt
    from app.governor import postgres as gp

    assert "trades, orders, trade_reservations IN SHARE ROW EXCLUSIVE MODE" in inspect.getsource(lt)
    assert "candidate_acceptances" not in inspect.getsource(lt)
    source = inspect.getsource(gp)
    assert source.index("ledger_transaction(self._sessions") < source.index("LOCK TABLE candidate_acceptances")
