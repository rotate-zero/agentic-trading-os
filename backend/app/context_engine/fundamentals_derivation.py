"""
TTM (trailing-twelve-month) derivation from Finnhub's `/stock/financials-
reported` — decision #94's validated method, reimplemented here as a
pure, independently-testable function rather than inline in the refresh
job that calls it.

**The core finding this implements (decision #94):** each quarterly
report's line items are fiscal-year-to-date CUMULATIVE figures, not
discrete 3-month values — confirmed against real AAPL/TSLA data (TSLA's
raw revenue sequence: 19.3B -> 41.8B -> 69.9B climbing through calendar
2025, dropping to 22.4B the instant FY2026 starts). A discrete quarter's
value = its own cumulative figure minus the previous quarter's cumulative
- UNLESS the value dropped relative to the previous quarter, which is
itself the signal a new fiscal year just started (a cumulative YTD figure
cannot decrease within a fiscal year for the metrics this covers), in
which case that quarter's cumulative figure IS already discrete - it's
the new fiscal year's Q1. This reset signal is structurally reliable, not
just a heuristic that usually works: `/stock/financials-reported`'s
`freq=quarterly` only ever returns standalone 10-Qs, and a fiscal year
has exactly three of those (Q4 is always embedded in the 10-K instead,
never filed standalone) - so two chronologically-adjacent quarterly
reports can ONLY show a cumulative decrease at exactly a fiscal-year
rollover, never mid-year.

**The Q4 gap this creates, and how it's filled.** Because standalone
10-Qs never include a fiscal year's Q4, TTM's 4-most-recent-quarters
window will cross exactly one such gap whenever the current fiscal year
has fewer than 3 quarters filed so far (i.e., whenever the most recent
raw quarterly report is Q1 or Q2 of its fiscal year) - filled via that
prior fiscal year's 10-K total minus its own last quarterly cumulative.
v1 scope: handles exactly one such gap. A window needing two or more
gap-fills (very sparse quarterly history) returns None rather than a
guess - see "Honest-state posture" below.

**Concept-tag lookup** (`_find_concept`) reuses the exact candidate lists
and first-match-wins approach `backend/scripts/check_finnhub_context_
data.py` already validated empirically for AAPL/TSLA - concept-tag
consistency across quarters (same `us-gaap_` tag throughout) was
confirmed for both, so this doesn't need per-symbol special-casing.

**Honest-state posture:** any metric this can't confidently derive 4
discrete quarters for (concept not found, insufficient history, more
than one gap) returns `None` rather than guessing - a missing TTM is more
honest than a wrong one, same principle decision #94 itself states for
the ETF-empty-response case.

**What this has NOT been re-verified against:** M0's spike script
validated this reasoning against two real symbols (AAPL, TSLA) via a
human reading raw output; no clean, reusable derivation function existed
to lift verbatim, only a diagnostic one. This reimplementation is
unit-tested against synthetic data shaped to match that exact validated
pattern (tests/test_fundamentals_derivation.py), not against a second
live Finnhub call - this session has no live Finnhub key or network
access to `finnhub.io`. Worth a real smoke test against a live key before
trusting this in production, flagged here rather than presented as
re-confirmed.
"""
from __future__ import annotations

_TTM_REPORT_COUNT = 4  # a trailing-twelve-months figure needs exactly 4 discrete quarters -- no more, no less

# Same candidate lists, same order, as check_finnhub_context_data.py's
# already-validated concept lookup -- first match wins per report.
REVENUE_CONCEPTS = [
    "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap_RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap_Revenues",
    "us-gaap_SalesRevenueNet",
]
NET_INCOME_CONCEPTS = [
    "us-gaap_NetIncomeLoss",
    "us-gaap_ProfitLoss",
]
OPERATING_CASH_FLOW_CONCEPTS = [
    "us-gaap_NetCashProvidedByUsedInOperatingActivities",
    "us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]


def _find_concept(line_items: list[dict], candidates: list[str]) -> float | None:
    by_concept = {item.get("concept"): item.get("value") for item in line_items}
    for candidate in candidates:
        if candidate in by_concept:
            return by_concept[candidate]
    return None


def _chronological_discrete(quarterly_reports: list[dict], section: str, candidates: list[str]) -> list[dict]:
    """Oldest-first list of {end_date, cumulative, discrete, is_reset}
    for every quarterly report where this concept was found at all --
    reports missing the concept break the cumulative chain (their
    successor can't safely diff against a cumulative two-or-more quarters
    back) and are simply excluded, not included as a None placeholder."""
    chronological = sorted((r for r in quarterly_reports if r.get("endDate")), key=lambda r: r["endDate"])
    result: list[dict] = []
    prev_cumulative: float | None = None
    for report in chronological:
        cumulative = _find_concept(report.get("report", {}).get(section, []), candidates)
        if cumulative is None:
            prev_cumulative = None
            continue
        is_reset = prev_cumulative is None or cumulative < prev_cumulative
        discrete = cumulative if is_reset else cumulative - prev_cumulative
        result.append({
            "end_date": report["endDate"], "cumulative": cumulative, "discrete": discrete, "is_reset": is_reset,
        })
        prev_cumulative = cumulative
    return result


def _annual_value_covering(annual_reports: list[dict], section: str, candidates: list[str], after_end_date: str) -> float | None:
    """The annual (10-K) report whose fiscal year covers the quarter
    right after `after_end_date` -- i.e. the closest annual endDate that's
    still later than it. That's the 10-K whose total, minus
    `after_end_date`'s own cumulative, yields that fiscal year's missing
    discrete Q4."""
    candidates_reports = [r for r in annual_reports if r.get("endDate") and r["endDate"] > after_end_date]
    if not candidates_reports:
        return None
    closest = min(candidates_reports, key=lambda r: r["endDate"])
    return _find_concept(closest.get("report", {}).get(section, []), candidates)


def derive_ttm_metric(
    quarterly_reports: list[dict], annual_reports: list[dict], section: str, candidates: list[str]
) -> float | None:
    """One metric's TTM -- sum of the 4 most recent discrete quarters,
    crossing at most one fiscal-year Q4 gap. See module docstring for the
    full algorithm and its v1 scope limit."""
    entries = _chronological_discrete(quarterly_reports, section, candidates)
    if not entries:
        return None
    entries = list(reversed(entries))  # most-recent-first for this walk

    collected: list[float] = []
    gaps_filled = 0
    for i, entry in enumerate(entries):
        collected.append(entry["discrete"])
        if len(collected) == _TTM_REPORT_COUNT:
            return sum(collected)

        if entry["is_reset"]:
            if gaps_filled >= 1:
                return None  # v1 scope: at most one gap -- see module docstring
            if i + 1 >= len(entries):
                return None  # nothing before the reset to compute a gap-fill against
            prior_last = entries[i + 1]
            annual_value = _annual_value_covering(annual_reports, section, candidates, after_end_date=prior_last["end_date"])
            if annual_value is None:
                return None
            collected.append(annual_value - prior_last["cumulative"])
            gaps_filled += 1
            if len(collected) == _TTM_REPORT_COUNT:
                return sum(collected)
            # Loop continues to i+1 next iteration, which appends
            # prior_last's own discrete value -- already correctly
            # computed in _chronological_discrete, no special-casing
            # needed here.

    return None  # ran out of quarterly history before reaching 4


def most_recent_period_label(quarterly_reports: list[dict]) -> str | None:
    """"YYYY-QN" for the most recent quarterly report's own endDate,
    calendar-quarter based (Jan-Mar=Q1 ... Oct-Dec=Q4) — a v1
    simplification, not true fiscal-quarter awareness (a company whose
    fiscal year doesn't align to the calendar, e.g. Apple, would get a
    calendar-quarter label here, not its own internal Q-numbering).
    Purely descriptive metadata for symbol_fundamentals.financials_period
    — nothing in the TTM math above depends on this label being fiscal-
    quarter-accurate."""
    dated = [r for r in quarterly_reports if r.get("endDate")]
    if not dated:
        return None
    latest = max(dated, key=lambda r: r["endDate"])["endDate"]
    year, month = latest[:4], int(latest[5:7])
    quarter = (month - 1) // 3 + 1
    return f"{year}-Q{quarter}"


def derive_ttm(quarterly_reports: list[dict], annual_reports: list[dict]) -> dict[str, float | None]:
    """All three TTM metrics `symbol_fundamentals` needs, one call."""
    return {
        "revenue_ttm": derive_ttm_metric(quarterly_reports, annual_reports, "ic", REVENUE_CONCEPTS),
        "net_income_ttm": derive_ttm_metric(quarterly_reports, annual_reports, "ic", NET_INCOME_CONCEPTS),
        "operating_cash_flow_ttm": derive_ttm_metric(quarterly_reports, annual_reports, "cf", OPERATING_CASH_FLOW_CONCEPTS),
    }
