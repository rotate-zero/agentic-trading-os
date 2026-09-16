# Backtest run metadata surfaced in the Backtest Results panel

`GET /intelligence/backtest-runs` (decision #136) had no frontend caller. This delivery adds one: `BacktestResultsPanel.tsx` now shows a run's own metadata (strategy_version, config_hash, date range, data_version, feature_version, walk_forward_fold, is_holdout, sweep_id, created_at) alongside the `StrategyOutcome` rows it already lists for that run.

**Decision number:** **#139**, confirmed against `confirmed-decisions.md`. Drafted throughout under the temp identifier `backtest-runs-metadata-surface` until packaging, per this project's parallel-work numbering discipline. Between the original three-source check and delivery, `main` moved with a pure doc-sequence correction (commit `6d1b3ce`, "Doc Sequence edited" — reordered decisions #135/#137 into strict numeric sequence, normalized heading style, and backfilled #138's own `CHANGES.md`; no code, no new decision, `INDEX.md` untouched). This delivery — its code, `docs/architecture/strategy-engine-design.md`, and `docs/decisions/INDEX.md` — was unaffected by that commit (confirmed byte-identical against both the pre- and post-sequence-edit base); only `docs/decisions/confirmed-decisions.md`'s own append point moved, so that file was rebased onto the corrected version rather than reusing the earlier copy. The three-source check was re-run against the new `main`: #138 still the last minted number in all three sources. No collision, no renumbering.

## What this closes

Decision #136 shipped backend-only, naming "showing a selected run's own metadata alongside `BacktestResultsPanel.tsx`'s outcome rows" as the natural next step, not built there. This delivery is exactly that.

## Files changed

- `frontend/src/services/api-client.ts`
  - New `BacktestRunWireShape`/`BacktestRunsWireShape` types, matching `schemas/performance.py`'s `BacktestRun` field-for-field.
  - New `fetchBacktestRuns(limit?, runId?, strategyName?, sweepId?)` — same positional-params shape and conditional-append query building as `fetchStrategyOutcomes`, since this function has exactly one caller today.
- New `frontend/src/hooks/useBacktestRunMeta.ts` — sibling to `useBacktestOutcomes.ts`, resolves one run's metadata by `run_id`. No-ops (no fetch) when `runId` is empty/undefined. No `refetch`: a `backtests` row is written once and never changes.
- `frontend/src/components/backtest-results/BacktestResultsPanel.tsx`
  - New `RunMetaBanner`/`RunMetaDetail`, rendered only when the panel's existing `appliedRunId` filter (decision #134) is set — never for the unfiltered "everything" view.
  - Reuses the file's own established expand-in-place pattern (`OutcomeRow`/`OutcomeDetail`): a one-line summary with a toggle revealing the full field set.
  - Three honest states, kept distinct: loading, absent ("No run metadata found for this run_id" — a syntactically valid UUID with no match), and error (a malformed run_id, since the filter field is free text) — separate from the panel's existing outcomes-fetch error banner.
- `docs/architecture/strategy-engine-design.md` — §7 gained one new as-built note with two diagrams (cross-component data flow; internal flow inside `RunMetaBanner`/`RunMetaDetail`), inserted directly after decision #136's own note.
- `docs/decisions/confirmed-decisions.md` / `INDEX.md` — decision #139.
- `TESTING.md` — rewritten for this delivery (delete-first).

## Placement decision

One real fork was presented to Saqib before any code was written: inline in `BacktestResultsPanel.tsx` (tied to the existing `appliedRunId` filter) vs. a new independent sub-view/tab vs. elsewhere. Saqib confirmed the first — no new shared state needed, since `appliedRunId` already identifies "the one run this panel is currently about."

## Deliberately NOT built

- No new `WorkspaceContext.tsx` state — `appliedRunId` already exists and already serves this purpose.
- No manual "Refresh" for the run-metadata banner — unlike outcomes (which can grow as a run progresses), a `backtests` row is complete and immutable the moment it's written, so there's nothing a refresh would pick up that a `runId` change doesn't already trigger.
- No standalone runs-browser view (listing/paginating recent runs independent of a specific `run_id`) — outside this task's scope; the route already supports `strategy_name`/`sweep_id` filters if that's wanted later.

## Tests

`npx tsc -b` — identical to a freshly re-pulled `main` baseline: exactly the 4 known `#35 GridPresetPicker` errors, zero new. `npx vite build` — clean, 91 modules (+1, the new hook file). No frontend test framework exists in this codebase; query-string construction and the three-state render logic were verified by direct source trace, not a live network call — no backend/Postgres was available in this delivery's own environment. See `TESTING.md` for the full checklist.
