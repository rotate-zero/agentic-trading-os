# Phased Roadmap

**Extracted from:** `../architecture/system-design.md` §7.
**Companion documents:** [`../architecture/system-design.md`](../architecture/system-design.md), [`../architecture/trading-intelligence-architecture.md`](../architecture/trading-intelligence-architecture.md), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md), [`../decisions/future-ideas.md`](../decisions/future-ideas.md).

Each phase should be a mergeable, demoable slice — no phase depends on unfinished code from a later phase.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| 1 | React + Lightweight Charts, static candle JSON, custom drawing support | Chart renders candles + one of each overlay type from a static file |
| 2 | FastAPI backend, Event Bus + Market Clock scaffolding, WebSocket channel plumbing, PostgreSQL + Alembic wired | Chart receives live overlay pushes over WebSocket instead of static file; a dummy event round-trips through the Event Bus |
| 3 | One broker adapter — IBKR (Alpaca not available for a Bangladesh-resident account; see `../decisions/confirmed-decisions.md` #1) | Live ticks for 1 symbol flow adapter → engine → chart |
| 4 | Full Market Data Engine + Feature Engine: multi-symbol subscribe, normalize, cache, persist, compute features once | 100-symbol universe streaming with a `FeatureSet` published per symbol, no dropped ticks |
| 5 | Scanner + Strategy Engine + Opportunity/Decision/Planning Engines + Governor + Portfolio State Engine | Opportunities appear on chart with confidence, entry/stop/target; Governor can reject a plan against Portfolio State |
| 6 | Execution Engine (dry-run first, then live) + Position Monitor | Approved plan produces a paper order end-to-end; Position Monitor flags an open position as "weakening" |

## Status (living — update as phases complete)

Status as of decision #164 (baseline `main` through decision #163).

- **Phase 1 — done.** Multi-tab Main Windows, 8×8 grid picker, ticker search, nested sub-window menu, candle-count stepper, per-sub-window background color, localStorage save/load. See repo `README.md` for verification notes.
- **Phase 2 — done.** FastAPI, Alembic/PostgreSQL, the two-lane Event Bus (decisions #2 and #9), Market Clock, `DebounceScheduler` (decision #10), and WebSocket plumbing are in place. The dummy-event round trip and real database migration were verified during the scaffold delivery. The frontend swap that was once paused later resumed and completed through the provider-agnostic live candle path (decision #35); `DebounceScheduler` is now used by real engines, including Market State (decision #93).
- **Phase 3 — functionally complete end to end; empirical provider coverage remains uneven.** The broker order was revised away from Alpaca (decision #1). `MarketDataProvider`/`BrokerAdapter`, the streaming/historical registry roles, `TickIngestBridge`, Polygon historical access, Finnhub streaming, and the provider-agnostic subscribe/chart path are built (decisions #28–#35). Polygon was later exercised with a real configured key and real pre-market minute data. Finnhub's protocol is tested against a local stand-in, but the repository still records no real-Finnhub-server/key validation. IBKR connection/panel and historical-backtest paths exist (decisions #144–#145), but both decisions and `ibkr-broker-panel-validation.md` explicitly record that no real Gateway/TWS/account session was reached; that validation is deferred, not blocked by the earlier passport-renewal reason. The Phase 3 adapter → engine → chart path is therefore built, while real IBKR and Finnhub validation remain open.
- **Phase 4 — substantial baseline built; scale exit criterion still open.** Feature Engine now publishes server-side SMA/EMA (including slopes), VWAP and extended-session accumulators, previous-day/Camarilla/pre-market/VPOC price levels, Daily Levels, gap/session change, ATR, Regression, KAMA, RVOL, and pre-market volume ratio across the supported paths (decisions #45, #51–#71, #83, and #100–#102). Level Interaction tracks price-coordinate features and Daily Levels with touch/hold/reject/conquer state while excluding non-price metrics (decisions #46, #64, #85–#86, and #160). Per-symbol Market State and its SPY/QQQ/IWM cross-symbol composite are built (decisions #93 and #97). Context is built as three providers—`CalendarProvider`, `FundamentalsProvider`, and `NewsFlagProvider`—over global and per-symbol aggregation paths (decisions #92 and #96). The Scanner has a persisted/editable universe, scorer, on-demand runner and routes, and `ScannerPanel` with `useScannerState`/`useScannerUniverse`; it remains request-driven rather than the scheduled promotion system designed for the full phase (decision #102 records the later pre-market scoring integration). **The Phase 4 exit criterion—100-symbol streaming with a `FeatureSet` per symbol and no dropped ticks—is now measured on synthetic input, not yet on a real feed (decision #169).** A scratch-database harness (`backend/scripts/measure_live_pipeline_scale.py`) drove the real live-path objects through synthetic 100-symbol candle bursts: Feature Engine and Level Interaction Engine processed every published 1m candle with exact 100/100 per-symbol coverage and no drops at N=1/10/25/50/100, draining 14–20x inside the 60-second per-candle-minute budget (2.74s/4.19s at a 16-candle burst; a supplementary 60-candle stress point still held 100/100 coverage at 9.11s/14.03s). Real Finnhub/IBKR delivery, genuine tick burstiness, and provider symbol-count caps remain unmeasured—the Core-100 list is still not settled, and the Finnhub free-tier WebSocket symbol ceiling (`scanner-design.md` §7) is a separate, still-open question this measurement does not bear on.
- **Phase 5–6 — partial, with a clear built/not-built boundary.** Built: the Strategy Engine's seven registered strategies (ORB, Gap, Volume Spike, First Pullback, Reversal, Momentum, VWAP; decisions #99, #104–#105, #109–#110, and #113), `StrategyScheduler` and central `gate_conditions` enforcement (decisions #114–#119), Opportunity Cache (decisions #114–#115), and the non-scoring agreement/conflict view (decisions #121 and #123). D4 remains open, so there is no Opportunity Engine ranking. The Scanner is partial and on-demand only: no continuous `MarketActivityScanner`, cadence scheduler, Scheduler promotion, or `LiveTickRelay.set_active_symbols` activation. Performance Intelligence is partial but substantial: persistence (#120), queries and corrections (#122/#124), routes and UI (#123, #127, #133, #137–#139, #162), and the Backtest Runner writer (#128) exist; no live Execution/Position Monitor writer exists. Backtest Runner is built for fixture replay, isolated IBKR acquisition, and sweeps (decisions #128, #131, #145, #159, and #163). The World View read facade and frontend surface are built (decisions #150 and #154), but its Portfolio State slot honestly returns `null`. Not started as application modules: Opportunity ranking, Decision Engine, Trade Planning, Portfolio State, Execution Engine, and Position Monitor. Governor is partial only in the narrow sense that its widened event schema exists (decision #6); no Governor rule engine exists. `IBKRAdapter.place_order()` still raises `NotImplementedError`.

```
Market data / Feature Engine [built]
              |
              v
On-demand Scanner [partial] ---> continuous cadence / top-N promotion [not started]
              |
              v
Strategy Engine + Scheduler + gates [built]
              |
              v
Opportunity Cache + conflict view [built] ---> Opportunity ranking [not started]
                                                        |
                                                        v
Decision Engine [not started] -> Trade Planning [not started] -> Governor [partial: schema only]
                                                                          |
Portfolio State [not started] --------------------------------------------+
                                                                          v
Execution Engine [not started] -> Position Monitor [not started]
              |
              v
Performance Intelligence [partial: backtest path built, live writer absent]
              |
              v
World View facade [built; Portfolio State input unavailable]
```
