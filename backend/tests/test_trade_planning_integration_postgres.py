"""P2 persistence, replay, rollback and real AuthorizerStub/ledger integration.

Uses the configured PostgreSQL database and the existing isolated-row cleanup.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.governor.engine import AuthorizerStub
from app.governor.ports import LedgerCommitError
from app.governor.postgres import PostgresTradeLedger
from app.models.execution_ledger import Trade, TradeReservation
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.execution import TradePlanned
from app.trade_planning.plan import ReferenceObservation
from app.trade_planning.planner import plan_entry
from app.trade_planning.proposal import serialize_proposal
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots
from tests.test_authorization_ledger_postgres import decision
from tests.test_governor_engine import _FakeMarketClock, _FakePortfolioState, _empty_portfolio, _opportunity_payload
from tests.test_position_ledger_postgres import NAME, TS, database  # noqa: F401
from tests.test_trade_planning import opportunity


def planned_record(**changes):
    plan = plan_entry("AAPL", opportunity("BUY", 98., 110.), ReferenceObservation(100., TS, TS), 1000., TS)
    return decision(proposal=serialize_proposal(plan), **changes)


def rows():
    with SessionLocal() as s:
        return s.scalars(select(Trade)).all(), s.scalars(select(TradeReservation)).all()


def test_proposal_persisted_detached_in_thesis_only_and_matches_reservation():
    record = planned_record()
    PostgresTradeLedger(SessionLocal).commit_decision(record)
    record.proposal["sizing"]["fixed_notional_usd"] = "2000"
    trade, reservation = rows()[0][0], rows()[1][0]
    assert trade.thesis["proposal"]["sizing"]["fixed_notional_usd"] == "1000.0"
    assert "proposal" not in trade.decision_record
    assert trade.thesis["proposal"]["size"] == reservation.qty == record.qty
    assert Decimal(trade.thesis["proposal"]["entry"]) == reservation.reference_price
    assert trade.thesis["proposal"]["planned_risk_usd"] == "20.0"


@pytest.mark.parametrize("reason", ["loss_exposure_unknown", "daily_loss_cap_reached", "projected_loss_exceeds_daily_cap"])
def test_risk_rejection_has_proposal_but_no_permission_or_reservation(reason):
    record = planned_record(decision="rejected", reasons=[reason], opportunity_id=None,
                            client_order_id=None, qty=None, reference_price=None)
    result = PostgresTradeLedger(SessionLocal).commit_decision(record)
    trades, reservations = rows()
    assert result.opportunity_id is None
    assert reservations == []
    assert trades[0].execution_venue is None
    assert trades[0].thesis["proposal"] == record.proposal
    assert all(trades[0].decision_record[k] is None for k in ["opportunity_id", "client_order_id", "qty", "reference_price"])


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"size": True}, {"entry": "NaN"}, {"r_multiple": float("inf")},
    {"planned_risk_usd": float("nan")}, {"sizing": {}}, {"extra": 1},
])
def test_invalid_proposal_fails_before_transaction_and_leaves_no_rows(change):
    record = planned_record()
    record.proposal.update(change)
    def forbidden_session():
        pytest.fail("invalid proposal opened a transaction")
    with pytest.raises(LedgerCommitError, match="proposal"):
        PostgresTradeLedger(forbidden_session).commit_decision(record)
    assert rows() == ([], [])


@pytest.mark.parametrize("change", [{"size": 11}, {"entry": "101"}, {"stop": "97"}, {"direction": "short"}])
def test_proposal_cannot_disagree_with_authorized_terms(change):
    record = planned_record()
    record.proposal.update(change)
    with pytest.raises(LedgerCommitError, match="proposal differs"):
        PostgresTradeLedger(SessionLocal).commit_decision(record)
    assert rows() == ([], [])


def test_early_rejection_cannot_carry_a_proposal():
    record = planned_record(decision="rejected", reasons=["symbol_busy"], opportunity_id=None,
                            client_order_id=None, qty=None, reference_price=None)
    with pytest.raises(LedgerCommitError, match="rule-6"):
        PostgresTradeLedger(SessionLocal).commit_decision(record)
    assert rows() == ([], [])


def test_identical_proposal_replay_then_conflict_leaves_committed_rows_unchanged():
    record = planned_record()
    ledger = PostgresTradeLedger(SessionLocal)
    ledger.commit_decision(record)
    assert ledger.commit_decision(record).opportunity_id == record.opportunity_id
    equivalent = deepcopy(record.proposal)
    equivalent["planned_risk_usd"] = "20.00"
    ledger.commit_decision(replace(record, proposal=equivalent))
    conflict = deepcopy(record.proposal)
    conflict["planned_risk_usd"] = "21.0"
    with pytest.raises(LedgerCommitError, match="conflicting proposal"):
        ledger.commit_decision(replace(record, proposal=conflict))
    trades, reservations = rows()
    assert len(trades) == len(reservations) == 1
    assert trades[0].thesis["proposal"] == record.proposal
    assert reservations[0].qty == record.qty


@pytest.mark.parametrize("stored", [None, {"schema_version": 999}, {"schema_version": 1, "size": 10}])
def test_present_malformed_stored_proposal_is_never_treated_as_historical_absence(stored):
    record = planned_record()
    ledger = PostgresTradeLedger(SessionLocal)
    ledger.commit_decision(record)
    with SessionLocal.begin() as s:
        row = s.get(Trade, UUID(record.opportunity_id))
        row.thesis = {**row.thesis, "proposal": stored}
    with pytest.raises(LedgerCommitError, match="committed proposal"):
        ledger.commit_decision(record)
    assert rows()[0][0].thesis["proposal"] == stored


def test_historical_absence_replays_without_backfill_and_conflicts_with_present_proposal():
    record = decision()
    ledger = PostgresTradeLedger(SessionLocal)
    ledger.commit_decision(record)
    ledger.commit_decision(record)
    present = planned_record().proposal
    with pytest.raises(LedgerCommitError, match="conflicting proposal"):
        ledger.commit_decision(replace(record, proposal=present))
    assert "proposal" not in rows()[0][0].thesis
    # And a new proposal-bearing approval cannot be replayed as historical.
    other = planned_record()
    ledger.commit_decision(other)
    with pytest.raises(LedgerCommitError, match="conflicting proposal"):
        ledger.commit_decision(replace(other, proposal=None))


def test_failure_after_trade_insert_before_reservation_insert_rolls_back_both():
    class FaultSession(Session):
        pass
    factory = sessionmaker(bind=SessionLocal.kw["bind"], class_=FaultSession, autoflush=False)
    def fail_after_trade(session, context):
        if any(isinstance(row, Trade) for row in session.new):
            assert session.scalar(select(Trade)) is not None  # actual INSERT happened
            assert session.scalar(select(TradeReservation)) is None
            raise LedgerCommitError("injected between trade and reservation")
    event.listen(FaultSession, "after_flush", fail_after_trade)
    try:
        with pytest.raises(LedgerCommitError, match="injected between"):
            PostgresTradeLedger(factory).commit_decision(planned_record())
    finally:
        event.remove(FaultSession, "after_flush", fail_after_trade)
    assert rows() == ([], [])


def test_proposal_detached_before_session_is_opened():
    record = planned_record()
    original = deepcopy(record.proposal)
    def factory():
        record.proposal.clear()  # A caller mutation after validation cannot alter storage.
        return SessionLocal()
    PostgresTradeLedger(factory).commit_decision(record)
    assert rows()[0][0].thesis["proposal"] == original


@pytest.mark.asyncio
@pytest.mark.parametrize("direction,entry,stop,target,qty,reason", [
    ("BUY", 100., 99.5, 103., 10, None),
    ("SELL", 100., 100.5, 97.5, 10, None),
    ("BUY", 333.33, 333., 334., 3, None),
    ("BUY", 500., 499., 501., 2, None),
    ("BUY", 1000., 999., 1001., 1, None),
    ("BUY", 1000.01, 999., 1001., None, "notional_below_one_share"),
    ("BUY", 100., 80., 110., None, "projected_loss_exceeds_daily_cap"),
    ("BUY", None, 99., 110., None, "no_reference_price"),
    ("BUY", 0., 99., 110., None, "no_reference_price"),
    ("BUY", -1., 99., 110., None, "no_reference_price"),
    ("BUY", float("nan"), 99., 110., None, "no_reference_price"),
    ("BUY", 100., 100., 110., None, "invalid_stop_geometry"),
    ("BUY", 100., 99., 90., None, "invalid_target_geometry"),
    ("SELL", 100., 101., 110., None, "invalid_target_geometry"),
])
async def test_authorizer_plan_to_real_postgres_and_compatible_events(monkeypatch, direction, entry, stop, target, qty, reason):
    import app.governor.engine as module
    real_planner = module.plan_entry
    calls = []
    def planner(*args):
        result = real_planner(*args)
        calls.append(result)
        return result
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return TS
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    monkeypatch.setattr(module, "plan_entry", planner)
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots(market_state={}, context={}))
    bus = EventBus()
    published = []
    def observe(envelope):
        assert rows()[0]  # Durable commit precedes every outgoing event.
        published.append(envelope)
    bus.subscribe_all(observe)
    await bus.start()
    authorizer = AuthorizerStub(bus, PostgresTradeLedger(SessionLocal), _FakePortfolioState(_empty_portfolio()),
        market_clock=_FakeMarketClock(), execution_mode_provider=lambda: "simulated")
    authorizer.start()
    try:
        if entry is not None:
            authorizer._reference_price._on_price_updated(EventEnvelope(event_type=EventType.PRICE_UPDATED,
                symbol="AAPL", timestamp=TS - timedelta(seconds=1),
                payload={"price": entry, "exchange_ts": (TS - timedelta(hours=1)).isoformat()}))
        await authorizer._process_one({"symbol": "AAPL", "payload": _opportunity_payload(strategy=NAME,
            direction=direction, structural_invalidation=stop, structural_target=target)})
        await bus._normal_queue.join()
        await bus._critical_queue.join()
        trades, reservations = rows()
        assert len(calls) == len(trades) == 1
        if reason:
            assert [e.event_type for e in published] == [EventType.PLAN_REJECTED]
            assert published[0].payload == {"symbol": "AAPL", "reasons": [reason]}
            assert reservations == []
            if reason == "projected_loss_exceeds_daily_cap":
                assert trades[0].thesis["proposal"] == serialize_proposal(calls[0])
            else:
                assert "proposal" not in trades[0].thesis
        else:
            assert [e.event_type for e in published] == [EventType.TRADE_PLANNED, EventType.GOVERNOR_DECISION, EventType.ORDER_APPROVED]
            plan = calls[0]
            assert plan.size == published[0].payload["size"] == published[2].payload["qty"] == reservations[0].qty == qty
            assert Decimal(str(plan.entry)) == reservations[0].reference_price == Decimal(str(published[0].payload["entry"]))
            # Literal pre-P2 wire contract: no proposal metadata in events.
            assert published[0].payload == TradePlanned(direction="long" if direction == "BUY" else "short",
                entry=entry, stop=stop, target=target, size=qty,
                r_multiple=round(abs(target-entry)/abs(entry-stop), 4)).model_dump(mode="json")
            assert published[1].payload == {"action": "approved", "reasons": [], "size_multiplier": None, "delay_seconds": None}
            assert published[2].payload == {"order_id": reservations[0].client_order_id, "symbol": "AAPL", "side": direction,
                "qty": qty, "order_type": "market", "limit_price": None, "position_effect": "open"}
            proposal = trades[0].thesis["proposal"]
            assert proposal == serialize_proposal(plan)
            assert proposal["planned_at"] == trades[0].decision_record["decided_at"] == TS.isoformat()
            assert proposal["reference_age_seconds"] == "1.0"
            assert proposal["reference_source_age_seconds"] == "3600.0"
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.parametrize("change", [{"schema_version": 2}, {"entry": "NaN"}, {"r_multiple": float("nan")}])
def test_invalid_requested_replay_does_not_modify_persisted_approval(change):
    record = planned_record()
    ledger = PostgresTradeLedger(SessionLocal)
    ledger.commit_decision(record)
    bad = deepcopy(record.proposal)
    bad.update(change)
    with pytest.raises(LedgerCommitError, match="proposal"):
        ledger.commit_decision(replace(record, proposal=bad))
    trades, reservations = rows()
    assert len(trades) == len(reservations) == 1
    assert trades[0].thesis["proposal"] == record.proposal
    assert reservations[0].qty == record.qty


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_proposal", "between_inserts"])
async def test_real_persistence_failure_publishes_nothing(monkeypatch, failure):
    import app.governor.engine as module
    from tests.test_governor_engine import _FakeReferencePriceTracker
    monkeypatch.setattr(module, "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots(market_state={}, context={}))
    class FaultSession(Session):
        pass
    factory = sessionmaker(bind=SessionLocal.kw["bind"], class_=FaultSession, autoflush=False)
    def fail_after_trade(session, context):
        if any(isinstance(row, Trade) for row in session.new):
            raise LedgerCommitError("failure between inserts")
    if failure == "between_inserts":
        event.listen(FaultSession, "after_flush", fail_after_trade)
    else:
        monkeypatch.setattr(module, "serialize_proposal", lambda plan: {"schema_version": 999})
    bus = EventBus()
    published = []
    bus.subscribe_all(published.append)
    await bus.start()
    authorizer = AuthorizerStub(bus, PostgresTradeLedger(factory), _FakePortfolioState(_empty_portfolio()),
        market_clock=_FakeMarketClock(), execution_mode_provider=lambda: "simulated",
        reference_price_tracker=_FakeReferencePriceTracker())
    try:
        await authorizer._process_one({"symbol": "AAPL", "payload": _opportunity_payload(strategy=NAME)})
        await bus._critical_queue.join()
        await bus._normal_queue.join()
        assert published == []
        assert rows() == ([], [])
    finally:
        if failure == "between_inserts":
            event.remove(FaultSession, "after_flush", fail_after_trade)
        await bus.stop()
