# `regular_open` test-staleness fix (decision #129) — test-only

Copy this into your repo root, overwriting the existing path. Test-only
delivery: **zero changes to any production file** — confirmed directly
(`diff -rq` of `backend/app/` against a fresh pull of current `main`,
clean). One file changed: `backend/tests/test_feature_engine.py`
(one test's assertion widened by one key).

## What this closes

Decision #128's Backtest Runner v1 close-out traced this run's second
failing test to a specific, previously-unlogged cause rather than
folding it into the documented #119 flaky cluster, and flagged it
precisely without fixing it (out of scope for that documentation-only
entry). This delivery closes exactly that gap.

## The bug

`test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
publishes its fixture candle at exactly regular-session open. Since
decision #111, `_update_gap` publishes a real `regular_open` key
whenever today's regular session has opened — independent of whether
`gap_pct`/`gap_dollars` are computable yet. #111's own text says it
updated "three existing gap tests"; this test isn't one of them (it
lives in the VWAP-warmup group), so its hardcoded exact-equality dict
was never widened and has been silently stale ever since, failing
deterministically rather than intermittently.

## The fix

One key added to one assertion:

```python
assert features == {
    "vwap": 100.0,
    "session_volume": 10.0,
    "vwap_ext": 100.0,
    "session_volume_ext": 10.0,
    "regular_open": 100.0,
}
```

## Confirmed per the acceptance criteria

- Target test: 3/3 isolated reruns failed identically before the fix
  (same missing-key diff every time — genuinely deterministic, not
  timing-sensitive), 3/3 passed after.
- Full suite, real local Postgres 16: 702 collected / 700 passed / 2
  failed before (matches decision #128's own reported baseline exactly)
  → 702 collected / 701 passed / 1 failed after, run twice.
- The one remaining failure (`test_daily_levels_carry_level_interaction_once_touched`)
  separately re-confirmed genuinely intermittent — 1 fail / 4 pass across
  isolated reruns, consistent with its documented #119-cluster signature
  — and deliberately left untouched.
- `diff -rq` against a freshly-pulled untouched clone: only
  `backend/tests/test_feature_engine.py` differs in `backend/`.

## Not done, on purpose, per this fix's own scope boundary

No audit of other test files for similar staleness against #111's
`regular_open` key beyond the one instance this task traced. No
production code touched — `regular_open`'s publish behavior is exactly
as decision #111 designed it; only the stale assertion was wrong.
