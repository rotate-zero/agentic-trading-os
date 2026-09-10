# Testing — `strategy-outcomes`/`opportunity-conflicts` observability routes (decision #123)

This file is a fresh delete-first replacement, scoped to this delivery only.
It supersedes whatever `TESTING.md` existed before this change (decision
#122's own write-up); look at `docs/decisions/` for the history of prior
deliveries' own validation.

## A mid-session decision-number collision, found and handled

This session started against a clone whose latest decision was #121. Partway
through, a routine re-diff against a freshly-pulled tarball (done before
writing anything to the decision log, per this project's own protocol)
showed `backend/app/trading_intelligence/performance_queries.py` and
`backend/tests/test_performance_queries.py` now existed on `main` — the
sibling parallel track's own delivery had landed mid-session and claimed
decision **#122**. This session's own work was file-disjoint from that
delivery throughout (confirmed below), so nothing needed to change except
the decision number: this delivery re-pulled `main` live, rebased its own
changes onto the current tip, re-checked the decision-log tail a second
time immediately before writing anything, and is recorded as **decision
#123** — same renumbering pattern as #98/#99, #111/#112, #114/#115, #120/#121.

## What this delivery touched

- `backend/app/api/routes/intelligence.py` — two new routes appended,
  additive only
- **New:** `backend/tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py`
- `frontend/src/services/api-client.ts` — two new fetch wrappers + wire types
- **New:** `frontend/src/hooks/useStrategyOutcomes.ts`
- **New:** `frontend/src/hooks/useOpportunityConflicts.ts`
- `frontend/src/components/ai-panel/AIAnalysisPanel.tsx` — one new small
  section (`ConflictStatus`)
- `frontend/src/components/workspace/InfoTab.tsx` — one new small section
  (`RecentClosedTrades`, in `GeneralContent`) + `ConnectorContent` now also
  calls `useOpportunityConflicts`
- `docs/decisions/confirmed-decisions.md` (decision #123 appended)
- `docs/decisions/INDEX.md` (matching row appended)
- `docs/architecture/strategy-engine-design.md` (one new §12 staged-plan
  bullet, closing the "route intentionally skipped" note both #120 and #121
  carried — no other section changed)
- This file

Note: `frontend/tsconfig.tsbuildinfo` was also regenerated locally in this
sandbox by running `npx tsc -b` below (an incremental-build cache file,
tracked in git, not hand-edited) — deliberately **not** included in the
delivered zip, since it embeds this sandbox's own absolute paths and will
regenerate correctly the moment `npx tsc -b` is run in the real environment.

Confirmed by `diff -rq` against a freshly-pulled, untouched second clone
immediately before writing this file: no other file differs (modulo
`__pycache__`/`.pytest_cache`/`node_modules`/`dist`/`.env`, none of which are
part of the repo). In particular, `app/trading_intelligence/performance.py`,
`app/trading_intelligence/performance_queries.py`,
`backend/tests/test_performance_intelligence.py`,
`backend/tests/test_performance_queries.py`,
`app/models/trading_intelligence.py`, `app/schemas/performance.py`,
`app/trading_intelligence/opportunity_view.py`, `frontend/src/hooks/
useOpportunities.ts`, and the five other existing routes in
`intelligence.py` (`/state`, `/market-state`, `/context`, `/series`,
`/opportunities`) are all untouched — confirmed, not assumed.

## Environment

Real local PostgreSQL 16, matching the project's actual `postgres:16` image
(`docker-compose.yml`) — provisioned directly in this sandbox via
`apt-get install postgresql postgresql-contrib` (Docker itself isn't
available in this sandbox; this is a native local server, not a container,
but genuinely real Postgres, not a mock or SQLite substitute). Role/database
created to match `app/core/config.py`'s actual defaults exactly
(`trading`/`trading`@`localhost:5432`/`trading_workspace`), so no
environment-variable overrides were needed. Schema built with
`alembic upgrade head` (all 8 migrations, through
`0008_strategy_outcomes_and_backtests.py` — no new migration needed for this
delivery). Backend Python dependencies installed from
`backend/requirements.txt`. Frontend dependencies installed via `npm install`
(Node 22.22.2, npm 10.9.7).

## Before this change (baseline)

Full suite run against a freshly migrated database, on an untouched clone
already carrying decision #122 (the sibling track's own delivery):

```
627 collected, 624 passed, 3 failed in 46.05s
```

All 3 failures matched decision #119's documented four-test flaky cluster
exactly (that cluster flickers between 1-4 failures per run, per #119's own
description — this run happened to surface 3 of the 4):

- `test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
- `test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction`
- `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`

Total (627) matches the decision log's own running arithmetic: 619 (post-#121
baseline, per #122's own entry) + 8 (#122's new tests) = 627.

## New tests — `backend/tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py`

6 new tests, real Postgres throughout:

1. `test_strategy_outcomes_table_actually_empty_returns_honest_empty_collection`
   — wipes the whole table (safe: nothing else in this codebase writes to
   it), asserts `{"outcomes": []}` exactly, 200
2. `test_strategy_outcomes_populated_case_round_trips_actual_fields` — one
   `StrategyOutcomeRecord` inserted directly via the ORM (not via
   `record_strategy_outcome()`, which belongs to #120/#122's own footprint),
   asserts 18 real returned field values, not just status/length
3. `test_strategy_outcomes_orders_by_exit_filled_at_descending`
4. `test_strategy_outcomes_limit_caps_returned_rows`
5. `test_opportunity_conflicts_route_reflects_real_cache_agreement` — real
   `EventBus`/`OpportunityCache` via `app.router.lifespan_context(app)` +
   httpx `ASGITransport`, real `OpportunityCreated` envelopes published,
   asserting the route's actual `agreements` shape
6. `test_opportunity_conflicts_route_symbol_filter_forwards_correctly` — a
   real conflict on one symbol, a real agreement on a second, confirming
   `symbol` filtering doesn't leak either direction

Found and fixed one real bug of its own while writing this suite:
`StrategyOutcomeRecord.outcome_id` can't be read off an ORM object after its
`SessionLocal()` session has closed (`sqlalchemy.orm.exc.
DetachedInstanceError`) — fixed by generating and comparing against `uuid.
uuid4()` values captured in local variables before insert, never read back
off a detached instance.

Run 3 independent times in isolation, freshly migrated DB:

```
6 passed in 1.29s
6 passed in 1.25s
6 passed in 1.23s
```

100% stable, zero flakiness observed.

## After this change

Full suite re-run twice, each time against a **freshly dropped and
recreated** database (this project's own established practice for avoiding
order-dependent failures, per decision #119):

```
Run 1: 633 collected, 631 passed, 2 failed in 46.49s
Run 2: 633 collected, 630 passed, 3 failed in 45.41s
```

Both totals are 633 — exactly 627 (baseline) + 6 (this delivery's new
tests), confirming zero tests were silently lost or duplicated. Every
failure across both runs is a member of decision #119's documented four-test
flaky cluster (run 1 surfaced 2 of the 4; run 2 surfaced 3 — the same
"1-3 failing per run depending on execution order" pattern #119's own entry
describes). None of the four touch `strategy_outcomes`,
`opportunity_conflicts`, or `intelligence.py` at all — #119's own entry
already traced each to Feature Engine cold-start/timing races with "no code
path anywhere near `strategy_engine/`." **Zero new/unexpected failures in
either run. Zero regressions.**

## Frontend validation

```
npx tsc -b
```

4 errors, all in `src/components/workspace/GridPresetPicker.tsx` — confirmed
byte-for-byte identical (same 4 errors, same lines) against a freshly-pulled
untouched clone before treating them as pre-existing (decision #35). Zero
new TypeScript errors anywhere this delivery touched.

```
npx vite build
```

```
✓ 84 modules transformed.
✓ built in 4.21s
```

Clean build, no warnings beyond Vite's own standard output.

No new frontend test file — confirmed by search that no hook in this
codebase has a companion test file today (`useOpportunities.ts` included),
so this delivery doesn't introduce a new testing pattern solely for itself.

## Area actually changed, verified in isolation

`app/api/routes/intelligence.py`'s two new routes and
`test_strategy_outcomes_and_opportunity_conflicts_routes.py` together, run 3
independent times against a freshly migrated DB each time: **100% stable,
zero flakiness observed** (see above).
