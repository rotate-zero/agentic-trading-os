"""
TTM derivation tests. Synthetic data shaped exactly like decision #94's
validated cumulative-revenue pattern (climbing through a fiscal year,
resetting at the next one's Q1) — not real Finnhub output, since this
session has no live key (see fundamentals_derivation.py's own docstring
for that caveat).
"""
from __future__ import annotations

from app.context_engine.fundamentals_derivation import (
    NET_INCOME_CONCEPTS,
    REVENUE_CONCEPTS,
    derive_ttm,
    derive_ttm_metric,
)

_REVENUE_CONCEPT = REVENUE_CONCEPTS[2]  # "us-gaap_Revenues" — pick one candidate, consistent throughout


def _quarterly(end_date: str, cumulative_revenue: float) -> dict:
    return {"endDate": end_date, "form": "10-Q", "report": {"ic": [{"concept": _REVENUE_CONCEPT, "value": cumulative_revenue}]}}


def _annual(end_date: str, total_revenue: float) -> dict:
    return {"endDate": end_date, "form": "10-K", "report": {"ic": [{"concept": _REVENUE_CONCEPT, "value": total_revenue}]}}


def test_ttm_when_most_recent_quarter_is_itself_a_reset():
    """Most recent quarter is FY2026's Q1 (itself a reset, already
    discrete) — TTM still needs FY2025's Q4, gap-filled from the annual
    report, then FY2025's Q3 and Q2. This is NOT a "no gap needed" case:
    trailing-twelve-months from a fiscal year's Q1 always reaches back
    across the Q4 boundary, since Q4 is the quarter immediately preceding
    any Q1 in real calendar time."""
    quarterly = [
        _quarterly("2025-03-31", 19.3),
        _quarterly("2025-06-30", 41.8),
        _quarterly("2025-09-30", 69.9),
        _quarterly("2026-03-31", 22.4),  # reset — new FY's Q1
    ]
    annual = [_annual("2025-12-31", 104.9)]

    ttm = derive_ttm_metric(quarterly, annual, "ic", REVENUE_CONCEPTS)

    q1_fy26 = 22.4                # reset, already discrete
    q4_fy25 = 104.9 - 69.9         # 35.0, gap-filled
    q3_fy25 = 69.9 - 41.8          # 28.1
    q2_fy25 = 41.8 - 19.3          # 22.5
    expected = q1_fy26 + q4_fy25 + q3_fy25 + q2_fy25
    assert abs(ttm - expected) < 1e-9


def test_ttm_none_when_most_recent_quarter_is_a_reset_with_no_annual_report():
    """Same shape as the test above, but no annual report available to
    gap-fill from — correctly None, not a guess."""
    quarterly = [
        _quarterly("2025-03-31", 19.3),
        _quarterly("2025-06-30", 41.8),
        _quarterly("2025-09-30", 69.9),
        _quarterly("2026-03-31", 22.4),
    ]
    assert derive_ttm_metric(quarterly, [], "ic", REVENUE_CONCEPTS) is None


def test_ttm_crossing_exactly_one_q4_gap():
    """Most recent quarter is FY2026's Q2 — only 2 quarters filed so far
    this fiscal year, so the 4-quarter window needs FY2025's Q4, which
    only exists inside the 10-K, gap-filled as annual_total - Q3_cumulative."""
    quarterly = [
        _quarterly("2025-03-31", 19.3),
        _quarterly("2025-06-30", 41.8),
        _quarterly("2025-09-30", 69.9),
        _quarterly("2026-03-31", 22.4),   # reset — FY2026 Q1
        _quarterly("2026-06-30", 41.4),   # FY2026 Q2 cumulative (discrete = 41.4 - 22.4 = 19.0)
    ]
    annual = [_annual("2025-12-31", 104.9)]  # FY2025 full-year total

    ttm = derive_ttm_metric(quarterly, annual, "ic", REVENUE_CONCEPTS)

    q2_fy26 = 41.4 - 22.4       # 19.0
    q1_fy26 = 22.4               # reset, already discrete
    q4_fy25 = 104.9 - 69.9        # 35.0, gap-filled
    q3_fy25 = 69.9 - 41.8         # 28.1
    expected = q2_fy26 + q1_fy26 + q4_fy25 + q3_fy25
    assert abs(ttm - expected) < 1e-9


def test_ttm_none_when_gap_fill_needs_missing_annual_report():
    """Deeper-in-the-window gap (most recent quarter is NOT itself the
    reset — same shape as test_ttm_crossing_exactly_one_q4_gap above,
    just without the annual report needed to actually fill it)."""
    quarterly = [
        _quarterly("2025-03-31", 19.3),
        _quarterly("2025-06-30", 41.8),
        _quarterly("2025-09-30", 69.9),
        _quarterly("2026-03-31", 22.4),
        _quarterly("2026-06-30", 41.4),
    ]
    ttm = derive_ttm_metric(quarterly, [], "ic", REVENUE_CONCEPTS)  # no annual report at all
    assert ttm is None


def test_ttm_none_when_fewer_than_four_quarters_available_and_no_gap_possible():
    quarterly = [_quarterly("2026-03-31", 22.4), _quarterly("2026-06-30", 41.4)]
    ttm = derive_ttm_metric(quarterly, [], "ic", REVENUE_CONCEPTS)
    assert ttm is None


def test_ttm_none_when_concept_not_found_at_all():
    quarterly = [{"endDate": "2026-03-31", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_SomethingElse", "value": 1.0}]}}]
    ttm = derive_ttm_metric(quarterly, [], "ic", REVENUE_CONCEPTS)
    assert ttm is None


def test_ttm_empty_response_returns_none_for_etf():
    """Decision #94: ETFs return an empty list from financials-reported —
    should yield None, not crash, not a fabricated 0."""
    assert derive_ttm_metric([], [], "ic", REVENUE_CONCEPTS) is None


def test_derive_ttm_returns_all_three_metrics_independently():
    quarterly = [
        {
            "endDate": "2025-06-30", "form": "10-Q",
            "report": {
                "ic": [{"concept": REVENUE_CONCEPTS[2], "value": 41.8}, {"concept": NET_INCOME_CONCEPTS[0], "value": 4.5}],
            },
        },
        {
            "endDate": "2025-09-30", "form": "10-Q",
            "report": {
                "ic": [{"concept": REVENUE_CONCEPTS[2], "value": 69.9}, {"concept": NET_INCOME_CONCEPTS[0], "value": 7.0}],
            },
        },
        {
            "endDate": "2026-03-31", "form": "10-Q",
            "report": {
                "ic": [{"concept": REVENUE_CONCEPTS[2], "value": 22.4}, {"concept": NET_INCOME_CONCEPTS[0], "value": 2.5}],
            },
        },
        {
            "endDate": "2026-06-30", "form": "10-Q",
            "report": {
                "ic": [{"concept": REVENUE_CONCEPTS[2], "value": 41.4}, {"concept": NET_INCOME_CONCEPTS[0], "value": 5.5}],
            },
        },
    ]
    annual = [
        {"endDate": "2025-12-31", "form": "10-K", "report": {"ic": [{"concept": REVENUE_CONCEPTS[2], "value": 104.9}, {"concept": NET_INCOME_CONCEPTS[0], "value": 11.0}]}},
    ]
    result = derive_ttm(quarterly, annual)

    q2_fy26_rev, q1_fy26_rev, q4_fy25_rev, q3_fy25_rev = 41.4 - 22.4, 22.4, 104.9 - 69.9, 69.9 - 41.8
    assert abs(result["revenue_ttm"] - (q2_fy26_rev + q1_fy26_rev + q4_fy25_rev + q3_fy25_rev)) < 1e-9

    q2_fy26_ni, q1_fy26_ni, q4_fy25_ni, q3_fy25_ni = 5.5 - 2.5, 2.5, 11.0 - 7.0, 7.0 - 4.5
    assert abs(result["net_income_ttm"] - (q2_fy26_ni + q1_fy26_ni + q4_fy25_ni + q3_fy25_ni)) < 1e-9

    assert result["operating_cash_flow_ttm"] is None  # no "cf" section present in this synthetic data at all


def test_most_recent_period_label():
    from app.context_engine.fundamentals_derivation import most_recent_period_label

    quarterly = [_quarterly("2025-03-31", 19.3), _quarterly("2026-06-30", 41.4), _quarterly("2025-09-30", 69.9)]
    assert most_recent_period_label(quarterly) == "2026-Q2"


def test_most_recent_period_label_empty():
    from app.context_engine.fundamentals_derivation import most_recent_period_label

    assert most_recent_period_label([]) is None
