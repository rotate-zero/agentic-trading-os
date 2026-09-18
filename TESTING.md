# TESTING — pending delivery `world-view-v1`

This is a replacement repo-root `TESTING.md`: **delete the old `TESTING.md` first, then apply this delivery**.

## Base and database setup

- Exact untouched-main base: `0913746a1c2c5dae3ecb72844ea2ab1c5bab8d19`.
- Fresh source tarball extracted with no enclosing directory at `/tmp/world-view-v1-main.QuishM`.
- PostgreSQL 18.6 isolated cluster: `/tmp/world-view-v1-pg.amvOql/data`.
- Connection: `127.0.0.1:55434`, database/user `trading_workspace`/`trading`, server timezone UTC.
- Both full-suite runs began from a freshly created database migrated through Alembic `0010` with `alembic upgrade head`.
- Python 3.14 project environment: `/home/rotate_zero/projects/agentic-trading-os/backend/.venv`.

Environment prefix used for all pytest commands:

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55434 \
  POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading
```

## Commands and exact results

### Untouched-main baseline

```bash
cd backend
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55434 \
  POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  /home/rotate_zero/projects/agentic-trading-os/backend/.venv/bin/pytest -q --tb=short
```

Result: **772 passed, 0 failed, 0 skipped; 75,497 warnings; 790.87s (13:10)**.

### New World View tests

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55434 \
  POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  /home/rotate_zero/projects/agentic-trading-os/backend/.venv/bin/pytest \
  tests/test_world_view.py -q --tb=short
```

Result: **4 passed, 0 failed, 0 skipped; 221 warnings; 1.14s**.

The four collected cases prove:

- real Market State production through `FeaturesUpdated` and a bounded condition wait;
- real Context production through `evaluate_all()` / `evaluate_for_symbol()`;
- live and backtest outcomes written only through `record_strategy_outcome()` and never raw INSERT;
- strict population separation and honest empty lists when either population is absent;
- unmodified source envelopes for computed and never-computed symbols;
- `portfolio` serialization as JSON `null`;
- route behavior with and without `symbol`;
- no World View persistence, using exact before/after counts for every public PostgreSQL table after source-owned work is drained.

### New plus affected coverage

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55434 \
  POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  /home/rotate_zero/projects/agentic-trading-os/backend/.venv/bin/pytest \
  tests/test_world_view.py \
  tests/test_intelligence_routes.py \
  tests/test_market_state_engine.py \
  tests/test_market_state_engine_timeframe_race.py \
  tests/test_strategy_integration_contract.py \
  tests/test_context_engine.py \
  tests/test_performance_intelligence.py \
  tests/test_performance_queries.py \
  tests/test_performance_analytics_routes.py \
  tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py \
  -q --tb=short
```

Result: **84 passed, 0 failed, 0 skipped; 4,412 warnings; 8.14s**.

### Changed-tree full suite

The isolated database was dropped, recreated, and migrated through `0010` before this run, matching baseline setup.

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55434 \
  POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  /home/rotate_zero/projects/agentic-trading-os/backend/.venv/bin/pytest -q --tb=short
```

Result: **775 passed, 1 failed, 0 skipped; 76,449 warnings; 790.60s (13:10)**.

Failure:

```text
tests/test_feature_engine.py::test_stop_waits_for_an_in_flight_compute_before_returning
AssertionError: assert 3 == 1
```

The test passed immediately when rerun alone:

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55434 \
  POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  /home/rotate_zero/projects/agentic-trading-os/backend/.venv/bin/pytest \
  tests/test_feature_engine.py::test_stop_waits_for_an_in_flight_compute_before_returning \
  -q --tb=short
```

Isolation result: **1 passed, 0 failed, 0 skipped; 2,934 warnings; 1.16s**.

This is unrelated to World View: the failing test runs before `test_world_view.py`, and this delivery changes neither Feature Engine nor its tests. Its wall-clock `candle_ts` can land on an aggregated-timeframe boundary, producing more valid `FeaturesUpdated` events than its `len(received) == 1` assertion permits. It is reported as a related follow-up and was not changed.

## Warning profile

Warnings are existing Python 3.14 deprecations from FastAPI and pytest-asyncio (`asyncio.iscoroutinefunction`, event-loop policy APIs). No warning was hidden or filtered by this delivery.

## Environment limitations

- No frontend command was run because the delivery changes no frontend file.
- Portfolio State cannot be integration-tested because direct code search confirms it has no application implementation; the approved v1 contract tests the reserved `null` slot instead.

## Delivery footprint

- `backend/app/world_view/__init__.py`
- `backend/app/world_view/composite.py`
- `backend/app/api/routes/intelligence.py`
- `backend/tests/test_world_view.py`
- `docs/architecture/trading-intelligence-architecture.md`
- `docs/architecture/system-design.md`
- `docs/decisions/confirmed-decisions.md`
- `CHANGES.md`
- `TESTING.md` (replacement)

No `INDEX.md` row or real decision number is included. Frozen archives are untouched.

The open decision file is over 136KB after appending `world-view-v1`, above the documented rollover threshold. It still contains eight unnumbered pending entries, so rollover remains a merge-time integration follow-up rather than part of this delivery.
