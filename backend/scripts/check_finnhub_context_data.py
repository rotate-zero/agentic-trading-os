"""
Empirical check for Context Engine's v1 provider set (decision #90) — does
Finnhub's free tier actually serve what FundamentalsProvider and
NewsFlagProvider need, at the depth/rate documented, not just what the docs
claim? M0 items #1 and #2 from the Market State + Context Engine build list.

Four separate questions, each a real gate before any Context Engine code
gets written:

1. /stock/profile2 (company_profile2) — sector/industry, for
   symbol_fundamentals.profile_updated_at fields
   (trading-intelligence-architecture.md §5).
2. /stock/financials-reported (financials_reported) — income/balance/
   cash-flow, for revenue_ttm/net_income_ttm/operating_cash_flow_ttm. This
   endpoint's nested concept/value report shape is NOT hardcoded here —
   dumped raw (top-level keys + one sample line item) instead, since
   nobody has confirmed the real shape against a live key yet. TTM
   requires summing trailing quarters; whether that's cleanly derivable
   from this response is itself part of what this checks.
3. /calendar/earnings (earnings_calendar) — next_earnings_date.
4. /company-news (company_news) — candidate NewsFlagProvider source.
   Checked specifically for whether each article's 'datetime' field is a
   real per-article unix timestamp (needed for recency_seconds/count_15m,
   decision #90's output shape) rather than just a calendar date.

Also runs a real burst of raw HTTP calls against /stock/profile2 (cheapest
of the four) to observe the ACTUAL rate ceiling and any X-Ratelimit-*
response headers Finnhub actually sends — same "confirm empirically, don't
trust documentation alone" discipline already applied to Polygon's plan
limits (decision #30) and the pre-market 1m-bar check
(check_premarket_data_availability.py). Uses `requests` directly for this
part (not the finnhub-python client) specifically to see response headers
on EVERY call, not just the one that finally fails — finnhub-python only
attaches `.response` to its exception on failure. `requests` is not a
direct project dependency, but ships transitively via finnhub-python
(confirmed by FinnhubAPIException's own use of response.json()/
.status_code/.text — that's the `requests` Response API) — no new
dependency added.

Test symbols deliberately include SPY: ETFs typically have no
profile/financials/earnings the way single-name stocks do. If profile2/
financials-reported/earnings_calendar all come back empty for SPY, that's
a real finding for decision #91's SPY/QQQ/IWM data-quality question, not
a bug in this script — noted in the verdict, not silently treated as a
failed check.

Needs FINNHUB_API_KEY set (.env or environment) — this hits the real
Finnhub API, not a mock. No network access to finnhub.io exists in the
sandbox this script was written in (same standing gap as the Polygon
default noted in core/config.py) — it has not been run against a live
key. Run it yourself and read the printed verdicts.

Usage:
    cd backend
    python scripts/check_finnhub_context_data.py
    python scripts/check_finnhub_context_data.py TSLA NVDA SPY   # custom symbols
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Running this as `python scripts/foo.py` does NOT put backend/ on
# sys.path — Python adds the script's OWN directory, not the invoking
# cwd. Same fix as every other script in this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import finnhub  # noqa: E402
import requests  # noqa: E402

from app.core.config import get_settings  # noqa: E402

_DEFAULT_SYMBOLS = ["AAPL", "TSLA", "SPY"]
_BURST_CALLS = 40  # documented general limit is 60/min; enough to find the real ceiling without wasting budget for the rest of the day
_PROFILE2_URL = "https://finnhub.io/api/v1/stock/profile2"
_RATELIMIT_HEADERS = ("X-Ratelimit-Limit", "X-Ratelimit-Remaining", "X-Ratelimit-Reset", "Retry-After")


def _print_header(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _truncate(obj: object, limit: int = 800) -> str:
    text = json.dumps(obj, indent=2, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n... [truncated, {len(text)} chars total]"


async def check_profile2(client: finnhub.Client, symbol: str) -> dict | list:
    return await asyncio.to_thread(client.company_profile2, symbol=symbol)


async def check_financials_reported(client: finnhub.Client, symbol: str, freq: str) -> dict:
    return await asyncio.to_thread(client.financials_reported, symbol=symbol, freq=freq)


async def check_earnings_calendar(client: finnhub.Client, symbol: str) -> dict:
    today = date.today()
    horizon = today + timedelta(days=180)  # generous — next_earnings_date could be up to a quarter out
    return await asyncio.to_thread(
        client.earnings_calendar,
        _from=today.isoformat(),
        to=horizon.isoformat(),
        symbol=symbol,
    )


async def check_company_news(client: finnhub.Client, symbol: str) -> list:
    today = date.today()
    week_ago = today - timedelta(days=7)
    return await asyncio.to_thread(
        client.company_news,
        symbol=symbol,
        _from=week_ago.isoformat(),
        to=today.isoformat(),
    )


def _inspect_news_timestamps(articles: list) -> str:
    if not articles:
        return "no articles returned in the last 7 days — can't assess timestamp granularity from this sample"
    sample = articles[0]
    dt_val = sample.get("datetime")
    if dt_val is None:
        return "articles have NO 'datetime' field at all — recency_seconds/count_15m are not derivable from this endpoint as-is"
    # Finnhub's documented convention is a unix seconds timestamp. A value
    # in the billions range confirms seconds-resolution (not just a date);
    # anything smaller is suspicious and worth eyeballing the raw payload.
    if isinstance(dt_val, (int, float)) and dt_val > 1_000_000_000:
        return f"'datetime' looks like a real unix-seconds timestamp ({dt_val}) — recency_seconds/count_15m should be derivable"
    return f"'datetime' present but doesn't look like a plausible unix-seconds value ({dt_val!r}) — inspect the raw payload above before relying on it"


def _run_ratelimit_burst(api_key: str, symbol: str) -> None:
    _print_header(f"BURST TEST — {_BURST_CALLS} rapid calls to /stock/profile2 (symbol={symbol})")
    print(
        "Firing calls back-to-back with no throttling to find the REAL ceiling "
        f"(documented general limit: {get_settings().finnhub_max_calls_per_minute}/min). "
        "Watching for the first non-200 and for any X-Ratelimit-*/Retry-After headers.\n"
    )
    start = time.monotonic()
    first_failure_at: tuple[int, float] | None = None
    for i in range(1, _BURST_CALLS + 1):
        call_start = time.monotonic()
        try:
            resp = requests.get(_PROFILE2_URL, params={"symbol": symbol, "token": api_key}, timeout=10)
        except requests.RequestException as exc:
            print(f"  call {i:>2}: transport error — {exc}")
            continue
        elapsed = time.monotonic() - call_start
        headers_seen = {h: resp.headers[h] for h in _RATELIMIT_HEADERS if h in resp.headers}
        header_str = f" | headers: {headers_seen}" if headers_seen else ""
        print(f"  call {i:>2}: status={resp.status_code} elapsed={elapsed:.3f}s{header_str}")
        if resp.status_code != 200 and first_failure_at is None:
            first_failure_at = (i, time.monotonic() - start)
            print(f"    -> FIRST FAILURE at call #{i}, {first_failure_at[1]:.2f}s into the burst. Body: {resp.text[:300]}")

    total_elapsed = time.monotonic() - start
    print(f"\nBurst finished in {total_elapsed:.2f}s.")
    if first_failure_at is None:
        print(
            f"VERDICT: all {_BURST_CALLS} calls succeeded within {total_elapsed:.2f}s with no rate-limit "
            f"response and no X-Ratelimit-* headers observed. Either the ceiling is comfortably above "
            f"{_BURST_CALLS} calls in this window, or Finnhub doesn't expose remaining-quota headers on "
            f"this tier/endpoint — widen _BURST_CALLS and rerun if you want a tighter bound."
        )
    else:
        idx, when = first_failure_at
        rate = (idx - 1) / when if when > 0 else float("inf")
        print(
            f"VERDICT: real ceiling is approximately {idx - 1} calls per {when:.2f}s (~{rate:.1f} calls/sec) "
            f"for /stock/profile2 specifically — compare against the documented "
            f"{get_settings().finnhub_max_calls_per_minute}/min general figure before trusting it for "
            f"FundamentalsProvider's refresh-schedule design."
        )


async def main() -> None:
    symbols = sys.argv[1:] if len(sys.argv) > 1 else _DEFAULT_SYMBOLS
    settings = get_settings()
    if not settings.finnhub_api_key:
        print("Can't run this check: FINNHUB_API_KEY is not set (.env or environment).")
        return

    client = finnhub.Client(api_key=settings.finnhub_api_key)

    for symbol in symbols:
        _print_header(f"SYMBOL: {symbol}")

        print("\n--- /stock/profile2 ---")
        try:
            profile = await check_profile2(client, symbol)
            print(_truncate(profile))
            if not profile:
                print(f"NOTE: empty response for {symbol} — expected for ETFs (SPY/QQQ/IWM have no company profile).")
        except finnhub.exceptions.FinnhubAPIException as exc:
            print(f"FAILED: status={exc.status_code} message={exc.message}")

        print("\n--- /stock/financials-reported (freq=quarterly) ---")
        try:
            fin_q = await check_financials_reported(client, symbol, "quarterly")
            data = fin_q.get("data", []) if isinstance(fin_q, dict) else []
            print(f"Top-level keys: {list(fin_q.keys()) if isinstance(fin_q, dict) else type(fin_q)}")
            print(f"Number of quarterly reports returned: {len(data)}")
            if data:
                report = data[0]
                print(f"Most recent report's top-level keys: {list(report.keys())}")
                bs = report.get("report", {}).get("bs", [])
                if bs:
                    print(f"Sample balance-sheet line item shape: {_truncate(bs[0], 300)}")
                print(f"Financial statement sections present: {list(report.get('report', {}).keys())}")
            else:
                print(f"NOTE: no quarterly reports for {symbol} — expected for ETFs (SPY/QQQ/IWM file no 10-Qs).")
        except finnhub.exceptions.FinnhubAPIException as exc:
            print(f"FAILED: status={exc.status_code} message={exc.message}")

        print("\n--- /calendar/earnings ---")
        try:
            earnings = await check_earnings_calendar(client, symbol)
            events = earnings.get("earningsCalendar", []) if isinstance(earnings, dict) else []
            print(f"Upcoming earnings events found (next 180 days): {len(events)}")
            if events:
                print(_truncate(events[0]))
            else:
                print(f"NOTE: no upcoming earnings for {symbol} in this window — expected for ETFs, or just means the next date is further out.")
        except finnhub.exceptions.FinnhubAPIException as exc:
            print(f"FAILED: status={exc.status_code} message={exc.message}")

        print("\n--- /company-news (last 7 days) ---")
        try:
            news = await check_company_news(client, symbol)
            print(f"Articles found: {len(news)}")
            if news:
                print(_truncate(news[0], 500))
            print(f"Timestamp check: {_inspect_news_timestamps(news)}")
        except finnhub.exceptions.FinnhubAPIException as exc:
            print(f"FAILED: status={exc.status_code} message={exc.message}")

    _run_ratelimit_burst(settings.finnhub_api_key, symbols[0])

    _print_header("OVERALL — read the per-symbol sections above before trusting this project-wide")
    print(
        "This script only reports what came back for each call — it deliberately does not\n"
        "compute a single pass/fail verdict across all four endpoints, since 'does this fit\n"
        "symbol_fundamentals' is a judgment call about the actual JSON shapes above, not\n"
        "something worth guessing at from inside the script."
    )


if __name__ == "__main__":
    asyncio.run(main())
