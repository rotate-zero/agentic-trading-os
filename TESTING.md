# Testing — Performance Intelligence's read-side query layer (decision #122)

This file is a fresh delete-first replacement, scoped to this delivery only
(the two `GROUP BY` query functions over `strategy_outcomes`). It supersedes
whatever `TESTING.md` existed before this change; look at `docs/decisions/`
for the history of prior deliveries' own validation write-ups.

## What this delivery touched

- **New:** `backend/app/trading_intelligence/performance_queries.py`
- **New:** `backend/tests/test_performance_queries.py`
- `docs/decisions/confirmed-decisions.md` (decision #122 appended)
- `docs/decisions/INDEX.md` (matching row appended)
- `docs/architecture/strategy-engine-design.md` (small §5 pointer only —
  locked schema untouched)
- This file

Confirmed by `diff -rq` against a freshly-pulled, untouched second clone
(`/home/claude/baseline-clone` in this session) immediately before writing
this file: no other file differs (modulo `__pycache__`/`.pytest_cache` build
artifacts, which aren't part of the repo). In particular, `app/
trading_intelligence/performance.py`, `backend/tests/
test_performance_intelligence.py`, `app/api/routes/intelligence.py`,
`app/models/trading_intelligence.py`, `app/schemas/performance.py`,
`app/trading_intelligence/opportunity_view.py`, and everything under
`frontend/` are all untouched — confirmed, not assumed.

## Environment

Real local PostgreSQL 16, matching the project's actual `postgres:16`
image (`docker-compose.yml`) — provisioned directly in this sandbox via
`apt-get install postgresql postgresql-contrib` (Docker itself isn't
available in this sandbox; this is a native local server, not a
container, but it is genuinely real Postgres, not a mock or SQLite
substitute). Role/database created to match `app/core/config.py`'s actual
defaults exactly (`trading`/`trading`@`localhost:5432`/`trading_workspace`),
so no environment-variable overrides were needed. Schema built with
`alembic upgrade head` (all 8 migrations, through `0008_strategy_outcomes_
and_backtests.py`) — never a hand-built schema.

Backend Python dependencies installed from `backend/requirements.txt`.

## Before this change (baseline)

Full suite run against a freshly migrated database:

```
617 passed, 2 failed in 46.05s
```

Both failures matched decision #119's documented flaky cluster exactly:
`test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_
warming_up` and `test_intelligence_routes.py::test_daily_levels_carry_
level_interaction_once_touched`. Total (619) matches the decision-log's
own running arithmetic exactly: 598 (#117/#118 baseline) − 3 (#119's
removed tests) + 8 (#120's new tests) + 16 (#121's new tests) = 619.

## Implementation, sanity-checked against real Postgres before the formal suite

Both query builders' compiled SQL were printed and inspected directly —
confirmed real `entry_filled_at AT TIME ZONE :market_timezone` and real
`context_at_entry ->> :session_type` extraction, not a Python-side
equivalent. Then exercised end-to-end against real data (synthetic rows
via the real `record_strategy_outcome()` write path): correct hour-of-day
bucketing across a DST-live September date, breakeven correctly excluded
from win count, the honest `None` group for a missing `session_type` key,
backtest isolation, empty-result handling, and the version-filter guard
all verified manually before the formal pytest suite was written.

## New tests — `backend/tests/test_performance_queries.py`

8 new tests, all synthetic `StrategyOutcome`s written through the real
`record_strategy_outcome()` write path (never a raw SQL insert), all run
against real Postgres:

1. `test_empty_table_returns_empty_list_for_both_queries`
2. `test_win_rate_by_hour_groups_correctly_and_computes_exact_win_rate`
3. `test_expectancy_by_session_type_groups_correctly_and_computes_exact_average`
4. `test_expectancy_by_session_type_groups_missing_key_as_honest_none`
5. `test_backtest_isolation_never_blends_live_and_backtest`
6. `test_strategy_version_isolation_does_not_blend_versions`
7. `test_strategy_name_filter_excludes_other_strategies`
8. `test_strategy_version_without_strategy_name_raises_value_error`

Run in isolation, 3 independent times, freshly cleaned between tests via
an autouse fixture: **16/16 passed every time** (this file's 8 plus
`test_performance_intelligence.py`'s existing 8, confirming the untouched
write-path suite still passes cleanly alongside the new one).

```
16 passed in 0.86s
16 passed in 0.90s
16 passed in 0.99s
```

No test for a NULL `realized_r` population: checked directly before
writing this suite — `StrategyOutcomeRecord.realized_r` is
`Numeric(10, 4), nullable=False` (ORM) and `StrategyOutcome.realized_r`
is `float` with no default and no `| None` (Pydantic) — there is no code
path that can produce a NULL-`realized_r` row via the real write path, so
there's no NULL policy to prove.

## After this change

Full suite re-run twice, each time against a **freshly dropped and
recreated** database (this project's own established practice for
avoiding order-dependent failures, per decision #119):

```
Run 1: 623 passed, 4 failed in 45.42s
Run 2: 624 passed, 3 failed in 45.33s
```

Both runs' totals are 627 — exactly 619 (baseline) + 8 (this delivery's
new tests), confirming zero tests were silently lost or duplicated.

Every failure across both runs is a member of decision #119's documented
four-test flaky cluster, confirmed by name against that entry's full text
(not just `INDEX.md`'s summary row) rather than assumed:

- `test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
- `test_feature_engine.py::test_feature_engine_backfills_from_persisted_history_on_cold_start`
- `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`
- `test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction`

None of these touch `strategy_outcomes`, `performance_queries.py`, or
anything else this delivery changed — #119's own entry already traced
each to Feature Engine cold-start/timing races with "no code path
anywhere near `strategy_engine/`," and this delivery doesn't touch that
code either. Consistent with #119's own description of this cluster
flickering "between 1-3 failing per run depending on execution order" —
run 1 surfaced all 4, run 2 surfaced 3; the baseline run (before this
change, above) happened to surface only 2. **Zero new/unexpected
failures in any run. Zero regressions.**

One incidental note, recorded honestly rather than glossed over: mid-session,
this sandbox's local Postgres process stopped on its own between two
"before/after" run batches (surfaced as a connection-refused error on the
next test invocation) and was restarted with `service postgresql start` —
the database and schema were intact afterward (same data directory, no
data loss). This is a sandbox-process artifact, not a test or migration
failure — the same category decision #116 already recorded once before
("a Postgres-availability artifact of that session's sandbox, not a
regression").

## Area actually changed, verified in isolation

`app/trading_intelligence/performance_queries.py` and `backend/tests/
test_performance_queries.py` together, run 3 independent times against a
freshly cleaned table each time: **100% stable, zero flakiness observed.**
