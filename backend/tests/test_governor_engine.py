"""
Orchestration tests for app/governor/engine.py's AuthorizerStub — a real
EventBus (already tested on its own merits in test_event_bus.py), fake
TradeLedgerPort/PortfolioStateReader (fork 1: this task doesn't own the
real ledger or Portfolio State — see governor/ports.py's own docstring),
and a fake MarketClock/ReferencePriceTracker so every test is
deterministic and fast.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pytest

from app.event_bus.bus import EventBus
from app.governor.engine import AuthorizerStub
from app.governor.ports import (
    LedgerCommitError,
    PortfolioSnapshot,
    TradeDecisionCommitResult,
    TradeDecisionRecord,
)
from app.governor import engine as engine_module
from app.schemas.events.envelope import EventEnvelope, EventType
from app.strategy_engine.base_strategy import Opportunity
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots

_NOW = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)


class _FakeMarketClock:
    def __init__(self, *, is_regular_session: bool = True, trading_day: date = date(2026, 9, 22)) -> None:
        self._is_regular_session = is_regular_session
        self._trading_day = trading_day

    def is_regular_session(self, ts=None) -> bool:
        return self._is_regular_session

    def trading_day(self, ts=None) -> date:
        return self._trading_day


class _FakeReferencePriceTracker:
    def __init__(self, price: float | None = 100.0) -> None:
        self._price = price
        self.started_with: EventBus | None = None

    def start(self, bus: EventBus) -> None:
        self.started_with = bus

    def stop(self) -> None:
        pass

    def get(self, symbol: str) -> float | None:
        return self._price


@dataclass
class _FakeTradeLedger:
    fail: bool = False
    committed: list[TradeDecisionRecord] = field(default_factory=list)

    def commit_decision(self, record: TradeDecisionRecord) -> TradeDecisionCommitResult:
        if self.fail:
            raise LedgerCommitError("simulated ledger failure")
        self.committed.append(record)
        return TradeDecisionCommitResult(committed_at=_NOW, opportunity_id=record.opportunity_id)


@dataclass
class _FakePortfolioState:
    snapshot: PortfolioSnapshot
    calls: list[tuple[str, date]] = field(default_factory=list)

    def get_snapshot(self, execution_mode: str, trading_day: date) -> PortfolioSnapshot:
        self.calls.append((execution_mode, trading_day))
        return self.snapshot


def _empty_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        execution_mode="simulated", trading_day=date(2026, 9, 22), as_of=_NOW, realized_pnl_today=0.0, exposures=()
    )


def _opportunity_payload(**overrides) -> dict:
    base = dict(
        strategy="TEST",
        version="test_v1",
        direction="BUY",
        confidence=0.8,
        structural_invalidation=98.0,
        structural_target=110.0,
        evidence={"conditions": {}, "reason": "test", "basis": "live"},
        status="actionable",
        setup_detected_at=_NOW,
    )
    base.update(overrides)
    return Opportunity(**base).model_dump(mode="json")


async def _build_and_start(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trade_ledger: _FakeTradeLedger | None = None,
    portfolio_state: _FakePortfolioState | None = None,
    execution_mode: str | None = "simulated",
    is_regular_session: bool = True,
    reference_price: float | None = 100.0,
    market_state_present: bool = True,
    context_present: bool = True,
) -> tuple[EventBus, AuthorizerStub, list[EventEnvelope], _FakeTradeLedger]:
    monkeypatch.setattr(
        engine_module,
        "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots(
            market_state={"trend_score": 1.0} if market_state_present else None,
            context={"news": []} if context_present else None,
        ),
    )
    bus = EventBus()
    await bus.start()
    published: list[EventEnvelope] = []
    bus.subscribe_all(lambda env: published.append(env))

    ledger = trade_ledger or _FakeTradeLedger()
    portfolio = portfolio_state or _FakePortfolioState(snapshot=_empty_portfolio())

    authorizer = AuthorizerStub(
        bus,
        ledger,
        portfolio,
        execution_mode_provider=lambda: execution_mode,
        market_clock=_FakeMarketClock(is_regular_session=is_regular_session),
        reference_price_tracker=_FakeReferencePriceTracker(reference_price),
    )
    authorizer.start()
    return bus, authorizer, published, ledger


async def _publish_opportunity(bus: EventBus, symbol: str = "AAPL", **overrides) -> None:
    from app.event_bus.events import make_envelope

    payload = _opportunity_payload(**overrides)
    envelope = EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=symbol, payload=payload)
    await bus.publish(envelope)


@pytest.mark.asyncio
async def test_approved_path_publishes_trade_planned_then_governor_decision_then_order_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus, authorizer, published, ledger = await _build_and_start(monkeypatch)
    try:
        await _publish_opportunity(bus, symbol="AAPL")
        await asyncio.sleep(0.15)

        types = [e.event_type for e in published if e.event_type != EventType.OPPORTUNITY_CREATED]
        assert types == [EventType.TRADE_PLANNED, EventType.GOVERNOR_DECISION, EventType.ORDER_APPROVED]

        order_approved = next(e for e in published if e.event_type == EventType.ORDER_APPROVED)
        assert order_approved.payload["position_effect"] == "open"
        assert order_approved.payload["side"] == "BUY"
        assert order_approved.payload["qty"] == 10  # floor(1000/100)
        assert order_approved.payload["order_id"].endswith(":entry")

        trade_planned = next(e for e in published if e.event_type == EventType.TRADE_PLANNED)
        assert trade_planned.payload["direction"] == "long"
        assert trade_planned.payload["origin"] == "auto"
        assert trade_planned.payload["corroboration"] == []

        assert len(ledger.committed) == 1
        assert ledger.committed[0].decision == "approved"
        assert ledger.committed[0].opportunity_id is not None
        assert ledger.committed[0].client_order_id == order_approved.payload["order_id"]
        assert ledger.committed[0].limits_snapshot == {
            "max_concurrent_positions": 1,
            "fixed_notional_usd": 1000.0,
            "daily_loss_cap_usd": 100.0,
        }
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_rejected_path_publishes_only_plan_rejected_and_mints_no_opportunity_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus, authorizer, published, ledger = await _build_and_start(monkeypatch, is_regular_session=False)
    try:
        await _publish_opportunity(bus, symbol="AAPL")
        await asyncio.sleep(0.15)

        real_events = [e for e in published if e.event_type != EventType.OPPORTUNITY_CREATED]
        assert len(real_events) == 1
        assert real_events[0].event_type == EventType.PLAN_REJECTED
        assert real_events[0].payload["reasons"] == ["outside_regular_session"]

        assert len(ledger.committed) == 1
        assert ledger.committed[0].decision == "rejected"
        assert ledger.committed[0].opportunity_id is None
        assert ledger.committed[0].client_order_id is None
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.parametrize("mode", ["paper", "live", "backtest", None])
@pytest.mark.asyncio
async def test_execution_mode_gate_never_falls_back(monkeypatch: pytest.MonkeyPatch, mode: str | None) -> None:
    bus, authorizer, published, ledger = await _build_and_start(monkeypatch, execution_mode=mode)
    try:
        await _publish_opportunity(bus, symbol="AAPL")
        await asyncio.sleep(0.15)

        real_events = [e for e in published if e.event_type != EventType.OPPORTUNITY_CREATED]
        assert len(real_events) == 1
        assert real_events[0].event_type == EventType.PLAN_REJECTED
        assert real_events[0].payload["reasons"] == ["execution_mode_not_permitted"]
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_ledger_commit_failure_publishes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    failing_ledger = _FakeTradeLedger(fail=True)
    bus, authorizer, published, ledger = await _build_and_start(monkeypatch, trade_ledger=failing_ledger)
    try:
        await _publish_opportunity(bus, symbol="AAPL")
        await asyncio.sleep(0.15)

        real_events = [e for e in published if e.event_type != EventType.OPPORTUNITY_CREATED]
        assert real_events == []
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_opportunity_created_with_no_symbol_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    bus, authorizer, published, ledger = await _build_and_start(monkeypatch)
    try:
        envelope = EventEnvelope(
            event_type=EventType.OPPORTUNITY_CREATED, symbol=None, payload=_opportunity_payload()
        )
        await bus.publish(envelope)
        await asyncio.sleep(0.1)

        assert ledger.committed == []
        real_events = [e for e in published if e.event_type != EventType.OPPORTUNITY_CREATED]
        assert real_events == []
    finally:
        await authorizer.stop()
        await bus.stop()


def test_governor_package_imports_no_broker_or_venue_module() -> None:
    """AC #6 (import-boundary test): the stub imports no broker/venue
    module beyond the port's types — and governor doesn't even need the
    port's types, since it never calls a venue directly."""
    import ast
    import pathlib

    governor_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "governor"
    forbidden_prefixes = ("app.broker_adapters", "app.services.broker_registry", "app.execution_engine")
    for py_file in governor_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), f"{py_file.name} imports {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), f"{py_file.name} imports {alias.name}"
