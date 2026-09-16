# TESTING — decision #139 (surface-backtest-runs-metadata)

Frontend-only delivery. No backend files touched, no migration, no API-contract change — `GET /intelligence/backtest-runs` (decision #136) is consumed exactly as it already exists.

## No frontend test framework

Confirmed, not assumed: no `vitest`/`jest`/any test runner in `frontend/package.json`'s `devDependencies`, no `*.test.*` or `*.spec.*` file anywhere under `frontend/src/`. Consistent with every prior frontend-only delivery in this log (#123, #137, #138). Verification below is direct source trace of the actual shipped logic, same posture those deliveries already took — not a placeholder for tests that should exist.

## Source-trace verification

**`fetchBacktestRuns()` (`api-client.ts`)** — traced against `GET /intelligence/backtest-runs`'s real implementation (`backend/app/api/routes/intelligence.py`) and its `BacktestRun` schema (`backend/app/schemas/performance.py`), both re-read in full this session, not from memory:
- Query params (`limit`, `run_id`, `strategy_name`, `sweep_id`) conditionally appended only when defined, matching `fetchStrategyOutcomes`'s established pattern and the route's own optional/AND-combined filter semantics exactly.
- Non-2xx → `ApiError` with the real backend detail, never swallowed.
- Every `BacktestRunWireShape` field name/type checked one-for-one against `BacktestRun`'s current field list — no invented or stale field.

**`useBacktestRuns.ts`**:
- `runId === undefined` → no fetch issued, `backtestRun`/`error` both `null`, `loading` `false`. Traced directly in `load()`'s early return — confirmed no network call is constructible in this branch.
- `runId` defined → fetch issued with `runId` only (`limit`/`strategyName`/`sweepId` all `undefined`), consistent with the route resolving to exactly the rows matching that single `run_id`.
- Success path: `backtestRun = wire.backtest_runs[0] ?? null`. Safe because `run_id` is `BacktestRunRecord`'s own primary key (confirmed directly against `backend/app/models/trading_intelligence.py`'s column definition, not assumed) — the array can only ever hold 0 or 1 element when filtered by `run_id` alone.
- Error path: `ApiError` message surfaced as `error`; `backtestRun` reset to `null` (never left stale from a previous, different `runId`).
- `runId` change mid-flight: `cancelled` flag set on cleanup, stale in-flight responses discarded — same pattern `useBacktestOutcomes.ts` already uses for its own equivalent race.

**`RunMetadataCard` (`BacktestResultsPanel.tsx`)**:
- Only rendered when `appliedRunId` is truthy (traced at the call site inside `BacktestResultsBody`'s render) — the "everything" default view renders nothing new.
- Four mutually-exclusive render branches confirmed structurally exhaustive: `loading` → `error` → `!backtestRun` (empty) → populated fields grid. No branch can silently render nothing when data is actually present or actually missing.
- Fields grid traced field-by-field against `BacktestRunWireShape`: all 11 non-`run_id` fields rendered (`run_id` itself omitted from the grid since it's already shown verbatim in the filter bar's own "Showing run_id=..." line directly above, confirmed at the call site — not omitted by oversight).
- `walk_forward_fold === null` renders `"—"`, matching this codebase's existing null-field convention (`OutcomeDetail`'s own optional-field rendering, same file).

## Build verification

Baseline established on a freshly re-pulled, untouched `main` (`git stash` this delivery's changes, not a separate clone, per this project's "re-pull rather than re-clone within a session" convention):

```
npx tsc -b
```
→ exactly the 4 known, pre-existing `GridPresetPicker.tsx` errors (`#35` baseline — `GRID_PRESETS` export, `preset`/`setPreset` properties, one implicit-`any` parameter). Confirmed pre-existing on untouched `main`, not attributed to this delivery without that check.

This delivery's tree, same command:
```
npx tsc -b
```
→ **identical 4 errors, zero new.**

```
npx vite build
```
→ clean, `dist/` build artifact removed afterward (not part of the delivery).

`frontend/tsconfig.tsbuildinfo` (a tracked build-cache artifact, not this delivery's own content) was reverted to its committed state after the `tsc -b` runs — not included in this delivery's diff.

## Footprint (confirmed by `diff -rq` against a freshly re-pulled clone immediately before packaging)

Modified:
- `frontend/src/services/api-client.ts`
- `frontend/src/components/backtest-results/BacktestResultsPanel.tsx`
- `docs/architecture/strategy-engine-design.md`
- `docs/decisions/confirmed-decisions.md`
- `docs/decisions/INDEX.md`

New:
- `frontend/src/hooks/useBacktestRuns.ts`
- `TESTING.md` (this file)
- `CHANGES.md`

Confirmed untouched: everything under `backend/`, any migration, `WorkspaceContext.tsx`, `useBacktestOutcomes.ts`, `useStrategyOutcomes.ts`, and every other frontend file not listed above.
