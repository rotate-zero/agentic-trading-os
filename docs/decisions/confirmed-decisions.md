# Confirmed Decisions

**Extracted from:** `../architecture/system-design.md` §9 (original numbering preserved).
**Companion documents:** [`../architecture/system-design.md`](../architecture/system-design.md),
[`../architecture/trading-intelligence-architecture.md`](../architecture/trading-intelligence-architecture.md),
[`future-ideas.md`](./future-ideas.md), and [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md).

This is the open, append-only range beginning at decision #185. Decisions #1–#184 are in
`archive/` without changes to their decision bodies. See [`README.md`](./README.md) for
the index and rollover protocol.

---

### 185. Simulated EOD flatten policy and shared session-window foundation (`simulated-eod-flatten-contract`)

Saqib approved four simulated EOD policies on 2026-09-29: (1) a position-bound reduce-only close needs no new Governor decision and may be attempted only on the entry trading day within `[regular close - 60 seconds, regular close)`, with no next-day EOD catch-up; (2) the first later stop/target observation is a durable fallback, actionable only after EOD placement expires and prior orders/fills are safely settled; (3) an accepted order remains working at the bell, so an after-hours/later fill or indefinite non-fill is possible; and (4) EOD is labelled by a valid post-opening, entry-day tick, with no extra maximum age and no candle label. This is a best-effort flatten attempt, never a guarantee of closure. It extends EX-5 option (a) to simulated EOD policy only; paper/live and EX-12 remain unresolved.

This delivery implements only the shared foundation: `core.session_window.eod_session_window(clock, opened_at, lead_seconds=60)` returns UTC `EodSessionWindow(flatten_at, close_at)` for a covered trading day; `contains(now)` implements the exact inclusive/exclusive bounds. A covered holiday/weekend returns `None`; a year without calendar coverage raises `UnsupportedEodCalendarError`. `MarketClock.has_calendar_for_year()` exposes read-only 2026 coverage without changing existing session methods. Settings validates the configured lead in `1..900`. The new tests cover both half-days, DST, exact boundaries, invalid inputs, unsupported years and parity with Backtest Runner's close derivation on every supported 2026 trading day. The 78 relevant focused and existing tests passed. Position Monitor timing/handoff, durable exit request and dispatch transitions, migration, Execution wiring, reconciliation and frontend are subsequent tasks; no EOD order can execute from this foundation alone. §6.6 contains the approved contract, diagrams and remaining acceptance cases.
