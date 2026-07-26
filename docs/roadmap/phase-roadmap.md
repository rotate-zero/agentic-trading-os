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

- **Phase 1 — done.** Multi-tab Main Windows, 8×8 grid picker, ticker search, nested sub-window menu, candle-count stepper, per-sub-window background color, localStorage save/load. See repo `README.md` for verification notes.
- **Phase 2 — backend scaffold delivered, frontend swap paused.** FastAPI skeleton, Alembic + PostgreSQL (plain, no TimescaleDB — decision #2), Event Bus with two dispatch lanes (decision #9, verified: a test proves a slow normal-lane subscriber cannot delay a critical-lane event) + Market Clock, `DebounceScheduler` utility (decision #10, unit-tested but not yet wired to a real consumer — Market State Engine doesn't exist until Phase 5), WebSocket gateway. Round-trip exit criterion (dummy event through the Event Bus, out over WebSocket) verified live end-to-end, DB migration verified against a real local Postgres. Frontend mock-data swap (candles.ts → live WS feed) was started, then paused to prioritize the broker adapter — plan unchanged, now has a real data source to point at.
- **Phase 3 — backend feature-complete for its scope, live connection unverified.** Broker order revised: IBKR only (decision #1) — Alpaca isn't open to Bangladesh-resident accounts. `IBKRAdapter` implements the full `BrokerAdapter` interface via `ib_async` (decision #13); `IBKRIngestBridge` buckets ticks into 1-minute candles as a Phase-3-minimal stand-in for Phase 4's real Market Data Engine (decision #16). Symbol-qualification failures and unexpected disconnects are handled cleanly (found via reading `ib_async` source, not assumed) and unit tested. `GET /market/candles` gives the chart a backfill route. Verified: `ib_async` API signatures, tick-bucketing logic, qualification-failure and disconnect-handler code paths, and the connect path via a real (refused) socket connection. **Not verified: an actual live connection to a running Gateway with a real IBKR account** — no path to that in this environment. Auto-reconnect after a disconnect is explicitly not built — deferred to Phase 4's Market Data Engine, not pulled forward. 2FA strategy documented (decision #14): IB Key + Gateway auto-restart, not SMS, not a claim of full headless operation. Frontend mock-swap remains paused.
- **Phase 3–6 — not started.**
