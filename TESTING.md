# TESTING — decision #140 (D18 backtest symbol namespace)

All database validation used real local PostgreSQL.

## Full-suite comparison

- Pre-change baseline on migration `0008`: `728 passed, 0 failed` in 433.83s.
- Final tree on migration `0009`: `737 passed, 0 failed` in 552.00s.
- The nine-test increase is exactly `test_symbol_namespace.py`'s one same-ticker live/backtest regression for each production lookup file. The live path kept the baseline's zero-failure signature.

## Focused verification

- Ten affected-area test modules, including all nine namespace regressions: `96 passed`.
- End-to-end D18 regression: `1 passed`. It seeded a live Daily Levels sentinel, ran the identical backtest twice with the same ticker, and proved:
  - live and backtest symbol identities coexist;
  - the live sentinel is unchanged;
  - both replay runs retain non-zero `volume_regime_score` and `volatility_regime_score`.
- Python compilation and `git diff --check -- backend` passed.

## Migration round-trip

Test fixture at `0009` contained live and backtest `symbols`/`daily_levels_state` rows for the same ticker.

1. `alembic downgrade 0008` succeeded; the backtest rows were removed, one live symbol and one live Daily Levels row remained, and both `is_backtest` columns were absent.
2. `alembic upgrade head` succeeded; revision returned to `0009`, both surviving rows were backfilled to `is_backtest=false`, and row counts remained one symbol/one level.

## Footprint

A fresh GitHub `main` overlay with the D18 package produced exactly 19 changed files, all under `backend/`. Documentation in this follow-up is limited to `docs/architecture/strategy-engine-design.md`, `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, `TESTING.md`, and `CHANGES.md`.
