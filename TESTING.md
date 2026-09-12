# TESTING.md — Backtest Runner v1 close-out (decision #128)

## What this documents

`backend/app/backtest_runner/` (Units 1-5, five separate deliveries) is
now feature-complete and this file's own close-out (Unit 6) is
documentation-only — zero `backend/app/` changes of its own. Full
reasoning, including the precise data/Context/D17 boundaries: decision
#128, `docs/decisions/confirmed-decisions.md`.

### A note on repo state during this close-out

Mid-close-out, a separate parallel session's Performance Analytics work
landed on `main` and had independently claimed decision #127 — a real
numbering collision, same category as #98/#99, #111/#112, #114/#115,
#120/#121, #122/#123. This entry's own decision was drafted under #127
too, before either session could see the other; reconciled by renumbering
this one to #128 rather than overwriting theirs (decisions are immutable).
Synced their files in (`intelligence.py`, `InfoTab.tsx`, `api-client.ts`,
`usePerformanceAnalytics.ts`, `test_performance_analytics_routes.py`) and
re-ran the full suite against the truly-current, merged `main` before
finalizing the numbers below — not against this session's own earlier,
now-stale snapshot. Cross-confirmed file-disjoint both ways: decision
#127's own entry states it never touched `backend/app/backtest_runner/`;
this entry confirms it never touched any of #127's files either.

### Files touched by this close-out

- `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md` —
  new decision #128 entry + index row.
- `docs/architecture/strategy-engine-design.md` — targeted addition to
  §7 (one diagram, one "as-built" note). No rewrite, no restructure.
- This file.

**Zero backend/app/ changes from this entry.** Confirmed by `diff -rq`
against a fresh untouched clone — see "Fresh-clone diff verification"
below. (`intelligence.py` et al. differ from an *older* clone only
because decision #127's own, independent work is now part of current
`main` — not because this close-out touched them.)

## What Backtest Runner v1 proves (built across Units 1-5, not this close-out)

A fixture-driven replay of one symbol through the real, unmodified engine
pipeline and a real, unmodified `Strategy.evaluate()` produces a real
`BacktestRunRecord` and a real `StrategyOutcomeRecord(is_backtest=True)`
resolving to it, with entry/exit snapshots captured at the real
`entry_filled_at`/`exit_filled_at` instants (not the signal candle), and
`is_backtest=True` isolation reused directly from decisions #120/#122.

## What it does NOT prove — read this before citing this milestone as more than it is

- **Real historical-market accuracy or strategy profitability.**
  `FixtureCandleProvider` validates replay/persistence plumbing only —
  hand-built or synthetic candles, never claimed otherwise.
- **Real historical Context Engine replay.** `FixtureBacktestContextProvider`
  is calendar-only (`MarketClock`-derived, real logic) with Fundamentals/
  News honestly absent — no point-in-time historical context source
  exists in this codebase yet. `HistoricalContextProvider` is a
  documented extension point, not a working implementation.
- **Multi-symbol replay, sweep execution, walk-forward validation,
  parameter optimization, ML/predictive performance, or production
  trading performance.** None of these were built, and Backtest Runner
  v1's own data structures are symbol-keyed specifically so a future
  multi-symbol runner can extend this without a rewrite — but nothing
  here exercises that path today.

## Backend validation — Backtest Runner's own test suite

Real local Postgres (provisioned this session), migrations applied via
`alembic upgrade head`.

```
cd backend
python -m pytest tests/test_backtest_runner_fixtures.py \
                  tests/test_replay_state_producer.py \
                  tests/test_fill_simulator_and_gate.py \
                  tests/test_backtest_runner.py \
                  tests/test_backtest_runner_regression.py -v
```

**64/64 passing.** Breakdown: 12 (fixture provider/context provider,
Unit 1) + 4 (replay state producer, real engines, Unit 2) + 16 (fill
simulator/D17 gate, pure, Unit 3) + 3 (full end-to-end run, Unit 4) + 20
(runner-level D17, singleton serialization, Polygon propagation,
strategy-branch regression guard, Unit 5) + 9
(`test_performance_queries.py`, decision #124's own suite, re-run
alongside as a sanity check every delivery).

Not re-proven here, deliberately: `performance_queries.py`'s own query
correctness (decisions #122/#124/#127 already cover it in full) —
Backtest Runner only ever calls `record_strategy_outcome()`, never those
query functions, and duplicating their tests here would test code this
task never touches.

## Full-suite regression — real local Postgres, run against current `main` (including decision #127's work) before this close-out's own doc changes

```
cd backend
python -m pytest -q
```

**702 collected, 700 passed, 2 failed.** Both failures checked
individually against the documented decision #119 flaky cluster, not
assumed:

- `tests/test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`
  — reproduces the cluster's documented intermittent/order-sensitive
  signature exactly (passed clean on isolated rerun), consistent with
  this test's full history in the decision log since #113.
- `tests/test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
  — matches the cluster by name and by its long-documented general
  fragility (this exact test's hardcoded assertion has needed widening
  multiple times before, for `session_volume` then `vwap_ext`/
  `session_volume_ext`, each logged as "expected, not a regression").
  **This run's specific cause is newly identified, not previously
  logged**: decision #111 made `_update_gap` publish `regular_open` for
  any candle at/after real regular-session open; this test's fixture
  candle sits exactly at regular-session open and has triggered that
  publish ever since #111 landed, but #111's own text only mentions
  updating "three existing gap tests" — not this one. Reproduces
  deterministically in isolation (confirmed by direct rerun), unlike the
  daily-levels member above. Flagged precisely here rather than folded
  into "2 known #119 failures, nothing to see" — a genuine, minor,
  pre-existing test-staleness gap, unrelated to any Backtest Runner or
  Performance Analytics work, and out of scope for this documentation-
  only close-out to fix (would mean editing `test_feature_engine.py`).

**Zero regressions from either parallel track**: same 2 failures, same
causes, both before and after decision #127's own work merged, and on a
tree with only this close-out's documentation changes applied on top.

## Fresh-clone diff verification

`diff -rq` against a freshly-pulled untouched clone of current `main`
(post-#127-merge) confirms this close-out's own change set is exactly the
"Files touched" list above — `backend/app/` and `backend/tests/`
untouched by this entry (Unit 6 adds no tests, per its own scope), no
accidental touch to any file outside the four documents this close-out
targets, and no interference with decision #127's own independent work.

## Known limitations / deferred, not done here

- `backend/app/api/routes/backtest.py` — the optional route to trigger a
  run over HTTP. Deferred every delivery so far, remains a separate
  follow-up decision, not built or tested in Units 1-6.
- The `regular_open` test-staleness finding above — flagged, not fixed,
  per this close-out's documentation-only scope.
