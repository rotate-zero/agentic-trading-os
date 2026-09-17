"""Request-scoped IBKR history acquisition for real-candle backtests.

This module is deliberately split into two lifecycles:

* :func:`acquire_ibkr_replay_data` owns an isolated, read-only
  :class:`~app.broker_adapters.ibkr_adapter.IBKRAdapter`, downloads every
  candle shape Feature Engine will ask for, validates it, and disconnects
  in ``finally``.
* :class:`PreloadedHistoricalCandleProvider` is a disconnected,
  run-scoped provider over those real downloaded candles. BacktestRunner
  installs only this object in the process-wide historical role while it
  replays; the network adapter is already gone.

The separation is a safety boundary, not a cache optimization. The
acquisition adapter is never registered, never receives a TickIngestBridge,
never subscribes to streaming data, and is never capable of being reached
through ``GET /market/candles``. It also ensures every IBKR/network failure
happens before BacktestRunner can write a BacktestRunRecord.

Only OHLCV history becomes real. FixtureBacktestContextProvider remains the
replay-safe calendar source and historical point-in-time fundamentals/news
remain absent.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.broker_adapters.base import (
    Candle,
    HistoricalDataUnavailableError,
    MarketDataProvider,
    SymbolNotFoundError,
    Tick,
)
from app.broker_adapters.ibkr_adapter import IBKRAdapter

__all__ = [
    "IBKRHistoricalAcquisitionError",
    "IBKRHistoricalConnectionError",
    "IBKRHistoricalContractError",
    "IBKRHistoricalIncompleteError",
    "IBKRHistoricalMalformedBarError",
    "IBKRHistoricalNoDataError",
    "IBKRHistoricalPacingError",
    "IBKRHistoricalPermissionError",
    "IBKRHistoricalTimeoutError",
    "IBKRReplayDataset",
    "PreloadedHistoricalCandleProvider",
    "acquire_ibkr_replay_data",
]

_ONE_DAY = timedelta(days=1)
_REQUEST_TIMEOUT_SECONDS = 60.0
_DATA_VERSION = "ibkr:TRADES:1m-ext:1d-rth"


class IBKRHistoricalAcquisitionError(Exception):
    """Stable application error translated by the HTTP route."""

    code = "ibkr_historical_error"
    http_status = 502

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class IBKRHistoricalConnectionError(IBKRHistoricalAcquisitionError):
    code = "ibkr_connection_unavailable"
    http_status = 503


class IBKRHistoricalContractError(IBKRHistoricalAcquisitionError):
    code = "ibkr_contract_unresolved"
    http_status = 400


class IBKRHistoricalPermissionError(IBKRHistoricalAcquisitionError):
    code = "ibkr_historical_permission_denied"
    http_status = 503


class IBKRHistoricalPacingError(IBKRHistoricalAcquisitionError):
    code = "ibkr_historical_pacing_rejected"
    http_status = 503


class IBKRHistoricalTimeoutError(IBKRHistoricalAcquisitionError):
    code = "ibkr_historical_timeout"
    http_status = 504


class IBKRHistoricalNoDataError(IBKRHistoricalAcquisitionError):
    code = "ibkr_no_data"
    http_status = 400


class IBKRHistoricalMalformedBarError(IBKRHistoricalAcquisitionError):
    code = "ibkr_malformed_response"
    http_status = 502


class IBKRHistoricalIncompleteError(IBKRHistoricalAcquisitionError):
    code = "ibkr_incomplete_response"
    http_status = 502


@dataclass(frozen=True)
class _IBKRErrorNotice:
    code: int
    message: str


@dataclass(frozen=True)
class IBKRReplayDataset:
    provider: "PreloadedHistoricalCandleProvider"
    data_version: str
    acquired_at: datetime
    contract_con_id: int | None


class PreloadedHistoricalCandleProvider(MarketDataProvider):
    """Disconnected provider over validated, real downloaded candles.

    It deliberately cannot become a streaming/live provider. The only
    supported operation is exact historical slicing for the two shapes
    Backtest Runner and Feature Engine request today.
    """

    def __init__(self, candles: dict[tuple[str, str], list[Candle]]) -> None:
        self._candles = candles

    async def connect(self) -> None:
        raise HistoricalDataUnavailableError(
            provider=type(self).__name__,
            reason="preloaded replay data has no live connection",
        )

    async def disconnect(self) -> None:
        return None

    def is_connected(self) -> bool:
        return False

    async def subscribe(self, symbols: list[str]) -> None:
        raise HistoricalDataUnavailableError(
            provider=type(self).__name__,
            reason="preloaded replay data cannot stream subscriptions",
        )

    async def unsubscribe(self, symbols: list[str]) -> None:
        return None

    async def get_historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        if timeframe not in ("1m", "1d"):
            raise HistoricalDataUnavailableError(
                provider=type(self).__name__,
                reason=f"preloaded replay data only covers 1m/1d, not {timeframe!r}",
            )
        key = (symbol, timeframe)
        if key not in self._candles:
            raise SymbolNotFoundError(symbol, provider=type(self).__name__)
        return [c for c in self._candles[key] if start <= c.candle_ts < end]

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        raise HistoricalDataUnavailableError(
            provider=type(self).__name__,
            reason="preloaded replay data cannot publish ticks",
        )


def _iter_one_day_chunks(start: datetime, end: datetime) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + _ONE_DAY, end)
        yield cursor, chunk_end
        cursor = chunk_end


def _one_minute_duration(start: datetime, end: datetime) -> str:
    seconds = max(1, math.ceil((end - start).total_seconds()))
    return "1 D" if seconds == 86400 else f"{seconds} S"


def _daily_duration(start: datetime, end: datetime) -> str:
    days = max(1, math.ceil((end - start).total_seconds() / 86400))
    return f"{days} D"


def _normalize_bar_timestamp(raw: Any, *, timeframe: str, market_timezone: str) -> datetime:
    if isinstance(raw, datetime):
        if raw.tzinfo is None or raw.utcoffset() is None:
            raise IBKRHistoricalMalformedBarError(
                f"IBKR returned a timezone-naive {timeframe} bar timestamp {raw!r}."
            )
        return raw.astimezone(timezone.utc)

    parsed_date: date | None = None
    if isinstance(raw, date):
        parsed_date = raw
    elif timeframe == "1d" and isinstance(raw, str):
        try:
            parsed_date = datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            try:
                parsed_date = date.fromisoformat(raw)
            except ValueError:
                parsed_date = None

    if parsed_date is not None and timeframe == "1d":
        local_midnight = datetime.combine(parsed_date, time.min, tzinfo=ZoneInfo(market_timezone))
        return local_midnight.astimezone(timezone.utc)

    raise IBKRHistoricalMalformedBarError(
        f"IBKR returned an unsupported or timezone-ambiguous {timeframe} bar timestamp {raw!r}."
    )


def _canonical_candle(bar: Any, *, timeframe: str, market_timezone: str) -> Candle:
    try:
        prices = [float(bar.open), float(bar.high), float(bar.low), float(bar.close)]
        volume = int(bar.volume)
        candle_ts = _normalize_bar_timestamp(
            bar.date,
            timeframe=timeframe,
            market_timezone=market_timezone,
        )
    except IBKRHistoricalAcquisitionError:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise IBKRHistoricalMalformedBarError(f"Malformed IBKR {timeframe} bar: {bar!r}") from exc

    if not all(math.isfinite(value) for value in prices) or volume < 0:
        raise IBKRHistoricalMalformedBarError(
            f"IBKR returned non-finite OHLC or negative volume for {timeframe} at {candle_ts.isoformat()}."
        )

    return Candle(
        timeframe=timeframe,
        open=prices[0],
        high=prices[1],
        low=prices[2],
        close=prices[3],
        volume=volume,
        candle_ts=candle_ts,
    )


def _merge_exact(
    raw_chunks: Iterable[Iterable[Any]],
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    market_timezone: str,
) -> list[Candle]:
    by_timestamp: dict[datetime, Candle] = {}
    for raw_chunk in raw_chunks:
        for raw_bar in raw_chunk:
            candle = _canonical_candle(
                raw_bar,
                timeframe=timeframe,
                market_timezone=market_timezone,
            )
            if not start <= candle.candle_ts < end:
                continue
            prior = by_timestamp.get(candle.candle_ts)
            if prior is not None and prior != candle:
                raise IBKRHistoricalIncompleteError(
                    "IBKR returned conflicting bars at overlapping chunk boundary "
                    f"{candle.candle_ts.isoformat()}."
                )
            by_timestamp[candle.candle_ts] = candle
    return [by_timestamp[ts] for ts in sorted(by_timestamp)]


def _error_category(code: int, message: str) -> str:
    lowered = message.lower()
    if code == 200 or "no security definition" in lowered or "ambiguous" in lowered:
        return "contract"
    if "pacing" in lowered or "throttl" in lowered or code == 420:
        return "pacing"
    if code in {354, 10089, 10186, 10187, 2188} or any(
        phrase in lowered
        for phrase in (
            "not subscribed",
            "market data permission",
            "market data permissions",
            "requires market data subscription",
        )
    ):
        return "permission"
    if code == 165 or "no data" in lowered or "returned no data" in lowered:
        return "no_data"
    if 2100 <= code < 2200:
        return "informational"
    if code in {502, 1100, 1300} or "socket disconnect" in lowered or "connection lost" in lowered:
        return "connection"
    return "request"


def _raise_classified(code: int, message: str) -> None:
    category = _error_category(code, message)
    detail = f"IBKR error {code}: {message}"
    if category == "contract":
        raise IBKRHistoricalContractError(detail)
    if category == "pacing":
        raise IBKRHistoricalPacingError(detail)
    if category == "permission":
        raise IBKRHistoricalPermissionError(detail)
    if category == "connection":
        raise IBKRHistoricalConnectionError(detail)
    if category in {"no_data", "informational"}:
        return
    raise IBKRHistoricalIncompleteError(detail)


async def acquire_ibkr_replay_data(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    host: str,
    port: int,
    client_id: int,
    daily_lookback_days: int,
    premarket_lookback_days: int,
    market_timezone: str,
    adapter_factory: Callable[..., IBKRAdapter] | None = None,
    request_timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
) -> IBKRReplayDataset:
    """Download and validate the complete run-scoped candle dataset.

    One-minute requests are serial one-day chunks. That combination is
    conservative under IBKR's duration/bar-size rules and makes each
    normally completed response small enough that a non-empty result is
    not mistaken for an obviously truncated giant response. There are no
    automatic retries, especially after pacing rejection.
    """
    factory = adapter_factory or IBKRAdapter
    adapter = factory(host=host, port=port, client_id=client_id)
    notices: list[_IBKRErrorNotice] = []
    disconnected = False

    def on_error(_req_id, error_code, error_string, *_args) -> None:
        notices.append(_IBKRErrorNotice(int(error_code), str(error_string)))

    def on_disconnect(*_args) -> None:
        nonlocal disconnected
        disconnected = True

    def clear_request_state() -> None:
        notices.clear()

    def inspect_request_state() -> None:
        if disconnected:
            raise IBKRHistoricalConnectionError(
                "IB Gateway/TWS disconnected before historical acquisition completed."
            )
        for notice in notices:
            category = _error_category(notice.code, notice.message)
            if category not in {"no_data", "informational"}:
                _raise_classified(notice.code, notice.message)

    async def run_request(awaitable, *, label: str):
        try:
            async with asyncio.timeout(request_timeout_seconds):
                result = await awaitable
        except TimeoutError as exc:
            raise IBKRHistoricalTimeoutError(
                f"IBKR historical request timed out after {request_timeout_seconds:g}s ({label})."
            ) from exc
        except IBKRHistoricalAcquisitionError:
            raise
        except SymbolNotFoundError:
            raise
        except ConnectionError as exc:
            raise IBKRHistoricalConnectionError(
                f"IB Gateway/TWS disconnected during {label}: {exc}"
            ) from exc
        except Exception as exc:  # ib_async RequestError is inspected by attributes
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", str(exc))
            if isinstance(code, int):
                _raise_classified(code, message)
            raise IBKRHistoricalIncompleteError(f"IBKR request failed during {label}: {exc}") from exc
        inspect_request_state()
        return result

    adapter.add_error_listener(on_error)
    adapter.add_disconnect_listener(on_disconnect)
    adapter.set_raise_request_errors(True)

    try:
        try:
            await adapter.connect()
        except Exception as exc:
            raise IBKRHistoricalConnectionError(
                f"Could not connect to IB Gateway/TWS at {host}:{port}: {exc}"
            ) from exc

        clear_request_state()
        try:
            contract = await run_request(
                adapter.qualify_historical_contract(symbol),
                label=f"contract qualification for {symbol}",
            )
        except SymbolNotFoundError as exc:
            raise IBKRHistoricalContractError(str(exc)) from exc

        minute_start = start - timedelta(days=premarket_lookback_days * 3)
        minute_chunks: list[Iterable[Any]] = []
        for chunk_start, chunk_end in _iter_one_day_chunks(minute_start, end):
            clear_request_state()
            bars = await run_request(
                adapter.request_historical_chunk(
                    contract,
                    timeframe="1m",
                    end=chunk_end,
                    duration_str=_one_minute_duration(chunk_start, chunk_end),
                    use_rth=False,
                ),
                label=f"1m [{chunk_start.isoformat()}, {chunk_end.isoformat()})",
            )
            minute_chunks.append(bars)

        daily_start = start - timedelta(days=daily_lookback_days)
        clear_request_state()
        daily_bars = await run_request(
            adapter.request_historical_chunk(
                contract,
                timeframe="1d",
                end=end,
                duration_str=_daily_duration(daily_start, end),
                use_rth=True,
            ),
            label=f"1d [{daily_start.isoformat()}, {end.isoformat()})",
        )

        minute_candles = _merge_exact(
            minute_chunks,
            timeframe="1m",
            start=minute_start,
            end=end,
            market_timezone=market_timezone,
        )
        daily_candles = _merge_exact(
            [daily_bars],
            timeframe="1d",
            start=daily_start,
            end=end,
            market_timezone=market_timezone,
        )

        primary = [c for c in minute_candles if start <= c.candle_ts < end]
        if not primary:
            raise IBKRHistoricalNoDataError(
                f"IBKR returned zero usable 1m TRADES bars for {symbol} in the exact "
                f"interval [{start.isoformat()}, {end.isoformat()})."
            )

        provider = PreloadedHistoricalCandleProvider(
            {
                (symbol, "1m"): minute_candles,
                (symbol, "1d"): daily_candles,
            }
        )
        return IBKRReplayDataset(
            provider=provider,
            data_version=_DATA_VERSION,
            acquired_at=datetime.now(timezone.utc),
            contract_con_id=getattr(contract, "conId", None) or None,
        )
    finally:
        try:
            await adapter.disconnect()
        finally:
            adapter.remove_error_listener(on_error)
            adapter.remove_disconnect_listener(on_disconnect)
