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


def _clean_market_state_history_only(ticker: str) -> None:
    """Deletes only `market_state_history` rows for `ticker`, leaving
    the `symbols` row itself alone. Needed for "SPY" specifically:
    migration 0004 permanently seeds it into `symbols` (and
    `scanner_universe_symbols`, which has its own FK to `symbols.id`),
    so `_clean_test_symbol`'s unconditional `DELETE FROM symbols` throws
    a real ForeignKeyViolation for it — found via actually running these
    tests against real Postgres, not assumed. "QQQ"/"IWM" aren't seeded,
    so they use the regular `_clean_test_symbol` (full cleanup,
    including the `symbols` row itself)."""
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM market_state_history WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
            {"t": ticker},
        )
        session.commit()
    finally:
        session.close()


def _clean_cross_symbol_test_state() -> None:
    """Full cleanup for one cross-symbol test: market_state_history rows
    for SPY/QQQ/IWM/__MARKET__, plus the QQQ/IWM/__MARKET__ symbols rows
    themselves (SPY's symbols row is permanently seeded — see
    _clean_market_state_history_only)."""
    _clean_market_state_history_only("SPY")
    _clean_test_symbol("QQQ")
    _clean_test_symbol("IWM")
    _clean_test_symbol("__MARKET__")


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
        engine._latest_features[(ticker, "1m")] = {
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


# --- Cross-symbol synthesis (CrossSymbolState, M3, decision #97) -----------
#
# Real "SPY"/"QQQ"/"IWM" tickers are required here — _CROSS_SYMBOL_TICKERS
# (engine.py) is hardcoded to those exact strings, unlike the per-symbol
# tests above which can use any disposable TESTMS* ticker. Cleaned up
# before/after each test, same convention as _clean_test_symbol elsewhere
# in this file — safe against a real dev DB since nothing else in the
# suite persists rows for these tickers (test_scanner_runner.py's own use
# of "SPY" is a fake in-memory FeatureEngine, no DB writes at all).


async def test_cross_symbol_state_not_synthesized_until_all_three_report():
    _clean_cross_symbol_test_state()
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        received: list = []
        bus.subscribe(EventType.MARKET_STATE_CHANGED, lambda env: received.append(env))

        await _publish(bus, "SPY", close=100.0, features={"sma_20_slope_angle": 5.0})
        await asyncio.sleep(0.2)
        await _publish(bus, "QQQ", close=100.0, features={"sma_20_slope_angle": 8.0})
        await asyncio.sleep(0.2)

        # Only SPY and QQQ have reported — no __MARKET__ envelope yet,
        # just the two per-symbol ones.
        assert [env.symbol for env in received] == ["SPY", "QQQ"]
    finally:
        await engine.stop()
        await bus.stop()
        _clean_cross_symbol_test_state()


async def test_cross_symbol_state_synthesizes_once_all_three_report():
    _clean_cross_symbol_test_state()
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        received: list = []
        bus.subscribe(EventType.MARKET_STATE_CHANGED, lambda env: received.append(env))

        await _publish(bus, "SPY", close=100.0, features={"sma_20_slope_angle": 10.0})   # trend_score 75
        await asyncio.sleep(0.2)
        await _publish(bus, "QQQ", close=100.0, features={"sma_20_slope_angle": 16.0})   # trend_score 90
        await asyncio.sleep(0.2)
        await _publish(bus, "IWM", close=100.0, features={"sma_20_slope_angle": 4.0})    # trend_score 60
        await asyncio.sleep(0.2)

        symbols_received = [env.symbol for env in received]
        assert symbols_received == ["SPY", "QQQ", "IWM", "__MARKET__"], symbols_received

        cross_payload = received[-1].payload
        assert cross_payload["spy_direction_score"] == pytest.approx(75.0)
        assert cross_payload["qqq_direction_score"] == pytest.approx(90.0)
        assert cross_payload["iwm_direction_score"] == pytest.approx(60.0)
        # trend_alignment_score = 100 - spread(75,90,60)/60*100 = 100 - 30/60*100 = 50
        assert cross_payload["trend_alignment_score"] == pytest.approx(50.0)
        # risk_on_score = 50 + ((90+60)/2 - 75) * (50/30) = 50 + 0 = 50
        assert cross_payload["risk_on_score"] == pytest.approx(50.0)
        # qqq_leadership_score = 50 + (90-75)*(50/30) = 75
        assert cross_payload["qqq_leadership_score"] == pytest.approx(75.0)
        # iwm_confirmation_score = 100 - |60-(75+90)/2|/30*100 = 100 - |60-82.5|/30*100 = 25
        assert cross_payload["iwm_confirmation_score"] == pytest.approx(25.0)
    finally:
        await engine.stop()
        await bus.stop()
        _clean_cross_symbol_test_state()


async def test_cross_symbol_state_persists_sentinel_row_with_correct_shape():
    _clean_cross_symbol_test_state()
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        await _publish(bus, "SPY", close=100.0, features={"sma_20_slope_angle": 0.0})
        await asyncio.sleep(0.2)
        await _publish(bus, "QQQ", close=100.0, features={"sma_20_slope_angle": 0.0})
        await asyncio.sleep(0.2)
        await _publish(bus, "IWM", close=100.0, features={"sma_20_slope_angle": 0.0})
        await asyncio.sleep(0.2)
    finally:
        await engine.stop()
        await bus.stop()

    session = SessionLocal()
    try:
        row = session.execute(
            text(
                "SELECT trend_score, spy_direction_score, qqq_direction_score, "
                "iwm_direction_score, trend_alignment_score FROM market_state_history msh "
                "JOIN symbols s ON s.id = msh.symbol_id WHERE s.ticker = '__MARKET__'"
            )
        ).fetchone()
        assert row is not None
        # Two-row-shape assertion, verified at the data layer too, not just
        # in-process: the sentinel row's per-symbol group is NULL, its
        # cross-symbol group is populated (models/market_state.py's
        # docstring; engine.py's write-time assertion is what enforces
        # this on the way in).
        assert row[0] is None  # trend_score
        assert row[1] is not None  # spy_direction_score
        assert row[2] is not None  # qqq_direction_score
        assert row[3] is not None  # iwm_direction_score
        assert row[4] == pytest.approx(100.0)  # perfect alignment — all three at trend_score 50
    finally:
        session.close()
    _clean_cross_symbol_test_state()


async def test_spy_qqq_iwm_use_tighter_max_interval_than_ordinary_symbols():
    """Confirms the tighter debounce ceiling (§4: ~3-5s vs ~10s) is
    actually wired up per-scheduler, not just documented — same style as
    reading engine internals directly that this file's other tests
    already use (e.g. engine._queue in the stop() test)."""
    from app.market_state_engine.engine import _CROSS_SYMBOL_MAX_INTERVAL_SECONDS, _MAX_INTERVAL_SECONDS

    _clean_market_state_history_only("SPY")
    _clean_test_symbol("TESTMS5")
    bus = EventBus()
    await bus.start()
    engine = MarketStateEngine(bus)
    engine.start()
    try:
        await _publish(bus, "SPY", close=100.0, features={"sma_20_slope_angle": 0.0})
        await _publish(bus, "TESTMS5", close=100.0, features={"sma_20_slope_angle": 0.0})
        await asyncio.sleep(0.1)

        assert engine._schedulers["SPY"]._max_interval == _CROSS_SYMBOL_MAX_INTERVAL_SECONDS
        assert engine._schedulers["TESTMS5"]._max_interval == _MAX_INTERVAL_SECONDS
        assert _CROSS_SYMBOL_MAX_INTERVAL_SECONDS < _MAX_INTERVAL_SECONDS
    finally:
        await engine.stop()
        await bus.stop()
        _clean_market_state_history_only("SPY")
        _clean_test_symbol("TESTMS5")
