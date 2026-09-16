"""
Empirical check for docs/architecture/premarket-accumulator-design.md §3.

The check mirrors FeatureEngine._maybe_refresh_premarket_baseline's real
Polygon access pattern: one wide-range 1-minute request per symbol, followed by
local grouping into prior pre-market sessions. It answers three questions:

1. Does the configured Polygon tier return real 1-minute bars from 4:00-9:30am
   ET on prior trading days?
2. How long does the current six-symbol placeholder universe take under the
   configured REST-call ceiling?
3. Does Polygon's daily aggregate align with regular-session minute volume or
   with the full pre-market + regular + after-hours minute total?

Needs POLYGON_API_KEY set in backend/.env or the environment. This hits the
real Polygon API, not a mock.

Usage:
    cd backend
    python scripts/check_premarket_data_availability.py
    python scripts/check_premarket_data_availability.py AAPL 10
    python scripts/check_premarket_data_availability.py AAPL,MSFT,NVDA,AMD,TSLA,SPY 5
"""
from __future__ import annotations

import asyncio
import sys
import time as monotonic_time
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Running this as `python scripts/foo.py` does not put backend/ on
# sys.path. Add it explicitly so the documented invocation works.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker_adapters.base import Candle
from app.broker_adapters.polygon_provider import PolygonAdapter
from app.core.config import get_settings

_ET = ZoneInfo("America/New_York")
_DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "SPY"]
_DEFAULT_LOOKBACK_DAYS = 5


def _parse_symbols(raw: str | None) -> list[str]:
    if raw is None:
        return list(_DEFAULT_SYMBOLS)
    symbols = [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]
    if not symbols:
        raise ValueError("at least one symbol is required")
    return symbols


def _session_bucket(candle: Candle) -> str | None:
    local = candle.candle_ts.astimezone(_ET)
    wall_time = local.time().replace(tzinfo=None)
    if time(4, 0) <= wall_time < time(9, 30):
        return "premarket"
    if time(9, 30) <= wall_time < time(16, 0):
        return "regular"
    if time(16, 0) <= wall_time < time(20, 0):
        return "after_hours"
    return None


def _completed_day_volumes(bars: list[Candle], today: date) -> dict[date, dict[str, int]]:
    volumes: dict[date, dict[str, int]] = defaultdict(
        lambda: {"premarket": 0, "regular": 0, "after_hours": 0}
    )
    for bar in bars:
        local = bar.candle_ts.astimezone(_ET)
        if local.date() >= today:
            continue
        bucket = _session_bucket(bar)
        if bucket is not None:
            volumes[local.date()][bucket] += bar.volume
    return dict(volumes)


def _print_symbol_result(
    symbol: str,
    volumes: dict[date, dict[str, int]],
    lookback_days: int,
) -> list[date]:
    days = [day for day in sorted(volumes) if volumes[day]["premarket"] > 0]
    days = days[-lookback_days:]
    print(f"\n{symbol}")
    print(f"{'DATE':<12}{'PREMARKET VOLUME':<18}")
    for day in days:
        print(f"{day.isoformat():<12}{volumes[day]['premarket']:<18,}")
    if not days:
        print("No completed pre-market sessions returned.")
    return days


async def main() -> None:
    try:
        symbols = _parse_symbols(sys.argv[1] if len(sys.argv) > 1 else None)
        lookback_days = int(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_LOOKBACK_DAYS
        if lookback_days <= 0:
            raise ValueError("lookback days must be positive")
    except ValueError as exc:
        print(f"Can't run this check: {exc}")
        return

    try:
        provider = PolygonAdapter()
    except ValueError as exc:
        print(f"Can't run this check: {exc}")
        return

    settings = get_settings()
    today = datetime.now(_ET)
    range_start = today - timedelta(days=lookback_days * 3)
    print(
        f"Checking {len(symbols)} symbol(s), {lookback_days} completed pre-market "
        f"sessions each, with one wide-range 1m request per symbol.\n"
        f"Configured Polygon ceiling: {settings.polygon_max_calls_per_minute} calls/minute."
    )

    started = monotonic_time.monotonic()
    per_symbol: dict[str, tuple[list[Candle], dict[date, dict[str, int]], list[date]]] = {}
    for symbol in symbols:
        bars = await provider.get_historical(symbol, "1m", range_start, today)
        volumes = _completed_day_volumes(bars, today.date())
        days = _print_symbol_result(symbol, volumes, lookback_days)
        per_symbol[symbol] = (bars, volumes, days)
    minute_elapsed = monotonic_time.monotonic() - started

    symbols_with_data = sum(bool(days) for _, _, days in per_symbol.values())
    if symbols_with_data != len(symbols):
        print(
            f"\nMINUTE-BAR VERDICT: {symbols_with_data}/{len(symbols)} symbols returned "
            "completed pre-market sessions. The missing symbols need investigation before "
            "this provider can be treated as a complete baseline source."
        )
    else:
        print(
            f"\nMINUTE-BAR VERDICT: all {len(symbols)} symbols returned real pre-market "
            f"1m data. The {len(symbols)} wide-range requests completed in "
            f"{minute_elapsed:.1f}s under the configured limiter."
        )

    primary = symbols[0]
    _, primary_volumes, primary_days = per_symbol[primary]
    if not primary_days:
        print("\nDAILY-BAR VERDICT: skipped because the primary symbol returned no pre-market data.")
        return

    comparison_day = primary_days[-1]
    day_start = datetime.combine(comparison_day, time(0, 0), tzinfo=_ET)
    day_end = datetime.combine(comparison_day, time(23, 59, 59), tzinfo=_ET)
    focused_windows = {
        "premarket": (time(4, 0), time(9, 30)),
        "regular": (time(9, 30), time(16, 0)),
        "after_hours": (time(16, 0), time(20, 0)),
    }
    focused_bars: dict[str, list[Candle]] = {}
    for bucket, (start_time, end_time) in focused_windows.items():
        window_start = datetime.combine(comparison_day, start_time, tzinfo=_ET)
        window_end = datetime.combine(comparison_day, end_time, tzinfo=_ET)
        focused_bars[bucket] = await provider.get_historical(
            primary, "1m", window_start, window_end
        )
    daily_bars = await provider.get_historical(primary, "1d", day_start, day_end)
    daily_volume = daily_bars[-1].volume
    focused_counts = {
        bucket: sum(1 for bar in bars if _session_bucket(bar) == bucket)
        for bucket, bars in focused_bars.items()
    }
    session = {
        bucket: sum(bar.volume for bar in bars if _session_bucket(bar) == bucket)
        for bucket, bars in focused_bars.items()
    }
    regular_volume = session["regular"]
    full_volume = sum(session.values())
    regular_delta = abs(daily_volume - regular_volume)
    full_delta = abs(daily_volume - full_volume)
    wide_premarket_volume = primary_volumes[comparison_day]["premarket"]

    print(f"\nDaily-bar comparison for {primary} on {comparison_day.isoformat()}:")
    print(f"  Polygon 1d volume:          {daily_volume:>15,}")
    print(
        f"  focused pre-market 1m:     {session['premarket']:>15,} "
        f"({focused_counts['premarket']} bars)"
    )
    print(
        f"  focused regular-session 1m:{regular_volume:>15,} "
        f"({focused_counts['regular']} bars)"
    )
    print(
        f"  focused after-hours 1m:    {session['after_hours']:>15,} "
        f"({focused_counts['after_hours']} bars)"
    )
    print(f"  summed 4:00am-8:00pm 1m:   {full_volume:>15,}")
    print(f"  wide-range pre-market 1m:  {wide_premarket_volume:>15,}")
    print(f"  |daily - regular|:         {regular_delta:>15,}")
    print(f"  |daily - full|:            {full_delta:>15,}")

    regular_relative_delta = regular_delta / daily_volume
    full_relative_delta = full_delta / daily_volume
    tolerance = 0.02
    if full_relative_delta <= tolerance and full_delta < regular_delta:
        print(
            "DAILY-BAR VERDICT: the daily aggregate aligns more closely with the full "
            "extended-hours minute total, so it folds pre-market/after-hours volume into "
            "one number and cannot isolate a pre-market-only baseline."
        )
    elif regular_relative_delta <= tolerance and regular_delta < full_delta:
        print(
            "DAILY-BAR VERDICT: the daily aggregate aligns more closely with the regular-session "
            "minute total, so it excludes rather than isolates pre-market volume. It still "
            "cannot supply a pre-market-only baseline; the 1m path is required."
        )
    else:
        print(
            "DAILY-BAR VERDICT: inconclusive on inclusion semantics — the daily aggregate "
            "does not reconcile within 2% of either minute-bar sum, so proximity alone is "
            "not evidence that it includes or excludes extended-hours volume. The daily bar "
            "still exposes only one combined volume and cannot isolate a pre-market baseline; "
            "the 1m path is required."
        )

    if wide_premarket_volume != session["premarket"]:
        print(
            "WIDE-RANGE VERDICT: the wide-range request used by the current Feature Engine "
            "did not reproduce the focused pre-market request's volume for the same symbol/day. "
            "The baseline-fetch shape needs investigation before its values can be trusted."
        )
    else:
        print(
            "WIDE-RANGE VERDICT: the implementation-shaped wide request and focused "
            "pre-market request produced the same volume for the comparison day."
        )

    total_elapsed = monotonic_time.monotonic() - started
    print(
        f"\nTotal probe time ({len(symbols) + len(focused_windows)} minute requests + "
        "1 daily request): "
        f"{total_elapsed:.1f}s."
    )


if __name__ == "__main__":
    asyncio.run(main())
