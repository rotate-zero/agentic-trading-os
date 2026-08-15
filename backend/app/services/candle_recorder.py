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


class CandleRecorder:
    def __init__(self, bus: EventBus, session_factory: type[Session] | None = None) -> None:
        self._bus = bus
        self._session_factory = session_factory or SessionLocal
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._writer_task: asyncio.Task | None = None

    def start(self) -> None:
        self._bus.subscribe(EventType.CANDLE_CLOSED, self._on_candle_closed)
        self._writer_task = asyncio.create_task(self._writer_loop(), name="candle-recorder-writer")
        logger.info("CandleRecorder started — persisting CandleClosed events to Postgres")

    async def stop(self) -> None:
        """
        Confirmed decision #47 — this used to be `task.cancel()` with
        nothing awaiting it, which only SCHEDULES cancellation; it doesn't
        block until the task has actually unwound. Found via a real,
        reproducible bug, not by inspection: a cancelled task blocked
        inside `asyncio.to_thread(...)` (this writer's DB call) can't be
        interrupted until that thread finishes — so `stop()` could return,
        and a caller could reasonably believe shutdown was complete, while
        a write was still in flight and landed afterward. That's exactly
        what corrupted a test's cleanup (a stray write racing a DELETE,
        producing a foreign-key violation) — a real symptom of the same
        risk existing in production shutdown, not a test-only artifact.
        """
        if self._writer_task is not None and not self._writer_task.done():
            self._writer_task.cancel()
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
        existing = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one_or_none()
        if existing is not None:
            return existing
        # This class' writer task is the only caller that ever inserts
        # into `symbols` from the live pipeline, so there's no real
        # concurrent-insert race to defend against — ON CONFLICT DO
        # NOTHING + re-select is cheap insurance, not a load-bearing
        # requirement.
        session.execute(pg_insert(Symbol).values(ticker=ticker).on_conflict_do_nothing(index_elements=["ticker"]))
        session.commit()
        return session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()
