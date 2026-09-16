# CHANGES — decision #140 (D18 backtest symbol namespace)

Backtest replay persistence is now isolated from live trading for the same ticker.

## What changed

- Added `is_backtest` to `symbols` and `daily_levels_state`; existing rows migrate to the live namespace.
- Enforced Daily Levels origin with a composite `(symbol_id, is_backtest)` foreign key.
- Run-scoped Feature, Market State, and Level Interaction engines use the backtest namespace.
- Updated all nine production `Symbol.ticker` lookup files found by grep so live paths explicitly select `is_backtest=false`.
- Backtest Feature Engines fetch daily history on every run instead of restoring a prior replay's checkpoint, preserving ATR/RVOL regime scores on identical repeat runs.
- Added same-ticker collision coverage for every lookup file and strengthened the end-to-end replay regression.

Option (c), real namespacing, was chosen over `finally` cleanup. Cleanup would require deleting dependent rows across seven symbol-FK tables or restoring live rows reconciliation had updated/archived; it also cannot prevent in-run visibility or survive process termination before `finally`.

Documentation: `strategy-engine-design.md` D18 is resolved; decision #140 is recorded in the decision log and index. The pre-market async-prewarm fix and D4 readiness check are now unblocked but remain separate work.
