"""Request-scoped acquisition of already-recorded candles for stored-candle
backtests (task ``stored-candle-backtest``).

``POST /backtest/run/stored`` replays the existing :class:`BacktestRunner`
over candles that ``CandleRecorder`` already wrote to PostgreSQL, so a
run needs no IBKR connection or any other external provider. This module
is the read half of that path and mirrors ``ibkr_historical.py``'s two-
lifecycle split:

* :func:`read_stored_replay_data` is a **synchronous** function that owns
  its own short-lived database session (created inside the worker thread
  that runs it, never shared with the event loop or another request). It
  reads everything the replay will need in one read-only snapshot
  transaction and closes the session before returning. :func:`acquire_
  stored_replay_data` is the awaitable wrapper the route calls
  (``asyncio.to_thread``).
* The result is a :class:`StoredReplayDataset` whose provider is the
  *same* disconnected, run-scoped
  :class:`~app.backtest_runner.ibkr_historical.PreloadedHistoricalCandleProvider`
  the IBKR path already uses (reused, not re-implemented). BacktestRunner
  installs only that in-memory object while it replays; the database
  session is already gone by then.

**What is read, exactly.**

* Namespace: ``symbols.is_backtest = FALSE`` only. A backtest-namespace
  ``Symbol`` row with the same ticker (the one BacktestRunner's own
  engines write to) is never read here, so a run cannot replay its own or
  another run's output.
* Primary 1-minute candles: the exact interval ``[start, end)`` — start
  inclusive, end exclusive, no clamping, ordered by ``candle_ts``.
* 1-minute warm-up: ``[start - 3 * feature_engine_premarket_lookback_days
  days, end)`` — the same calendar window ``FeatureEngine``'s pre-market
  baseline asks its historical provider for (and the IBKR path acquires).
* Daily warm-up: ``[start - daily_levels_lookback_days days, end)`` of
  **recorded** ``1d`` rows, the window ``FeatureEngine``'s Daily
  Levels/ATR/RVOL refresh asks for. Whatever is recorded is served;
  nothing is synthesized. There is deliberately no
  ``fixture_daily_history`` here: if the database holds no daily rows
  the replay's daily-derived features take ``FeatureEngine``'s existing
  honest "no history" path (regime scores stay at their existing
  insufficient-data value), exactly as they would live.

**No look-ahead.** Nothing at or after ``end`` is read. In addition, any
``1d`` row whose trading day is not strictly earlier than the trading day
of the last primary candle is dropped: ``FeatureEngine`` can never use
such a row for any replayed candle (it keeps only trading days strictly
before the current candle's day), so removing it loses nothing and means
no still-forming daily bar is even present in the run-scoped provider.
``PreloadedHistoricalCandleProvider.get_historical`` then slices to
``start <= candle_ts < end`` per call, so ``FeatureEngine`` only ever
receives rows older than the candle it is processing.

**Missing data.** Only one thing is *required*: at least one recorded 1m
candle inside ``[start, end)``; without it :class:`StoredHistoryNoDataError`
is raised before any ``BacktestRunRecord`` is written. Missing warm-up is
not an error and no threshold is invented here — thin or absent warm-up
simply flows into the engines' existing insufficient-data behaviour, and
``outcomes_recorded == 0`` is a valid answer. Rows with non-finite prices
or negative volume raise :class:`StoredHistoryMalformedError` rather than
being repaired or skipped. Historical point-in-time fundamentals and news
are **not** stored anywhere this path reads, so they stay absent, same as
the other two backtest paths.

A source row is never modified: the session is opened read-only at
``REPEATABLE READ`` and only ``SELECT`` statements are issued.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.backtest_runner.ibkr_historical import PreloadedHistoricalCandleProvider
from app.broker_adapters.base import Candle
from app.core.market_clock import get_market_clock
from app.db.session import SessionLocal
from app.models.market_data import Candle as CandleRow
from app.models.market_data import Symbol

__all__ = [
    "STORED_DATA_VERSION",
    "StoredHistoryError",
    "StoredHistoryMalformedError",
    "StoredHistoryNoDataError",
    "StoredHistoryUnavailableError",
    "StoredReplayDataset",
    "acquire_stored_replay_data",
    "read_stored_replay_data",
]

# BacktestRunRecord.data_version is String(32); this is 29 characters.
STORED_DATA_VERSION = "stored:postgres:candles:1m-1d"


class StoredHistoryError(Exception):
    """Stable application error translated by the HTTP route."""

    code = "stored_history_error"
    http_status = 500

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class StoredHistoryNoDataError(StoredHistoryError):
    code = "stored_candles_no_data"
    http_status = 422


class StoredHistoryMalformedError(StoredHistoryError):
    code = "stored_candles_malformed"
    http_status = 422


class StoredHistoryUnavailableError(StoredHistoryError):
    code = "stored_history_unavailable"
    http_status = 503


@dataclass(frozen=True)
class StoredReplayDataset:
    provider: PreloadedHistoricalCandleProvider
    data_version: str
    acquired_at: datetime
    primary_candle_count: int
    warmup_minute_candle_count: int
    daily_candle_count: int


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # candles.candle_ts is timestamptz; a naive value would mean the
        # driver stopped returning offsets. Refuse rather than guess.
        raise StoredHistoryMalformedError(
            f"A stored candle has a timezone-naive timestamp {value!r}."
        )
    return value.astimezone(timezone.utc)


def _fetch_candles(session, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
    rows = session.execute(
        select(
            CandleRow.candle_ts,
            CandleRow.open,
            CandleRow.high,
            CandleRow.low,
            CandleRow.close,
            CandleRow.volume,
        )
        .join(Symbol, Symbol.id == CandleRow.symbol_id)
        .where(
            Symbol.ticker == symbol,
            Symbol.is_backtest.is_(False),
            CandleRow.timeframe == timeframe,
            CandleRow.candle_ts >= start,
            CandleRow.candle_ts < end,
        )
        .order_by(CandleRow.candle_ts)
    ).all()

    candles: list[Candle] = []
    for candle_ts, open_, high, low, close, volume in rows:
        prices = [float(open_), float(high), float(low), float(close)]
        ts = _to_utc(candle_ts)
        if not all(math.isfinite(p) for p in prices) or volume is None or int(volume) < 0:
            raise StoredHistoryMalformedError(
                f"Stored {timeframe} candle for {symbol} at {ts.isoformat()} has non-finite "
                "OHLC values or a negative volume; the run is refused rather than repaired."
            )
        candles.append(
            Candle(
                timeframe=timeframe,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                volume=int(volume),
                candle_ts=ts,
            )
        )
    return candles


def read_stored_replay_data(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    daily_lookback_days: int,
    premarket_lookback_days: int,
) -> StoredReplayDataset:
    """Read the complete run-scoped dataset in one worker-owned snapshot.

    Synchronous on purpose: call it through :func:`acquire_stored_replay_data`
    (or ``asyncio.to_thread``). The session is created here, used only by
    the calling thread, and always closed.
    """
    minute_start = start - timedelta(days=premarket_lookback_days * 3)
    daily_start = start - timedelta(days=daily_lookback_days)

    session = SessionLocal()
    try:
        try:
            # One consistent, read-only snapshot for all three reads, so a
            # concurrently running CandleRecorder cannot make the 1m and
            # 1d series disagree about what was recorded.
            session.connection(
                execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
            )
            minute_candles = _fetch_candles(session, symbol, "1m", minute_start, end)
            daily_candles = _fetch_candles(session, symbol, "1d", daily_start, end)
        except SQLAlchemyError as exc:
            raise StoredHistoryUnavailableError(
                f"Recorded candles could not be read from PostgreSQL ({type(exc).__name__})."
            ) from exc
    finally:
        session.close()

    primary = [c for c in minute_candles if c.candle_ts >= start]
    if not primary:
        raise StoredHistoryNoDataError(
            f"No recorded 1m candles for {symbol} in the exact interval "
            f"[{start.isoformat()}, {end.isoformat()}). Stored-candle backtests replay only "
            "candles already recorded in this database; nothing is fetched or generated."
        )

    # Drop daily rows FeatureEngine could never legitimately use for any
    # replayed candle (see module docstring: no still-forming daily bar).
    clock = get_market_clock()
    last_primary_day = clock.trading_day(primary[-1].candle_ts)
    daily_candles = [c for c in daily_candles if clock.trading_day(c.candle_ts) < last_primary_day]

    provider = PreloadedHistoricalCandleProvider(
        {
            (symbol, "1m"): minute_candles,
            (symbol, "1d"): daily_candles,
        }
    )
    return StoredReplayDataset(
        provider=provider,
        data_version=STORED_DATA_VERSION,
        acquired_at=datetime.now(timezone.utc),
        primary_candle_count=len(primary),
        warmup_minute_candle_count=len(minute_candles) - len(primary),
        daily_candle_count=len(daily_candles),
    )


async def acquire_stored_replay_data(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    daily_lookback_days: int,
    premarket_lookback_days: int,
) -> StoredReplayDataset:
    """Awaitable wrapper: the blocking read runs in a worker thread that
    owns its database session, leaving the event loop free."""
    return await asyncio.to_thread(
        read_stored_replay_data,
        symbol=symbol,
        start=start,
        end=end,
        daily_lookback_days=daily_lookback_days,
        premarket_lookback_days=premarket_lookback_days,
    )
