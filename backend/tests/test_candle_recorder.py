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
import time
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


def _clean_stoprace_symbol() -> None:
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = '__CR_STOPRACE__')"))
        session.execute(text("DELETE FROM symbols WHERE ticker = '__CR_STOPRACE__'"))
        session.commit()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_stop_waits_for_an_in_flight_write_before_returning():
    """
    Flaky-test-cluster root-cause pass, following on from decision #84 —
    same shape as `test_level_interaction_engine.py`'s own
    `test_stop_waits_for_an_in_flight_persist_before_returning`, applied
    here since `CandleRecorder.stop()` shared the exact `task.cancel()` +
    `await task` pattern decision #84 proved doesn't actually wait for an
    in-flight `asyncio.to_thread` write to finish, but was left unfixed at
    the time (flagged there as "very likely" carrying the identical bug).

    `_write_one` is wrapped with an artificial delay so a write is
    GUARANTEED to still be running in the executor thread at the exact
    moment `stop()` is called — no `asyncio.sleep` between enqueue and
    stop, which is precisely the gap that let this bug hide before.

    Two things prove the fix, not just the absence of a crash:
    - `stop()` itself must take at least as long as (most of) the
      artificial delay — if it returned quickly, that would mean it's
      still cancelling rather than actually waiting for the write.
    - Deleting the symbol row immediately after `stop()` returns must not
      raise a ForeignKeyViolation — proving the write that referenced
      `symbol_id` had genuinely finished, not just that timing happened
      to work out this run.

    Feeds `recorder._queue` directly rather than publishing through the
    Bus, same reasoning the LevelInteractionEngine version of this test
    documents: `bus.publish` only enqueues onto the BUS's own queue,
    dispatched to `_on_candle_closed` by the bus's separate `_consume`
    task on a later loop iteration — publishing and immediately calling
    `stop()` would risk the sentinel reaching this recorder's own queue
    ahead of the real item, which would exit the worker before the real
    item was ever pulled off the queue at all. Queuing directly isolates
    this test to the one thing it's meant to prove: cancellation-vs-drain
    behavior once an item is genuinely in this recorder's own queue.
    """
    _clean_stoprace_symbol()
    bus = EventBus()
    await bus.start()
    recorder = CandleRecorder(bus)

    delay_seconds = 0.3
    original_write_one = recorder._write_one

    def _slow_write_one(*args, **kwargs):
        time.sleep(delay_seconds)  # runs inside the executor thread — a real blocking delay, not a mock
        return original_write_one(*args, **kwargs)

    recorder._write_one = _slow_write_one  # type: ignore[method-assign]
    recorder.start()

    try:
        candle_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=1)
        # Fed straight onto the recorder's own queue (see docstring above)
        # — same dict shape _on_candle_closed itself would have produced.
        recorder._queue.put_nowait(
            {
                "symbol": "__CR_STOPRACE__", "timeframe": "1m", "open": 1.0, "high": 1.0,
                "low": 1.0, "close": 1.0, "volume": 10, "candle_ts": candle_ts,
            }
        )
        # Give the writer task a real chance to dequeue the item and get
        # asyncio.to_thread's executor submission actually running (into
        # the artificial 0.3s sleep) before stop() is called.
        await asyncio.sleep(0.05)

        t0 = time.monotonic()
        await recorder.stop()  # nothing queued after this — the one item is still mid-write
        elapsed = time.monotonic() - t0

        # A generous floor, not delay_seconds itself: ~0.05s of the
        # artificial delay was already spent during the sleep above,
        # before stop() was even called.
        assert elapsed >= 0.15, (
            f"stop() returned after {elapsed:.3f}s — expected it to block for close to the "
            f"remaining {delay_seconds}s artificial write delay; it isn't actually waiting "
            "for in-flight work anymore"
        )

        # The real proof: this must not raise a ForeignKeyViolation. If
        # stop() let the write land after this DELETE instead of before
        # it, this call reproduces the exact bug decision #84 fixed for
        # LevelInteractionEngine, still live here beforehand.
        _clean_stoprace_symbol()
    finally:
        await bus.stop()
        _clean_stoprace_symbol()
