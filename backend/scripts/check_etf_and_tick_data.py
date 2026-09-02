"""
Empirical check for two related M0 items on the Market State + Context
Engine build list:

M0 #3 — does the existing Polygon/Finnhub integration actually serve
SPY/QQQ/IWM cleanly (liquidity, quote quality, ETF-specific quirks)? This
exact question is already flagged, unanswered, in
trading-intelligence-architecture.md §4 ("Data source confirmation still
needed before implementation") — this script is that spike. Part 1 checks
Polygon daily bars ONLY, not 1m — decision #39 already found, against a
real account, that the free/Basic tier returns NOT_AUTHORIZED for ANY
minute-timeframe query, symbol-independent, so testing that again for
SPY/QQQ/IWM specifically would just re-confirm a known plan limitation
while spending part of the 5-calls/min budget for zero new information.
It pulls a 14-day daily-bar window (one call per symbol) instead of a
single day, so gaps/continuity are actually checkable.

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
    _print_header("PART 1 — Polygon daily bar quality for SPY/QQQ/IWM")
    print(
        "NOT checking 1m bars here: decision #39 already found, against a real account, that Polygon's "
        "free/Basic tier returns NOT_AUTHORIZED for ANY minute-timeframe query, symbol-independent — "
        "re-testing that for SPY/QQQ/IWM specifically would just re-confirm a known plan limitation while "
        "spending part of the 5-calls/min budget for zero new information. Daily bars are the one "
        "granularity this plan actually serves, so that's what this checks — across a window, not a single "
        "day, so gaps/continuity are actually visible with one call per symbol."
    )
    try:
        provider = PolygonAdapter()
    except ValueError as exc:
        print(f"Can't run this part: {exc}")
        return

    # A wide window (not "yesterday specifically") deliberately sidesteps
    # needing to compute the exact most-recently-completed ET trading day
    # from UTC "now" — Polygon only ever returns a bar for a day once that
    # day's session has actually closed, so a wide range just naturally
    # includes whatever's actually complete without any ET/UTC boundary
    # math that could get the boundary wrong depending what time of day
    # this runs (a real risk here specifically: Saqib runs this from
    # Bangladesh, UTC+6, up to ~10-11 hours off US Eastern).
    window_days = 14
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)

    for symbol in symbols:
        print(f"\n--- {symbol} ---")
        try:
            bars = await provider.get_historical(symbol, "1d", window_start, window_end)
        except HistoricalDataUnavailableError as exc:
            print(f"daily bars: UNAVAILABLE — {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — spike script, want to see any failure mode raw
            print(f"daily bars: FAILED with {type(exc).__name__}: {exc}")
            continue

        if not bars:
            print(f"daily bars: none returned across the last {window_days} calendar days — worth a closer look, this shouldn't be empty for a liquid ETF")
            continue

        # Rough expectation only — doesn't account for market holidays,
        # just gives a sanity ballpark, not a precise pass/fail.
        expected_weekdays = sum(
            1 for i in range(window_days) if (window_start + timedelta(days=i)).weekday() < 5
        )
        zero_volume_days = sum(1 for b in bars if b.volume == 0)
        volumes = [b.volume for b in bars]
        closes = [b.close for b in bars]

        print(f"  {len(bars)} daily bars returned (~{expected_weekdays} weekdays in the {window_days}-day window — some gap is normal for holidays, a LARGE gap isn't)")
        print(f"  date range: {bars[0].candle_ts} -> {bars[-1].candle_ts}")
        print(f"  volume range: {min(volumes):,.0f} - {max(volumes):,.0f}, {zero_volume_days} zero-volume day(s)")
        print(f"  close price range: {min(closes):.2f} - {max(closes):.2f}")
        print(f"  most recent bar: {bars[-1]}")


# --- Part 2: raw Finnhub WS trade capture with conditions -------------------


def _connect_url(api_key: str) -> str:
    return _WS_URL_TEMPLATE.format(token=api_key)


def _safe_str(exc: BaseException) -> str:
    """The one job of an error handler is to not itself crash. str(exc) is
    usually safe but isn't guaranteed for every exception type (e.g.
    websockets' InvalidStatus.__str__ dereferences a response object that
    only exists on a properly-constructed instance) — cheap insurance."""
    try:
        return str(exc)
    except Exception:  # noqa: BLE001 — deliberately broad, this IS the fallback path
        return repr(exc)


async def _capture_trades(symbols: list[str], capture_seconds: int, api_key: str) -> dict[str, list[dict]]:
    _print_header(f"PART 2 — live Finnhub trade capture, {capture_seconds}s, symbols={symbols}")
    captured: dict[str, list[dict]] = defaultdict(list)

    try:
        ws_cm = websockets.connect(_connect_url(api_key))
        ws = await ws_cm.__aenter__()
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        print(f"Couldn't open the WebSocket connection — {type(exc).__name__}: {_safe_str(exc)}")
        print("(the REST key working elsewhere doesn't guarantee WS access — different endpoint, worth checking separately if this fails)")
        return captured

    try:
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
    finally:
        await ws_cm.__aexit__(None, None, None)

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
        "Part 1 tells you whether Polygon's daily bars are usable for SPY/QQQ/IWM the same way\n"
        "they are for single-name stocks — the only granularity the free/Basic tier actually serves\n"
        "(decision #39). Part 2 tells you two things at once: whether Finnhub's real-time stream\n"
        "covers these three symbols with normal trade frequency (M0 #3), and whether a tick-rule\n"
        "signed-volume proxy is computable from data already flowing through the system today with\n"
        "zero new external dependencies (M0 #4's raw evidence). Neither part computes a go/no-go\n"
        "verdict for you — that's a judgment call once you've read the numbers."
    )


if __name__ == "__main__":
    asyncio.run(main())
