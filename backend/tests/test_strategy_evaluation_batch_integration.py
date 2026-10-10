"""C2 integration: real EventBus dispatch, real Scheduler/engines, the observation
reader, the unchanged OpportunityCache consumer, and the real app lifespan.

DB-gated like test_strategy_scheduler.py's real-engine tier (the real
MarketStateEngine persists). Demonstrates that observation adds no
authorization/order side effects and leaves the existing consumers unchanged.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.main as main_module
from app.context_engine.engine import ContextEngine
from app.event_bus.bus import EventBus, WILDCARD
from app.event_bus.events import make_envelope
from app.main import app as fastapi_app
from app.market_state_engine.engine import MarketStateEngine
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.features import FeatureSet
from app.services import broker_registry
from app.strategy_engine.scheduler import StrategyScheduler, default_registry
from app.trading_intelligence.candidate_batch_wire import batch_to_payload
from app.trading_intelligence.candidate_eligibility import CandidateFreshnessPolicy
from app.trading_intelligence.candidate_observation import CandidateObservationReader
from app.trading_intelligence.opportunity_cache import OpportunityCache
from tests.test_opportunity_candidate_contract import make_batch
from tests.test_strategy_scheduler import (
    _TS,
    _FakeCalendarProvider,
    _StubStrategy,
    _clean_test_symbol,
    _db_available,
    _install_engine_singletons,
    _make_opportunity,
)

pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")

ORDER_AND_SELECTION_EVENTS = {
    EventType.OPPORTUNITY_SELECTED, EventType.TRADE_PLANNED, EventType.GOVERNOR_DECISION,
    EventType.ORDER_APPROVED, EventType.PLAN_REJECTED, EventType.ORDER_FILLED,
    EventType.ORDER_STATUS_CHANGED, EventType.POSITION_ADJUSTED, EventType.POSITION_CLOSED,
}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def _boot(strategies, ticker: str):
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    market_state_engine = MarketStateEngine(bus)
    market_state_engine.start()
    context_engine = ContextEngine(bus, providers=[_FakeCalendarProvider()], symbol_providers=[])
    context_engine.start()
    _install_engine_singletons(market_state_engine, context_engine)
    scheduler = StrategyScheduler(bus, strategies=strategies, now=lambda: _TS + timedelta(seconds=61))
    scheduler.start()
    return bus, market_state_engine, context_engine, scheduler


async def _publish_candle(bus, context_engine, ticker: str, close: float = 100.0, slope: float = 10.0) -> None:
    await context_engine.evaluate_all()
    await context_engine.evaluate_for_symbol(ticker)
    payload = FeatureSet(timeframe="1m", candle_ts=_TS, close=close, features={"sma_20_slope_angle": slope})
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=ticker))
    await asyncio.sleep(0.6)  # MarketStateEngine's debounced worker: compute + persist + publish


async def test_real_engines_real_seven_strategies_produce_one_complete_coherent_batch():
    ticker = "TESTC2A"
    bus, mse, ctx, scheduler = await _boot(default_registry(_TS), ticker)
    clock = Clock(_TS - timedelta(minutes=5))
    reader = CandidateObservationReader(bus, freshness_policy=CandidateFreshnessPolicy.create({"1m": 120.0}), clock=clock)
    reader.start()
    legacy: list[EventEnvelope] = []
    bus.subscribe(EventType.OPPORTUNITY_CREATED, lambda e: legacy.append(e))
    try:
        clock.now = _TS + timedelta(seconds=62)
        await _publish_candle(bus, ctx, ticker, slope=1.0)  # real, MATCH-failing candle
        snap = reader.snapshot(_TS + timedelta(seconds=70))
        assessed = [a for a in snap.eligibility.assessments if a.symbol == ticker]
        assert len(assessed) == 7  # every registered strategy version this trigger covered, once
        assert {a.kind for a in assessed} <= {"no_opportunity", "gated", "opportunity", "error"}
        assert all(a.kind != "error" for a in assessed)
        assert all(a.source_candle_ts == _TS and a.source_interval_close == _TS + timedelta(minutes=1) for a in assessed)
        assert len({a.completed_at for a in assessed}) == 1  # one batch, one completion
        assert len(legacy) == sum(1 for a in assessed if a.kind == "opportunity")  # legacy stream == batch's opportunities
        assert snap.arrival_sequence == 1
    finally:
        await reader.stop()
        await scheduler.stop()
        await ctx.stop()
        await bus.stop()
        await mse.stop()
        _clean_test_symbol(ticker)


async def test_observation_changes_no_existing_consumer_and_adds_no_authorization_or_order_events():
    ticker = "TESTC2B"
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    bus, mse, ctx, scheduler = await _boot([stub], ticker)
    cache = OpportunityCache(bus)
    cache.start()
    clock = Clock(_TS - timedelta(minutes=5))
    reader = CandidateObservationReader(bus, freshness_policy=CandidateFreshnessPolicy.create({"1m": 120.0}), clock=clock)
    reader.start()
    seen: list[EventType] = []
    bus.subscribe(WILDCARD, lambda e: seen.append(e.event_type))
    try:
        clock.now = _TS + timedelta(seconds=62)
        await _publish_candle(bus, ctx, ticker)
        # Existing consumer: OpportunityCache still receives the legacy event, unchanged.
        cached = cache.get_snapshot(ticker) if hasattr(cache, "get_snapshot") else None
        assert cached is not None and "A" in str(cached)
        # New reader: one eligible candidate built ONLY from the complete batch.
        snap = reader.snapshot(_TS + timedelta(seconds=70))
        assert [(a.strategy, a.direction) for a in snap.eligibility.eligible] == [("A", "long")]
        # No authorization / selection / order events appeared anywhere on the bus.
        assert not ORDER_AND_SELECTION_EVENTS & set(seen)
        assert EventType.STRATEGY_EVALUATION_COMPLETED in seen and EventType.OPPORTUNITY_CREATED in seen
        # Legacy event precedes the batch (batch is published after the whole pass).
        assert seen.index(EventType.OPPORTUNITY_CREATED) < seen.index(EventType.STRATEGY_EVALUATION_COMPLETED)
    finally:
        await reader.stop()
        await cache.stop()
        await scheduler.stop()
        await ctx.stop()
        await bus.stop()
        await mse.stop()
        _clean_test_symbol(ticker)


async def test_missing_context_with_real_engines_yields_an_unavailable_batch_and_no_evaluation():
    ticker = "TESTC2C"
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    bus, mse, ctx, scheduler = await _boot([stub], ticker)
    clock = Clock(_TS - timedelta(minutes=5))
    reader = CandidateObservationReader(bus, clock=clock)
    reader.start()
    legacy: list[EventEnvelope] = []
    bus.subscribe(EventType.OPPORTUNITY_CREATED, lambda e: legacy.append(e))
    try:
        clock.now = _TS + timedelta(seconds=62)
        # No evaluate_for_symbol(): ContextEngine has never evaluated this symbol.
        payload = FeatureSet(timeframe="1m", candle_ts=_TS, close=100.0, features={"sma_20_slope_angle": 10.0})
        await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=ticker))
        await asyncio.sleep(0.6)
        snap = reader.snapshot(_TS + timedelta(seconds=70))
        assert stub.calls == [] and legacy == []
        frames = [f for f in snap.unavailable_frames if f.symbol == ticker]
        assert [p.reason for p in frames[0].prerequisites] == ["context_unavailable"]
        assert snap.eligibility.assessments == ()
    finally:
        await reader.stop()
        await scheduler.stop()
        await ctx.stop()
        await bus.stop()
        await mse.stop()
        _clean_test_symbol(ticker)


async def test_real_lifespan_starts_exposes_and_stops_the_reader_and_keeps_replay_batches_out():
    async with fastapi_app.router.lifespan_context(fastapi_app):
        reader = fastapi_app.state.candidate_observation_reader
        assert reader is not None and reader.snapshot().status == "running"
        assert len(broker_registry._streaming_ownership_listeners) == 1
        bus = main_module.get_event_bus()
        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        live = make_batch(future, symbol="ZZC2LIVE")
        replay = make_batch(future, symbol="ZZC2RPLY", mode="backtest")
        for batch in (live, replay):
            await bus.publish(make_envelope(EventType.STRATEGY_EVALUATION_COMPLETED, batch_to_payload(batch), symbol=batch.symbol))
        await asyncio.wait_for(_until(lambda: reader.snapshot().arrival_sequence >= 2), 4)
        snap = reader.snapshot()
        assert [a.symbol for a in snap.eligibility.assessments] == ["ZZC2LIVE"]
        assert dict(snap.diagnostics.by_reason) == {"mode_mismatch": 1}
        assert snap.freshness_status == "freshness_policy_unconfigured"
        assert snap.eligibility.eligible == ()
    assert fastapi_app.state.candidate_observation_reader is None
    assert reader.snapshot().status == "stopped"
    assert broker_registry._streaming_ownership_listeners == []


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0.005)
