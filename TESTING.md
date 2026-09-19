# TESTING — feature-engine-test-isolation-fix (decision #153)

**Test-only delivery** (plus docs). Two timestamp lines in
`backend/tests/test_feature_engine.py` changed; no production code did. Verified
against real PostgreSQL 16 with a controlled clock, because the defect is a
function of the minute of the day, not of test order.

## Reproduced facts (isolation, original tree, real `FeatureEngine` / `MarketClock` / `candle_aggregator`)

Clock control: `libfaketime` in start-at mode (`FAKETIME="@<instant>"` — the clock
starts at that instant and then ticks, so asyncio timing is unaffected; the test
computes its candle within ~1–3 s of process start, and the failing payload's own
`candle_ts` confirmed the intended minute).

```
cd backend
FAKETIME="@2026-09-18 14:45:00" LD_PRELOAD=/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1 \
  python3 -m pytest "tests/test_feature_engine.py::test_stop_waits_for_an_in_flight_compute_before_returning" -q -vv
# -> AssertionError: assert 3 == 1   (timeframes 1m, 5m, 15m; candle_ts 2026-09-18T14:44:00Z)
```

That is decision #150's symptom, produced by a **single isolated test** — so
cross-test contamination is ruled out. (`EventBus` is per-instance and the test
builds its own; `conftest.py`'s autouse fixture resets module singletons that this
test doesn't use.)

Original stop-race test, 3 repetitions per instant, identical every repetition
(`FAKETIME` = candle + 1 min):

| candle (UTC, Fri 2026-09-18 unless noted) | session | received |
|---|---|---|
| 14:45 | regular | 1 (pass) |
| 14:39 | regular | **2** (1m, 5m) |
| 14:44 | regular | **3** (1m, 5m, 15m) |
| 14:29 | regular | **4** (1m, 5m, 15m, 1h) |
| 08:14 | pre-market | **3** |
| 20:29 | after-hours | **3** |
| 20:59 | after-hours | **4** |
| Sat 2026-09-19 14:44 | closed | 1 (pass) |
| Mon 2026-09-07 (Labor Day) 14:44 | closed | 1 (pass) |

Original cold-start test (`FAKETIME` = candle + 8 min, since its decisive candle
is `now - 8 min`), 3 repetitions each: **2 / 3 / 4** events at regular-session
candles :39 / :44 / :29, **3** at pre-market :14; pass at the 1-event minute, at
the Saturday and Labor Day controls, and at the two after-hours cases tried. The
extra 5m/15m events carry `session_volume`/`vwap`/`vwap_ext`/`regular_open` and no
`sma_3`.

Pure bucket-completion count for a whole trading weekday (Fri 2026-09-18):
1 event on 1248 minutes, 2 on 128, 3 on 49, 4 on 15 — i.e. about one minute in five
of the 04:00–20:00 ET window fails the original test; weekends and holidays never.

## Changes made

- `backend/tests/test_feature_engine.py`: `candle_ts` / `base_ts` in
  `test_stop_waits_for_an_in_flight_compute_before_returning` and
  `test_feature_engine_backfills_from_persisted_history_on_cold_start` now use
  `_et(2026, 8, 15, 12, 0).astimezone(timezone.utc).replace(second=0, microsecond=0)`
  (a Saturday, unconditionally `Session.CLOSED`) — the anchor the same file already
  uses — with a comment at each site. Assertions, filters, timeouts unchanged.
- Docs: decision #153 (`confirmed-decisions.md`), one `INDEX.md` row, `CHANGES.md`,
  this file.

## Tests actually completed

Before = untouched tree; after = fixed tree. Real PostgreSQL 16, DB migrated with
`alembic upgrade head`.

| Check | Before | After |
|---|---|---|
| Each of the two tests × the 9 instants above × 3 reps | fails at every 2/3/4-event instant (tables above) | **27/27 pass per test** |
| 10 repetitions at each test's 3-event instant | 0 passed / 10 failed (each test) | **10 passed / 0 failed** (each test) |
| Whole `test_feature_engine.py`, clock 2026-09-18 14:45:00 | 80 passed, 1 failed (stop-race test) | **81 passed** |
| Whole `test_feature_engine.py`, clock 2026-09-18 14:52:00 | 80 passed, 1 failed (cold-start test) | **81 passed** |
| Whole module at 14:30:00 (4-event), 08:15:00 (pre-market), 20:30:00 (after-hours) | not run | **81 passed** each |
| Whole module, real sandbox clock (a Saturday) | 81 passed | **81 passed** |
| **Full backend suite** ×3 each, DB dropped/recreated per run, interleaved, natural (Saturday) clock | **776 passed** ×3 (wall 13:07, 13:07, 13:07) | **776 passed** ×3 (wall 13:08, 13:07, 13:07) |

No other test in `test_feature_engine.py` failed at any instant probed.
The "revert and restore" step of decision #149's methodology was realized as
untouched-tree vs fixed-tree runs rather than an in-place edit.

## What was NOT covered

- **The full suite at weekday session hours was not run.** All six full-suite runs
  above used the sandbox's natural clock, a Saturday, where neither the original
  nor the fixed tests can fail — they show "776/776, same collection, no
  regression", **not** that the fix works. The controlled-clock rows are what
  demonstrate the fix. Worth running the full suite once on a weekday between
  04:00 and 20:00 ET in your normal environment (before/after) to see the #150
  symptom disappear end to end.
- **Timing observation, out of scope, not debugged:** a full run takes ~13 minutes
  here and spends several minutes on
  `test_backtest_runner_regression.py::test_backtest_runner_namespace_preserves_live_levels_and_repeat_runs_keep_nonzero_regime_scores`
  (a Backtest Runner replay advancing ~1 `market_state_history` row/second with the
  CPU idle). Slow, not stuck — every run completed. Nothing about it was modified.
  (Early attempts on a slow-disk sandbox instance looked like a hang; they weren't.)
- Decision #150's original run was never re-observed; that it landed on a
  :14/:44 (or :29/:59) session minute is inferred from the symptom (3 events)
  matching the mechanism.
- Other `datetime.now()`-derived timestamps elsewhere in the suite were not audited
  beyond this module.
- The sandbox PostgreSQL had `fsync` disabled (throwaway instance, slow disk) —
  sandbox-only, not part of this delivery.

## Manual merge notes

None expected. The only code file touched is `backend/tests/test_feature_engine.py`;
`main` was unchanged for it between the start-of-task pull and the pre-packaging
re-pull. If the World View frontend session lands a decision first, renumber #153
to the next free number in all three places (entry header, entry's assignment
paragraph, `INDEX.md` row) and note the collision inline.
