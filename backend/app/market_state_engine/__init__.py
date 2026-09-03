"""
Market State Engine — per-symbol trend/volatility/volume/VWAP/acceleration
scoring off Feature Engine output, plus SPY/QQQ/IWM cross-symbol
synthesis (`CrossSymbolState`) on top of it. See
docs/architecture/trading-intelligence-architecture.md §4 (decision #91
for the score-first shape, dimension list, and `CrossSymbolState` shape;
decision #93 for the per-symbol build's formulas and two deliberate
deviations from that list; decision #97 for the cross-symbol build —
M3 — including confirmation that Polygon/Finnhub serve SPY/QQQ/IWM
cleanly enough, per decision #95's M0 spike).
"""
