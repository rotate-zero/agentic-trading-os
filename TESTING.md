# TESTING — decision #165: first-class sweep outcome filtering

## Behavior changed

`GET /intelligence/strategy-outcomes` now accepts `sweep_id` and resolves
sweep membership with a SQL join from `strategy_outcomes.backtest_run_id` to
`backtests.run_id` and `backtests.sweep_id`. It requires `is_backtest=true`,
rejects malformed UUIDs with 400, returns an honest empty 200 for an unknown
valid sweep, AND-combines with `backtest_run_id`, and applies ordering and
`limit` once globally.

`fetchStrategyOutcomes()` accepts the additive optional `sweepId`. A sweep
refresh now performs exactly two requests: `/backtest-runs?sweep_id=...` for
all run metadata and `/strategy-outcomes?is_backtest=true&sweep_id=...` for
all outcomes. The hook retains both collections so zero-outcome runs remain
visible.

## Baseline and validation

Starting repository: `/home/rotate_zero/projects/agentic-trading-os`, branch
`main`, commit `57157cce7cce04a2172e97047c9dcf64f6ff03f9`, clean and equal to
`origin/main`. Starting decision tail: #164. The nine-file start hashes were
recorded before editing; no allowed file differed from `origin/main`.

Clean-main baselines:

- targeted route module: `12 passed`;
- full backend suite: `804 passed, 0 failed, 0 skipped`;
- `npx tsc -b`: four known decision-#35 `GridPresetPicker.tsx` errors only.

Post-change targeted real-Postgres validation used an isolated PostgreSQL 18
instance on port 55434 with migrations through 0011:

- Alembic upgrade through 0011 — passed;
- targeted route module — `18 passed`.

Post-change validation:

- full backend suite against the isolated database: `810 passed, 0 failed, 0 skipped`;
- `npx tsc -b`: the same four known `GridPresetPicker.tsx` errors, with no new errors;
- `npx vite build`: passed, 101 modules transformed;
- `git diff --check`: passed;
- source review confirms one sweep outcomes request, no per-run outcomes map, no client merge/re-sort, and no model/schema/migration changes.
- `unzip -l backtest-sweep-outcomes-filter.zip`: exactly the nine listed root-relative files, with no wrapper directory or metadata.

## Coverage

The added real-Postgres tests cover two runs in one sweep, exclusion of a
different sweep and live outcomes, unknown and malformed sweep IDs, missing
backtest isolation, consistent and inconsistent combined filters, global
newest-first ordering, and one global limit across runs. Existing route tests
remain in the same module. No frontend test framework was added; TypeScript,
Vite, static source checks, and request-shape review cover the hook/client
change.

## Collision, continuity, and footprint

The final remote fetch confirmed `origin/main == 57157cce7cce04a2172e97047c9dcf64f6ff03f9`, unchanged from the starting commit. All nine origin blobs matched the nine recorded start hashes, so there was no collision. Archives cover contiguous `001-060`, `061-079`, `080-090`, `091-106`, `107-121`, and `134-160` plus the existing `122-133` range; the live index/log end at #164 on refreshed `main`, so this delivery is #165. Existing decision bodies and archive files remain unchanged.

The exact allowed footprint is:

1. `backend/app/api/routes/intelligence.py`
2. `backend/tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py`
3. `frontend/src/services/api-client.ts`
4. `frontend/src/hooks/useBacktestSweepOutcomes.ts`
5. `docs/architecture/backtest-runner-design.md`
6. `docs/decisions/INDEX.md`
7. `docs/decisions/confirmed-decisions.md`
8. `CHANGES.md`
9. `TESTING.md`

No model, schema, migration, sweep execution code, panel, unrelated hook or
route is permitted to change. The delivery archive will contain exactly these
nine root-relative paths and no wrapper directory, logs, caches, or metadata.
