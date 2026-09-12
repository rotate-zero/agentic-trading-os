# TESTING.md — `regular_open` test-staleness fix (decision #129)

## What this documents

A single, targeted test fix: `backend/tests/test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
was widened to assert the `regular_open` key that decision #111's
`_update_gap` has genuinely been publishing in this test's payload since
#111 landed. Full reasoning: decision #129, `docs/decisions/confirmed-decisions.md`.

This closes the exact gap decision #128's own close-out identified and
deliberately left unfixed (that entry's scope was documentation-only for
Backtest Runner v1; fixing this meant editing `test_feature_engine.py`,
outside its footprint).

### Files touched by this fix

- `backend/tests/test_feature_engine.py` — one test's exact-equality
  assertion widened by one key, plus an explanatory comment.
- `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md` —
  new decision #129 entry + index row.
- This file.

**Zero other `backend/app/` or `backend/tests/` changes.** Confirmed by
`diff -rq` against a freshly re-pulled untouched clone — see "Fresh-clone
diff verification" below.

## The bug, precisely

`_update_gap` (`backend/app/feature_engine/engine.py`) publishes
`regular_open` as its own `features` key whenever
`state["regular_open"] is not None` — true the moment today's regular
session has opened, deliberately independent of whether `gap_pct`/
`gap_dollars` are also computable yet (decision #111). The fixture candle
in `test_vwap_publishes_even_while_sma_is_still_warming_up` is published
at `_et(2026, 8, 11, 9, 30)` — exactly regular-session open — so
`regular_open: 100.0` has been a real, present key in this test's actual
`FEATURES_UPDATED` payload ever since #111 landed. Decision #111's own
text names only "three existing gap tests" as updated for the new key;
this test lives in the VWAP-warmup group, not the gap group, so its
hardcoded exact-equality dict was never touched and silently fell out of
sync with the real payload.

This is the same "hardcoded dict needs widening every time a new key
joins the set" pattern this test's own comment already documents
happening twice before (`session_volume`, then `vwap_ext`/
`session_volume_ext`) — just never caught for `regular_open` specifically
until decision #128's own targeted trace during Backtest Runner
close-out verification.

## The fix

```python
assert features == {
    "vwap": 100.0,
    "session_volume": 10.0,
    "vwap_ext": 100.0,
    "session_volume_ext": 10.0,
    "regular_open": 100.0,
}
```

with an inline comment naming both decision #111 (why the key exists)
and decision #129 (why it's now asserted). No change to `_update_gap`,
`indicators/gap.py`, or any other test in this file.

## Why this one is a real fix, not another #119-cluster entry

Confirmed genuinely deterministic for this specific cause — 3/3 isolated
reruns failed identically (same missing-`regular_open` diff) before the
fix, and 3/3 passed clean after. Separately,
`test_daily_levels_carry_level_interaction_once_touched` (the #119
cluster's other currently-failing member, left completely untouched by
this change) was re-run in isolation 5 times during this task's
verification: failed once, passed clean the other four — consistent with
its long-documented order/timing-sensitive signature (decisions #113
onward). The two were not conflated; only the genuinely deterministic one
was touched.

## Verification

Real local Postgres 16 (provisioned directly in this session —
`apt-get install postgresql`, `service postgresql start`, `trading`/
`trading`/`trading_workspace`, `alembic upgrade head`).

**Target test, before the fix** (freshly-pulled untouched clone), 3
isolated reruns:

```
cd backend
python -m pytest tests/test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up -v
```

All 3 failed identically:
`AssertionError: ... Left contains 1 more item: {'regular_open': 100.0}`.

**Full suite, before the fix** (same untouched clone):

```
cd backend
python -m pytest -q
```

**702 collected, 700 passed, 2 failed** — matching decision #128's own
reported baseline exactly:
`test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
and `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`.

**Target test, after the fix**, 3 isolated reruns: all 3 passed.

**Full suite, after the fix**, run twice against the same live database:

**702 collected, 701 passed, 1 failed** both times —
`test_daily_levels_carry_level_interaction_once_touched` only,
separately re-confirmed intermittent above (not a regression from this
change; same pre-existing #119-cluster member, unaffected by this fix's
footprint).

No frontend files touched by this change — `npx tsc -b`/`npx vite build`
not re-run for this delivery.

## Fresh-clone diff verification

`diff -rq` against a freshly-pulled untouched clone of current `main`,
taken immediately before writing decision #129, confirms this fix's own
change set is exactly the "Files touched" list above —
`backend/app/` and every other test file in `backend/tests/` untouched.

## Known limitations / deferred, not done here

- The `regular_open` field remains published as designed (decision
  #111) — this fix only updates a stale test assertion to match real,
  intended behavior; no production code path changed.
- No other test files were audited for similar staleness against #111's
  `regular_open` key beyond the one this task traced and fixed — out of
  scope for this targeted fix.
