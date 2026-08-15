"""
CandleRecorder + candle_store integration test — run against a REAL local
Postgres, not mocked, per this project's own established verification
standard (confirmed decisions #34, #37, #38 all did the same for their
respective real-process/real-DB claims). Requires the DB configured in
app.core.config's defaults (postgres_host=localhost etc.) to actually be
reachable with the `candles`/`symbols` schema already migrated
(`alembic upgrade head`) — skipped automatically, not failed, if it isn't,
since CI/a fresh clone won't have Postgres running by default and that's
a legitimate, expected state, not a broken one.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed
from app.services import candle_store
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


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


@pytest.fixture(autouse=True)
def _clean_test_rows():
    """Deletes only the symbol this test uses, before and after — doesn't
    touch anything else that might be in the DB from real app usage."""

    def _delete():
        session = SessionLocal()
        try:
            session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = '__TEST_ZZZZ__')"))
            session.execute(text("DELETE FROM symbols WHERE ticker = '__TEST_ZZZZ__'"))
            session.commit()
        finally:
            session.close()

    _delete()
    yield
    _delete()


@pytest.mark.asyncio
async def test_candle_recorder_persists_and_candle_store_reads_it_back():
    bus = EventBus()
    await bus.start()
    recorder = CandleRecorder(bus)
    recorder.start()
    try:
        candle_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=1)
        envelope = make_envelope(
            EventType.CANDLE_CLOSED,
            CandleClosed(timeframe="1m", open=100.0, high=101.5, low=99.5, close=100.75, volume=1234, candle_ts=candle_ts),
            symbol="__TEST_ZZZZ__",
        )
        await bus.publish(envelope)

        # Give the bus's consume loop + the recorder's own decoupled writer
        # queue time to actually land the row — both are real asyncio
        # tasks, not something a single await resolves synchronously.
        await asyncio.sleep(0.3)

        recorded = candle_store.get_recorded_candles(
            "__TEST_ZZZZ__", "1m", candle_ts - timedelta(minutes=1), candle_ts + timedelta(minutes=1)
        )
        assert len(recorded) == 1
        assert recorded[0].open == 100.0
        assert recorded[0].high == 101.5
        assert recorded[0].low == 99.5
        assert recorded[0].close == 100.75
        assert recorded[0].volume == 1234
        assert recorded[0].candle_ts == candle_ts
    finally:
        await recorder.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_duplicate_candle_closed_does_not_raise_or_duplicate_the_row():
    """Same guard tick_ingest.py itself accepts as a known rare race
    (confirmed decision #42) — a second CandleClosed for the same minute
    must not crash the recorder or produce two rows."""
    bus = EventBus()
    await bus.start()
    recorder = CandleRecorder(bus)
    recorder.start()
    try:
        candle_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=1)
        payload = CandleClosed(timeframe="1m", open=1.0, high=2.0, low=0.5, close=1.5, volume=10, candle_ts=candle_ts)
        for _ in range(2):
            await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol="__TEST_ZZZZ__"))
        await asyncio.sleep(0.3)

        recorded = candle_store.get_recorded_candles(
            "__TEST_ZZZZ__", "1m", candle_ts - timedelta(minutes=1), candle_ts + timedelta(minutes=1)
        )
        assert len(recorded) == 1
    finally:
        await recorder.stop()
        await bus.stop()


def test_get_recorded_candles_returns_empty_for_a_never_seen_symbol():
    result = candle_store.get_recorded_candles(
        "__TEST_ZZZZ__", "1m", datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc)
    )
    assert result == []
