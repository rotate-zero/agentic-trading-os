# TESTING.md — Decision #130: `is_backtest` isolation on `GET /strategy-outcomes`

## What changed and why

`GET /intelligence/strategy-outcomes` (decision #123) shipped with no
`is_backtest` filter at all — harmless while `strategy_outcomes` had no
writer, but a live bug the moment Backtest Runner v1 (decision #128)
started writing real `is_backtest=True` rows: "Recent Closed Trades"
(`InfoTab.tsx`) would have silently rendered a backtest-simulated trade
as if it were a real closed one, with zero visual distinction.

This delivery closes that gap by making `is_backtest` a strict selector
on the route — copying the same discipline `performance_queries.py`'s
`_common_filters()` already enforces for the two aggregate queries next
to this route — and adds an optional `backtest_run_id` filter for
inspecting one specific backtest run's outcomes.

Full design reasoning lives in decision #130
(`docs/decisions/confirmed-decisions.md`); this file covers what to run
to verify it and what was deliberately not covered.

## Files changed

- `backend/app/api/routes/intelligence.py` — `GET /strategy-outcomes`
  gains `is_backtest: bool = Query(False)` and
  `backtest_run_id: str | None = Query(None)`; docstring corrected.
- `backend/tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py`
  — 6 new tests (12 total in the file).
- `frontend/src/services/api-client.ts` — `fetchStrategyOutcomes()`
  gains two new optional positional params (`isBacktest`,
  `backtestRunId`); query-string building switched to the
  conditional-append pattern `_performanceAnalyticsQuery` already uses.
- `frontend/src/hooks/useStrategyOutcomes.ts` — now calls
  `fetchStrategyOutcomes(limit, /* isBacktest */ false)` explicitly.
  Public hook signature (`useStrategyOutcomes(limit?: number)`)
  unchanged — `InfoTab.tsx`'s one call site needed no edit.
- `docs/architecture/strategy-engine-design.md` — §16's decision-#123
  checklist bullet corrected (was: "zero real rows in production"; now:
  zero real *live* rows, real *backtest* rows since #128); new diagram
  added to §7, directly after the existing decision-#128 as-built
  diagram, showing the `is_backtest` split feeding the two route call
  shapes.
- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md` —
  new decision #130 entry.

## What was deliberately NOT built

- **No "view backtest results" toggle UI.** This task closes the silent
  conflation; it does not add a way to browse backtest outcomes from
  the UI. `backtest_run_id`/`isBacktest` exist end-to-end (route →
  `api-client.ts`) for whatever future work wants them — e.g. a
  backtest-results viewer — but no UI consumes them yet.
- **No `InfoTab.tsx` change.** Checked directly against the live file:
  once `useStrategyOutcomes.ts` pins `isBacktest: false`, backtest rows
  never reach that component, so no visual-distinction badge or
  conditional rendering was needed.
- **`backend/app/backtest_runner/**`, `backend/app/main.py`,
  `useOpportunities.ts`, `useOpportunityConflicts.ts`,
  `useContextSnapshot.ts`, `usePerformanceAnalytics.ts`** — explicit
  boundaries for this task, confirmed untouched by `diff -rq` against a
  fresh clone.

## How to verify

### Backend (real Postgres — no SQLite, no mocks)

```bash
# From a clean environment:
apt-get install -y postgresql postgresql-contrib
service postgresql start
su - postgres -c "psql -c \"CREATE USER trading WITH SUPERUSER PASSWORD 'trading';\""
su - postgres -c "psql -c \"CREATE DATABASE trading_workspace OWNER trading;\""

cd backend
pip install -r requirements.txt --break-system-packages   # or use a venv
python -m alembic upgrade head

# Focused test file (12 tests: 6 pre-existing + 6 new for decision #130)
python -m pytest -q tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py -v

# Full suite
python -m pytest -q
```

Observed in this session:

- Focused file: 12 passed, stable across 3 isolated reruns.
- Full suite baseline (fresh clone, before this delivery): 702
  collected, 702 passed.
- Full suite after this delivery: 708 collected (exactly +6). One run
  showed 707 passed / 1 failed
  (`test_daily_levels_carry_level_interaction_once_touched`); a later
  run showed 708 passed / 0 failed. This is the pre-existing #119
  flaky cluster's other documented member (decision #129) — genuinely
  intermittent, confirmed by 5 isolated reruns of that one test across
  this session (3 fails, 2 passes), unrelated to and untouched by
  anything in this delivery. Not a regression.

### Frontend

```bash
cd frontend
npm install
npx tsc -b 2>&1 | grep -v "GridPresetPicker"   # should print nothing
npx vite build                                  # should succeed
```

Observed: `tsc -b` raw output shows exactly the 4 known decision-#35
`GridPresetPicker` errors and nothing else; `vite build` succeeds (86
modules transformed).

### Manual API check

```bash
# Default — live rows only (empty today, honestly, since no live writer exists)
curl "http://localhost:8000/intelligence/strategy-outcomes"

# Explicit live-only (same as above)
curl "http://localhost:8000/intelligence/strategy-outcomes?is_backtest=false"

# Backtest rows (real, after running a backtest via Backtest Runner v1)
curl "http://localhost:8000/intelligence/strategy-outcomes?is_backtest=true"

# One specific backtest run
curl "http://localhost:8000/intelligence/strategy-outcomes?is_backtest=true&backtest_run_id=<run-id>"

# Rejected — backtest_run_id without is_backtest=true (400)
curl "http://localhost:8000/intelligence/strategy-outcomes?backtest_run_id=<run-id>"

# Rejected — malformed UUID (400)
curl "http://localhost:8000/intelligence/strategy-outcomes?is_backtest=true&backtest_run_id=not-a-uuid"
```

## Manual merge notes

None. This delivery has zero file overlap with the parallel Backtest
Runner trigger-route work (`backend/app/api/routes/backtest.py` does
not exist anywhere in this delivery's changed-file list) — confirmed by
`diff -rq` against a freshly re-pulled clone immediately before writing
the decision log entry.
