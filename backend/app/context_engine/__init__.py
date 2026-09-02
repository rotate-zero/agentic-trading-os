"""
Context Engine — thin aggregator over independent ContextProvider
instances. See docs/architecture/trading-intelligence-architecture.md §5
(decision #90 for the provider boundary, decision #92 for this M1 slice's
build and its two deliberate deviations from §5's original interface).

v1 (this slice): CalendarProvider only. FundamentalsProvider/
NewsFlagProvider join once M0's Finnhub spike results land — see
M0-SPIKE-NOTES.md at the repo root.
"""
