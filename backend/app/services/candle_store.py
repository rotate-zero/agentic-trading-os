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
