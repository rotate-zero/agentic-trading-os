# CHANGES — pending delivery `world-view-v1`

Base commit: `0913746a1c2c5dae3ecb72844ea2ab1c5bab8d19` (GitHub `main` at inspection time).

## What changed

- Added `backend/app/world_view/composite.py` with:
  - stateless `WorldView.snapshot(symbol: str | None = None)`;
  - frozen `WorldViewSnapshot` carrying `symbol`, unmodified Market State and Context envelopes, separated Performance Intelligence, and `portfolio`;
  - four existing aggregate reads executed off-loop with `asyncio.to_thread` and separated by explicit live/backtest selectors.
- Added `backend/app/world_view/__init__.py` as the package's public import surface.
- Added thin `GET /intelligence/world-view` with optional `symbol` to the existing intelligence router.
- Added `backend/tests/test_world_view.py` covering real public source evaluation, `record_strategy_outcome()` writes, live/backtest separation, empty populations, honest missing-symbol envelopes, option-A Portfolio serialization, route calls with/without `symbol`, and exact all-table before/after counts proving World View writes nothing.
- Updated `docs/architecture/trading-intelligence-architecture.md` §15 from unbuilt intent to the as-built contract and expanded its existing ASCII diagram with external and internal data flow.
- Updated the World View tree entry in `docs/architecture/system-design.md`.
- Appended the unnumbered `world-view-v1` implementation entry to `docs/decisions/confirmed-decisions.md`. No real number or `INDEX.md` row was assigned.
- Replaced repo-root `TESTING.md` with this delivery's exact setup, commands, and results.

## Confirmed Portfolio choice

Saqib selected option A: `portfolio: dict[str, Any] | None` is present now and serializes as `null` until Portfolio State exists. It means unavailable source, not empty portfolio. No Portfolio engine, fake positions, zero buying power, or placeholder data was created.

## Behavior and boundaries

- Market State and Context retain their complete public `get_snapshot(symbol)` envelopes.
- `symbol` scopes only Market State and Context in v1.
- Performance is system-wide and all-matching-history, not recent or symbol-scoped.
- Performance shape is exactly:

  ```text
  live     → hourly_win_rates + session_expectancy
  backtest → hourly_win_rates + session_expectancy
  ```

- `state_snapshot.py` remains the separate two-source capture mechanism for `StrategyOutcome` entry/exit fields.
- No persistence, migration, write path, cache, scheduler, background task, event subscription, WebSocket channel, startup work, frontend change, or performance SQL was added.

## Validation summary

- Untouched-main full baseline: 772 passed, 0 failed, 0 skipped; 75,497 warnings; 790.87s.
- New World View tests: 4 passed, 0 failed, 0 skipped; 221 warnings; 1.14s.
- New plus affected tests: 84 passed, 0 failed, 0 skipped; 4,412 warnings; 8.14s.
- Changed-tree full suite: 775 passed, 1 failed, 0 skipped; 76,449 warnings; 790.60s. The one pre-existing Feature Engine test failed because it received three valid events where it asserts exactly one; it passed immediately in isolation (1 passed, 2,934 warnings, 1.16s). This delivery does not touch that engine/test, and World View tests execute later alphabetically.

## Related follow-up, not changed

`test_stop_waits_for_an_in_flight_compute_before_returning` uses a wall-clock candle timestamp and asserts exactly one published timeframe. On aggregation boundaries it can receive additional valid timeframe events. Its exact-count assertion should be made deterministic in a separately approved task; this delivery leaves it untouched.

The open decision file is over 136KB and still contains eight unnumbered pending deliveries. Per the task boundary, rollover is deferred until merge-time numbering makes the archive boundary safe.
