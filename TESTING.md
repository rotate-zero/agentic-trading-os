# TESTING.md — Decision #136: read-only `GET /intelligence/backtest-runs`

## What this delivery is

A new read-only route exposing the `backtests` table — its first
reader since decision #128 started writing real rows to it. Every real
backtest run since #128 has written a row (`run_id`, `sweep_id`,
`strategy_name`, `strategy_version`, `config_hash`, `symbol_universe`,
`date_range_start/end`, `data_version`, `feature_version`,
`walk_forward_fold`, `is_holdout`, `created_at`), and before this
delivery, none of them had ever been read back. `GET /strategy-
outcomes?backtest_run_id=X` (decision #130) shows what a run
*produced*; this route shows a run's own metadata.

Full reasoning — the fork discussed with Saqib over filters/response
shape, why this stayed out of `performance_queries.py`, the stale
docstrings found and fixed, and a decision-number collision with a
parallel session reconciled per this project's own standing
three-source re-check — lives in `docs/decisions/confirmed-
decisions.md`, decision #136. This file covers what to run to verify
it.

**Zero frontend changes.** This delivery is backend-only, deliberately
— see "What was deliberately NOT built" below.

## Files changed

**New:**
- `backend/tests/test_backtest_runs_route.py` — 9 tests, real
  Postgres. See "Tests" below.

**Modified:**
- `backend/app/api/routes/intelligence.py` — new route
  `GET /intelligence/backtest-runs`, appended after the existing
  `/expectancy-by-session-type` route. Nothing else in this file
  changed.
- `backend/app/models/trading_intelligence.py` — two docstring
  corrections (`BacktestRunRecord`'s own class docstring, and the
  module-level docstring's `backtests` bullet). Both previously said
  "no Backtest Runner writes to this table yet (§7: not built now)" —
  false since decision #128.
- `backend/app/schemas/performance.py` — same two-copy correction, on
  `BacktestRun`'s own class docstring and this file's module-level
  docstring.
- `docs/architecture/system-design.md` — §4.13's line on deferred
  tables still said `backtests` was "shape locked, not yet created."
  Found while checking for other copies of the same stale claim (not
  one of the two named in scope); corrected, since it's the same error
  in the same category, not a separate cleanup.
- `docs/architecture/strategy-engine-design.md` — §7 gained one new
  as-built note with two diagrams (cross-component data flow;
  the route's own internal request→filter→query→validation→envelope
  flow), inserted immediately after decision #135's own as-built note
  (see "A decision-number collision" below) — not overwriting it.

No migration changes. `backtests` already exists
(`0008_strategy_outcomes_and_backtests.py`, decision #120).

## The route

```
GET /intelligence/backtest-runs
    ?limit=<int, default 50, max 500>
    &run_id=<uuid>
    &strategy_name=<string>
    &sweep_id=<uuid>
```

- All three filters are optional and AND-combined (never blended).
- Newest-first by `created_at` (this table has no `exit_filled_at`-
  equivalent field to order by, unlike `strategy_outcomes`).
- `run_id`/`sweep_id` are real UUID columns — a malformed value for
  either is a `400` with a message naming which parameter was
  malformed, not a silently-empty result.
- Response: `{"backtest_runs": [BacktestRun, ...]}` — the existing
  Pydantic contract, reused as-is, matching this file's own envelope-
  naming convention (`outcomes`, `hourly_win_rates`,
  `session_expectancy`).
- An empty or no-match result is `{"backtest_runs": []}`, `200` — never
  an error, same convention every other route in this file follows.

## Why this stayed out of `performance_queries.py`

That module's own docstring scopes it strictly to `GROUP BY`
aggregations over `strategy_outcomes` ("two real queries, exactly
two" — win rate by hour, expectancy by session type). This route is a
raw recent-rows read over a *different* table, with no aggregation and
no grouping key — exactly `GET /strategy-outcomes`'s own shape (a
direct SQLAlchemy `select` built inline in the route), not that
module's. Built inline in `intelligence.py` accordingly.

## A decision-number collision, reconciled per this project's own
## standing three-source re-check

This delivery's own investigation and implementation were carried out
citing **#135** throughout, confirmed as the correct next number via
the standard three-source check at session start. A parallel session
closing the historical-provider gap in Backtest Runner replay also
claimed and merged **#135** first, mid-session — not discovered until
the mandatory re-check immediately before writing the decision-log
entry. **Final number assigned to this delivery: #136** — every
internal citation (route docstring, corrected model/schema docstrings,
the `system-design.md` correction, this file, the test file, and the
`strategy-engine-design.md` as-built note) was renumbered before
packaging. `strategy-engine-design.md` §7 was rebased onto the
freshly re-pulled `main` — this delivery's own note now sits
immediately after decision #135's real, already-merged as-built note,
not in place of it.

Zero file overlap either way: that delivery's own footprint statement
lists `intelligence.py`/`performance_queries.py` as untouched by it;
this delivery never touches anything under
`backend/app/backtest_runner/` or `backend/app/api/routes/backtest.py`
— confirmed by `diff -rq` against a freshly re-pulled clone both before
writing and immediately before packaging.

## Tests

New file `backend/tests/test_backtest_runs_route.py`, 9 tests, real
Postgres — a separate file, not an extension of
`test_strategy_outcomes_and_opportunity_conflicts_routes.py`, matching
this suite's own one-file-per-route-group precedent
(`test_performance_analytics_routes.py` already sits separately from
that file despite both testing `intelligence.py` routes).

- `test_route_reflects_a_real_backtest_runner_write` — drives a real
  fixture `BacktestRunner.run()` end-to-end (same fast stub-strategy/
  4-candle pattern `test_backtest_runner.py` already established, on a
  dedicated synthetic symbol `ZZBTR5`, distinct from that file's own
  `ZZUNIT4`) and confirms the route reads back, by `run_id`, exactly
  what the real write path produced. This is the proof that matters:
  the row under test was genuinely produced by `_write_backtest_run_
  record()`, not hand-inserted.
- The remaining 8 tests seed `BacktestRunRecord` rows directly via a
  local `_insert_backtest_run()` helper (same shape as the sibling test
  file's own helper of the same name): `strategy_name` filter,
  `sweep_id` filter, `run_id` filter, newest-first ordering, `limit`
  capping, honest-empty-collection for an unknown `run_id`, malformed
  `run_id` → 400, malformed `sweep_id` → 400. Ordering/limit tests use
  explicit year-2099 `created_at` timestamps so they're
  deterministically newest regardless of anything else in the table.

## How to verify

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or your usual env
pip install -r requirements.txt --break-system-packages

# Fresh DB, per this project's standing convention
psql -c "CREATE USER trading WITH PASSWORD 'trading' SUPERUSER;"
psql -c "CREATE DATABASE trading_workspace OWNER trading;"
psql -d trading_workspace -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
cp .env.example .env
alembic upgrade head

python -m pytest tests/test_backtest_runs_route.py -v   # this delivery's own 9 tests, isolated
python -m pytest -q                                       # full suite
```

Expected on the isolated run: 9 passed, 9 total.

Expected on the full suite: 728 collected (719 on the current `main`,
i.e. including decision #135's own 4 new tests, + this delivery's own
9). Failures will range from 0 to 3 depending on run — every failure
you might see belongs to the long-documented #119 flaky cluster
(`test_vwap_publishes_even_while_sma_is_still_warming_up`,
`test_daily_levels_carry_level_interaction_once_touched`,
`test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction`,
`test_feature_engine_backfills_from_persisted_history_on_cold_start`)
— named across decisions #93/#113/#114/#116-119/#122-135, unrelated to
this delivery. Final verification run for this delivery: 728
collected, 727 passed / 1 failed
(`test_daily_levels_carry_level_interaction_once_touched` alone — a
documented member of that cluster). None of this delivery's own 9 new
tests were ever among the flickering set, confirmed by rerunning
`test_backtest_runs_route.py` alone multiple times in isolation with
9/9 passing every time.

**Manually confirming the route directly** (optional, the automated
tests above already prove this):

```bash
# with the backend running, and at least one real backtest already run
curl -s "http://localhost:8000/intelligence/backtest-runs?limit=5" | python -m json.tool

# a specific run, once you have its run_id from a POST /backtest/run response
curl -s "http://localhost:8000/intelligence/backtest-runs?run_id=<uuid>" | python -m json.tool

# a malformed id — expect 400
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8000/intelligence/backtest-runs?run_id=not-a-uuid"
```

## What was deliberately NOT built

- **No frontend work.** Matching this project's own established
  backend-then-frontend sequencing (`performance_queries.py` at #122
  before #127 exposed it over HTTP; Context Engine's split at #98/#125
  is the same shape). Showing a selected run's own metadata — e.g.
  alongside `BacktestResultsPanel.tsx`'s outcome rows, using
  `WorkspaceContext.tsx`'s `lastBacktestRunId` (decision #134) as the
  `run_id` to look up — is the natural next step this leaves open, not
  an oversight.
- **No `strategy_name`/`sweep_id` companion-requirement validation.**
  Unlike `/strategy-outcomes`'s `backtest_run_id`-requires-
  `is_backtest=true` rule, this route's three filters have no
  interdependency — each is independently optional, so there was
  nothing analogous to enforce.
- **No changes to `performance_queries.py`, `backtest.py`, or anything
  under `backend/app/backtest_runner/`.** Explicit file boundaries for
  this task; confirmed untouched by `diff -rq`.

## Manual merge notes

No files this delivery touched are also touched by decision #135
(the historical-provider-gap delivery merged just before this one) —
confirmed by that delivery's own stated footprint and this delivery's
own `diff -rq`, both ways. `strategy-engine-design.md` is the one file
both deliveries append to, but at genuinely different points (#135's
own as-built note, then this delivery's, immediately after) — a normal
git merge of both onto the same base should apply cleanly without a
manual conflict resolution. No other overlap with any other in-flight
parallel session is known at the time of this delivery.
