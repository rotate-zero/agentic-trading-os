# TESTING.md — Performance analytics exposure (decision #127)

## What changed

`backend/app/trading_intelligence/performance_queries.py` already contained
two working query functions — `get_win_rate_by_hour()` and
`get_expectancy_by_session_type()` (decision #122, correction #124) — with
no route, no frontend client, and no UI. This delivery closes that gap:
two new thin routes, a frontend API client + hook, and a minimal "Strategy
Performance" section in the workspace UI. Full reasoning: decision #127,
`docs/decisions/confirmed-decisions.md`.

### Files touched

- `backend/app/api/routes/intelligence.py` (extended) — two new routes
  appended at file end: `GET /intelligence/win-rate-by-hour`,
  `GET /intelligence/expectancy-by-session-type`. No other change to this
  file.
- `backend/tests/test_performance_analytics_routes.py` (new) — 9 tests.
- `frontend/src/services/api-client.ts` (extended) — wire types
  (`HourlyWinRateWireShape`, `WinRateByHourWireShape`,
  `SessionTypeExpectancyWireShape`, `ExpectancyBySessionTypeWireShape`,
  `PerformanceAnalyticsFilters`) and two fetch wrappers
  (`fetchWinRateByHour`, `fetchExpectancyBySessionType`), inserted between
  the existing opportunity-conflicts and context sections.
- `frontend/src/hooks/usePerformanceAnalytics.ts` (new) — combined hook
  fetching both endpoints.
- `frontend/src/components/workspace/InfoTab.tsx` (extended) — new
  `StrategyPerformanceSummary` component + `formatHourEt` helper, rendered
  in `GeneralContent` directly below `RecentClosedTrades`; one new import.
- `docs/architecture/strategy-engine-design.md` (extended) — §5's existing
  ASCII diagram extended (not duplicated) to show the new route/hook/UI
  chain, one new paragraph noting the exposure, one new Stage 12 checklist
  entry.
- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md` — new
  decision #127 entry.
- This file.

### Explicitly NOT touched

`performance_queries.py`, `models/trading_intelligence.py`,
`schemas/performance.py`, `main.py`, `useStrategyOutcomes.ts`,
`useOpportunityConflicts.ts`, `useOpportunities.ts`, `useContextSnapshot.ts`,
`AIAnalysisPanel.tsx`, `backend/app/backtest_runner/*`,
`backend/app/context_engine/*`, `backend/app/api/websocket/channels.py`,
`CHANGES.md` (belongs to the concurrent Backtest Runner "Unit 5" delivery
already on `main` — see "Parallel-work note" below).

## What was NOT built (explicitly out of scope)

- No new analytics calculation — both routes are thin wrappers over the
  existing query functions. No route-level recomputation.
- No date filter, symbol filter, or pagination on either route — neither
  underlying query function supports them, so none is exposed. The only
  filters exposed are the three the functions actually have:
  `strategy_name`, `strategy_version`, `is_backtest`.
- No filter UI (strategy/backtest dropdowns) in the frontend section —
  minimal surfacing only, per this task's own scope. Both routes stay
  fully filterable for a future, separately-considered UI.
- No new dashboard, page, tab, or charting system.
- No Pydantic schema addition — `dataclasses.asdict()` is used directly,
  matching `performance_queries.py`'s own stated reasoning for keeping
  these as local dataclasses.

## A correction worth flagging

An earlier draft of this task's instructions asserted that
`get_win_rate_by_hour()`/`get_expectancy_by_session_type()` support only
`strategy_name`/`strategy_version`, and warned against "inventing"
`is_backtest`. Read directly against the real, current function
signatures in `performance_queries.py` before any route was written, this
is incorrect: `is_backtest: bool = False` is both functions' own third
keyword argument, already fully implemented and documented in each
function's own docstring. It is kept as a real, exposed filter on both
routes. See decision #127's own entry for the full account.

## Parallel-work note

Partway through this task, a routine re-pull of `main` surfaced a new,
unrelated parallel landing: Backtest Runner "Unit 5" (test-only, its own
`CHANGES.md`, one new file `backend/tests/test_backtest_runner_regression.py`,
20 new tests). Confirmed via `diff -rq` that `backend/app/backtest_runner/`
was byte-identical to this task's starting snapshot and that
`intelligence.py` upstream carried zero backtest-related content —
genuinely file-disjoint. That file was pulled into this session's local
working tree purely so this delivery's own before/after test counts
reflect current `main`, not a stale starting snapshot. It is **not** part
of this delivery's zip — it already exists on `main`, so a checkout that
tracks `main` already has it. This delivery's `CHANGES.md` is likewise
left untouched, since it belongs to that other delivery, not this one.

## Backend validation

Real PostgreSQL 16, provisioned locally to match the project's documented
setup exactly (`trading`/`trading`/`trading_workspace`), migrated via
`alembic upgrade head`.

**Baseline** (fresh untouched clone of current `main`, including Backtest
Runner Unit 5's own +20 tests — see parallel-work note above):

```
693 collected, 691 passed, 2 failed
```

The 2 failures are the pre-existing, documented decision-#119 flaky
cluster (`test_vwap_publishes_even_while_sma_is_still_warming_up`,
`test_daily_levels_carry_level_interaction_once_touched`) — a standing
baseline fixture, not investigated per standing project convention.

**Working tree** (baseline + this delivery):

```
702 collected, 700 passed, 2 failed
```

Exactly +9 — this delivery's own new tests, all passing. Same 2
flaky-cluster failures, zero regressions. Re-ran the full suite twice
against a freshly wiped-and-recreated database; identical result both
times. The 9 new tests were also run in isolation twice for stability;
100% pass rate both times.

### New tests (`backend/tests/test_performance_analytics_routes.py`)

Mirrors `test_strategy_outcomes_and_opportunity_conflicts_routes.py`'s own
route-test posture: direct `StrategyOutcomeRecord` ORM inserts, proving
each route forwards to and shapes the real query-layer result — does
**not** re-test the grouping/arithmetic itself, which
`test_performance_queries.py` already covers exhaustively (8 tests, real
Postgres).

- Empty-table case for both routes (`{"hourly_win_rates": []}` /
  `{"session_expectancy": []}`, 200, not an error)
- Populated case for both routes, asserting exact field values including
  the honest-`None` session-type group
- `strategy_name` filter correctly isolates rows
- `is_backtest` filter correctly isolates rows (mixed live/backtest rows
  with deliberately opposite outcomes, so blending would be detectable)
- `strategy_version` without `strategy_name` → 400 for both routes

## Frontend validation

**Baseline** (fresh untouched clone):

```
npx tsc -b   → 4 errors, all in GridPresetPicker.tsx (decision #35, known pre-existing)
npx vite build → not reached (tsc -b exits non-zero); ran standalone: succeeds, 86 modules
```

**Working tree:**

```
npx tsc -b   → same 4 errors, same lines, GridPresetPicker.tsx only
npx vite build → succeeds, 86 modules transformed
```

No new frontend errors. No new frontend tests added, matching this
codebase's existing test-free hook convention.

## Known limitations

- `strategy_outcomes` has zero real rows in production today (no
  Execution Engine exists yet to write one) — both routes and the new UI
  section will show "No performance data yet." until that changes. This
  is expected and matches the exact posture decision #123 already
  shipped for `/strategy-outcomes`/`/opportunity-conflicts`.
- The new `usePerformanceAnalytics.ts` hook exposes a caller-visible
  `error` state, unlike every other existing hook in this codebase
  (`useStrategyOutcomes`, `useOpportunityConflicts`, `useContextSnapshot`
  all collapse fetch failures into an empty/default result). This is a
  deliberate, stated deviation — see decision #127 — not an inconsistency
  to "fix" by matching the older hooks; if anything, the older hooks are
  candidates for the same fix in a future, separately-scoped task.

## Final verification

`diff -rq` against a freshly-pulled untouched clone of current `main`
confirms the only files changed are the ones listed under "Files touched"
above.
