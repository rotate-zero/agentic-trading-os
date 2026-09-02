"""
Market State Engine — per-symbol trend/volatility/volume/VWAP/acceleration
scoring off Feature Engine output. See
docs/architecture/trading-intelligence-architecture.md §4 (decision #91
for the score-first shape and dimension list, decision #93 for this
build's formulas and two deliberate deviations from that list).

v1 (this build): per-symbol only. Cross-symbol synthesis
(`CrossSymbolState` — SPY/QQQ/IWM) is M3, not built here — it needs M0's
still-pending confirmation that Polygon/Finnhub actually serve those
three cleanly (M0-SPIKE-NOTES.md), which this per-symbol engine, generic
over any tracked symbol, doesn't depend on at all.
"""
