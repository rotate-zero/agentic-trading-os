# TESTING — decision #141 (D19 Daily Levels run isolation)

All database validation used real local PostgreSQL.

## Empirical confirmation

Before implementation, two identical runs generated distinct `run_id` values but reused all ten Daily Levels database IDs and `level_id` values. Every row's `updated_at` advanced during run 2. No row archived because the identical fixture matched every cluster; the same shared active-row pool fed the existing unmatched-row archive loop.

Decision #140's unchanged regression passed during this investigation, confirming its live/backtest isolation and non-zero repeat-run regime-score guarantees still held.

## Full-suite comparison

- Fresh pre-change database through migration `0009`: `737 passed, 0 failed` in 552.38s.
- Fresh final database through migration `0010`: `741 passed, 0 failed` in 788.11s.
- The four-test increase covers Feature Engine constructor pairing, the database CHECK's two invalid combinations, cascading deletion, and two-run end-to-end isolation.

## Focused verification

- New two-run regression: `1 passed` in 237.08s. Two identical ticker/scenario runs produced disjoint database IDs and `level_id` values; every run-1 field, including `updated_at`, remained unchanged after run 2; neither run's rows were archived.
- Existing #140 live-sentinel/non-zero-regime regression passed unchanged.
- Daily Levels, replay producer, and namespace modules: `30 passed`.
- Namespace module after adding the database invariant and cascade checks: `11 passed`; final module count is 12 with the constructor-pairing test.
- Python compilation and `git diff --check -- backend` passed.

## Migration round-trip

The `0010` fixture contained same-ticker live and backtest Daily Levels rows plus the referenced `backtests` row.

1. `alembic downgrade 0009` succeeded. `backtest_run_id`, its FK/CHECK, and the widened index disappeared; the old `(symbol_id, status)` index returned; both Daily Levels rows remained.
2. `alembic upgrade head` succeeded. The live row remained with `backtest_run_id=NULL`; only the unassignable legacy backtest Daily Levels row was removed; its unrelated `backtests` row remained; the FK, CHECK, and widened index returned.

## Safety and footprint

Source inspection confirmed `backend/app` has no code path that deletes a `backtests` row and no table has an FK to `daily_levels_state.id`. Migration `0010` contains no mutation of Market State, Level Interaction, candles, scanner-universe, fundamentals, or symbols.

The implementation changes eight backend files: one new migration; four runtime/model files; and three test files. Documentation changes are limited to `docs/architecture/strategy-engine-design.md`, `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, `TESTING.md`, and `CHANGES.md`. A fresh-main overlay compared by `diff -rq` produced exactly these 13 files. Nothing under `frontend/` changed.
