"""
Read-side counterpart to CandleRecorder (candle_recorder.py) — queries
what's already been persisted into the `candles` table. Split into its
own module because the recorder is a long-lived background service
(subscribe + writer task) while this is a plain, stateless query function
called per-request from GET /market/candles; nothing about reading needs
the recorder's lifecycle, and a route importing a "recorder" module to
run a read would be a confusing reason for the two to be coupled.

Returns the same `Candle` (== CandleClosed) shape every MarketDataProvider
returns from get_historical() — see app/broker_adapters/base.py's own note
on why Candle IS CandleClosed — so GET /market/candles (app/api/routes/
market.py) can treat a self-recorded result and a provider-fetched result
identically, no branching needed at the call site beyond "which one did I
get."
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.market_data import Candle as CandleRow
from app.models.market_data import Symbol
from app.schemas.events.market_data import CandleClosed as Candle


def get_recorded_candles(symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
    """
    Synchronous (same reasoning as CandleRecorder — this DB engine is
    sync by design; callers on the async side should wrap this in
    asyncio.to_thread, same as any other blocking call in this codebase).
    Returns [] for "nothing recorded yet" — including a DB that's
    unreachable entirely (caught and logged, not raised) — a caller
    should treat that identically to "no self-recorded history" and fall
    through to an external provider, not surface a 500 over what's still
    an optional enhancement path at this phase.
    """
    session = SessionLocal()
    try:
        rows = (
            session.execute(
                select(CandleRow)
                .join(Symbol, Symbol.id == CandleRow.symbol_id)
                .where(
                    Symbol.ticker == symbol,
                    CandleRow.timeframe == timeframe,
                    CandleRow.candle_ts >= start,
                    CandleRow.candle_ts <= end,
                )
                .order_by(CandleRow.candle_ts)
            )
            .scalars()
            .all()
        )
        return [
            Candle(
                timeframe=row.timeframe,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=row.volume,
                candle_ts=row.candle_ts,
            )
            for row in rows
        ]
    finally:
        session.close()


def get_latest_recorded_candle(symbol: str, timeframe: str) -> Candle | None:
    """
    The single most recently persisted candle for (symbol, timeframe), or
    None if nothing's recorded yet. Built for GET /market/feed-status
    (decision #44's still-open after-hours verification item): "how far
    behind is the live feed right now" is a single-row question, not a
    range query — a plain ORDER BY candle_ts DESC LIMIT 1, rather than
    calling get_recorded_candles() with an artificially wide [start, end]
    window just to pick out its own last element.

    Same "let the caller decide how to treat a DB failure" posture as
    get_recorded_candles()/get_recent_closes() above — no try/except here;
    GET /market/feed-status wraps this call itself (see that route).
    """
    session = SessionLocal()
    try:
        row = (
            session.execute(
                select(CandleRow)
                .join(Symbol, Symbol.id == CandleRow.symbol_id)
                .where(Symbol.ticker == symbol, CandleRow.timeframe == timeframe)
                .order_by(CandleRow.candle_ts.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        return Candle(
            timeframe=row.timeframe,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=row.volume,
            candle_ts=row.candle_ts,
        )
    finally:
        session.close()


def get_recent_closes(
    symbol: str, timeframe: str, before: datetime, limit: int, *, strict_before: bool = False
) -> list[float]:
    """
    Read-side addition alongside get_recorded_candles() above, for a
    consumer (app/feature_engine/engine.py) that wants "the last `limit`
    closes up to a point in time" rather than a wall-clock date range.

    Returns closes in chronological order (oldest -> newest) — the shape a
    moving-average function needs — even though the query itself runs
    newest-first: ORDER BY candle_ts DESC LIMIT n is the only way to bound
    the scan to `limit` rows instead of scanning the whole partition.

    strict_before=True excludes a candle at exactly `before` — used by
    FeatureEngine to backfill PRIOR closes only; it already has the
    current candle's own close from the event payload and deliberately
    doesn't re-read it back from the DB (see feature_engine/engine.py's
    module docstring on why — avoids a real ordering race against
    CandleRecorder's write of that same candle).

    Returns [] for "nothing recorded yet," same posture as
    get_recorded_candles().
    """
    session = SessionLocal()
    try:
        ts_filter = CandleRow.candle_ts < before if strict_before else CandleRow.candle_ts <= before
        rows = (
            session.execute(
                select(CandleRow.close)
                .join(Symbol, Symbol.id == CandleRow.symbol_id)
                .where(
                    Symbol.ticker == symbol,
                    CandleRow.timeframe == timeframe,
                    ts_filter,
                )
                .order_by(CandleRow.candle_ts.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [float(c) for c in reversed(rows)]
    finally:
        session.close()
