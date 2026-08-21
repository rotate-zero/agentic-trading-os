# TESTING — Feature Engine indicator expansion, Stages 1 + 2 (combined)

Stage 1's own zip failed to download, so this is a single combined
delivery covering both stages — everything needed is in this one archive,
nothing depends on the earlier zip. Unzip directly onto the repo root
(paths mirror the repo layout).

## What's in this zip

**New files:**
- `backend/app/feature_engine/indicators/session_change.py` — Stage 1,
  pure math for Session % / $ Change.
- `backend/app/feature_engine/indicators/gap.py` — Stage 1, pure math for
  Gap % / $.
- `backend/app/feature_engine/indicators/atr.py` — Stage 2, Wilder ATR
  pure math.

**Updated files (each already contains BOTH stages' changes, not just
one):**
- `backend/app/feature_engine/engine.py` — `open` now read from the
  candle payload; `_update_gap` (Stage 1); the daily-candle cache
  extracted out of `self._daily_levels_state[symbol]["candles"]` into its
  own shared `self._daily_candle_cache[symbol]`, and `_update_atr` (Stage
  2) reading from it — the same fetch Daily Levels itself uses, zero
  second provider call.
- `backend/app/feature_engine/indicators/__init__.py` — exports `gap`,
  `session_change`, and `atr`.
- `backend/app/core/config.py` — new `feature_engine_atr_period: int = 14`.
- `backend/tests/test_feature_engine.py` — 15 new tests total (9 from
  Stage 1, 6 from Stage 2).
- `docs/architecture/feature-engine-indicator-expansion.md` — Stage 0
  through Stage 2 all marked complete; D1/D2/D3 resolved AND implemented.
- `docs/architecture/system-design.md` — §4.5 reflects Session % Change,
  Gap, and ATR all now actually computed.
- `docs/decisions/confirmed-decisions.md` — decisions **#67, #68, #69**.

## How to verify

```bash
cd backend
pip install -r requirements.txt --break-system-packages   # no new dependency, harmless to re-run
alembic upgrade head                                       # no new migration — no-op if already at 0003
pytest -q
```

Expect **196 passed** on a clean DB — verified directly for this exact
combined file set (not inferred from the two separate stage deliveries):
unzipped this exact zip onto a completely fresh checkout, reset Postgres
to a freshly-migrated empty state, and ran the full suite before sending
this. Same result both times.

Two KNOWN, pre-existing, intermittent failure classes may show up
depending on timing — neither caused by this work, both confirmed
pre-existing by reproducing them against a completely unmodified
checkout:

1. Three tests in `test_feature_engine.py` that seed timestamps with
   `datetime.now()` instead of the file's own `_et()` helper (decision #68).
2. A `symbols` FK-violation in `test_intelligence_routes.py` — a
   different specific test fails each run, same error shape, passes in
   isolation every time (decision #69).

Neither is in scope for this delivery to fix. To isolate just the new work:

```bash
pytest -q -k "session_change or gap or atr" tests/test_feature_engine.py -v
```

Expect **15 passed**.

## Next stage

Stage 3 (Linear Regression) is next — introduces a new per-indicator
`(timeframe, period)` config shape (`feature_engine_regression_configs`)
and extends `_window_capacity`. Not started.
