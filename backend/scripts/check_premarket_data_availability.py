"""
Empirical check for docs/architecture/premarket-accumulator-design.md §3
— does Polygon's free tier actually give us what `premarket_volume_ratio`
needs? Two separate questions, both real gates on that feature (NOT on
vwap_ext/session_volume_ext, which need no historical data at all and
are already built):

1. Does `get_historical(symbol, "1m", ...)` return real bars for the
   4:00-9:30am ET window on prior days, or nothing (plan limitation)?
2. Does Polygon's DAILY bar already fold pre-market volume into one
   number (making it useless as a pre-market-only baseline), or can we
   derive a pre-market-only total by summing 1m bars ourselves?

Needs POLYGON_API_KEY set (.env or environment) — this hits the real
Polygon API, not a mock. Deliberately small: one symbol, a handful of
days — the 5-calls/minute default rate limit (settings.polygon_max_calls_per_minute)
makes anything wider slow to run interactively; widen SYMBOL/LOOKBACK_DAYS
yourself if the first pass looks promising and you want more confidence
before committing the real feature's code to this data source.

Usage:
    cd backend
    python scripts/check_premarket_data_availability.py
    python scripts/check_premarket_data_availability.py TSLA 10   # symbol, lookback days
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Running this as `python scripts/foo.py` (this docstring's own documented
# usage) does NOT put backend/ on sys.path — Python adds the script's OWN
# directory (scripts/), not the invoking cwd. Without this, `from app...`
# below fails with ModuleNotFoundError regardless of which directory you
# ran it from. Same fix applied to scripts/test_scanner_pipeline.py, which
# had the identical bug — caught here, not there, only because this
# script was written second; both needed it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker_adapters.polygon_provider import PolygonAdapter

_ET = ZoneInfo("America/New_York")
_DEFAULT_SYMBOL = "AAPL"
_DEFAULT_LOOKBACK_DAYS = 5  # matches feature_engine_rvol_lookback_days's own default


def _premarket_window(trading_day: datetime) -> tuple[datetime, datetime]:
    start = trading_day.replace(hour=4, minute=0, second=0, microsecond=0)
    end = trading_day.replace(hour=9, minute=30, second=0, microsecond=0)
    return start, end


async def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_SYMBOL
    lookback_days = int(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_LOOKBACK_DAYS

    try:
        provider = PolygonAdapter()
    except ValueError as exc:
        print(f"Can't run this check: {exc}")
        return

    print(f"Checking pre-market 1m historical availability for {symbol}, last {lookback_days} weekdays...\n")

    today = datetime.now(_ET)
    checked = 0
    day_offset = 1
    results: list[tuple[str, int, float]] = []  # (date, bar_count, total_volume)

    while checked < lookback_days and day_offset < lookback_days * 3:  # generous cap to skip weekends
        candidate = today - timedelta(days=day_offset)
        day_offset += 1
        if candidate.weekday() >= 5:  # Sat/Sun — skip, don't burn a call on a day with no session at all
            continue

        start, end = _premarket_window(candidate)
        bars = await provider.get_historical(symbol, "1m", start, end)
        total_volume = sum(b.volume for b in bars)
        results.append((candidate.strftime("%Y-%m-%d"), len(bars), total_volume))
        checked += 1

    print(f"{'DATE':<12}{'1m BARS':<10}{'PREMARKET VOLUME':<18}")
    for date_str, bar_count, volume in results:
        print(f"{date_str:<12}{bar_count:<10}{volume:<18,.0f}")

    if all(count == 0 for _, count, _ in results):
        print(
            "\nVERDICT: zero 1m bars returned for ANY day's pre-market window — either a plan "
            "limitation (see polygon_provider.py's _is_plan_limitation) or pre-market data isn't "
            "available on this tier at all. premarket_volume_ratio is NOT buildable against "
            "Polygon as-is; the IBKR path (already confirmed to have deep 1-min history) is the "
            "real answer here, which pushes this feature onto the same timeline as the IBKR "
            "subscription rather than being buildable sooner."
        )
    else:
        avg_volume = sum(v for _, c, v in results if c > 0) / max(1, sum(1 for _, c, _ in results if c > 0))
        print(
            f"\nVERDICT: pre-market 1m bars ARE available. Rough average pre-market volume across "
            f"the days with data: {avg_volume:,.0f}. Worth also spot-checking one day's DAILY bar "
            f"(via get_historical with timeframe='1d') against today's regular-session volume to "
            f"see whether Polygon's daily figure already includes pre-market — if the numbers "
            f"nearly match session_volume alone, pre-market is likely excluded from the daily bar "
            f"(good — no double-counting risk); if the daily figure is noticeably larger, it's "
            f"probably folded in already."
        )


if __name__ == "__main__":
    asyncio.run(main())
