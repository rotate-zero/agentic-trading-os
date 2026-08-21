# TESTING — Feature Engine indicator expansion, Stage 3 + 4 (Regression + KAMA)

Since git is up to date through Stage 2 (decisions #67–#69), this delivery
is incremental on top of that — it does NOT re-include `session_change.py`,
`gap.py`, or `atr.py` (unchanged, already in your repo). Unzip directly
onto the repo root.

## What's in this zip

**New files:**
- `backend/app/feature_engine/indicators/regression.py` — OLS linear
  regression, pure math.
- `backend/app/feature_engine/indicators/kama.py` — Kaufman Adaptive
  Moving Average + Efficiency Ratio, pure math.

**Updated files:**
- `backend/app/feature_engine/engine.py` — new `(timeframe, period[, ...])`
  config parsing/validation in `__init__`; `_window_capacity` now accounts
  for all four indicator families, not just SMA/EMA; `_apply_close` gained
  its first per-`(indicator, timeframe)` applicability check, since
  Regression/KAMA (unlike SMA/EMA) are only configured for specific
  timeframes.
- `backend/app/core/config.py` — new `feature_engine_regression_configs`,
  `feature_engine_kama_configs`, `feature_engine_kama_seed_multiplier`.
- `backend/app/feature_engine/indicators/__init__.py` — exports
  `regression` and `kama`.
- `backend/tests/test_feature_engine.py` — 14 new tests (8 pure-math, 2
  config-validation, 4 engine-level).
- `docs/architecture/feature-engine-indicator-expansion.md` — all five
  stages now marked complete; this design doc is effectively closed out.
- `docs/architecture/system-design.md` — §4.5 fully updated.
- `docs/decisions/confirmed-decisions.md` — new entry **#70**.

## How to verify

```bash
cd backend
pip install -r requirements.txt --break-system-packages   # no new dependency, harmless to re-run
alembic upgrade head                                       # no new migration — no-op if already at 0003
pytest -q
```

Expect **207 passed, 3 failed** on a freshly-migrated DB — verified
directly for this exact combined file set before sending, same as every
prior delivery in this thread. The 3 failures are the SAME pre-existing,
wall-clock-dependent flakiness documented in decision #68 (not this
delivery's doing) — a 4th test in that same class
(`test_feature_engine_backfills_from_persisted_history_on_cold_start`)
surfaced once during my own verification runs today but passes cleanly in
isolation every time; not re-investigated further, decision #70 has the
note.

To isolate just the new work:

```bash
pytest -q -k "regression or kama" tests/test_feature_engine.py -v
```

Expect **14 passed**.

## Config shape, if you want to tune periods later

```python
feature_engine_regression_configs: list[dict] = [
    {"timeframe": "1m", "period": 9},
    {"timeframe": "5m", "period": 9},
]
feature_engine_kama_configs: list[dict] = [
    {"timeframe": "1m", "er_period": 9, "fast_period": 2, "slow_period": 30},
    {"timeframe": "5m", "er_period": 9, "fast_period": 2, "slow_period": 30},
]
```

Adding e.g. `{"timeframe": "1m", "period": 21}` to the regression list is a
config change, not a code change — `FeatureEngine.__init__` validates each
entry (`period >= 2`, non-empty `timeframe` for regression;
`er_period`/`fast_period`/`slow_period` all positive for KAMA) and raises
`ValueError` immediately on anything malformed, rather than failing
silently later.

## What this closes out

All five feature families from your original design brief — ATR, Session
% / $ Change, Gap % / $, Linear Regression, KAMA — are now built, tested,
and documented. `feature-engine-indicator-expansion.md` is effectively
done; anything further (slope-change/acceleration, chart/frontend
exposure for any of these) is a fresh, small design conversation per that
doc's own §8, not a continuation of an open thread.
