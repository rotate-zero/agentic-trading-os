"""
CandleRecorder — pulls the "Persist candles/ticks to PostgreSQL via a
write-behind recorder (non-blocking)" responsibility (system-design.md
§4.2, Market Data Engine) forward into the current Phase 3 pipeline,
ahead of the formal Market Data Engine itself. §4.2's full internal split
(ConnectionManager -> Normalizer -> StateCache -> Publisher +
HistoricalWriter, independent async tasks fed by one queue) is still
Phase 4 work — this is just the HistoricalWriter piece, wired directly
onto the existing Event Bus rather than a formal internal pipeline that
doesn't exist yet.

Built in response to a real reported gap: intraday minute-level history
only ever existed for as long as a browser tab had been open and
listening live — reconnecting, refreshing, or opening a second sub-window
on an already-tracked symbol showed nothing until new live ticks arrived,
even though this app had already seen and closed candles for that symbol
earlier in the same run. Root cause: TickIngestBridge has been publishing
CandleClosed onto the Event Bus since Phase 3, and nothing was ever
listening to persist it — the `candles`/`symbols` tables
(app/models/market_data.py) were scaffolded in Phase 2 and have been
sitting completely unused since.

Deliberately NOT a general-purpose historical data source: this only ever
records symbol+"1m" — TickIngestBridge's fixed bucket size (see its own
docstring) — the SAME granularity the frontend always requests before
resampling client-side into 5m/15m/1h/4h/1d (confirmed decision #35,
frontend/src/utils/resample.ts). There's no reason to store anything
coarser separately; the client already knows how to build it from this.

Subscribes on the Event Bus's normal lane, the same lane PriceUpdated/
CandleClosed already flow through. Deliberately does NOT perform the DB
write inside the subscriber callback itself: EventBus._consume() awaits
asyncio.gather() over every handler for an event before pulling the next
one off its queue (app/event_bus/bus.py) — a slow synchronous DB write
done there would block live price fan-out for every other subscriber on
the same lane, the exact opposite of "via a write-behind recorder
(non-blocking)". Instead: the subscriber callback only pushes onto an
in-memory asyncio.Queue (near-instant, no I/O) and returns; a separate
background task drains that queue and performs the actual write via
asyncio.to_thread — the DB engine here is synchronous by design
(app/db/session.py: "no need for async DB access until throughput
actually demands it"), so to_thread is what keeps a blocking psycopg2
call off the event loop, the same pattern PolygonAdapter already uses
for its own synchronous REST client calls.

A DB that isn't reachable (e.g. Postgres not running locally yet) must
not crash the app over what's still an optional-enhancement path at this
phase — every write failure is caught and logged per-item, same
soft-fail posture as Finnhub/Polygon auto-connect in main.py.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.partitions import ensure_month_partition
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.models.market_data import Candle as CandleRow
from app.models.market_data import Symbol
from app.schemas.events.envelope import EventEnvelope, EventType

logger = logging.getLogger(__name__)

# Poison-pill used by stop() to drive a graceful writer-loop exit instead of
# cancelling the task outright — see stop()'s own docstring (flaky-test-
# cluster root-cause pass, following on from confirmed decision #84) for the
# real shutdown race this replaces. A private object identity, never a plain
# value, so it can never collide with a real CandleClosed-derived queue item
# (always a dict).
_STOP_SENTINEL = object()


class CandleRecorder:
    def __init__(self, bus: EventBus, session_factory: type[Session] | None = None) -> None:
        self._bus = bus
        self._session_factory = session_factory or SessionLocal
        self._queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()  # object half is _STOP_SENTINEL only
        self._writer_task: asyncio.Task | None = None

    def start(self) -> None:
        self._bus.subscribe(EventType.CANDLE_CLOSED, self._on_candle_closed)
        self._writer_task = asyncio.create_task(self._writer_loop(), name="candle-recorder-writer")
        logger.info("CandleRecorder started — persisting CandleClosed events to Postgres")

    async def stop(self) -> None:
        """
        Confirmed decision #47 first replaced a bare `task.cancel()` (which
        only SCHEDULES cancellation) with `task.cancel()` + `await task` —
        an improvement, but decision #84 later proved, for the sibling
        `LevelInteractionEngine`, that this still ISN'T a genuine wait: the
        Task here is suspended awaiting the plain `asyncio.Future` that
        `asyncio.to_thread` (`loop.run_in_executor` internally) hands back.
        `Future.cancel()` on THAT object transitions it to CANCELLED
        synchronously, regardless of whether the real OS thread underneath
        it — the one actually running `_write_one`, including its own
        blocking DB commit — has finished. `await task` then returns
        almost immediately, while the write keeps running to completion on
        its own, fully detached from anything this method's caller can see
        or wait on. Decision #84 flagged this exact file as "very likely"
        carrying the identical latent bug, deliberately deferred at the
        time; re-confirmed present by direct inspection and a standalone
        repro (mirroring #84's own verification method) during the flaky-
        test-cluster root-cause pass this fixes it in.

        For an engine whose thread-pool work WRITES rows keyed on
        `symbol_id`, an orphaned write surviving past `stop()`'s return is
        exactly what can race a caller's own post-`stop()` cleanup (e.g. a
        test's `DELETE FROM symbols`) into a real, intermittent
        ForeignKeyViolation — and, more importantly for the flaky cluster,
        can leave a write for a symbol's history still landing on Postgres
        AFTER a supposedly-clean shutdown, exactly the shape of race a
        cold-start backfill immediately afterward (in a fresh process, or a
        fresh test) can lose to.

        Fixed the same way #84 fixed it for `LevelInteractionEngine`: a
        poison-pill drain, not cancellation. `stop()` no longer calls
        `.cancel()` at all — it enqueues `_STOP_SENTINEL` onto the SAME
        queue `_writer_loop` already reads, then awaits the writer task
        with nothing cancelled. Because the queue is FIFO, the sentinel is
        only ever dequeued after every item genuinely ahead of it —
        including one already running inside `to_thread` — has fully
        finished. No timeout-then-cancel fallback, on purpose, same
        trade-off #84 already made explicit: correctness over shutdown
        latency for a background DB writer.
        """
        if self._writer_task is not None and not self._writer_task.done():
            await self._queue.put(_STOP_SENTINEL)
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
        self._writer_task = None

    # --- Event Bus subscriber (must stay fast — see module docstring) -------

    def _on_candle_closed(self, envelope: EventEnvelope) -> None:
        if envelope.symbol is None:
            return  # shouldn't happen — TickIngestBridge always sets symbol — but never worth crashing the bus over
        self._queue.put_nowait({"symbol": envelope.symbol, **envelope.payload})

    # --- background writer ---------------------------------------------------

    async def _writer_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP_SENTINEL:
                    # Graceful stop() request — not a real CandleClosed
                    # payload, nothing to persist. Everything queued AHEAD
                    # of this has already been fully written by the time
                    # we see it, since this is a plain FIFO queue.
                    self._queue.task_done()
                    break
                try:
                    await asyncio.to_thread(self._write_one, item)
                except Exception:  # noqa: BLE001 — one bad/unreachable-DB write must not kill the recorder or the app
                    logger.exception("CandleRecorder failed to persist a candle for %s", item.get("symbol"))
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    def _write_one(self, item: dict[str, Any]) -> None:
        candle_ts = item["candle_ts"]
        if isinstance(candle_ts, str):
            candle_ts = datetime.fromisoformat(candle_ts)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        session = self._session_factory()
        try:
            ensure_month_partition(session, candle_ts.date())
            symbol_id = self._get_or_create_symbol_id(session, item["symbol"])

            stmt = (
                pg_insert(CandleRow)
                .values(
                    candle_ts=candle_ts,
                    symbol_id=symbol_id,
                    timeframe=item["timeframe"],
                    open=item["open"],
                    high=item["high"],
                    low=item["low"],
                    close=item["close"],
                    volume=item["volume"],
                )
                # First-write-wins on a genuine duplicate close is fine —
                # see tick_ingest.py's own accepted-race note (confirmed
                # decision #42): a duplicate CandleClosed for the same
                # minute is a known, rare, already-accepted edge case, not
                # worth a competing DO UPDATE that could clobber good data
                # with a possibly-partial duplicate.
                .on_conflict_do_nothing(constraint="uq_candle_symbol_tf_ts")
            )
            session.execute(stmt)
            session.commit()
        finally:
            session.close()

    def _get_or_create_symbol_id(self, session: Session, ticker: str) -> int:
        existing = session.execute(
            select(Symbol.id).where(Symbol.ticker == ticker, Symbol.is_backtest.is_(False))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        # This class' writer task is the only caller that ever inserts
        # into `symbols` from the live pipeline, so there's no real
        # concurrent-insert race to defend against — ON CONFLICT DO
        # NOTHING + re-select is cheap insurance, not a load-bearing
        # requirement.
        session.execute(
            pg_insert(Symbol)
            .values(ticker=ticker, is_backtest=False)
            .on_conflict_do_nothing(index_elements=["ticker", "is_backtest"])
        )
        session.commit()
        return session.execute(
            select(Symbol.id).where(Symbol.ticker == ticker, Symbol.is_backtest.is_(False))
        ).scalar_one()
