# Strategy Performance defaults to Backtest and names the selected data source

**Backfill note:** decision #138 shipped without overwriting this file — it was
left showing #137's own entry. This is a reconstruction, written from decision
#138's own entry in `confirmed-decisions.md` and the verification record already
present in `TESTING.md`, following this project's normal one-delivery-per-file
`CHANGES.md` convention (same delete-first pattern `TESTING.md` itself already
uses). No new verification was run to produce this file — every claim below is
sourced from #138's existing, already-confirmed record, not re-derived.

**Decision number:** **#138**, confirmed against `confirmed-decisions.md`. No
renumbering — #138's own entry doesn't describe a collision.

## What this closes

Decision #137 connected `StrategyPerformanceSummary` (`InfoTab.tsx`) to the
existing `isBacktest` analytics filter but kept **Live** as the default view.
Before an Execution Engine exists, only Backtest Runner writes performance
outcomes — so #137's own default meant the section still opened to "no data yet"
for anyone who never touched the toggle, even though real backtest-derived rows
have existed since decision #128. This delivery corrects the default so the
section is useful on first load, and makes the selected data source impossible to
misread as live results.

## Files changed

- `frontend/src/components/workspace/InfoTab.tsx` — the only code change.
  `StrategyPerformanceSummary`:
  - `view` now initializes to `"backtest"` instead of `"live"`. Live remains fully
    selectable for whenever live outcomes exist.
  - The header and persistent source line now identify backtest-derived simulated
    performance or live-trading-derived performance in every state — loading,
    empty, and populated — not only once data arrives.
  - The two empty-state messages stay mode-specific and honest, distinguishing
    absent backtest performance from absent live performance.
  - `strategyName`/`strategyVersion` selection remains deferred — unchanged from
    #137, restated as still deliberate, not an oversight of this delivery.
  - No backend, API-client, or hook contract change: both `isBacktest: true`/
    `isBacktest: false` calls already existed from #137; only which one is
    selected by default changed.
- `docs/decisions/confirmed-decisions.md` / `INDEX.md` — decision #138.
- `TESTING.md` — rewritten for this delivery (delete-first), including a fresh
  untouched-`main`-baseline comparison and a boundary `diff -rq` immediately
  before packaging.

## Deliberately NOT built

Everything #137 already deferred stays deferred here: `strategyName`/
`strategyVersion` selectors, since no source of selectable strategy names exists
anywhere in this codebase yet. This delivery is a default-and-labeling correction
to #137's own scope, not an extension of it.

## Tests

Per `TESTING.md`: `npx tsc -b` reproduces exactly the four known decision #35
`GridPresetPicker` errors against both the untouched baseline and the changed
tree — zero new errors. `npx vite build` clean on both. No frontend test
framework exists in this codebase; the `view` default, the `isBacktest` boolean
passed on each render, and the mode-specific empty-state copy were verified by
direct source trace of `StrategyPerformanceSummary` and `usePerformanceAnalytics`,
not a live network observation — recorded in full in `TESTING.md`.
