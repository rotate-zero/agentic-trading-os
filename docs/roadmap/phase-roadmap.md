# Phased Roadmap

**Extracted from:** `../architecture/system-design.md` §7.
**Companion documents:** [`../architecture/system-design.md`](../architecture/system-design.md), [`../architecture/trading-intelligence-architecture.md`](../architecture/trading-intelligence-architecture.md), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md), [`../decisions/future-ideas.md`](../decisions/future-ideas.md).

Each phase should be a mergeable, demoable slice — no phase depends on unfinished code from a later phase.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| 1 | React + Lightweight Charts, static candle JSON, custom drawing support | Chart renders candles + one of each overlay type from a static file |
| 2 | FastAPI backend, Event Bus + Market Clock scaffolding, WebSocket channel plumbing, PostgreSQL + Alembic wired | Chart receives live overlay pushes over WebSocket instead of static file; a dummy event round-trips through the Event Bus |
| 3 | One broker adapter (Alpaca first — simpler auth than IBKR) | Live ticks for 1 symbol flow adapter → engine → chart |
| 4 | Full Market Data Engine + Feature Engine: multi-symbol subscribe, normalize, cache, persist, compute features once | 100-symbol universe streaming with a `FeatureSet` published per symbol, no dropped ticks |
| 5 | Scanner + Strategy Engine + Opportunity/Decision/Planning Engines + Governor + Portfolio State Engine | Opportunities appear on chart with confidence, entry/stop/target; Governor can reject a plan against Portfolio State |
| 6 | Execution Engine (dry-run first, then live) + Position Monitor | Approved plan produces a paper order end-to-end; Position Monitor flags an open position as "weakening" |

## Status (living — update as phases complete)

- **Phase 1 — done.** Multi-tab Main Windows, 8×8 grid picker, ticker search, nested sub-window menu, candle-count stepper, per-sub-window background color, localStorage save/load. See repo `README.md` for verification notes.
- **Phase 2 — in progress.** Scope per [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md): FastAPI skeleton, Alembic + PostgreSQL (plain, no TimescaleDB — decision #2), Event Bus with two dispatch lanes (decision #9) + Market Clock, `DebounceScheduler` utility (decision #10), WebSocket gateway, frontend mock-data swap to live pushes of the same shapes.
- **Phase 3–6 — not started.**
