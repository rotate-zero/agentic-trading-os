"""Read-only, request-scoped summary of candles available to stored replay."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.backtest_runner.stored_history import (
    StoredHistoryUnavailableError,
    _to_utc,
    daily_usable_before,
    stored_history_starts,
)
from app.db.session import SessionLocal
from app.models.market_data import Candle as CandleRow
from app.models.market_data import Symbol


@dataclass(frozen=True)
class StoredCoverage:
    symbol: str
    start: datetime
    end: datetime
    recorded_count: int
    recorded_first: datetime | None
    recorded_last: datetime | None
    requested_count: int
    requested_first: datetime | None
    requested_last: datetime | None
    warmup_minute_count: int
    warmup_daily_count: int

    def as_dict(self) -> dict:
        return asdict(self)


def _scope(timeframe: str):
    return (CandleRow.timeframe == timeframe, Symbol.is_backtest.is_(False))


def _bounds(session, symbol: str, timeframe: str, *conditions):
    return session.execute(
        select(func.count(), func.min(CandleRow.candle_ts), func.max(CandleRow.candle_ts))
        .select_from(CandleRow)
        .join(Symbol, Symbol.id == CandleRow.symbol_id)
        .where(Symbol.ticker == symbol, *_scope(timeframe), *conditions)
    ).one()


def read_stored_coverage(
    *, symbol: str, start: datetime, end: datetime,
    daily_lookback_days: int, premarket_lookback_days: int,
) -> StoredCoverage:
    """Query one worker-owned REPEATABLE READ, read-only PostgreSQL snapshot.

    Counts are observations of rows, not OHLCV validation or a replay dry run.
    """
    minute_start, daily_start = stored_history_starts(start, daily_lookback_days, premarket_lookback_days)
    session = SessionLocal()
    try:
        try:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
            total, first, last = _bounds(session, symbol, "1m")
            requested, requested_first, requested_last = _bounds(
                session, symbol, "1m", CandleRow.candle_ts >= start, CandleRow.candle_ts < end,
            )
            warmup, _, _ = _bounds(
                session, symbol, "1m", CandleRow.candle_ts >= minute_start, CandleRow.candle_ts < start,
            )
            daily_count = 0
            if requested_last is not None:
                daily_timestamps = session.execute(
                    select(CandleRow.candle_ts)
                    .join(Symbol, Symbol.id == CandleRow.symbol_id)
                    .where(
                        Symbol.ticker == symbol, *_scope("1d"),
                        CandleRow.candle_ts >= daily_start, CandleRow.candle_ts < end,
                    )
                ).scalars()
                daily_count = sum(daily_usable_before(ts, requested_last) for ts in daily_timestamps)
        except SQLAlchemyError as exc:
            raise StoredHistoryUnavailableError(
                f"Recorded candles could not be read from PostgreSQL ({type(exc).__name__})."
            ) from exc
    finally:
        session.close()

    return StoredCoverage(
        symbol=symbol, start=start, end=end,
        recorded_count=total, recorded_first=_to_utc(first) if first else None,
        recorded_last=_to_utc(last) if last else None,
        requested_count=requested, requested_first=_to_utc(requested_first) if requested_first else None,
        requested_last=_to_utc(requested_last) if requested_last else None,
        warmup_minute_count=warmup if requested else 0,
        warmup_daily_count=daily_count,
    )


async def acquire_stored_coverage(**kwargs) -> StoredCoverage:
    return await asyncio.to_thread(read_stored_coverage, **kwargs)
