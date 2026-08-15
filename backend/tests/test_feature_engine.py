"""
FeatureEngine tests, in three tiers:

1. Pure math (indicators.sma) — no DB, no event loop, always runs.
2. In-memory accumulation through a real EventBus — no DB required, since
   FeatureEngine only needs a DB read on the very first candle it sees for
   a never-before-seen symbol, and this tier deliberately stays inside
   that first candle's warm-up window. Always runs.
3. DB-backed cold-start backfill ("restart survival") — run against a REAL
   local Postgres, not mocked, same standard as test_candle_recorder.py
   (confirmed decisions #34, #37, #38). Skipped, not failed, if Postgres
   isn't reachable, same reasoning as test_candle_recorder.py's own skip.
   Only this specific test is decorated with the skip — the rest of this
   file needs no DB at all.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.feature_engine.engine import FeatureEngine
from app.feature_engine.indicators import sma
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed
from app.services.candle_recorder import CandleRecorder


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — this IS the availability check
        return False


def _clean_test_symbol(ticker: str) -> None:
    session = SessionLocal()
    try:
        session.execute(text(f"DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = '{ticker}')"))
        session.execute(text(f"DELETE FROM symbols WHERE ticker = '{ticker}'"))
        session.commit()
    finally:
        session.close()


async def _publish_candle(bus: EventBus, symbol: str, candle_ts: datetime, close: float, timeframe: str = "1m") -> None:
    payload = CandleClosed(timeframe=timeframe, open=close, high=close, low=close, close=close, volume=10, candle_ts=candle_ts)
    await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=symbol))


# --- Tier 1: pure math ------------------------------------------------------


def test_sma_computes_mean_of_last_n_closes():
    assert sma([1.0, 2.0, 3.0], 3) == 2.0
    assert sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 4.0  # uses only the most recent 3


def test_sma_returns_none_during_warmup_not_zero_or_error():
    assert sma([1.0, 2.0], 3) is None


def test_sma_rejects_nonpositive_period():
    with pytest.raises(ValueError):
        sma([1.0, 2.0, 3.0], 0)


# --- Tier 2: in-memory accumulation, no DB needed ---------------------------


@pytest.mark.asyncio
async def test_feature_engine_publishes_once_warmed_up_and_not_before():
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[3])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        closes = [100.0, 102.0, 104.0]  # SMA(3) on the 3rd close = 102.0
        for i, close in enumerate(closes):
            await _publish_candle(bus, "__TEST_FE_MEM__", base_ts + timedelta(minutes=i), close)
            await asyncio.sleep(0.05)

        assert len(received) == 1  # only the 3rd close had enough history — no premature publish
        features = received[0].payload["features"]
        assert features["sma_3"] == 102.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_feature_engine_ignores_non_1m_timeframes():
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, "__TEST_FE_5M__", datetime.now(timezone.utc), 100.0, timeframe="5m")
        await asyncio.sleep(0.1)
        assert received == []
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_get_snapshot_reflects_latest_computed_values():
    """
    Pure in-memory — sma_periods=[1] deliberately: SMA(1) is just the
    current close, and max_period=1 is the one case _compute_one() skips
    the cold-start DB backfill entirely (`if self._max_period > 1`), so
    this proves get_snapshot()'s own shape/filtering logic in isolation
    from anything DB-related (that's covered separately, with real
    Postgres, in the cold-start test above).
    """
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1])
    engine.start()

    try:
        await _publish_candle(bus, "__TEST_FE_SNAP_A__", datetime.now(timezone.utc), 100.0)
        await _publish_candle(bus, "__TEST_FE_SNAP_B__", datetime.now(timezone.utc), 200.0)
        await asyncio.sleep(0.1)

        full = engine.get_snapshot()
        assert full["__TEST_FE_SNAP_A__"]["1m"]["features"]["sma_1"] == 100.0
        assert full["__TEST_FE_SNAP_B__"]["1m"]["features"]["sma_1"] == 200.0

        filtered = engine.get_snapshot(symbol="__TEST_FE_SNAP_A__")
        assert set(filtered.keys()) == {"__TEST_FE_SNAP_A__"}  # B excluded when filtering by A

        assert engine.get_snapshot(symbol="__TEST_FE_NEVER_PUBLISHED__") == {}
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_feature_engine_drops_duplicate_candle_closed():
    """Mirrors tick_ingest.py's own accepted rare-duplicate race (confirmed
    decision #42) — a repeated CandleClosed for the same minute must not
    double-count that close into the SMA window."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[3])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts, 100.0)
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts, 100.0)  # exact duplicate
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts + timedelta(minutes=1), 102.0)
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts + timedelta(minutes=2), 104.0)
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[-1].payload["features"]["sma_3"] == 102.0  # (100+102+104)/3, not double-counted
    finally:
        await engine.stop()
        await bus.stop()


# --- Tier 3: DB-backed cold-start backfill ("restart survival") ------------


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_feature_engine_backfills_from_persisted_history_on_cold_start():
    """
    Simulates a real restart: prior candles already sit in Postgres
    (written by a normal CandleRecorder in an earlier 'process lifetime'),
    then a BRAND NEW FeatureEngine instance — no in-memory window for this
    symbol yet — sees its first CandleClosed and must backfill from
    persisted history to publish a correct SMA immediately, rather than
    silently resetting to a multi-candle warm-up as if it were a new
    symbol. Same "rebuild from persisted history on startup" requirement
    already decided for Market State Engine (trading-intelligence-
    architecture.md §4), applied here.
    """
    ticker = "__TEST_FE_COLD__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)

    try:
        # Phase 1: an earlier "process" persists 2 prior closes via a real CandleRecorder.
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            await _publish_candle(bus, ticker, base_ts, 100.0)
            await _publish_candle(bus, ticker, base_ts + timedelta(minutes=1), 102.0)
            await asyncio.sleep(0.3)  # let the write-behind writer actually land both rows
        finally:
            await recorder.stop()
        # Phase 2: a FRESH FeatureEngine — no in-memory window for this symbol —
        # simulating what a real restart looks like.
        engine = FeatureEngine(bus, sma_periods=[3])
        engine.start()
        received: list = []
        bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

        try:
            await _publish_candle(bus, ticker, base_ts + timedelta(minutes=2), 104.0)
            await asyncio.sleep(0.2)

            assert len(received) == 1  # correct on the FIRST event after cold start, no re-warm-up needed
            assert received[0].payload["features"]["sma_3"] == 102.0
        finally:
            await engine.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
def test_get_recent_closes_returns_empty_for_a_never_seen_symbol():
    from app.services import candle_store

    result = candle_store.get_recent_closes("__TEST_FE_NEVER_SEEN__", "1m", datetime.now(timezone.utc), limit=10)
    assert result == []
