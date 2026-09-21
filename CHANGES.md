# CHANGES — decision #163: `POST /backtest/sweep` frontend UI

## Current delivery

Frontend-only. Gives `POST /backtest/sweep` (decision #159, backend-only
until now) a real UI caller, and gives its own results a way to be
browsed afterward — closing the same "backend capability real, UI never
wired it" gap this project has repeatedly closed before.

### New files

- `frontend/src/hooks/useBacktestSweepRun.ts` — owns one
  `POST /backtest/sweep` call end to end (status machine + live elapsed
  timer), sibling to `useBacktestRun.ts`/`useIbkrBacktestRun.ts`.
- `frontend/src/hooks/useBacktestSweepOutcomes.ts` — resolves a
  `sweep_id` to its member runs (`GET /backtest-runs?sweep_id=`,
  decision #136) plus their merged, re-sorted `StrategyOutcome` rows (no
  backend `sweep_id` filter exists on `/strategy-outcomes` itself —
  flagged as a real follow-up, not built here).

### Changed files

- `frontend/src/components/backtest/BacktestPanel.tsx` — third "Sweep"
  trigger mode: symbols (free-text, comma/space-separated) + scenarios
  (checkboxes over the 4 known `BACKTEST_SCENARIOS`), client-side pair
  cap, sweep-specific honest wait-state copy, new `SweepResultsView`/
  `SweepPairRow` results rendering, publishes `lastBacktestSweepId` on
  completion.
- `frontend/src/components/backtest-results/BacktestResultsPanel.tsx` —
  new independent `sweep_id` filter type alongside the unchanged `run_id`
  one, its own auto/manual state mirroring decision #134's exactly, new
  `RunsInSweepStrip` + shared `OutcomesListSection` (reused by both
  filter types).
- `frontend/src/services/api-client.ts` — additive only: new
  `BacktestSweepPairResultWireShape`/`BacktestSweepResultWireShape`
  types, `BACKTEST_SWEEP_MAX_PAIRS` constant, `triggerBacktestSweep()`.
  Also: two stale `(decision #130)` citations corrected to `(decision #131)`
  (lines formerly 830/914 — the `POST /backtest/run` trigger route's own
  real number, per decision #131's own retroactive-reconciliation text;
  independently flagged by both decision #162 and this session's own
  required reading).
- `frontend/src/hooks/useBacktestOutcomes.ts` — new, additive, backward-
  compatible `enabled` param (default `true`) so it can skip its own
  fetch while `BacktestResultsPanel.tsx`'s sweep_id view is the active
  one. One stale `(decision #130)` citation in `useBacktestRun.ts`
  corrected to `(decision #131)` alongside it (same root cause).
- `frontend/src/state/WorkspaceContext.tsx`,
  `frontend/src/types/workspace.ts` — new `lastBacktestSweepId` /
  `setLastBacktestSweepId`, mirroring `lastBacktestRunId` end to end.
- `docs/architecture/backtest-runner-design.md` — new as-built note
  (trigger-side + results-side data-flow diagrams, an internal-flow
  diagram for the changed `BacktestResultsBody`), correction pointers
  added to the existing #130/#131 and #152 as-built notes, and two more
  stale `(decision #130)` citations corrected to `(decision #131)`.

### Not touched

Everything under `backend/`. Everything decision #162 touched
(`InfoTab.tsx`, `strategy-engine-design.md`) — that delivery landed on
`main` mid-session; this one was re-synced against it before being
assigned its own real decision number (see `confirmed-decisions.md`
entry #163's own "Parallel work" note).

See `TESTING.md` for verification performed and `confirmed-decisions.md`
entry #163 for the full reasoning behind every design choice above.
