# CHANGES — feature-engine-test-isolation-fix (decision #153)

Base: `main` as pulled at the start of the task, re-pulled immediately before
packaging (three-source re-check: `INDEX.md` tail, `confirmed-decisions.md`
tail, `docs/decisions/archive/` file list — last real number #152, archive
unchanged through `122-133.md`; no concurrent change touched any file below).

## What changed

**Test-only delivery. No production code changed.**

Decision #150 reported a full-suite-only failure of
`test_feature_engine.py::test_stop_waits_for_an_in_flight_compute_before_returning`
(received 3 `FeaturesUpdated`, expected 1; passes in isolation). The task
framed it as cross-test contamination. **It isn't.** It is a wall-clock-
dependent test, and it fails in isolation too — at the right minute of the day.

- `backend/tests/test_feature_engine.py` — two timestamp sites moved from
  `datetime.now(utc) - N minutes` to the file's existing fixed Saturday anchor
  (`_et(2026, 8, 15, 12, 0)`, unconditionally `Session.CLOSED`), each with a short
  comment saying why the non-session timestamp is intentional:
  - `test_stop_waits_for_an_in_flight_compute_before_returning` (the named test)
  - `test_feature_engine_backfills_from_persisted_history_on_cold_start` (identical
    defect, same file/root cause/assertion pattern; inclusion approved by Saqib)
- `docs/decisions/confirmed-decisions.md` — new entry #153.
- `docs/decisions/INDEX.md` — one new row (#153).
- `CHANGES.md`, `TESTING.md` — replaced (delete-first).

No new components, modules, routes or fixtures were added, so there is no new
functionality to describe. Assertions, event filters, timeouts, `FeatureEngine`
(including decision #149's `stop()` fix), `CandleRecorder`, `EventBus`,
`conftest.py`, `backend/app/world_view/` and every frontend file are untouched.

## Why (mechanism)

```
CandleClosed(1m, candle_ts)  ->  FeatureEngine._compute_one
   |
   |-- always: publish FeaturesUpdated(1m)                              (1 event)
   |
   `-- if MarketClock.session_bounds(candle_ts) is not None            (trading weekday,
         |                                                               04:00-20:00 ET)
         `-- for width in (5m, 15m, 60m):
               if candle_aggregator.completes_bucket(candle_ts, session_start, width):
                   publish FeaturesUpdated(width)                       (+1 event each)

test (before):  candle_ts = now(UTC) - 1 min   -> event count = f(minute of day)
test (after):   candle_ts = Sat 2026-08-15 12:00 ET -> session_bounds is None -> always 1 event
```

Events per candle minute on a trading weekday (Fri 2026-09-18, computed through
the real `MarketClock` + `candle_aggregator`): 1 event on 1248 minutes, 2 on 128,
3 on 49, 4 on 15. Never anything but 1 on weekends/holidays. The EventBus in the
test is its own per-instance object, so no earlier test can add events.

## Deliberately NOT done

- No change to `FeatureEngine`, `CandleRecorder`, `EventBus` or fixtures — there was
  no isolation defect to fix.
- The unrelated slow test #46
  (`test_backtest_runner_regression.py::test_backtest_runner_namespace_preserves_live_levels_and_repeat_runs_keep_nonzero_regime_scores`,
  several minutes in this sandbox; the suite does complete, ~13 min per full run), Backtest Runner, DB
  performance and production config were not debugged or modified.
- Other tests elsewhere in the suite that use `datetime.now()` were not audited
  beyond `test_feature_engine.py`.
- A duplicated `_clean_test_symbol(ticker)` call at the end of the stop-race test's
  `finally` block was noticed and left alone (harmless, out of scope).
