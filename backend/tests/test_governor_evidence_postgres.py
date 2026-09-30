"""governor-approval-evidence: real-PostgreSQL proof that an approval's evidence is
stored in `trades.thesis["evidence"]` inside the existing decision/reservation
transaction, replays idempotently, refuses conflicting replays, and that a bad
payload leaves nothing behind.

Rows are tagged with NAME so the shared autouse `database` fixture cleans them.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.governor.ports import LedgerCommitError, TradeDecisionRecord
from app.governor.postgres import PostgresTradeLedger
from app.models.execution_ledger import Trade, TradeReservation
from app.schemas.events.envelope import EventEnvelope, EventType
from tests.test_governor_engine import _build_and_start, _opportunity_payload
from tests.test_position_ledger_postgres import NAME, TS, database  # noqa: F401

EVIDENCE = {
    "conditions": {"or_minutes": 5, "or_high": 101.5, "breakout": True, "flag": None, "bars": [1, 2.5, "x"]},
    "reason": "ORB buy \u2014 unicode",
    "basis": "closed",
}


def decision(**changes) -> TradeDecisionRecord:
    trade_id = str(uuid4())
    record = TradeDecisionRecord(
        "AAPL", NAME, "1", "BUY", "approved", [],
        {"max_concurrent_positions": 1, "fixed_notional_usd": 1000.0, "daily_loss_cap_usd": 100.0},
        "simulated", True, True, 98.0, 110.0, 0.8, TS, TS,
        trade_id, f"{trade_id}:entry", 10, 100.0,
        evidence=EVIDENCE,
    )
    return replace(record, **changes)


def rows():
    with SessionLocal() as s:
        return s.scalars(select(Trade)).all(), s.scalars(select(TradeReservation)).all()


# --- round trip --------------------------------------------------------------


def test_evidence_round_trips_in_thesis_with_the_reservation_and_stays_out_of_decision_record():
    record = decision()
    PostgresTradeLedger(SessionLocal).commit_decision(record)
    with SessionLocal() as s:
        trade = s.scalar(select(Trade))
        assert trade.thesis["evidence"] == EVIDENCE
        assert trade.thesis["evidence"]["conditions"]["breakout"] is True  # bool stays bool
        assert trade.thesis["evidence"]["conditions"]["flag"] is None
        assert "evidence" not in trade.decision_record
        # the pre-existing thesis keys are untouched
        assert trade.thesis["final_stop"] == 98 and trade.thesis["structural_target"] == 110
        assert trade.thesis["confidence"] == 0.8
        # same transaction: the reservation exists for this trade
        assert s.get(TradeReservation, trade.trade_id).client_order_id == record.client_order_id


def test_stored_evidence_is_a_detached_copy_of_the_callers_dict():
    mutable = {"conditions": {"bars": [1, 2]}, "reason": "r", "basis": "closed"}
    PostgresTradeLedger(SessionLocal).commit_decision(decision(evidence=mutable))
    mutable["conditions"]["bars"].append(3)
    mutable["extra"] = 1
    with SessionLocal() as s:
        assert s.scalar(select(Trade)).thesis["evidence"] == {
            "conditions": {"bars": [1, 2]}, "reason": "r", "basis": "closed"}


def test_empty_evidence_dict_is_stored_as_given_and_absent_evidence_is_not_invented():
    PostgresTradeLedger(SessionLocal).commit_decision(decision(evidence={}))
    PostgresTradeLedger(SessionLocal).commit_decision(decision(evidence=None))
    with SessionLocal() as s:
        theses = [t.thesis for t in s.scalars(select(Trade)).all()]
    assert sorted("evidence" in t for t in theses) == [False, True]
    assert next(t for t in theses if "evidence" in t)["evidence"] == {}


# --- invalid payloads --------------------------------------------------------


class _Cycle(dict):
    pass


def _cycle():
    d: dict = {}
    d["self"] = d
    return d


@pytest.mark.parametrize(
    "bad",
    [
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": float("-inf")},
        {"conditions": {"bars": [1, float("nan")]}},
        {"x": datetime(2026, 9, 30, tzinfo=timezone.utc)},
        {"x": {1, 2}},
        {"x": (1, 2)},
        {"x": Decimal("1.5")},
        {"x": object()},
        {1: "int key"},
        _cycle(),
        [],
        "text",
    ],
    ids=lambda v: repr(v)[:36],
)
def test_bad_evidence_is_refused_and_leaves_no_trade_or_reservation(bad):
    with pytest.raises(LedgerCommitError, match="evidence"):
        PostgresTradeLedger(SessionLocal).commit_decision(decision(evidence=bad))
    trades, reservations = rows()
    assert trades == [] and reservations == []


def test_bad_replay_evidence_does_not_disturb_the_committed_approval():
    record = decision()
    PostgresTradeLedger(SessionLocal).commit_decision(record)
    with pytest.raises(LedgerCommitError, match="evidence"):
        PostgresTradeLedger(SessionLocal).commit_decision(replace(record, evidence={"x": float("nan")}))
    with SessionLocal() as s:
        assert s.scalar(select(Trade)).thesis["evidence"] == EVIDENCE


def test_a_rejected_decision_may_not_carry_evidence():
    record = decision(decision="rejected", reasons=["outside_regular_session"], opportunity_id=None,
                      client_order_id=None, qty=None, reference_price=None)
    with pytest.raises(LedgerCommitError, match="only an approved"):
        PostgresTradeLedger(SessionLocal).commit_decision(record)
    assert rows() == ([], [])
    PostgresTradeLedger(SessionLocal).commit_decision(replace(record, evidence=None))
    with SessionLocal() as s:
        assert "evidence" not in s.scalar(select(Trade)).thesis


# --- rollback ----------------------------------------------------------------


def _faulty_factory():
    class FaultSession(Session):
        pass

    return FaultSession, sessionmaker(bind=SessionLocal.kw["bind"], class_=FaultSession, autoflush=False)


def test_failure_after_flush_rolls_back_evidence_trade_and_reservation_together():
    FaultSession, factory = _faulty_factory()

    def fail_before(session):
        session.flush()
        # evidence, trade and reservation are all pending in the SAME transaction
        assert session.scalar(select(Trade)).thesis["evidence"] == EVIDENCE
        assert session.scalar(select(TradeReservation)) is not None
        raise LedgerCommitError("injected before commit")

    event.listen(FaultSession, "before_commit", fail_before)
    with pytest.raises(LedgerCommitError, match="injected"):
        PostgresTradeLedger(factory).commit_decision(decision())
    assert rows() == ([], [])


def test_lost_acknowledgement_replay_finds_the_committed_evidence_and_is_identical():
    FaultSession, factory = _faulty_factory()
    record = decision()

    def lose_ack(session):
        raise LedgerCommitError("lost acknowledgement")

    event.listen(FaultSession, "after_commit", lose_ack)
    with pytest.raises(LedgerCommitError, match="lost"):
        PostgresTradeLedger(factory).commit_decision(record)
    result = PostgresTradeLedger(SessionLocal).commit_decision(record)
    assert result.opportunity_id == record.opportunity_id
    trades, reservations = rows()
    assert len(trades) == len(reservations) == 1 and trades[0].thesis["evidence"] == EVIDENCE


# --- replay ------------------------------------------------------------------


def test_identical_replay_is_idempotent_and_concurrent_replays_agree():
    record = decision()
    barrier = Barrier(2)

    def commit():
        barrier.wait(timeout=5)
        return PostgresTradeLedger(SessionLocal).commit_decision(record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit) for _ in range(2)]
        assert [f.result(timeout=5).opportunity_id for f in futures] == [record.opportunity_id] * 2
    PostgresTradeLedger(SessionLocal).commit_decision(record)  # and once more, sequentially
    trades, reservations = rows()
    assert len(trades) == len(reservations) == 1
    assert trades[0].thesis["evidence"] == EVIDENCE


@pytest.mark.parametrize(
    "conflicting",
    [
        {**EVIDENCE, "reason": "different"},
        {**EVIDENCE, "extra": 1},
        {k: v for k, v in EVIDENCE.items() if k != "basis"},
        {**EVIDENCE, "conditions": {**EVIDENCE["conditions"], "breakout": 1}},  # True is not 1
        {**EVIDENCE, "conditions": {**EVIDENCE["conditions"], "bars": [1, 2.5]}},
        {},
        None,
    ],
    ids=["value", "extra-key", "missing-key", "bool-vs-int", "list-length", "empty", "absent"],
)
def test_conflicting_evidence_on_replay_is_refused_and_stored_evidence_is_unchanged(conflicting):
    record = decision()
    PostgresTradeLedger(SessionLocal).commit_decision(record)
    with pytest.raises(LedgerCommitError, match="conflicting"):
        PostgresTradeLedger(SessionLocal).commit_decision(replace(record, evidence=conflicting))
    trades, reservations = rows()
    assert len(trades) == len(reservations) == 1
    assert trades[0].thesis["evidence"] == EVIDENCE


def test_evidence_offered_against_a_legacy_approval_without_evidence_is_a_conflict():
    record = decision(evidence=None)
    PostgresTradeLedger(SessionLocal).commit_decision(record)
    PostgresTradeLedger(SessionLocal).commit_decision(record)  # identical legacy replay still fine
    with pytest.raises(LedgerCommitError, match="conflicting"):
        PostgresTradeLedger(SessionLocal).commit_decision(replace(record, evidence=EVIDENCE))
    with SessionLocal() as s:
        assert "evidence" not in s.scalar(select(Trade)).thesis


def test_existing_conflict_checks_still_win_for_other_field_changes():
    record = decision()
    PostgresTradeLedger(SessionLocal).commit_decision(record)
    with pytest.raises(LedgerCommitError, match="conflicting or incomplete"):
        PostgresTradeLedger(SessionLocal).commit_decision(replace(record, qty=20))


# --- AuthorizerStub + real ledger --------------------------------------------


async def _publish(bus, evidence, symbol="AAPL", **overrides):
    payload = _opportunity_payload(strategy=NAME, **overrides)
    payload["evidence"] = evidence  # applied after model_dump so invalid values arrive untouched
    await bus.publish(EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=symbol, payload=payload))


@pytest.mark.asyncio
async def test_engine_persists_the_accepted_opportunitys_evidence(monkeypatch):
    bus, authorizer, published, _ = await _build_and_start(monkeypatch, trade_ledger=PostgresTradeLedger(SessionLocal))
    try:
        await _publish(bus, EVIDENCE)
        await asyncio.sleep(0.4)
        types = [e.event_type for e in published if e.event_type != EventType.OPPORTUNITY_CREATED]
        assert types == [EventType.TRADE_PLANNED, EventType.GOVERNOR_DECISION, EventType.ORDER_APPROVED]
        trades, reservations = rows()
        assert len(trades) == len(reservations) == 1
        assert trades[0].thesis["evidence"] == EVIDENCE
        assert "evidence" not in trades[0].decision_record
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.parametrize("bad", [{"conditions": {"close": float("nan")}}, {"x": float("inf")}, {"x": object()}],
                         ids=["nan", "inf", "object"])
@pytest.mark.asyncio
async def test_engine_publishes_no_approval_and_leaves_no_rows_for_bad_evidence(monkeypatch, bad):
    bus, authorizer, published, _ = await _build_and_start(monkeypatch, trade_ledger=PostgresTradeLedger(SessionLocal))
    try:
        await _publish(bus, bad)
        await asyncio.sleep(0.4)
        assert [e.event_type for e in published if e.event_type != EventType.OPPORTUNITY_CREATED] == []
        assert rows() == ([], [])
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_engine_rule_rejection_is_still_audited_without_evidence_even_if_it_is_bad(monkeypatch):
    bus, authorizer, published, _ = await _build_and_start(
        monkeypatch, trade_ledger=PostgresTradeLedger(SessionLocal), is_regular_session=False)
    try:
        await _publish(bus, {"x": float("nan")})
        await asyncio.sleep(0.4)
        assert [e.event_type for e in published if e.event_type != EventType.OPPORTUNITY_CREATED] == [
            EventType.PLAN_REJECTED]
        trades, reservations = rows()
        assert len(trades) == 1 and reservations == []
        assert trades[0].decision == "rejected" and "evidence" not in trades[0].thesis
    finally:
        await authorizer.stop()
        await bus.stop()
