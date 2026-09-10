# TESTING.md — Performance Intelligence persistence layer (decision #120)

Replaces the previous `TESTING.md` (delete-first, per this project's own convention).

## What this delivery is

Builds the persistence layer for Performance Intelligence, locked by decision #89 / `docs/architecture/strategy-engine-design.md` §5 (`StrategyOutcome`) and §7 (`BacktestRun`): a new Alembic migration, ORM models, Pydantic schemas, and a write-time-invariant-enforcing write path. No live caller is wired — Execution Engine/Position Monitor don't exist yet.

## Files changed

New:
- `backend/alembic/versions/0008_strategy_outcomes_and_backtests.py` — creates `backtests` then `strategy_outcomes` (real FK from the latter to the former)
- `backend/app/schemas/performance.py` — `StrategyOutcome`, `BacktestRun` Pydantic contracts
- `backend/app/trading_intelligence/performance.py` — `record_strategy_outcome()`, the write path
- `backend/tests/test_performance_intelligence.py` — 8 new tests

Modified:
- `backend/app/models/trading_intelligence.py` — adds `StrategyOutcomeRecord`/`BacktestRunRecord` ORM classes alongside the existing `LevelInteractionState`/`LevelInteractionEvent`, extends the module docstring
- `backend/app/db/base.py` — one comment line extended (no new import needed; `trading_intelligence` was already imported)
- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md` — new decision #120
- `docs/architecture/strategy-engine-design.md` — §5's stale "no table" line corrected, new §12 completed-item bullet, new open item D17 in §10

Untouched, confirmed by `diff -rq` against a fresh untouched clone: `app/trading_intelligence/opportunity_cache.py`, `app/strategy_engine/scheduler.py`, `app/strategy_engine/gate_conditions.py`, every `strategy_engine/*_strategy.py`, and `app/api/routes/intelligence.py` (the sibling parallel track's own file-disjoint footprint).

## What was NOT built (explicitly out of scope, per the task)

- Any real Execution Engine / Position Monitor fill handler that calls `record_strategy_outcome()` for real
- Backtest Runner logic (§7: "not built now")
- Any query/aggregation logic (rank, expectancy-by-regime, etc.) — §5: "every one of these is a GROUP BY... computed on demand"
- The optional `GET /intelligence/strategy-outcomes` observability route — intentionally skipped to avoid any collision risk with the sibling track's concurrent edits to `intelligence.py`
- `feature_snapshots` / `opportunities` tables — referenced by UUID only, no FK, since neither exists yet anywhere in this codebase

## New conventions established (first use anywhere in this codebase — confirmed absent by grep before writing)

1. **JSONB for dict-shaped fields** (`evidence`, `market_state_at_entry`/`_at_exit`, `context_at_entry`/`_at_exit`). No existing table had a dict-typed column — `level_interaction_events`, the file originally pointed to as prior art, turned out to have none.
2. **Native PostgreSQL UUID primary/foreign keys** (`outcome_id`, `run_id`, and every UUID-shaped reference column). Every other table in this codebase uses an `Integer`/`BigInteger` `Identity()` autoincrement PK. `gen_random_uuid()` used as the server-side default — a PostgreSQL 16 builtin, no `pgcrypto` extension required.
3. `symbol_universe` uses `postgresql.ARRAY(String)`, not JSONB — a homogeneous list of tickers, kept out of the JSONB convention deliberately (JSONB reserved for genuinely dict-shaped fields).

## A real, unresolved cross-contract gap — recorded, not patched (new open item D17)

§5 locks `market_state_at_entry`/`market_state_at_exit`/`context_at_entry`/`context_at_exit` as REQUIRED dict fields. `state_snapshot.py`'s (#98) own capture functions can honestly return `None` for a cold-start symbol. This delivery does **not** weaken §5 to paper over that — all four fields stay required, exactly as locked. The gap is tracked as **D17** in `strategy-engine-design.md` §10, to be resolved only when a real Execution Engine/Position Monitor caller actually needs to construct a `StrategyOutcome` from live capture data.

## The `entry_qty == exit_qty` invariant

`record_strategy_outcome()` checks this **before** `SessionLocal()` is even opened, and raises `ValueError` — not caught by anything downstream. This deliberately does **not** copy `MarketStateEngine._persist`'s existing soft-fail (catch/log/rollback) precedent for its own write-time assertion: that pattern is correct for an unattended background worker, wrong here, since this function has no live caller yet to protect from crashing and a future real caller needs the failure to be loud. Genuine DB-layer errors (e.g. an FK violation) are rolled back then re-raised, never swallowed.

## How to verify

```bash
cd backend
pip install -r requirements.txt  # psycopg2-binary, sqlalchemy, alembic, pydantic, pytest, etc.
cp .env.example .env             # then point POSTGRES_* at a real local Postgres 16
alembic upgrade head             # applies through 0008 (backtests, strategy_outcomes)
pytest -q                        # full suite
pytest tests/test_performance_intelligence.py -v   # just this delivery's own tests
```

To confirm the migration is reversible:
```bash
alembic downgrade -1   # drops strategy_outcomes and backtests cleanly
alembic upgrade head   # re-creates both
```

## Test results (real local Postgres 16, this session's own freshly provisioned instance)

**Before this change** (untouched clone, migrated through 0007): 595 collected, 593 passed, 2 failed.
**After this change** (migrated through 0008): 603 collected — exactly 595 + 8 new tests — 601 passed, same 2 failed.

**Zero regressions.** The 2 failures are pre-existing and unrelated, confirmed against decision #119's own documented tail rather than assumed similar:
- `tests/test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up` (documented since #114, consistent)
- `tests/test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched` (documented since #114, intermittent)

Decision #119 also documents two additional intermittent, order/timing-sensitive failures unrelated to `strategy_engine/` (`test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction`, `test_feature_engine.py::test_feature_engine_backfills_from_persisted_history_on_cold_start`) — neither surfaced in this session's runs, consistent with their own "intermittent" framing; not a discrepancy.

**This delivery's own 8 tests** (`test_performance_intelligence.py`) were run 3 times independently: 8/8 passed every time, zero flakiness. Covers: valid `StrategyOutcome`/`BacktestRun` construction; missing-required-field rejection; `entry_qty != exit_qty` rejected with zero rows written; a full round-trip with every field re-verified after read-back (including all five JSONB dict fields byte-for-byte, and `backtest_run_id`/`feature_snapshot_id` both exercised as `None`); a real Postgres FK violation (`IntegrityError`) for a nonexistent `backtest_run_id`, with confirmed rollback; and a `strategy_outcomes` row successfully referencing a real, pre-created `backtests` row.

## Manual merge notes for the sibling parallel track

No overlap expected. This delivery's footprint is entirely new files plus two files (`app/models/trading_intelligence.py`, `app/db/base.py`) the sibling track has no stated reason to also touch. If both deliveries land in the same working tree, apply in either order — there is no shared file requiring a manual three-way merge.
