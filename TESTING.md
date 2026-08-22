# TESTING — Relative Volume (RVOL)

Incremental on top of everything already in your repo through decision
#70. Unzip directly onto the repo root.

## What's in this zip

**New file:**
- `backend/app/feature_engine/indicators/rvol.py` — RVOL pure math.

**Updated files:**
- `backend/app/feature_engine/engine.py` — new `_update_rvol`; **`_update_vwap`'s
  return type changed from `float | None` to `dict[str, float]`** so it can
  also publish `session_volume` (previously tracked internally, never
  exposed) — RVOL reads that plus the shared daily-candle cache ATR/Daily
  Levels already populate. Zero new provider calls, zero new accumulator.
- `backend/app/core/config.py` — new `feature_engine_rvol_lookback_days: int = 5`.
- `backend/app/feature_engine/indicators/__init__.py` — exports `rvol`.
- `backend/tests/test_feature_engine.py` — 9 new tests, plus **one
  existing test updated** (`test_vwap_publishes_even_while_sma_is_still_warming_up`
  had an exact-equality assertion on the published features dict that
  needed to include the new `session_volume` key — not a design change,
  just an assertion that needed updating alongside the refactor).

## New feature key

`rvol` — session_volume so far ÷ (avg of last 5 complete trading days'
volume × elapsed fraction of the regular session). >1.0 means busier than
normal for this time of day; <1.0 means quieter. Only present during
regular session, and only once 5 complete prior daily volumes are cached.

`session_volume` also now appears alongside `vwap` whenever `vwap` does —
today's regular-session cumulative volume, previously computed internally
but never published on its own.

## How to verify

```bash
cd backend
pip install -r requirements.txt --break-system-packages   # no new dependency
alembic upgrade head                                       # no new migration
pytest -q
```

Expect **216–218 passed** out of 219 on a freshly-migrated DB (exact count
varies run to run) — the handful of failures are two ALREADY-DOCUMENTED
pre-existing flaky classes (decisions #68, #69/#70), not this delivery's
doing: a wall-clock-timing class in `test_feature_engine.py`, and an
order-dependent class in `test_intelligence_routes.py` (a different
specific test fails each run — this time it also included a
`ZeroDivisionError` variant of the same underlying issue, noted in
decision #71). Both pass cleanly every time in isolation.

To isolate just the new work:

```bash
pytest -q -k "rvol" tests/test_feature_engine.py -v
```

Expect **9 passed**.

## Worth knowing before you look at the code

`_update_vwap` is now the internal mechanism behind THREE published
features (`vwap`, `session_volume`, and — indirectly — `rvol`), not one.
If you ever touch its accumulator logic, all three are affected.
