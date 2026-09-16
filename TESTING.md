# Backtest run metadata surfacing — verification

The inherited `TESTING.md` was deleted before this task-specific record was written.

## Rebase note

This delivery was originally verified against `main` commit `6bb7189`. Before packaging, `main` had moved to `6d1b3ce` ("Doc Sequence edited" — reordered decisions #135/#137 into strict numeric sequence in `confirmed-decisions.md`, normalized their heading style, and backfilled #138's own `CHANGES.md`; no code changed, `INDEX.md` untouched). All three of this delivery's own code files, `docs/decisions/INDEX.md`, and `docs/architecture/strategy-engine-design.md` were confirmed byte-identical between `6bb7189` and `6d1b3ce` (direct `diff`, not assumed), so none needed rebasing. Only `docs/decisions/confirmed-decisions.md`'s append point moved; that file's own entry was rebased onto the corrected version. `tsc -b`/`vite build` were re-run on the final, rebased tree (below) rather than trusting the pre-rebase run.

## Untouched GitHub `main` baseline

Fresh `main` clone (`git clone --depth 1`, commit `6d1b3ce`) via `npm install`d frontend dependencies fresh.

- `cd frontend && npx tsc -b` — exit 1, exactly four decision #35 `GridPresetPicker.tsx` errors: missing `GRID_PRESETS`, missing `preset`, missing `setPreset`, and implicit-any `p`.
- `cd frontend && npx vite build` — exit 0, 90 modules transformed.

## Changed tree (post-rebase)

- `cd frontend && npx tsc -b` — exit 1, the identical four `GridPresetPicker.tsx` errors and no new TypeScript errors.
- `cd frontend && npx vite build` — exit 0, 91 modules transformed (exactly +1, the new `useBacktestRunMeta.ts` hook file).

## Manual verification — no frontend test framework exists in this codebase

Confirmed by grep (no `vitest`/`jest` dependency, no `*.test.*` file anywhere under `frontend/`), consistent with every prior frontend-only decision's own note. Verified by direct source trace instead:

- **Query construction (`fetchBacktestRuns`)** — `limit`/`run_id`/`strategy_name`/`sweep_id` are each appended to the query string only when defined, matching `fetchStrategyOutcomes`'s own conditional-append pattern; traced against all 2^4 presence/absence combinations by inspection, none conflict.
- **`useBacktestRunMeta` reactivity** — `useEffect`'s dependency array is `[runId]`; an empty/undefined `runId` short-circuits before any fetch and resets `run`/`loading`/`error` to their absent state, so clearing the panel's filter correctly clears the banner rather than leaving a stale prior run's metadata on screen.
- **Three-state rendering (`RunMetaBanner`)** — traced each of loading / error / absent (`run === null`, no error) / present against the component's own conditional JSX; each renders mutually exclusively, and only the present state renders the expand toggle.
- **run_id linkage** — confirmed `RunMetaBanner`'s `runId` prop is always `BacktestResultsBody`'s own `appliedRunId` (the same value passed to `useBacktestOutcomes`'s `backtestRunId` param), so the banner and the outcomes list can never show two different runs at once, in both auto (following `lastBacktestRunId`) and manual (typed/cleared) filter modes.

No running backend/Postgres was available in this delivery's own environment to exercise the full round trip end-to-end (matching decision #137's own note for the same reason).

## Boundary comparison

Immediately before packaging: `main` re-fetched (`6d1b3ce`, confirmed current), and `diff -rq --exclude=.git --exclude=node_modules --exclude=dist --exclude='*.tsbuildinfo'` run against a fresh clone of it. Task changes found in exactly: `frontend/src/services/api-client.ts`, `frontend/src/components/backtest-results/BacktestResultsPanel.tsx`, `frontend/src/hooks/useBacktestRunMeta.ts` (new), `docs/architecture/strategy-engine-design.md`, `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, `CHANGES.md`, and this `TESTING.md` replacement — nothing else. Nothing under `backend/` touched.
