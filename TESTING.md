# TESTING.md — Decision #137: Strategy Performance Live/Backtest toggle

## What this delivery is

`InfoTab.tsx`'s `StrategyPerformanceSummary` can now switch between **Live**
and **Backtest** performance data. Before this, the section always called
`usePerformanceAnalytics()` with no arguments, so `isBacktest` was always
`undefined` and the backend's own default (`false`, live-only) was the only
thing it could ever request — structurally guaranteed to show "no data yet"
forever, since no Execution Engine exists, even though real backtest-derived
`StrategyOutcome` rows have existed since decision #128.

Full reasoning — why local state, why the empty-state wording, and a real
stale-data gap found and fixed while wiring this — lives in
`docs/decisions/confirmed-decisions.md`, decision #137. This file covers
what to run to verify it.

**Frontend-only.** Zero backend changes. `usePerformanceAnalytics.ts` and
`api-client.ts` are both unchanged — everything needed already existed.

## Files changed

**Modified:**
- `frontend/src/components/workspace/InfoTab.tsx` — the only code change.
  `StrategyPerformanceSummary` gained a local `view: "live" | "backtest"`
  state (default `"live"`), a two-button toggle, an explicit
  `isBacktest` passed to `usePerformanceAnalytics()`, a widened loading
  gate (`loading` alone, not `loading && isEmpty`), mode-specific empty-
  state copy, and an always-visible `— Live`/`— Backtest` header label
  (plus a small "not live trading results" line in Backtest mode).
  Nothing else in the file changed.
- `docs/architecture/strategy-engine-design.md` — §5 (Performance
  Intelligence) gained one new as-built note with two diagrams
  (cross-component data flow; the internal toggle/loading-gate state
  flow), inserted immediately after decision #122's own line about the
  two `performance_queries.py` functions.

**New:**
- This file, `CHANGES.md`, and the matching `confirmed-decisions.md` /
  `INDEX.md` entries.

**Explicitly untouched, confirmed by `diff -rq` against a freshly
re-pulled clone:** everything under `backend/`,
`frontend/src/services/api-client.ts`,
`frontend/src/hooks/usePerformanceAnalytics.ts`,
`frontend/src/components/backtest/BacktestPanel.tsx`,
`frontend/src/components/backtest-results/BacktestResultsPanel.tsx`,
`frontend/src/hooks/useBacktestRun.ts`,
`frontend/src/hooks/useBacktestOutcomes.ts`, and anything under a
`backtest-runs`-style directory (the parallel session's own scope).

## What was deliberately deferred

`strategyName`/`strategyVersion` filters — both routes and the hook
already support them, but there is no existing source of selectable
strategy names anywhere in this codebase (checked directly). Adding
selectors now would mean solving that data-source/UX question too, which
is its own, later task — not a natural extension of a two-state
provenance toggle. This keeps faith with decision #127's own original
framing of this section as a minimal surfacing of an existing capability,
not a new dashboard.

## How to verify

### 1. TypeScript / build

```bash
cd frontend
npm install
npx tsc -b
npx vite build
```

Expected `npx tsc -b` output — **exactly** these 4 pre-existing errors,
confirmed against a freshly re-pulled untouched clone both before and
after this delivery's change (same baseline, zero new errors):

```
src/components/workspace/GridPresetPicker.tsx(2,10): error TS2305: Module '"../../types/workspace"' has no exported member 'GRID_PRESETS'.
src/components/workspace/GridPresetPicker.tsx(6,11): error TS2339: Property 'preset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(6,19): error TS2339: Property 'setPreset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(19,30): error TS7006: Parameter 'p' implicitly has an 'any' type.
```

`npx vite build` — clean, no errors, on both the baseline and this
delivery's tree.

### 2. Functional checks (manual, in the running app)

No frontend test framework exists in this codebase (no `vitest`/`jest`
dependency, no `*.test.*` file anywhere under `frontend/` — checked
directly, consistent with decision #123's own note that this project's
hooks are exercised by real usage, not a test suite), and this delivery
doesn't introduce one for a single component's toggle logic — that would
be a disproportionate, inconsistent change for this task's scope. Verify
by running the app against a real local backend + Postgres:

1. Open the Info tab's General view. **Strategy Performance — Live**
   should be the default (no clicking required) — confirms the default
   stays Live.
2. With no live `StrategyOutcome` rows (the current state of any fresh
   environment — no Execution Engine exists), Live mode shows *"No live
   data yet — no Execution Engine exists to write it."*
3. Click **Backtest**. Header immediately updates to **Strategy
   Performance — Backtest**, with *"Derived from backtest StrategyOutcome
   data — not live trading results."* shown beneath it.
4. While the Backtest request is in flight, confirm the panel shows
   **"Loading…"** — not the previous Live state's content (there was none
   to show in a fresh environment, but see the network-tab check below
   for the case where Live *did* have data first).
5. If real backtest rows exist (`POST /backtest/run` has been called at
   least once, decision #131), Backtest mode should show real win-rate/
   expectancy numbers under the Backtest label. If none exist yet,
   confirm the honest empty state: *"No backtest data yet — run a
   backtest to populate this."*
6. Open the browser's network tab and confirm the two requests fired on
   toggle are `GET /intelligence/win-rate-by-hour?is_backtest=true` and
   `GET /intelligence/expectancy-by-session-type?is_backtest=true`; on
   switching back to Live, confirm `is_backtest=false` on both (never
   omitted, never blended).
7. To directly observe the stale-data fix: seed at least one real Live
   `StrategyOutcome` row (needs a live Execution Engine, or a direct DB
   insert for testing), switch to Live, confirm the numbers render, then
   click Backtest with the network artificially throttled — confirm the
   panel shows "Loading…" rather than continuing to display the Live
   numbers under a "— Backtest" header.

### Why no automated query-string test was added

No live backend/Postgres was available to exercise the full round trip
in this delivery's own environment. The `is_backtest=<bool>` query-string
construction (`_performanceAnalyticsQuery` in `api-client.ts`, unchanged
by this decision) was verified by direct source trace against both
`{ isBacktest: true }` and `{ isBacktest: false }` rather than by
observing a live network call — same reasoning as item 6 above, to be
confirmed against a real running app when one is available.
