"""
Empirical check for two related M0 items on the Market State + Context
Engine build list:

M0 #3 — does the existing Polygon/Finnhub integration actually serve
SPY/QQQ/IWM cleanly (liquidity, quote quality, ETF-specific quirks)? This
exact question is already flagged, unanswered, in
trading-intelligence-architecture.md §4 ("Data source confirmation still
needed before implementation") — this script is that spike.

M0 #4 — does Feature Engine get the signed-volume/uptick-downtick raw
signal now, or does Participation stay blocked? This script doesn't build
that feature (M0 is explicitly "nothing coded yet") — it gathers the raw
evidence needed to make that call honestly: a short live capture of real
Finnhub trade prints for SPY/QQQ/IWM (+ one liquid single-name stock for
comparison), showing (a) what trade-condition codes actually show up on
real prints, and (b) what a plain tick-rule classification (+1 if price >
previous trade price, -1 if lower, 0 if unchanged) looks like against
real data. This is a read-only demonstration, not new production code —
Tick, _MinuteBucket, and CandleClosed are untouched.

Deliberately NOT reusing FinnhubAdapter for the tick-capture half: that
adapter's Tick model has no field for trade conditions (confirmed by
reading broker_adapters/base.py — Tick is symbol/price/size/exchange_ts
only), so routing through it would silently discard exactly the field
this check needs to see. This script talks to Finnhub's documented raw
WebSocket protocol directly instead (same protocol FinnhubAdapter's own
docstring documents: wss://ws.finnhub.io?token=..., {"type":"subscribe",
"symbol":...}, trade messages shaped {"type":"trade","data":[{"s","p",
"t","v","c"}]}) — nothing here depends on FinnhubAdapter internals.

Needs FINNHUB_API_KEY and POLYGON_API_KEY set (.env or environment) — this
hits real Finnhub/Polygon endpoints, not mocks. No network access to
finnhub.io or polygon.io exists in the sandbox this script was written in
(same standing gap as the Polygon default noted in core/config.py) — it
has not been run against live keys. Run it yourself.

The live tick-capture half only produces meaningful output during US
market hours (9:30am-4:00pm ET, weekdays) — outside that window it will
correctly show near-zero trades, which is the data being honest about the
market being closed, not a bug in this script.

Usage:
    cd backend
    python scripts/check_etf_and_tick_data.py
    python scripts/check_etf_and_tick_data.py --capture-seconds 60
    python scripts/check_etf_and_tick_data.py --symbols SPY QQQ IWM AAPL --capture-seconds 45
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets  # noqa: E402

from app.broker_adapters.polygon_provider import PolygonAdapter  # noqa: E402
from app.broker_adapters.base import HistoricalDataUnavailableError  # noqa: E402
from app.core.config import get_settings  # noqa: E402

_ET = ZoneInfo("America/New_York")
_DEFAULT_SYMBOLS = ["SPY", "QQQ", "IWM"]
_DEFAULT_CAPTURE_SECONDS = 30
_WS_URL_TEMPLATE = "wss://ws.finnhub.io?token={token}"


def _print_header(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _most_recent_weekday(reference: datetime) -> datetime:
    candidate = reference - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _warn_if_market_likely_closed() -> None:
    now_et = datetime.now(_ET)
    is_weekday = now_et.weekday() < 5
    in_session_hours = (now_et.hour, now_et.minute) >= (9, 30) and (now_et.hour, now_et.minute) < (16, 0)
    if not (is_weekday and in_session_hours):
        print(
            f"\nNOTE: current time is {now_et.strftime('%Y-%m-%d %H:%M %Z')} — outside regular US market "
            f"hours (9:30am-4:00pm ET, weekdays). The live tick-capture section below will likely show "
            f"few or zero trades. That's expected, not a failure — rerun during market hours for a real "
            f"read on trade volume/frequency."
        )


# --- Part 1: Polygon historical bar quality for SPY/QQQ/IWM ----------------


async def _check_polygon_bars(symbols: list[str]) -> None:
    _print_header("PART 1 — Polygon historical bar quality (1m + 1d) for SPY/QQQ/IWM")
    try:
        provider = PolygonAdapter()
    except ValueError as exc:
        print(f"Can't run this part: {exc}")
        return

    target_day = _most_recent_weekday(datetime.now(timezone.utc))
    day_start = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    for symbol in symbols:
        print(f"\n--- {symbol} ---")
        try:
            bars_1m = await provider.get_historical(symbol, "1m", day_start, day_end)
        except HistoricalDataUnavailableError as exc:
            print(f"1m bars: UNAVAILABLE — {exc}")
            bars_1m = []
        except Exception as exc:  # noqa: BLE001 — spike script, want to see any failure mode raw
            print(f"1m bars: FAILED with {type(exc).__name__}: {exc}")
            bars_1m = []

        if bars_1m:
            total_volume_1m = sum(b.volume for b in bars_1m)
            zero_volume_bars = sum(1 for b in bars_1m if b.volume == 0)
            print(
                f"1m bars: {len(bars_1m)} bars, total volume {total_volume_1m:,.0f}, "
                f"{zero_volume_bars} bars with zero volume ({zero_volume_bars / len(bars_1m):.1%})"
            )
            print(f"  first bar: {bars_1m[0]}")
            print(f"  last bar:  {bars_1m[-1]}")
        else:
            print("1m bars: none returned")

        try:
            bars_1d = await provider.get_historical(symbol, "1d", day_start, day_end)
        except HistoricalDataUnavailableError as exc:
            print(f"1d bar: UNAVAILABLE — {exc}")
            bars_1d = []
        except Exception as exc:  # noqa: BLE001
            print(f"1d bar: FAILED with {type(exc).__name__}: {exc}")
            bars_1d = []

        if bars_1d and bars_1m:
            daily_volume = bars_1d[0].volume
            summed_1m_volume = sum(b.volume for b in bars_1m)
            diff_pct = abs(daily_volume - summed_1m_volume) / daily_volume if daily_volume else float("nan")
            print(
                f"1d bar volume: {daily_volume:,.0f} vs. summed 1m volume: {summed_1m_volume:,.0f} "
                f"(diff {diff_pct:.1%}) — large diffs may indicate session-boundary/extended-hours handling quirks specific to ETFs"
            )
        elif bars_1d:
            print(f"1d bar volume: {bars_1d[0].volume:,.0f} (no 1m bars to cross-check against)")


# --- Part 2: raw Finnhub WS trade capture with conditions -------------------


def _connect_url(api_key: str) -> str:
    return _WS_URL_TEMPLATE.format(token=api_key)


async def _capture_trades(symbols: list[str], capture_seconds: int, api_key: str) -> dict[str, list[dict]]:
    _print_header(f"PART 2 — live Finnhub trade capture, {capture_seconds}s, symbols={symbols}")
    captured: dict[str, list[dict]] = defaultdict(list)

    async with websockets.connect(_connect_url(api_key)) as ws:
        for symbol in symbols:
            await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
        print(f"Subscribed to {symbols}. Listening for {capture_seconds}s...\n")

        deadline = asyncio.get_running_loop().time() + capture_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if message.get("type") != "trade":
                continue
            for trade in message.get("data", []):
                symbol = trade.get("s")
                if symbol is None:
                    continue
                captured[symbol].append(trade)

        for symbol in symbols:
            await ws.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))

    return captured


def _summarize_conditions(trades: list[dict]) -> Counter:
    codes: Counter = Counter()
    for t in trades:
        conditions = t.get("c") or []
        if not conditions:
            codes["<none>"] += 1
        for c in conditions:
            codes[str(c)] += 1
    return codes


def _tick_rule_classification(trades: list[dict]) -> tuple[int, int, int]:
    """Simplest possible signed-volume proxy: +1/-1/0 vs. the previous
    trade's price, in arrival order. This is the 'tick rule', NOT the
    Lee-Ready algorithm (which needs bid/ask quotes to classify trades at
    the midpoint) — Finnhub's free-tier WS stream is trade prints only, no
    quotes, so tick rule is the ceiling of what's achievable without a new
    data source. Labeled honestly as an approximation, not full order-flow
    classification, same distinction #91/future-ideas#13 already draw
    between Participation (observable) and causal inference (blocked)."""
    up = down = flat = 0
    prev_price = None
    for t in sorted(trades, key=lambda x: x.get("t", 0)):
        price = t.get("p")
        if price is None:
            continue
        if prev_price is not None:
            if price > prev_price:
                up += 1
            elif price < prev_price:
                down += 1
            else:
                flat += 1
        prev_price = price
    return up, down, flat


def _report_capture(captured: dict[str, list[dict]]) -> None:
    if not any(captured.values()):
        print("No trades captured for any symbol in the capture window — see the market-hours note above.")
        return

    for symbol, trades in captured.items():
        print(f"\n--- {symbol}: {len(trades)} trades captured ---")
        if not trades:
            continue
        prices = [t["p"] for t in trades if t.get("p") is not None]
        sizes = [t["v"] for t in trades if t.get("v") is not None]
        print(f"  price range: {min(prices):.2f} - {max(prices):.2f}")
        print(f"  size range:  {min(sizes)} - {max(sizes)}")

        codes = _summarize_conditions(trades)
        print(f"  condition codes observed: {dict(codes.most_common(10))}")

        up, down, flat = _tick_rule_classification(trades)
        total = up + down + flat
        if total:
            print(
                f"  tick-rule classification: {up} upticks ({up/total:.1%}), {down} downticks "
                f"({down/total:.1%}), {flat} unchanged ({flat/total:.1%})"
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=_DEFAULT_SYMBOLS)
    parser.add_argument("--capture-seconds", type=int, default=_DEFAULT_CAPTURE_SECONDS)
    parser.add_argument("--skip-polygon", action="store_true", help="skip Part 1 if you only want the tick capture")
    parser.add_argument("--skip-capture", action="store_true", help="skip Part 2 if you only want Polygon bar checks")
    args = parser.parse_args()

    settings = get_settings()
    _warn_if_market_likely_closed()

    if not args.skip_polygon:
        if not settings.polygon_api_key:
            print("Skipping Part 1: POLYGON_API_KEY is not set.")
        else:
            await _check_polygon_bars(args.symbols)

    if not args.skip_capture:
        if not settings.finnhub_api_key:
            print("Skipping Part 2: FINNHUB_API_KEY is not set.")
        else:
            captured = await _capture_trades(args.symbols, args.capture_seconds, settings.finnhub_api_key)
            _report_capture(captured)

    _print_header("OVERALL")
    print(
        "Part 1 tells you whether Polygon's delayed bars are usable for SPY/QQQ/IWM the same way\n"
        "they are for single-name stocks. Part 2 tells you two things at once: whether Finnhub's\n"
        "real-time stream covers these three symbols with normal trade frequency (M0 #3), and\n"
        "whether a tick-rule signed-volume proxy is computable from data already flowing through\n"
        "the system today with zero new external dependencies (M0 #4's raw evidence). Neither part\n"
        "computes a go/no-go verdict for you — that's a judgment call once you've read the numbers."
    )


if __name__ == "__main__":
    asyncio.run(main())
