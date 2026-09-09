"""
Strategy Scheduler tests (decision #112/#114, strategy-engine-design.md
§10 D10). Two tiers, same split test_strategy_integration_contract.py
(M4) already established:

  1. Pure unit tests (no DB, no real engines) — registry/trigger-grouping,
     the FeaturesUpdated handler's caching-only behavior, the
     MarketStateChanged handler's control flow (skip-when-absent,
     cross-symbol exclusion, exception isolation, publish-on-match), and
     the ContextChanged reconstruction helper — each exercised in
     isolation with a fake EventBus and fake ContextEngine.

  2. A real-engine, real-EventBus integration test (DB-gated, same
     _db_available()/skipif posture as test_strategy_integration_contract.py)
     — proves the module docstring's central claim: reacting to
     MarketStateChanged (not FeaturesUpdated) against REAL
     MarketStateEngine/ContextEngine output actually resolves the race a
     FeaturesUpdated-triggered design has. This is a real correction, not
     a hypothetical: the first version of scheduler.py subscribed to
     FeaturesUpdated directly, and this exact test (in its earlier form)
     is what caught it failing — MarketStateEngine's debounced worker
     hadn't cached anything yet by the time the handler ran. Left as a
     regression test for that specific failure mode, not just a happy
     path. A second integration test runs the REAL 7-strategy registry
     through a real (but MATCH-failing) candle, confirming no crash and
     no fabricated Opportunity.

     NOT covered here, flagged rather than silently skipped: a real
     strategy's OWN MATCH conditions actually firing through this full
     stack end-to-end. Each strategy's test file already proves its
     MATCH/SCORE logic correct against hand-built inputs.

  3. gate_conditions enforcement (§2b, decision #117) — registration-time
     validation (raises for an unrecognized key/value, including a
     regression guard that the real 7-strategy registry stays valid),
     the per-candle skip against a REAL MarketClock (never mocked —
     gate_conditions.py itself never stands MarketClock in for anything)
     using real pre-market/regular/after-hours timestamps, per-strategy
     isolation (an ungated strategy alongside a gated one; a gate-check
     exception not blocking the strategy after it), and one DB-gated
     real-engine end-to-end test proving the actual failure mode this
     closes against REAL MarketStateEngine/ContextEngine output, not a
     hand-built MarketState. See test_gate_conditions.py for the
     underlying registry/check functions' own direct unit tests
     (independent of any Scheduler wiring).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

import app.context_engine.engine as context_engine_module
import app.market_state_engine.engine as market_state_engine_module
from app.context_engine.engine import ContextEngine
from app.context_engine.provider import ContextProvider
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.market_state_engine.engine import MarketStateEngine
from app.schemas.events.context import ContextChanged
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.base_strategy import Opportunity, ScheduleTrigger, Strategy, StrategyConfig, every_candle
from app.strategy_engine.scheduler import _CROSS_SYMBOL_SENTINEL, StrategyScheduler, _default_registry

_TS = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)

# Same calendar date as _TS above (Monday 2026-08-10, a real non-holiday
# trading weekday) but given directly in ET so it's unambiguous which
# Session each one falls in, for gate_conditions tests (decision #117).
# _TS itself (14:00 UTC = 10:00 ET) is already inside regular session —
# these two are deliberately outside it.
_ET = ZoneInfo("America/New_York")
_PRE_MARKET_TS = datetime(2026, 8, 10, 7, 0, tzinfo=_ET)  # 07:00 ET — pre-market
_AFTER_HOURS_TS = datetime(2026, 8, 10, 17, 0, tzinfo=_ET)  # 17:00 ET — after-hours


# --- shared fakes/stubs ------------------------------------------------------


class _StubStrategy(Strategy):
    """Stands in for a real Strategy — evaluate() returns whatever the
    test hands it, rather than any real GATE/MATCH/SCORE logic. Every
    real strategy's own test file already proves that logic correct in
    isolation; nothing here re-tests it. `name`/`trigger` are set as
    INSTANCE attributes (vs. every real strategy's class-level
    `name = "ORB"` / `trigger = every_candle(...)`) only so one stub
    class can stand in for several differently-named/triggered
    strategies — Strategy's ABC declares both as plain type annotations,
    satisfied either way."""

    def __init__(
        self,
        name: str,
        *,
        result: Opportunity | None = None,
        raises: bool = False,
        trigger: ScheduleTrigger | None = None,
    ) -> None:
        super().__init__(
            StrategyConfig(strategy_name=name, version="stub_v1", params={}, active_from=_TS)
        )
        self.name = name
        self.trigger = trigger if trigger is not None else every_candle(timeframe="1m")
        self._result = result
        self._raises = raises
        self.calls: list[tuple[str, MarketState, FeatureSet, ContextChanged]] = []

    async def evaluate(
        self, symbol: str, market_state: MarketState, features: FeatureSet, context: ContextChanged
    ) -> Opportunity | None:
        self.calls.append((symbol, market_state, features, context))
        if self._raises:
            raise RuntimeError(f"{self.name} deliberately raising for the exception-isolation test")
        return self._result


def _make_opportunity(strategy_name: str, *, direction: str = "BUY") -> Opportunity:
    return Opportunity(
        strategy=strategy_name,
        version="stub_v1",
        direction=direction,
        confidence=0.75,
        structural_invalidation=99.0,
        structural_target=105.0,
        evidence={"conditions": {}, "reason": "stub", "basis": "live"},
        setup_detected_at=_TS,
    )


def _market_state(**overrides) -> MarketState:
    defaults = dict(
        timeframe="1m",
        candle_ts=_TS,
        trend_score=70.0,
        volatility_regime_score=50.0,
        volume_regime_score=60.0,
        vwap_relationship_score=55.0,
        acceleration_score=10.0,
    )
    defaults.update(overrides)
    return MarketState(**defaults)


def _features_updated_envelope(symbol: str, timeframe: str = "1m", **overrides) -> EventEnvelope:
    defaults = dict(timeframe=timeframe, candle_ts=_TS, close=100.0, features={})
    defaults.update(overrides)
    return make_envelope(EventType.FEATURES_UPDATED, FeatureSet(**defaults), symbol=symbol)


def _market_state_changed_envelope(symbol: str, **overrides) -> EventEnvelope:
    return make_envelope(EventType.MARKET_STATE_CHANGED, _market_state(**overrides), symbol=symbol)


_CONTEXT_DICT = {"providers": {"calendar": {"session": "regular"}}}


class _FakeContextEngine:
    def __init__(self, symbols: dict[str, dict] | None = None) -> None:
        self._symbols = symbols if symbols is not None else {}

    def get_snapshot(self, symbol: str | None = None) -> dict:
        return {"global": {"providers": {}, "evaluated_at": None}, "symbols": dict(self._symbols)}


def _install_fake_context(monkeypatch: pytest.MonkeyPatch, *, present: bool = True) -> None:
    import app.strategy_engine.scheduler as scheduler_module

    symbols = {"AAPL": _CONTEXT_DICT} if present else {}
    monkeypatch.setattr(scheduler_module, "get_context_engine", lambda: _FakeContextEngine(symbols))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []

    def subscribe(self, event_type, handler) -> None:  # pragma: no cover — handlers called directly in tests
        pass

    async def publish(self, envelope: EventEnvelope) -> None:
        self.published.append(envelope)


# --- registry / trigger-grouping (pure) --------------------------------------


def test_default_registry_builds_all_seven_real_strategies_on_every_candle_1m():
    registry = _default_registry(_TS)
    assert len(registry) == 7
    assert {s.name for s in registry} == {
        "ORB", "Gap", "Volume Spike", "FirstPullback", "Reversal", "Momentum", "VWAP",
    }
    assert all(s.trigger.kind == "every_candle" and s.trigger.timeframe == "1m" for s in registry)


def test_every_candle_strategies_grouped_by_timeframe():
    strategies = [_StubStrategy("A"), _StubStrategy("B")]
    scheduler = StrategyScheduler(_FakeBus(), strategies=strategies)
    assert set(scheduler._every_candle_by_timeframe["1m"]) == set(strategies)


def test_unsupported_trigger_kind_registered_but_never_dispatched(caplog: pytest.LogCaptureFixture):
    odd = _StubStrategy("Odd", trigger=ScheduleTrigger(kind="on_event", event_name="SomethingHappened"))
    with caplog.at_level(logging.WARNING):
        scheduler = StrategyScheduler(_FakeBus(), strategies=[odd])
    assert "no live dispatch path exists" in caplog.text
    assert odd not in scheduler._every_candle_by_timeframe.get("1m", [])


# --- gate_conditions registration validation (decision #117) ----------------


def _stub_with_gate_conditions(name: str, gate_conditions: dict, **kwargs) -> _StubStrategy:
    """_StubStrategy's own StrategyConfig has no gate_conditions kwarg
    (see its docstring) — build one directly and swap it in, same
    "construct then override .config" shape as no other stub needs
    today."""
    stub = _StubStrategy(name, **kwargs)
    stub.config = StrategyConfig(
        strategy_name=name, version="stub_v1", params={}, gate_conditions=gate_conditions, active_from=_TS
    )
    return stub


def test_construction_raises_for_unrecognized_gate_condition_key():
    """Fails LOUDLY at registration (decision #117), not silently at
    eval time — see gate_conditions.py's own docstring for the full
    reasoning. Deliberately differs from the after_time/on_event
    precedent just above (registered-but-unreachable, no exception)."""
    bad = _stub_with_gate_conditions("Bad", {"vix_min": 20})
    with pytest.raises(ValueError, match="vix_min"):
        StrategyScheduler(_FakeBus(), strategies=[bad])


def test_construction_raises_naming_the_offending_strategy():
    bad = _stub_with_gate_conditions("VixGated", {"vix_min": 20})
    with pytest.raises(ValueError, match="VixGated"):
        StrategyScheduler(_FakeBus(), strategies=[bad])


def test_construction_succeeds_for_the_only_real_condition():
    ok = _stub_with_gate_conditions("OK", {"session": "regular"})
    StrategyScheduler(_FakeBus(), strategies=[ok])  # must not raise


def test_construction_succeeds_for_the_real_seven_strategy_registry():
    """Regression guard: every one of the 7 real strategies' own
    default_config() gate_conditions must stay something this module
    actually recognizes. If a future strategy build adds a new
    gate_conditions key without also teaching gate_conditions.py about
    it, THIS is the test that should start failing, at construction
    time — not a runtime surprise once the app is live."""
    StrategyScheduler(_FakeBus(), strategies=_default_registry(_TS))  # must not raise


# --- _on_features_updated: caching only, never evaluates (pure) -------------


async def test_features_updated_caches_for_a_watched_timeframe():
    scheduler = StrategyScheduler(_FakeBus(), strategies=[_StubStrategy("A")])  # watches "1m"
    await scheduler._on_features_updated(_features_updated_envelope("AAPL", close=123.45))
    cached = scheduler._latest_features[("AAPL", "1m")]
    assert isinstance(cached, FeatureSet)
    assert cached.close == 123.45


async def test_features_updated_ignores_unwatched_timeframe():
    scheduler = StrategyScheduler(_FakeBus(), strategies=[_StubStrategy("A")])  # watches "1m" only
    await scheduler._on_features_updated(_features_updated_envelope("AAPL", timeframe="5m"))
    assert ("AAPL", "5m") not in scheduler._latest_features


async def test_features_updated_ignores_envelope_with_no_symbol():
    scheduler = StrategyScheduler(_FakeBus(), strategies=[_StubStrategy("A")])
    envelope = _features_updated_envelope("AAPL")
    envelope.symbol = None
    await scheduler._on_features_updated(envelope)
    assert scheduler._latest_features == {}


async def test_features_updated_never_calls_evaluate_or_publishes(monkeypatch: pytest.MonkeyPatch):
    """The whole point of splitting the two handlers: FeaturesUpdated is
    cache-only, MarketStateChanged is the real trigger (see module
    docstring)."""
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = StrategyScheduler(fake_bus, strategies=[stub])
    await scheduler._on_features_updated(_features_updated_envelope("AAPL"))
    assert stub.calls == []
    assert fake_bus.published == []


# --- _on_market_state_changed: the real trigger (pure) -----------------------


async def test_market_state_changed_ignores_no_symbol():
    scheduler = StrategyScheduler(_FakeBus(), strategies=[_StubStrategy("A", result=_make_opportunity("A"))])
    envelope = _market_state_changed_envelope("AAPL")
    envelope.symbol = None
    await scheduler._on_market_state_changed(envelope)


async def test_market_state_changed_ignores_cross_symbol_sentinel(monkeypatch: pytest.MonkeyPatch):
    """The synthesized SPY/QQQ/IWM composite — see module docstring's
    'Cross-symbol synthesis is explicitly excluded' section."""
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = StrategyScheduler(fake_bus, strategies=[stub])
    await scheduler._on_features_updated(_features_updated_envelope(_CROSS_SYMBOL_SENTINEL))
    await scheduler._on_market_state_changed(_market_state_changed_envelope(_CROSS_SYMBOL_SENTINEL))
    assert stub.calls == []
    assert fake_bus.published == []


async def test_market_state_changed_ignores_unwatched_timeframe():
    scheduler = StrategyScheduler(_FakeBus(), strategies=[_StubStrategy("A")])  # watches "1m"
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL", timeframe="5m"))


async def test_market_state_changed_skips_when_features_not_yet_cached(monkeypatch: pytest.MonkeyPatch):
    """No prior FeaturesUpdated seen for (symbol, timeframe) — the
    narrow startup-ordering case the module docstring flags."""
    _install_fake_context(monkeypatch, present=True)
    fake_bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = StrategyScheduler(fake_bus, strategies=[stub])
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    assert stub.calls == []
    assert fake_bus.published == []


async def test_market_state_changed_skips_when_context_absent(monkeypatch: pytest.MonkeyPatch):
    _install_fake_context(monkeypatch, present=False)
    fake_bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = StrategyScheduler(fake_bus, strategies=[stub])
    await scheduler._on_features_updated(_features_updated_envelope("AAPL"))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    assert stub.calls == []
    assert fake_bus.published == []


# --- gate_conditions enforcement, per-candle (decision #117) ----------------


async def test_gated_strategy_skipped_when_candle_outside_regular_session(monkeypatch: pytest.MonkeyPatch):
    """The core failure mode this closes (§2b): a strategy declaring
    gate_conditions={"session": "regular"} must not have evaluate()
    called when the triggering candle's own timestamp falls outside
    regular session — against a REAL MarketClock check
    (gate_conditions.py never mocks MarketClock), not a stand-in."""
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    gated = _stub_with_gate_conditions("Gated", {"session": "regular"}, result=_make_opportunity("Gated"))
    scheduler = StrategyScheduler(fake_bus, strategies=[gated])

    await scheduler._on_features_updated(_features_updated_envelope("AAPL", candle_ts=_PRE_MARKET_TS))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL", candle_ts=_PRE_MARKET_TS))

    assert gated.calls == []  # evaluate() never reached — gated out before it
    assert fake_bus.published == []


async def test_gated_strategy_fires_when_candle_inside_regular_session(monkeypatch: pytest.MonkeyPatch):
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    gated = _stub_with_gate_conditions("Gated", {"session": "regular"}, result=_make_opportunity("Gated"))
    scheduler = StrategyScheduler(fake_bus, strategies=[gated])

    # default candle_ts=_TS (10:00 ET) — regular session
    await scheduler._on_features_updated(_features_updated_envelope("AAPL"))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))

    assert len(gated.calls) == 1
    assert len(fake_bus.published) == 1


async def test_strategy_with_no_gate_conditions_never_blocked_by_this_mechanism(
    monkeypatch: pytest.MonkeyPatch,
):
    """Empty gate_conditions (StrategyConfig's own default — no kwarg
    needed here) means no restriction declared, never "block
    everything" — proved against the SAME after-hours candle_ts that
    blocks the gated strategy above, so this isn't just "gating never
    runs," it's "gating correctly does nothing for an ungated
    strategy.\""""
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    ungated = _StubStrategy("Ungated", result=_make_opportunity("Ungated"))  # gate_conditions={} by default
    scheduler = StrategyScheduler(fake_bus, strategies=[ungated])

    await scheduler._on_features_updated(_features_updated_envelope("AAPL", candle_ts=_AFTER_HOURS_TS))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL", candle_ts=_AFTER_HOURS_TS))

    assert len(ungated.calls) == 1
    assert len(fake_bus.published) == 1


async def test_gate_check_is_per_strategy_not_a_blanket_skip(monkeypatch: pytest.MonkeyPatch):
    """Both strategies watch the same candle; only the one declaring
    the session gate is affected by an outside-session candle_ts."""
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    gated = _stub_with_gate_conditions("Gated", {"session": "regular"}, result=_make_opportunity("Gated"))
    ungated = _StubStrategy("Ungated", result=_make_opportunity("Ungated"))
    scheduler = StrategyScheduler(fake_bus, strategies=[gated, ungated])

    await scheduler._on_features_updated(_features_updated_envelope("AAPL", candle_ts=_PRE_MARKET_TS))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL", candle_ts=_PRE_MARKET_TS))

    assert gated.calls == []
    assert len(ungated.calls) == 1
    assert {env.payload["strategy"] for env in fake_bus.published} == {"Ungated"}


def _raising_gate_check(gate_conditions: dict, _candle_ts) -> bool:
    """Stands in for gate_conditions_satisfied() — raises only for a
    strategy that actually declares a condition (broken_gate's
    {"session": "regular"}), not for healthy's empty {} (StrategyConfig's
    own default), so this exercises "one strategy's gate check blows up"
    rather than "every gate check blows up.\""""
    if gate_conditions:
        raise RuntimeError("deliberate gate-check failure for the isolation test")
    return True


async def test_gate_check_exception_does_not_block_other_strategies(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Same isolation guarantee test_one_strategy_raising_does_not_
    block_the_others already proves for evaluate() itself, for the gate
    check: a bug raised while checking one strategy's gate_conditions
    must not stop the strategy after it in the loop."""
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    broken_gate = _stub_with_gate_conditions(
        "BrokenGate", {"session": "regular"}, result=_make_opportunity("BrokenGate")
    )
    healthy = _StubStrategy("Healthy", result=_make_opportunity("Healthy"))
    scheduler = StrategyScheduler(fake_bus, strategies=[broken_gate, healthy])

    import app.strategy_engine.scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "gate_conditions_satisfied", _raising_gate_check)

    await scheduler._on_features_updated(_features_updated_envelope("AAPL"))
    with caplog.at_level(logging.ERROR):
        await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))

    assert broken_gate.calls == []  # gate check raised — evaluate() never reached
    assert len(healthy.calls) == 1  # NOT blocked by broken_gate's gate-check exception
    assert {env.payload["strategy"] for env in fake_bus.published} == {"Healthy"}
    assert "gate_conditions check raised" in caplog.text
    assert "BrokenGate" in caplog.text


async def test_matching_strategy_gets_called_and_publishes(monkeypatch: pytest.MonkeyPatch):
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = StrategyScheduler(fake_bus, strategies=[stub])

    await scheduler._on_features_updated(_features_updated_envelope("AAPL", close=101.5))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL", trend_score=80.0))

    assert len(stub.calls) == 1
    symbol, market_state, features, context = stub.calls[0]
    assert symbol == "AAPL"
    assert isinstance(market_state, MarketState) and market_state.trend_score == 80.0
    assert isinstance(features, FeatureSet) and features.close == 101.5
    assert isinstance(context, ContextChanged) and context.providers == _CONTEXT_DICT["providers"]

    assert len(fake_bus.published) == 1
    published = fake_bus.published[0]
    assert published.event_type == EventType.OPPORTUNITY_CREATED
    assert published.symbol == "AAPL"
    assert published.payload["strategy"] == "A"


async def test_non_matching_strategy_returns_none_and_publishes_nothing(monkeypatch: pytest.MonkeyPatch):
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    stub = _StubStrategy("A", result=None)
    scheduler = StrategyScheduler(fake_bus, strategies=[stub])
    await scheduler._on_features_updated(_features_updated_envelope("AAPL"))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    assert len(stub.calls) == 1
    assert fake_bus.published == []


async def test_one_strategy_raising_does_not_block_the_others(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    broken = _StubStrategy("Broken", raises=True)
    healthy = _StubStrategy("Healthy", result=_make_opportunity("Healthy"))
    scheduler = StrategyScheduler(fake_bus, strategies=[broken, healthy])

    await scheduler._on_features_updated(_features_updated_envelope("AAPL"))
    with caplog.at_level(logging.ERROR):
        await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))

    assert len(broken.calls) == 1
    assert len(healthy.calls) == 1  # NOT skipped despite broken's exception
    assert len(fake_bus.published) == 1
    assert fake_bus.published[0].payload["strategy"] == "Healthy"
    assert "Broken" in caplog.text


async def test_two_matching_strategies_both_publish_independently(monkeypatch: pytest.MonkeyPatch):
    """No dedup/ranking across strategies — Opportunity Engine's job
    (§9), explicitly out of scope here (D10)."""
    _install_fake_context(monkeypatch)
    fake_bus = _FakeBus()
    a = _StubStrategy("A", result=_make_opportunity("A"))
    b = _StubStrategy("B", result=_make_opportunity("B"))
    scheduler = StrategyScheduler(fake_bus, strategies=[a, b])
    await scheduler._on_features_updated(_features_updated_envelope("AAPL"))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    assert {env.payload["strategy"] for env in fake_bus.published} == {"A", "B"}


# --- ContextChanged reconstruction, directly (pure) --------------------------


def test_read_context_returns_none_when_absent(monkeypatch: pytest.MonkeyPatch):
    _install_fake_context(monkeypatch, present=False)
    assert StrategyScheduler._read_context("AAPL") is None


def test_read_context_reconstructs_typed_object_when_present(monkeypatch: pytest.MonkeyPatch):
    _install_fake_context(monkeypatch, present=True)
    context = StrategyScheduler._read_context("AAPL")
    assert isinstance(context, ContextChanged)
    assert context.providers == _CONTEXT_DICT["providers"]


# --- real EventBus + real engines integration (DB-gated) --------------------


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


def _clean_test_symbol(ticker: str) -> None:
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM market_state_history WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
            {"t": ticker},
        )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


class _FakeCalendarProvider(ContextProvider):
    name = "calendar"

    async def evaluate(self) -> dict:
        return {"session": "regular"}


def _install_engine_singletons(market_state_engine: MarketStateEngine, context_engine: ContextEngine) -> None:
    market_state_engine_module._market_state_engine = market_state_engine
    context_engine_module._context_engine = context_engine


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


async def test_scheduler_end_to_end_real_engines_stub_strategy_publishes_opportunity():
    """Regression test for the exact failure this module's docstring
    documents: an earlier version subscribed to FeaturesUpdated directly
    and this test (in that earlier form) caught it never firing, because
    MarketStateEngine's debounced worker hadn't cached anything yet by
    the time the handler ran. Triggering off MarketStateChanged instead
    resolves it — proved here against REAL engines, not a hand-built
    stand-in dict."""
    ticker = "TESTSCH1"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    market_state_engine = MarketStateEngine(bus)
    market_state_engine.start()
    context_engine = ContextEngine(bus, providers=[_FakeCalendarProvider()], symbol_providers=[])
    context_engine.start()
    _install_engine_singletons(market_state_engine, context_engine)

    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = StrategyScheduler(bus, strategies=[stub])
    scheduler.start()

    received: list[EventEnvelope] = []
    bus.subscribe(EventType.OPPORTUNITY_CREATED, lambda env: received.append(env))

    try:
        await context_engine.evaluate_all()
        await context_engine.evaluate_for_symbol(ticker)
        payload = FeatureSet(timeframe="1m", candle_ts=_TS, close=100.0, features={"sma_20_slope_angle": 10.0})
        await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=ticker))
        # MarketStateEngine's debounced worker needs real wall-clock time
        # (compute + asyncio.to_thread persist + cache + publish) before
        # MarketStateChanged fires — this sleep is waiting for THAT event,
        # not guessing at cache timing the way the pre-fix version did.
        await asyncio.sleep(0.6)

        assert len(stub.calls) == 1
        called_symbol, called_market_state, called_features, called_context = stub.calls[0]
        assert called_symbol == ticker
        assert isinstance(called_market_state, MarketState)
        assert isinstance(called_features, FeatureSet)
        assert called_features.close == 100.0  # the real FeaturesUpdated payload, not a reconstruction
        assert isinstance(called_context, ContextChanged)
        assert called_context.providers.get("calendar") == {"session": "regular"}

        assert len(received) == 1
        assert received[0].symbol == ticker
        assert received[0].payload["strategy"] == "A"
    finally:
        await scheduler.stop()
        await context_engine.stop()
        await bus.stop()
        await market_state_engine.stop()
        _clean_test_symbol(ticker)


async def test_scheduler_end_to_end_real_seven_strategies_no_crash_no_fabricated_opportunity():
    """The real _default_registry() — all 7 built strategies — genuinely
    called against a real (but MATCH-failing) candle. Not a claim that
    any strategy's MATCH conditions are satisfied here; only that wiring
    7 real strategies through the full stack doesn't crash and doesn't
    fabricate an Opportunity out of conditions that don't warrant one."""
    ticker = "TESTSCH2"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    market_state_engine = MarketStateEngine(bus)
    market_state_engine.start()
    context_engine = ContextEngine(bus, providers=[_FakeCalendarProvider()], symbol_providers=[])
    context_engine.start()
    _install_engine_singletons(market_state_engine, context_engine)

    scheduler = StrategyScheduler(bus, strategies=_default_registry(_TS))
    scheduler.start()

    received: list[EventEnvelope] = []
    bus.subscribe(EventType.OPPORTUNITY_CREATED, lambda env: received.append(env))

    try:
        await context_engine.evaluate_all()
        await context_engine.evaluate_for_symbol(ticker)
        payload = FeatureSet(timeframe="1m", candle_ts=_TS, close=100.0, features={"sma_20_slope_angle": 1.0})
        await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=ticker))
        await asyncio.sleep(0.6)

        assert received == []  # honest: nothing legitimately fired, nothing fabricated either
    finally:
        await scheduler.stop()
        await context_engine.stop()
        await bus.stop()
        await market_state_engine.stop()
        _clean_test_symbol(ticker)


async def test_scheduler_end_to_end_gate_conditions_blocks_strategy_outside_regular_session():
    """The real-engine proof decision #117's task itself demanded: a
    strategy whose config declares {"session": "regular"} does NOT get
    evaluate() called when the triggering candle's timestamp falls
    outside regular session — against a REAL MarketStateEngine/
    ContextEngine/MarketClock, not a hand-built stand-in for any of
    them. `stub`'s own `result` is a real, well-formed Opportunity — if
    the gate weren't actually enforced here, this test would see it
    published."""
    ticker = "TESTSCH3"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    market_state_engine = MarketStateEngine(bus)
    market_state_engine.start()
    context_engine = ContextEngine(bus, providers=[_FakeCalendarProvider()], symbol_providers=[])
    context_engine.start()
    _install_engine_singletons(market_state_engine, context_engine)

    gated = _stub_with_gate_conditions("Gated", {"session": "regular"}, result=_make_opportunity("Gated"))
    scheduler = StrategyScheduler(bus, strategies=[gated])
    scheduler.start()

    received: list[EventEnvelope] = []
    bus.subscribe(EventType.OPPORTUNITY_CREATED, lambda env: received.append(env))

    try:
        await context_engine.evaluate_all()
        await context_engine.evaluate_for_symbol(ticker)
        premarket_ts = datetime(2026, 8, 10, 7, 0, tzinfo=ZoneInfo("America/New_York"))
        payload = FeatureSet(
            timeframe="1m", candle_ts=premarket_ts, close=100.0, features={"sma_20_slope_angle": 10.0}
        )
        await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=ticker))
        await asyncio.sleep(0.6)

        assert gated.calls == []  # gated out centrally, before evaluate() — real MarketClock, real candle_ts
        assert received == []
    finally:
        await scheduler.stop()
        await context_engine.stop()
        await bus.stop()
        await market_state_engine.stop()
        _clean_test_symbol(ticker)
