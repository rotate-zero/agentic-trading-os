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
   cash-flow, for revenue_ttm/net_income_ttm/operating_cash_flow_ttm.
   Structure is confirmed against a live key, and concept-tag consistency
   across a symbol's own quarters is ALSO confirmed (AAPL/TSLA both show
   the same three concepts across 4 straight quarterly reports) — but
   TWO real problems have surfaced past that point, both caught by
   running actual numbers rather than trusting the shape alone:
     (a) freq='quarterly' only returns 10-Q filings; neither AAPL nor
         TSLA files a 10-Q for their fiscal-year-end quarter (that
         quarter's numbers live in the 10-K instead), so "last 4 by
         endDate" silently skips a calendar quarter.
     (b) More seriously: the annual-vs-quarterly sanity check
         (_inspect_annual_concepts) caught AAPL's and TSLA's "quarterly"
         revenues summing to 124% and 62% MORE than their own annual
         revenue — structurally impossible for 4 genuinely discrete,
         non-overlapping quarters. TSLA's raw values even show the
         signature directly: 19.3B -> 41.8B -> 69.9B climbing through
         calendar 2025, then dropping to 22.4B the moment a new fiscal
         year starts. That's cumulative year-to-date data, not discrete
         quarters. The likely cause: a single 10-Q's XBRL data commonly
         tags BOTH a discrete 'three months ended' figure and a
         cumulative 'year-to-date' figure under the SAME concept name,
         and _find_concept's {concept: value} dict silently keeps
         whichever occurrence comes last, with no warning.
   _dump_raw_concept_occurrences() now fires automatically when the
   sum-vs-annual check looks wrong, printing every raw occurrence (not
   deduped) so the actual duplicate structure is visible directly rather
   than guessed at. The fix itself — picking the right occurrence, or
   differencing consecutive cumulative values to recover a true discrete
   quarter — is real FundamentalsProvider implementation work with tests,
   not something to bolt onto a spike script.
3. /calendar/earnings (earnings_calendar) — next_earnings_date.
4. /company-news (company_news) — candidate NewsFlagProvider source.
   Checked specifically for whether each article's 'datetime' field is a
   real per-article unix timestamp (needed for recency_seconds/count_15m,
   decision #90's output shape) rather than just a calendar date.

Runs two real bursts of raw HTTP calls to find ACTUAL rate ceilings and any
X-Ratelimit-* response headers Finnhub actually sends — same "confirm
empirically, don't trust documentation alone" discipline already applied
to Polygon's plan limits (decision #30) and the pre-market 1m-bar check
(check_premarket_data_availability.py):
  - /stock/profile2: every run so far shows it sharing a 60/min bucket
    with /stock/financials-reported specifically (confirmed 3 separate
    times: pre-burst consumption always matches exactly
    3×profile2 + 3×financials-reported calls, regardless of how many
    earnings-calendar/company-news calls happened in between).
  - /company-news: closes a real gap — every run so far only showed this
    endpoint NOT draining the profile2/financials-reported bucket, never
    what its OWN ceiling actually is. NewsFlagProvider will poll this on
    its own cadence, so it gets its own dedicated burst rather than an
    inferred "probably fine."
Uses `requests` directly for both (not the finnhub-python client)
specifically to see response headers on EVERY call, not just the one that
finally fails — finnhub-python only attaches `.response` to its exception
on failure. `requests` is not a direct project dependency, but ships
transitively via finnhub-python (confirmed by FinnhubAPIException's own
use of response.json()/.status_code/.text — that's the `requests`
Response API) — no new dependency added.

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
from collections import Counter
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
_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"
_RATELIMIT_HEADERS = ("X-Ratelimit-Limit", "X-Ratelimit-Remaining", "X-Ratelimit-Reset", "Retry-After")

_TTM_REPORT_COUNT = 4  # a trailing-twelve-months figure needs exactly 4 quarters — no more, no less
# Not an exhaustive XBRL taxonomy list — just the handful of concept names
# companies commonly use for each line item. Listed in rough order of how
# often real filings use them; first match wins per report.
_REVENUE_CONCEPTS = [
    "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap_RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap_Revenues",
    "us-gaap_SalesRevenueNet",
]
_NET_INCOME_CONCEPTS = [
    "us-gaap_NetIncomeLoss",
    "us-gaap_ProfitLoss",
]
_OPERATING_CASH_FLOW_CONCEPTS = [
    "us-gaap_NetCashProvidedByUsedInOperatingActivities",
    "us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]


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


def _find_concept(line_items: list[dict], candidates: list[str]) -> tuple[str, float] | None:
    by_concept = {item.get("concept"): item.get("value") for item in line_items}
    for candidate in candidates:
        if candidate in by_concept:
            return candidate, by_concept[candidate]
    return None


def _find_all_occurrences(line_items: list[dict], candidates: list[str]) -> list[tuple[str, float]]:
    """Unlike _find_concept, does NOT dedupe by concept name — returns every
    matching line item in original list order. Exists because _find_concept's
    {concept: value} dict silently keeps whichever occurrence comes LAST if a
    concept appears more than once, with no warning. A single 10-Q commonly
    tags both a discrete 'three months ended' figure and a cumulative
    'year-to-date' figure under the SAME us-gaap_ concept — if that's what's
    happening here, _find_concept could easily be picking the wrong one."""
    return [(item.get("concept"), item.get("value")) for item in line_items if item.get("concept") in candidates]


def _dump_raw_concept_occurrences(symbol: str, report: dict) -> None:
    """Diagnostic triggered when the annual-vs-quarterly sanity check looks
    wrong: does this report actually contain MULTIPLE line items under the
    same concept (discrete-quarter vs cumulative-YTD), or is _find_concept's
    single result the only one there is? Answers that directly instead of
    guessing at a fix."""
    rpt = report.get("report", {})
    print(f"\n  Raw (non-deduped) concept occurrences in the {report.get('endDate')} ({report.get('form')}) report:")
    for label, section, candidates in (
        ("revenue", rpt.get("ic", []), _REVENUE_CONCEPTS),
        ("net_income", rpt.get("ic", []), _NET_INCOME_CONCEPTS),
        ("operating_cash_flow", rpt.get("cf", []), _OPERATING_CASH_FLOW_CONCEPTS),
    ):
        occurrences = _find_all_occurrences(section, candidates)
        if not occurrences:
            print(f"    {label}: no occurrences at all")
        elif len(occurrences) == 1:
            print(f"    {label}: 1 occurrence — {occurrences[0][0]}: {occurrences[0][1]:,.0f} (not the source of the discrepancy)")
        else:
            print(f"    {label}: {len(occurrences)} occurrences (DUPLICATE — _find_concept only ever saw the LAST one):")
            for idx, (concept, value) in enumerate(occurrences):
                print(f"      [{idx}] {concept}: {value:,.0f}")


def _inspect_ttm_concepts(symbol: str, reports: list[dict]) -> None:
    """Does the last N quarters' financials-reported data actually support
    revenue_ttm/net_income_ttm/operating_cash_flow_ttm, or does the concept
    tag drift underneath us between quarters? Reports exactly what matched
    per quarter rather than assuming consistency from a single sample."""
    if not reports:
        return

    forms = Counter(r.get("form") for r in reports)
    print(f"Form types across all {len(reports)} returned reports: {dict(forms)}")

    # endDate is a 'YYYY-MM-DD' string per Finnhub's own convention — safe to sort lexicographically
    dated = [r for r in reports if r.get("endDate")]
    dated.sort(key=lambda r: r["endDate"], reverse=True)
    recent = dated[:_TTM_REPORT_COUNT]

    if len(recent) < _TTM_REPORT_COUNT:
        print(f"NOTE: only {len(recent)} dated reports available, fewer than the {_TTM_REPORT_COUNT} needed for a clean TTM sum.")

    print(f"\nInspecting the {len(recent)} most recent reports for {symbol} (newest first):")
    metric_matches: dict[str, list[str | None]] = {"revenue": [], "net_income": [], "operating_cash_flow": []}

    for report in recent:
        end_date = report.get("endDate")
        form = report.get("form")
        rpt = report.get("report", {})
        ic = rpt.get("ic", [])
        cf = rpt.get("cf", [])

        rev = _find_concept(ic, _REVENUE_CONCEPTS)
        ni = _find_concept(ic, _NET_INCOME_CONCEPTS)
        ocf = _find_concept(cf, _OPERATING_CASH_FLOW_CONCEPTS)

        metric_matches["revenue"].append(rev[0] if rev else None)
        metric_matches["net_income"].append(ni[0] if ni else None)
        metric_matches["operating_cash_flow"].append(ocf[0] if ocf else None)

        def _fmt(match: tuple[str, float] | None) -> str:
            return f"{match[0]}: {match[1]:,.0f}" if match else "NO MATCH from candidate list"

        print(f"  {end_date} ({form}):")
        print(f"    revenue             -> {_fmt(rev)}")
        print(f"    net_income          -> {_fmt(ni)}")
        print(f"    operating_cash_flow -> {_fmt(ocf)}")

    print(f"\nConcept-tag consistency across these {len(recent)} quarters:")
    for metric, matches in metric_matches.items():
        distinct = {m for m in matches if m is not None}
        none_count = matches.count(None)
        if none_count:
            print(f"  {metric}: {none_count}/{len(matches)} quarters had NO match at all — candidate list needs widening before this metric is usable.")
        elif len(distinct) == 1:
            print(f"  {metric}: SAME concept ('{distinct.pop()}') matched in all {len(matches)} quarters — safe to sum for TTM.")
        else:
            print(f"  {metric}: concept tag CHANGED across quarters ({sorted(distinct)}) — summing these directly would silently mix definitions. Needs a mapping step, not a straight sum.")


def _inspect_annual_concepts(symbol: str, quarterly_reports: list[dict], annual_reports: list[dict]) -> None:
    """Closes the last real unknown from the quarterly check: the fiscal
    Q4/year-end period never shows up in freq='quarterly' results because
    that period's numbers get filed via 10-K, not 10-Q. The standard
    analyst technique for a true TTM is deriving that missing quarter as
    (annual figure) - (sum of the 3 quarters within that fiscal year) —
    but that only works if the annual report uses the SAME concept tags.
    This checks exactly that, without doing the full subtraction/alignment
    logic itself — that belongs in FundamentalsProvider's real
    implementation with proper tests, not a disposable spike script."""
    if not annual_reports:
        print("No annual (10-K) reports returned at all — the derive-missing-quarter approach isn't available; freq='annual' returns nothing for this symbol.")
        return

    dated = [r for r in annual_reports if r.get("endDate")]
    if not dated:
        print("Annual reports returned but none have a usable endDate — inspect the raw payload before trusting this path.")
        return
    dated.sort(key=lambda r: r["endDate"], reverse=True)
    latest = dated[0]

    rpt = latest.get("report", {})
    rev = _find_concept(rpt.get("ic", []), _REVENUE_CONCEPTS)
    ni = _find_concept(rpt.get("ic", []), _NET_INCOME_CONCEPTS)
    ocf = _find_concept(rpt.get("cf", []), _OPERATING_CASH_FLOW_CONCEPTS)

    print(f"Most recent annual report: form={latest.get('form')} endDate={latest.get('endDate')}")

    def _fmt(match: tuple[str, float] | None) -> str:
        return f"{match[0]}: {match[1]:,.0f}" if match else "NO MATCH from candidate list"

    print(f"  revenue             -> {_fmt(rev)}")
    print(f"  net_income          -> {_fmt(ni)}")
    print(f"  operating_cash_flow -> {_fmt(ocf)}")

    same_tags = (
        (rev[0] if rev else None) in _REVENUE_CONCEPTS
        and (ni[0] if ni else None) in _NET_INCOME_CONCEPTS
        and (ocf[0] if ocf else None) in _OPERATING_CASH_FLOW_CONCEPTS
        and rev and ni and ocf
    )
    if not same_tags:
        print("VERDICT: at least one metric had no match on the annual report — the derive-missing-quarter plug isn't safely available as-is for this symbol without widening the candidate list.")
        return

    # Sort explicitly rather than trusting API response order — same fix
    # already applied in _inspect_ttm_concepts, needed here too since this
    # function was slicing quarterly_reports[:4] without sorting first.
    dated_quarters = [q for q in quarterly_reports if q.get("endDate")]
    dated_quarters.sort(key=lambda q: q["endDate"], reverse=True)
    recent_quarters = dated_quarters[:_TTM_REPORT_COUNT]

    quarterly_revenues: list[tuple[str, float]] = []
    for q in recent_quarters:
        q_rev = _find_concept(q.get("report", {}).get("ic", []), _REVENUE_CONCEPTS)
        if q_rev:
            quarterly_revenues.append((q["endDate"], q_rev[1]))

    if not (quarterly_revenues and rev):
        print("VERDICT: same concept family matched on the annual report — plug-derivation looks structurally available, but no quarterly revenue figures to sanity-check the magnitude against.")
        return

    quarterly_sum = sum(v for _, v in quarterly_revenues)
    print(
        f"\nDirect self-consistency check: sum of the {len(quarterly_revenues)} quarterly revenues just inspected "
        f"({', '.join(f'{d}: {v:,.0f}' for d, v in quarterly_revenues)}) = {quarterly_sum:,.0f}"
    )
    print(f"vs. most recent annual revenue = {rev[1]:,.0f}")

    if quarterly_sum > rev[1] * 1.15:
        overshoot_pct = (quarterly_sum / rev[1] - 1) * 100
        print(
            f"VERDICT: quarters sum to {overshoot_pct:.0f}% MORE than the full year — not explainable by period "
            f"offset or growth alone. If these were genuinely 4 discrete, non-overlapping quarters, their sum "
            f"should land close to the annual figure, not nearly double it. Most likely cause: at least one "
            f"quarterly report tags BOTH a discrete 'three months ended' figure and a cumulative "
            f"'year-to-date' figure under the same concept name, and _find_concept's dict-based lookup is "
            f"silently keeping the wrong one. Dumping every raw occurrence for the most recent quarter to "
            f"confirm directly:"
        )
        if recent_quarters:
            _dump_raw_concept_occurrences(symbol, recent_quarters[0])
        print(
            "\nIf duplicates show up above: the fix is picking the discrete-quarter occurrence specifically "
            "(or, if only a cumulative figure is ever tagged, differencing consecutive cumulative values "
            "within a fiscal year to recover the discrete quarter) — real work for FundamentalsProvider's "
            "implementation, not something to guess at here."
        )
    else:
        print(
            f"VERDICT: quarterly sum is within a plausible range of the annual figure "
            f"({quarterly_sum / rev[1]:.2f}x) — supports treating these as genuinely discrete, "
            f"non-overlapping quarters safe to sum for TTM."
        )


def _run_ratelimit_burst(api_key: str, endpoint_url: str, endpoint_label: str, params: dict) -> None:
    _print_header(f"BURST TEST — {_BURST_CALLS} rapid calls to {endpoint_label}")
    print(
        "Firing calls back-to-back with no throttling to find the REAL ceiling "
        f"(documented general limit: {get_settings().finnhub_max_calls_per_minute}/min). "
        "Watching for the first non-200 and for any X-Ratelimit-*/Retry-After headers.\n"
    )
    start = time.monotonic()
    first_failure_at: tuple[int, float] | None = None
    remaining_trend: list[int] = []  # tracks X-Ratelimit-Remaining across calls, when Finnhub sends it
    for i in range(1, _BURST_CALLS + 1):
        call_start = time.monotonic()
        try:
            resp = requests.get(endpoint_url, params={**params, "token": api_key}, timeout=10)
        except requests.RequestException as exc:
            print(f"  call {i:>2}: transport error — {exc}")
            continue
        elapsed = time.monotonic() - call_start
        headers_seen = {h: resp.headers[h] for h in _RATELIMIT_HEADERS if h in resp.headers}
        header_str = f" | headers: {headers_seen}" if headers_seen else ""
        print(f"  call {i:>2}: status={resp.status_code} elapsed={elapsed:.3f}s{header_str}")
        if "X-Ratelimit-Remaining" in resp.headers:
            try:
                remaining_trend.append(int(resp.headers["X-Ratelimit-Remaining"]))
            except ValueError:
                pass
        if resp.status_code != 200 and first_failure_at is None:
            first_failure_at = (i, time.monotonic() - start)
            print(f"    -> FIRST FAILURE at call #{i}, {first_failure_at[1]:.2f}s into the burst. Body: {resp.text[:300]}")

    total_elapsed = time.monotonic() - start
    print(f"\nBurst finished in {total_elapsed:.2f}s.")
    if first_failure_at is None and remaining_trend:
        print(
            f"VERDICT: all {_BURST_CALLS} calls succeeded within {total_elapsed:.2f}s. Finnhub DID send "
            f"X-Ratelimit-Remaining on every call — it dropped from {remaining_trend[0]} to {remaining_trend[-1]} "
            f"over the burst, so quota tracking is real, it just wasn't exhausted by {_BURST_CALLS} calls in "
            f"this window. At this drain rate the bucket would hit 0 after roughly "
            f"{remaining_trend[-1]} more calls before the window resets — widen _BURST_CALLS if you want to "
            f"actually see the 429 and confirm the documented ceiling directly."
        )
    elif first_failure_at is None:
        print(
            f"VERDICT: all {_BURST_CALLS} calls succeeded within {total_elapsed:.2f}s with no rate-limit "
            f"response and no X-Ratelimit-* headers observed at all. Either the ceiling is comfortably above "
            f"{_BURST_CALLS} calls in this window, or Finnhub doesn't expose remaining-quota headers on "
            f"this tier/endpoint — widen _BURST_CALLS and rerun if you want a tighter bound."
        )
    else:
        idx, when = first_failure_at
        rate = (idx - 1) / when if when > 0 else float("inf")
        print(
            f"VERDICT: real ceiling is approximately {idx - 1} calls per {when:.2f}s (~{rate:.1f} calls/sec) "
            f"for {endpoint_label} specifically — compare against the documented "
            f"{get_settings().finnhub_max_calls_per_minute}/min general figure before trusting it for "
            f"the relevant provider's refresh-schedule design."
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
        quarterly_data: list = []
        try:
            fin_q = await check_financials_reported(client, symbol, "quarterly")
            quarterly_data = fin_q.get("data", []) if isinstance(fin_q, dict) else []
            print(f"Top-level keys: {list(fin_q.keys()) if isinstance(fin_q, dict) else type(fin_q)}")
            print(f"Number of quarterly reports returned: {len(quarterly_data)}")
            if quarterly_data:
                report = quarterly_data[0]
                print(f"Most recent report's top-level keys: {list(report.keys())}")
                print(f"Financial statement sections present: {list(report.get('report', {}).keys())}")
                _inspect_ttm_concepts(symbol, quarterly_data)
            else:
                print(f"NOTE: no quarterly reports for {symbol} — expected for ETFs (SPY/QQQ/IWM file no 10-Qs).")
        except finnhub.exceptions.FinnhubAPIException as exc:
            print(f"FAILED: status={exc.status_code} message={exc.message}")

        print("\n--- /stock/financials-reported (freq=annual) — closing the missing-Q4 gap ---")
        try:
            fin_a = await check_financials_reported(client, symbol, "annual")
            annual_data = fin_a.get("data", []) if isinstance(fin_a, dict) else []
            print(f"Number of annual reports returned: {len(annual_data)}")
            if annual_data:
                _inspect_annual_concepts(symbol, quarterly_data, annual_data)
            else:
                print(f"NOTE: no annual reports for {symbol} — expected for ETFs.")
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

    _run_ratelimit_burst(
        settings.finnhub_api_key, _PROFILE2_URL, "/stock/profile2", {"symbol": symbols[0]}
    )

    # Closes the M0 #2 gap: every run so far has shown company-news calls
    # NOT denting the same X-Ratelimit-Remaining counter profile2 and
    # financials-reported share (60→53 pattern held across three separate
    # sessions) — but that only tells us it's not IN that bucket, not what
    # its own ceiling actually is. NewsFlagProvider will poll this
    # endpoint on its own cadence, so it deserves its own real number
    # rather than an inferred "probably fine."
    news_window_end = date.today()
    news_window_start = news_window_end - timedelta(days=7)
    _run_ratelimit_burst(
        settings.finnhub_api_key,
        _COMPANY_NEWS_URL,
        "/company-news",
        {"symbol": symbols[0], "from": news_window_start.isoformat(), "to": news_window_end.isoformat()},
    )

    _print_header("OVERALL — read the per-symbol sections above before trusting this project-wide")
    print(
        "This script only reports what came back for each call — it deliberately does not\n"
        "compute a single pass/fail verdict across all four endpoints, since 'does this fit\n"
        "symbol_fundamentals' is a judgment call about the actual JSON shapes above, not\n"
        "something worth guessing at from inside the script."
    )


if __name__ == "__main__":
    asyncio.run(main())
