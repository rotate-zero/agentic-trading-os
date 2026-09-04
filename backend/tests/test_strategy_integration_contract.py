"""
M4 — Market State + Context integration contract (decision #98).

This file is the "minimum integration/test harness" M4 task 22 asked
for: proof that a future ORB Strategy (or any Strategy Engine consumer)
can (a) subscribe to `MarketStateChanged`/`ContextChanged` on the Event
Bus and (b) independently call `MarketStateEngine.get_snapshot()`/
`ContextEngine.get_snapshot()` for a synchronous "current state" read,
and get a CONSISTENT answer from both paths. It deliberately does not
build `Strategy(ABC)`, `StrategyConfig`, `Opportunity`, or any
GATE/MATCH/SCORE/PROPOSE logic — none of that exists yet, on purpose
(strategy-engine-design.md §10's own staged plan; Strategy Engine
Stage 1 is separate, later work). "A future ORB consumer" below means
exactly that: a plain test subscriber standing in for one, nothing
strategy-specific.

Also covers:
  - task 23 — `app/trading_intelligence/state_snapshot.py`'s capture
    functions, exercised against real (not stubbed) engine output.
  - task 24 — a concrete compatibility check: a domain timestamp
    (`candle_ts`, sourced from the synthetic FeaturesUpdated payload,
    never `datetime.now()`) survives untouched through compute, cache,
    and `get_snapshot()`. This is the specific property a future Replay
    Engine depends on — see module docstrings on both engines'
    `get_snapshot()` for the fuller reasoning, including the honest
    limitation that Context Engine's own `evaluated_at` is wall-clock,
    not domain-safe, because Context Engine's cadence is timer-driven,
    not candle-driven (a pre-existing characteristic, unchanged here).

Needs real Postgres (MarketStateEngine persists on every recompute,
same as test_market_state_engine.py) — skipped as a whole, not failed,
if unreachable, same posture as that file.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

import app.context_engine.engine as context_engine_module
import app.market_state_engine.engine as market_state_engine_module
from app.context_engine.engine import ContextEngine, get_context_engine
from app.context_engine.provider import ContextProvider, SymbolContextProvider
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.market_state_engine.engine import MarketStateEngine, get_market_state_engine
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet
from app.trading_intelligence.state_snapshot import (
    capture_context_snapshot,
    capture_market_state_snapshot,
    capture_strategy_outcome_snapshots,
)

_TS = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)


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


def _clean_market_state_history_only(ticker: str) -> None:
    """SPY is permanently seeded into `symbols` (migration 0004) — same
    situation and same fix as test_market_state_engine.py's own helper
    of the same name."""
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM market_state_history WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
            {"t": ticker},
        )
        session.commit()
    finally:
        session.close()


def _clean_cross_symbol_rows() -> None:
    _clean_market_state_history_only("SPY")
    _clean_test_symbol("QQQ")
    _clean_test_symbol("IWM")
    _clean_test_symbol("__MARKET__")


async def _publish_features(bus: EventBus, symbol: str, close: float, features: dict[str, float]) -> None:
    payload = FeatureSet(timeframe="1m", candle_ts=_TS, close=close, features=features)
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=symbol))


class _FakeProvider(ContextProvider):
    """Market-wide fake — avoids CalendarProvider's real MarketClock
    dependency, same reasoning test_context_engine.py's own fakes use."""

    def __init__(self, name: str, output: dict) -> None:
        self.name = name
        self._output = output

    async def evaluate(self) -> dict:
        return self._output


class _FakeSymbolProvider(SymbolContextProvider):
    """Per-symbol fake — avoids FundamentalsProvider/NewsFlagProvider's
    real Finnhub network calls, same reasoning as above."""

    def __init__(self, name: str, output_by_symbol: dict[str, dict]) -> None:
        self.name = name
        self._output_by_symbol = output_by_symbol

    async def evaluate(self, symbol: str) -> dict:
        return self._output_by_symbol.get(symbol, {})


def _install_context_engine_singleton(engine: ContextEngine) -> None:
    """Bypasses get_context_engine()'s lazy-init default providers (real
    Finnhub-backed FundamentalsProvider/NewsFlagProvider) so
    state_snapshot.py's calls to the singleton — the exact code path a
    real future caller would use — resolve to THIS test's fake-provider
    instance instead. conftest.py's autouse `_reset_app_singletons`
    fixture already resets this module global to None before/after
    every test, so no manual cleanup is needed beyond that."""
    context_engine_module._context_engine = engine


def _install_market_state_engine_singleton(engine: MarketStateEngine) -> None:
    """Same reasoning as _install_context_engine_singleton, for
    MarketStateEngine — get_market_state_engine() takes no fake-friendly
    constructor args to intercept, so the module global is set directly."""
    market_state_engine_module._market_state_engine = engine


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


# --- MarketStateEngine.get_snapshot() -------------------------------------------


async def test_market_state_snapshot_matches_the_published_event():
    ticker = "TESTSIC1"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        received: list = []
        bus.subscribe(EventType.MARKET_STATE_CHANGED, lambda env: received.append(env))

        await _publish_features(bus, ticker, close=100.0, features={"sma_20_slope_angle": 10.0})
        await asyncio.sleep(0.2)

        assert len(received) == 1
        event_payload = received[0].payload
        snapshot = engine.get_snapshot(ticker)

        assert ticker in snapshot["symbols"]
        snap_state = snapshot["symbols"][ticker]
        # A synchronous get_snapshot() read must describe EXACTLY the
        # same state the async subscriber already received — the core
        # property a Strategy MATCH stage depends on regardless of
        # which of the two paths it happens to use.
        assert snap_state["trend_score"] == event_payload["trend_score"]
        # make_envelope serializes with model_dump(mode="json") (event_bus/events.py),
        # so event_payload["candle_ts"] is already an ISO string here, same as
        # get_snapshot()'s own — a plain string equality, not a type-juggled one.
        assert snap_state["candle_ts"] == event_payload["candle_ts"]
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


async def test_market_state_snapshot_carries_the_market_composite_for_any_symbol():
    """The cross-symbol composite isn't scoped to the requested symbol —
    asking for an unrelated symbol's snapshot still surfaces the
    current broad-market read, same reasoning ORB's own MATCH stage
    would eventually need both without a second call."""
    other_ticker = "TESTSIC2"
    _clean_test_symbol(other_ticker)
    _clean_cross_symbol_rows()
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        assert engine.get_snapshot(other_ticker)["market"] is None  # honest: not synthesized yet

        await _publish_features(bus, "SPY", close=100.0, features={"sma_20_slope_angle": 10.0})
        await asyncio.sleep(0.2)
        await _publish_features(bus, "QQQ", close=100.0, features={"sma_20_slope_angle": 16.0})
        await asyncio.sleep(0.2)
        await _publish_features(bus, "IWM", close=100.0, features={"sma_20_slope_angle": 4.0})
        await asyncio.sleep(0.2)

        snapshot = engine.get_snapshot(other_ticker)
        assert snapshot["symbols"] == {}  # never computed for this ticker — honest, not fabricated
        assert snapshot["market"] is not None
        assert snapshot["market"]["spy_direction_score"] == pytest.approx(75.0)
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(other_ticker)
        _clean_cross_symbol_rows()


async def test_market_state_snapshot_is_honest_about_a_symbol_never_computed():
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    try:
        snapshot = engine.get_snapshot("NEVERSEEN")
        assert snapshot["symbols"] == {}
    finally:
        await bus.stop()


# --- ContextEngine.get_snapshot() -----------------------------------------------


async def test_context_snapshot_merges_global_and_per_symbol_providers():
    ticker = "TESTSIC3"
    bus = EventBus()
    await bus.start()
    engine = ContextEngine(
        bus,
        providers=[_FakeProvider("calendar", {"session": "regular"})],
        symbol_providers=[_FakeSymbolProvider("fundamentals", {ticker: {"market_cap": 5_000_000_000}})],
    )
    try:
        await engine.evaluate_all()
        await engine.evaluate_for_symbol(ticker)

        snapshot = engine.get_snapshot(ticker)
        assert snapshot["symbols"][ticker]["providers"] == {
            "calendar": {"session": "regular"},
            "fundamentals": {"market_cap": 5_000_000_000},
        }
        # global is always present on its own too, independent of symbol
        assert snapshot["global"]["providers"] == {"calendar": {"session": "regular"}}
    finally:
        await bus.stop()


async def test_context_snapshot_is_honest_about_a_symbol_never_evaluated():
    bus = EventBus()
    await bus.start()
    engine = ContextEngine(bus, providers=[_FakeProvider("calendar", {"session": "regular"})], symbol_providers=[])
    try:
        await engine.evaluate_all()
        snapshot = engine.get_snapshot("NEVERSEEN")
        assert snapshot["symbols"] == {}  # global evaluated; per-symbol never was — not conflated
    finally:
        await bus.stop()


# --- the actual integration proof (M4 task 22) -----------------------------------


async def test_a_future_orb_consumer_can_read_both_engines_consistently():
    """Stands in for "a future ORB Strategy" without building one: a
    plain subscriber to both MarketStateChanged and ContextChanged,
    running against both real engines on one shared bus — the same
    wiring shape main.py uses in production — that ALSO independently
    calls both engines' get_snapshot(). Both paths must agree. This is
    the concrete proof task 22 asked for."""
    ticker = "TESTSIC4"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    market_engine = MarketStateEngine(bus)
    context_engine = ContextEngine(
        bus,
        providers=[_FakeProvider("calendar", {"session": "regular"})],
        symbol_providers=[_FakeSymbolProvider("news", {ticker: {"present": True, "count_15m": 2}})],
    )
    market_engine.start()
    try:
        market_events: list = []
        context_events: list = []
        bus.subscribe(EventType.MARKET_STATE_CHANGED, lambda env: market_events.append(env))
        bus.subscribe(EventType.CONTEXT_CHANGED, lambda env: context_events.append(env))

        await _publish_features(bus, ticker, close=100.0, features={"sma_20_slope_angle": 8.0})
        await context_engine.evaluate_for_symbol(ticker)
        await asyncio.sleep(0.2)

        # Path 1: event-driven — a consumer that only ever subscribes.
        assert any(env.symbol == ticker for env in market_events)
        assert any(env.symbol == ticker for env in context_events)

        # Path 2: synchronous read — a consumer whose trigger fires
        # independent of the last publish (e.g. ORB's own
        # after_time("09:30") schedule) and needs "current state right
        # now" rather than "whatever the last event happened to carry."
        market_snapshot = market_engine.get_snapshot(ticker)
        context_snapshot = context_engine.get_snapshot(ticker)

        assert ticker in market_snapshot["symbols"]
        assert ticker in context_snapshot["symbols"]

        # Both paths describe the same underlying state.
        market_event_payload = next(env.payload for env in market_events if env.symbol == ticker)
        assert market_snapshot["symbols"][ticker]["trend_score"] == market_event_payload["trend_score"]
        assert context_snapshot["symbols"][ticker]["providers"]["news"] == {"present": True, "count_15m": 2}
    finally:
        await market_engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


# --- state_snapshot.py contract (M4 task 23) -------------------------------------


async def test_capture_market_state_snapshot_returns_real_data():
    ticker = "TESTSIC5"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    _install_market_state_engine_singleton(engine)
    engine.start()
    try:
        assert capture_market_state_snapshot(ticker) is None  # honest: nothing computed yet

        await _publish_features(bus, ticker, close=100.0, features={"sma_20_slope_angle": 12.0})
        await asyncio.sleep(0.2)

        captured = capture_market_state_snapshot(ticker)
        assert captured is not None
        assert captured["trend_score"] == pytest.approx(80.0)  # 50 + 12 * (50/20)
        assert "market" in captured  # nested cross-symbol composite key always present, even if None
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


async def test_capture_context_snapshot_returns_real_data():
    ticker = "TESTSIC6"
    bus = EventBus()
    await bus.start()
    engine = ContextEngine(
        bus,
        providers=[],
        symbol_providers=[_FakeSymbolProvider("fundamentals", {ticker: {"sector": None, "market_cap": 1_000}})],
    )
    _install_context_engine_singleton(engine)
    try:
        assert capture_context_snapshot(ticker) is None  # honest: never evaluated yet

        await engine.evaluate_for_symbol(ticker)

        captured = capture_context_snapshot(ticker)
        assert captured == {"fundamentals": {"sector": None, "market_cap": 1_000}}
    finally:
        await bus.stop()


async def test_capture_strategy_outcome_snapshots_is_honest_for_an_unknown_symbol():
    """Neither engine has ever heard of this symbol — both halves of the
    capture must be None, never a fabricated default (strategy-engine-
    design.md §11)."""
    bus = EventBus()
    await bus.start()
    market_engine = MarketStateEngine(bus)
    context_engine = ContextEngine(bus, providers=[], symbol_providers=[])
    _install_market_state_engine_singleton(market_engine)
    _install_context_engine_singleton(context_engine)
    try:
        result = capture_strategy_outcome_snapshots("NEVERSEEN")
        assert result.market_state is None
        assert result.context is None
    finally:
        await bus.stop()


# --- compatibility constraint (M4 task 24) ---------------------------------------


async def test_candle_ts_survives_as_domain_time_not_wall_clock():
    """The one concrete property strategy-engine-design.md §7 requires:
    a strategy (and anything it reads) must be able to trust the
    payload's own domain timestamp rather than the moment it happened
    to be processed. `_TS` here is fixed years in the past — if
    get_snapshot() ever silently substituted `datetime.now()`, this
    assertion would fail immediately."""
    ticker = "TESTSIC7"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        await _publish_features(bus, ticker, close=100.0, features={"sma_20_slope_angle": 1.0})
        await asyncio.sleep(0.2)

        snapshot = engine.get_snapshot(ticker)
        # Compare as parsed datetimes, not raw strings — pydantic's
        # model_dump(mode="json") renders UTC with a "Z" suffix, Python's
        # own .isoformat() renders "+00:00"; same instant, different
        # spelling. The property under test is the VALUE surviving
        # untouched, not string formatting.
        snapshot_ts = datetime.fromisoformat(snapshot["symbols"][ticker]["candle_ts"].replace("Z", "+00:00"))
        assert snapshot_ts == _TS
        assert datetime.now(timezone.utc).year - _TS.year >= 0  # sanity: _TS is genuinely not "now"
        assert abs((datetime.now(timezone.utc) - _TS).total_seconds()) > 60  # not coincidentally close to real now
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)
