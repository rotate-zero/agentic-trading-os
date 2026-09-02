"""
MarketStateEngine tests. This engine persists on every recompute (see
engine module docstring), so like test_level_interaction_engine.py this
file needs real Postgres — skipped as a whole, not failed, if
unreachable, same posture and same `_db_available()` check.

Tests talk to MarketStateEngine directly via a real EventBus, publishing
synthetic FeaturesUpdated events — no FeatureEngine involved, since
FeaturesUpdated is self-contained.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.market_state_engine.engine import MarketStateEngine
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet

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


async def _publish(bus: EventBus, symbol: str, close: float, features: dict[str, float]) -> None:
    payload = FeatureSet(timeframe="1m", candle_ts=_TS, close=close, features=features)
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=symbol))


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


async def test_evaluate_publishes_market_state_changed_with_scored_fields():
    ticker = "TESTMS1"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        received: list = []
        bus.subscribe(EventType.MARKET_STATE_CHANGED, lambda env: received.append(env))

        await _publish(bus, ticker, close=100.0, features={
            "sma_20_slope_angle": 10.0, "atr_14_pct": 2.0, "rvol": 1.5, "vwap": 99.0,
        })
        await asyncio.sleep(0.2)

        assert len(received) == 1
        payload = received[0].payload
        assert payload["trend_score"] == 75.0  # 50 + 10 * (50/20)
        assert payload["acceleration_score"] is None  # first-ever observation for this symbol
        assert received[0].symbol == ticker
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


async def test_second_observation_populates_acceleration():
    ticker = "TESTMS2"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        received: list = []
        bus.subscribe(EventType.MARKET_STATE_CHANGED, lambda env: received.append(env))

        await _publish(bus, ticker, close=100.0, features={"sma_20_slope_angle": 0.0})
        await asyncio.sleep(1.1)  # clear the ~1s debounce floor before the second trigger
        await _publish(bus, ticker, close=100.0, features={"sma_20_slope_angle": 10.0})
        await asyncio.sleep(0.2)

        assert len(received) == 2
        assert received[0].payload["acceleration_score"] is None
        assert received[1].payload["acceleration_score"] is not None
    finally:
        await engine.stop()
        await bus.stop()
        _clean_test_symbol(ticker)


async def test_state_row_persisted_to_market_state_history():
    ticker = "TESTMS3"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        await _publish(bus, ticker, close=100.0, features={"sma_20_slope_angle": 5.0})
        await asyncio.sleep(0.2)
    finally:
        await engine.stop()
        await bus.stop()

    session = SessionLocal()
    try:
        row = session.execute(
            text(
                "SELECT trend_score FROM market_state_history msh "
                "JOIN symbols s ON s.id = msh.symbol_id WHERE s.ticker = :t"
            ),
            {"t": ticker},
        ).fetchone()
        assert row is not None
        assert float(row[0]) == 62.5  # 50 + 5 * (50/20)
    finally:
        session.close()
    _clean_test_symbol(ticker)


async def test_stop_drains_an_in_flight_worker_cycle_before_returning():
    """
    Same shutdown-safety guarantee as LevelInteractionEngine's own
    `test_stop_waits_for_an_in_flight_persist_before_returning` (decision
    #84), and reusing that test's exact two techniques, for the same two
    reasons documented there:

    - `_persist` wrapped with an artificial delay so a write is
      GUARANTEED to still be running in the executor thread at the
      moment `stop()` is called — proves the guarantee causally (stop()
      measurably blocks for it) rather than merely observing no crash,
      which a fast write could pass by luck even with the old
      cancel()-based bug.
    - Fed straight onto `engine._queue` (with `_latest_features` seeded
      to match what `_on_features_updated` would have set) rather than
      published through the Bus — publishing and immediately calling
      `stop()` races the poison pill into this engine's own queue AHEAD
      of the real item often enough to make the test flaky against
      correct code, an unrelated Bus-dispatch race this test isn't
      meant to be about.
    """
    ticker = "TESTMS4"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)

    delay_seconds = 0.3
    original_persist = engine._persist

    def _slow_persist(*args, **kwargs):
        import time as _time
        _time.sleep(delay_seconds)  # runs inside the executor thread — a real blocking delay, not a mock
        return original_persist(*args, **kwargs)

    engine._persist = _slow_persist  # type: ignore[method-assign]
    engine.start()

    try:
        engine._latest_features[ticker] = {
            "timeframe": "1m", "candle_ts": _TS, "close": 100.0, "features": {"sma_20_slope_angle": 5.0},
        }
        engine._queue.put_nowait(ticker)
        # Give the worker task a real chance to dequeue the item and get
        # asyncio.to_thread's executor submission actually running (into
        # the artificial 0.3s sleep) before stop() is called.
        await asyncio.sleep(0.05)

        t0 = time.monotonic()
        await asyncio.wait_for(engine.stop(), timeout=2.0)
        elapsed = time.monotonic() - t0

        # Generous floor, not delay_seconds itself — same reasoning as
        # LevelInteractionEngine's own version of this assertion.
        assert elapsed >= 0.15, f"stop() returned after {elapsed:.3f}s — expected it to block for the in-flight write"

        session = SessionLocal()
        try:
            row = session.execute(
                text(
                    "SELECT 1 FROM market_state_history msh "
                    "JOIN symbols s ON s.id = msh.symbol_id WHERE s.ticker = :t"
                ),
                {"t": ticker},
            ).fetchone()
            assert row is not None
        finally:
            session.close()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)
