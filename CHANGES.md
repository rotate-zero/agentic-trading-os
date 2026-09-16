# CHANGES — decision #139 (surface-backtest-runs-metadata)

Surfaces `GET /intelligence/backtest-runs` (decision #136) in the frontend for the first time. Frontend-only — no backend, migration, or API-contract change.

## What changed

- **New `frontend/src/hooks/useBacktestRuns.ts`** — resolves one backtest run's own metadata (`strategy_name`, `strategy_version`, `config_hash`, `symbol_universe`, `date_range`, `data_version`/`feature_version`, `walk_forward_fold`, `is_holdout`, `created_at`) by `run_id`. Separate from `useBacktestOutcomes.ts` — different table (`backtests` vs. `strategy_outcomes`), different granularity (one row per run's own settings vs. one row per closed trade). Skips the fetch entirely when no `run_id` is given.
- **`frontend/src/services/api-client.ts`** — new `BacktestRunWireShape`, `BacktestRunsWireShape`, `fetchBacktestRuns()`, matching `StrategyOutcomeWireShape`/`fetchStrategyOutcomes`'s established pattern.
- **`frontend/src/components/backtest-results/BacktestResultsPanel.tsx`** — new `RunMetadataCard`, rendered inside `BacktestResultsBody` whenever a specific `appliedRunId` is applied (decision #134's existing auto/manual filter-mode state — unchanged). Shows the selected run's own settings alongside its `StrategyOutcome` rows, with independent loading/error/empty state from the outcomes list beside it.
- **`WorkspaceContext.tsx` — deliberately not touched.** `appliedRunId` already carries the run_id linkage this needed; no new shared state was required.

## Design fork (presented before implementation)

Two options were presented: (A) an inline metadata card keyed to the panel's existing `appliedRunId` state, or (B) a separate, independently-browsable "Runs" sub-view for picking a run_id from a list. (A) was recommended and confirmed — it directly closes the exact gap decision #136's own docstring named, with zero new shared state. (B) remains a real, separate, larger feature that nothing in this task asked for.

## Docs

- `docs/architecture/strategy-engine-design.md` §7 — new as-built note, inserted directly after decision #136's own note (no rebase needed — re-checked upstream immediately before writing, `main` unchanged since the initial pull). Two diagrams: cross-component data flow (`BacktestResultsBody` → `useBacktestRuns` → `fetchBacktestRuns` → the existing route → `RunMetadataCard`) and internal flow within the changed module (`appliedRunId` change → conditional fetch → loading/error/empty/populated render).
- `docs/decisions/confirmed-decisions.md` + `docs/decisions/INDEX.md` — new entry, **decision #139**. Assigned via the standard three-source re-check (`INDEX.md`, `confirmed-decisions.md`'s tail, the archive file list) on a freshly re-pulled clone immediately before packaging — all three agreed on #138 as the last confirmed number, no collision. Drafted and cited during implementation under this delivery's commit slug (`surface-backtest-runs-metadata`); every internal citation renumbered to #139 before packaging.

## Verification

See `TESTING.md` for the full source-trace verification and build results. Summary: no frontend test framework exists in this codebase (confirmed by grep, consistent with prior frontend-only deliveries); `npx tsc -b` shows the identical 4 pre-existing `#35 GridPresetPicker` errors on both an untouched baseline and this delivery's tree, zero new; `npx vite build` clean.

## Footprint

Modified: `api-client.ts`, `BacktestResultsPanel.tsx`, `strategy-engine-design.md`, `confirmed-decisions.md`, `INDEX.md`. New: `useBacktestRuns.ts`, `TESTING.md`, `CHANGES.md`. Nothing under `backend/` or any migration touched; `WorkspaceContext.tsx` and `useBacktestOutcomes.ts` untouched. Confirmed by `diff -rq` against a freshly re-pulled clone immediately before packaging.
