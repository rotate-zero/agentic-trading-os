"""
Unit 1 tests for the Backtest Runner (see `backtest_runner/fixture_provider.py`
and `backtest_runner/context_provider.py`) — pure, DB-free. Covers:
  - FixtureCandleProvider satisfies MarketDataProvider honestly (range
    slicing, unknown-symbol/timeframe failure modes matching Polygon's own
    failure vocabulary).
  - load_fixture_candles_csv's naive-datetime rejection.
  - FixtureBacktestContextProvider / _ReplayClockCalendarProvider produce
    real MarketClock-derived facts for a REPLAYED instant, never
    wall-clock now, and never silently default before advance_to().
"""
from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.backtest_runner.context_provider import (
    FixtureBacktestContextProvider,
    HistoricalContextProvider,
    _ReplayClockCalendarProvider,
)
from app.backtest_runner.fixture_provider import FixtureCandleProvider, load_fixture_candles_csv
from app.broker_adapters.base import Candle, HistoricalDataUnavailableError, SymbolNotFoundError


def _candle(ts: datetime, o: float, h: float, l: float, c: float, v: int = 1000) -> Candle:
    return Candle(timeframe="1m", open=o, high=h, low=l, close=c, volume=v, candle_ts=ts)


# --- FixtureCandleProvider ---------------------------------------------------


async def test_fixture_provider_returns_candles_within_range():
    ts0 = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)
    candles = [_candle(ts0.replace(minute=30 + i), 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(5)]
    provider = FixtureCandleProvider.single("AAPL", "1m", candles)

    start = ts0.replace(minute=31)
    end = ts0.replace(minute=34)
    result = await provider.get_historical("AAPL", "1m", start, end)

    assert [c.candle_ts for c in result] == [ts0.replace(minute=m) for m in (31, 32, 33)]


async def test_fixture_provider_unknown_symbol_raises_symbol_not_found():
    provider = FixtureCandleProvider.single("AAPL", "1m", [])
    with pytest.raises(SymbolNotFoundError):
        await provider.get_historical("MSFT", "1m", datetime.now(timezone.utc), datetime.now(timezone.utc))


async def test_fixture_provider_non_1m_timeframe_raises_same_signal_as_polygon():
    """Fixture data is 1m-only (matching every v1 strategy's real
    requirement) — asking for anything else raises the SAME exception
    type PolygonAdapter raises for its own plan-tier gap, not a different
    failure shape a caller would need special-case handling for."""
    ts0 = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)
    provider = FixtureCandleProvider.single("AAPL", "1m", [_candle(ts0, 100, 101, 99, 100)])
    with pytest.raises(HistoricalDataUnavailableError):
        await provider.get_historical("AAPL", "5m", ts0, ts0)


async def test_fixture_provider_connect_lifecycle():
    provider = FixtureCandleProvider.single("AAPL", "1m", [])
    assert provider.is_connected() is False
    await provider.connect()
    assert provider.is_connected() is True
    await provider.disconnect()
    assert provider.is_connected() is False


def test_load_fixture_candles_csv_parses_and_requires_utc_offset(tmp_path: Path):
    csv_path = tmp_path / "fixture.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["candle_ts", "open", "high", "low", "close", "volume"])
        writer.writerow(["2026-01-28T14:30:00+00:00", "100", "101", "99", "100.5", "1000"])
        writer.writerow(["2026-01-28T14:31:00+00:00", "100.5", "102", "100", "101", "1200"])

    candles = load_fixture_candles_csv(csv_path)
    assert len(candles) == 2
    assert candles[0].candle_ts == datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)
    assert candles[1].open == 100.5


def test_load_fixture_candles_csv_rejects_naive_timestamp(tmp_path: Path):
    csv_path = tmp_path / "fixture_naive.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["candle_ts", "open", "high", "low", "close", "volume"])
        writer.writerow(["2026-01-28T14:30:00", "100", "101", "99", "100.5", "1000"])  # no offset

    with pytest.raises(ValueError, match="no UTC offset"):
        load_fixture_candles_csv(csv_path)


# --- BacktestContextProvider / FixtureBacktestContextProvider ---------------


async def test_replay_clock_calendar_provider_raises_before_advance_to():
    provider = _ReplayClockCalendarProvider()
    with pytest.raises(RuntimeError, match="advance_to"):
        await provider.evaluate()


async def test_replay_clock_calendar_provider_reports_fomc_day_for_replayed_date_not_wall_clock():
    """2026-01-28 is a real FOMC date (context_engine/providers/calendar.py's
    own hardcoded set) and a Wednesday, so this is a real regular-session
    trading instant, not a contrived one. The test's actual wall-clock
    'now' is irrelevant here by construction — fed_day must come from the
    replayed candle_ts, never datetime.now()."""
    provider = _ReplayClockCalendarProvider()
    fomc_ts = datetime(2026, 1, 28, 15, 30, tzinfo=timezone.utc)  # 10:30am ET, regular session
    provider.advance_to(fomc_ts)

    result = await provider.evaluate()

    assert result["fed_day"] is True
    assert result["trading_day"] == "2026-01-28"
    assert result["session"] == "open"
    assert result["is_market_open"] is True


async def test_replay_clock_calendar_provider_non_fomc_day():
    provider = _ReplayClockCalendarProvider()
    non_fomc_ts = datetime(2026, 1, 27, 15, 30, tzinfo=timezone.utc)
    provider.advance_to(non_fomc_ts)

    result = await provider.evaluate()

    assert result["fed_day"] is False


async def test_fixture_backtest_context_provider_builds_calendar_only_no_symbol_providers():
    """Confirms the FundamentalsProvider/NewsFlagProvider gap is honestly
    absent (empty list), never a fake stand-in."""
    fbcp = FixtureBacktestContextProvider()
    providers, symbol_providers = fbcp.build_engine_providers()

    assert len(providers) == 1
    assert providers[0].name == "calendar"
    assert symbol_providers == []


async def test_fixture_backtest_context_provider_advance_to_propagates_to_calendar():
    fbcp = FixtureBacktestContextProvider()
    ts = datetime(2026, 1, 28, 15, 30, tzinfo=timezone.utc)
    fbcp.advance_to(ts)

    providers, _ = fbcp.build_engine_providers()
    result = await providers[0].evaluate()

    assert result["trading_day"] == "2026-01-28"


def test_historical_context_provider_is_a_documented_stub_not_a_working_implementation():
    """Confirms HistoricalContextProvider exists as an extension point
    (importable, subclasses the ABC) but genuinely doesn't work yet —
    it should never be silently usable in place of the fixture provider."""
    hcp = HistoricalContextProvider()
    with pytest.raises(NotImplementedError):
        hcp.build_engine_providers()
    with pytest.raises(NotImplementedError):
        hcp.advance_to(datetime.now(timezone.utc))
