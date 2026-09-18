# CHANGES — pending decision (temp id: `flaky-test-cluster-rootcause`) — Root-caused the #119 flaky test cluster to a real, previously-deferred shutdown-race bug in two engines

**Decision number intentionally not assigned** — see this delivery's entry in `docs/decisions/confirmed-decisions.md` for the reasoning, and for the number to assign at merge time (143, per a three-source check at packaging time, unless another parallel session lands first — a **seven-way** collision, the widest yet in this log).

**Six parallel deliveries landed on `main` during this task's own session** (`data-feed-status-indicator`, `broker-connection-panel`, `ibkr-historical-backtest-provider`, `market-state-changed-websocket-channel`, `market-state-frontend-surfacing`, `chart-migration-stage-4-flagging`) — re-pulled and diffed against the working tree partway through and again immediately before packaging. Zero overlap either time: all six are frontend/docs/`backend/app/backtest_runner`-scoped, confirmed by their own footprint statements, and this delivery's four backend files were rebased cleanly onto the post-landing `main` with a byte-for-byte `diff -rq` confirming exactly those four files (plus this entry's own doc changes) differ from a fresh pull.

This task asked to root-cause — and fix, where a real fix exists — the 4-test flaky cluster carried and re-verified since decision #93, formally enumerated in decision #119. **Found a real, previously-deferred bug, not just a deeper restatement of "it's timing-sensitive":** `CandleRecorder.stop()` and `FeatureEngine.stop()` both still had the exact `task.cancel()` + `await task` shutdown pattern decision #84 already proved doesn't actually wait for in-flight `asyncio.to_thread` work to finish — #84 fixed the identical bug in the third engine (`LevelInteractionEngine.stop()`), explicitly flagged these two as "very likely" carrying it too, and deliberately deferred fixing them. That follow-up was never done until now.

## What changed

- **`backend/app/services/candle_recorder.py`** — `CandleRecorder.stop()` converted from cancel-based shutdown to a poison-pill drain (`_STOP_SENTINEL` enqueued onto the writer's own queue, no `.cancel()` call at all), the same already-proven pattern decision #84 shipped for `LevelInteractionEngine.stop()`. `_writer_loop` updated to recognize and gracefully exit on the sentinel.
- **`backend/app/feature_engine/engine.py`** — the identical fix applied to `FeatureEngine.stop()` and `_worker_loop`. This is the concrete mechanism behind this engine's own already-documented "async-timing race in cold-start backfill": an orphaned `to_thread` DB read/compute surviving past a supposedly-clean `stop()` contends for the same process-wide default `ThreadPoolExecutor` (only ~5 slots with `nproc=1` in this sandbox) and Postgres connection pool as whatever runs immediately afterward.
- **`backend/tests/test_candle_recorder.py`** — new deterministic regression test `test_stop_waits_for_an_in_flight_write_before_returning`, mirroring decision #84's own `test_stop_waits_for_an_in_flight_persist_before_returning` exactly. Verified both directions: fails 3/3 against the old cancel-based `stop()` (reverted temporarily to confirm), passes reliably against the fix.
- **`backend/tests/test_feature_engine.py`**:
  - New deterministic regression test `test_stop_waits_for_an_in_flight_compute_before_returning`, same shape, same both-directions verification (3/3 fail old / reliable pass fixed).
  - `test_feature_engine_backfills_from_persisted_history_on_cold_start` — its two `asyncio.sleep()` calls (0.3s, 0.2s) replaced with bounded, deterministic waits: a DB-row-count poll (mirroring `test_intelligence_routes.py`'s own `_wait_until_candles_persisted`) and a `received`-list-length poll.
  - `test_vwap_publishes_even_while_sma_is_still_warming_up` ("the most consistently-failing of the four") — its `asyncio.sleep(0.1)` replaced with the same bounded `received`-list poll. Its specific fragility traced precisely: an *undocumented*, unconditional `asyncio.to_thread` DB round trip (`_load_confirmed_daily_levels_for_today`, fired on every never-before-seen symbol regardless of whether Daily Levels matters to the test) racing a 0.1s sleep — the tightest margin of any test in the cluster.
  - Module docstring's Tier-2 "no DB required" claim corrected — found directly while root-causing this, not scope creep: confirmed by a real (unrelated) Postgres outage mid-session that every Tier-2 test in this file, not just Tier 3, depends on a reachable DB connection.
- **`docs/decisions/confirmed-decisions.md`** / **`INDEX.md`** — this delivery's own entry, full root-cause writeup, per-test disposition, and honest verification-limitation note.

## What did not change

- `test_daily_levels_carry_level_interaction_once_touched` and `test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction` — **zero test-body changes.** Both already use bounded, deterministic polling (an 8s timeout loop), not a fixed sleep; there was no anti-pattern in the test code to replace. Per decision #84's own corroborating analysis, their flakiness traces to the same engine-level shutdown race this delivery fixes — closing it there, not in already-correct test code, is the real fix.
- `LevelInteractionEngine.stop()` — already fixed by decision #84; untouched here.
- Anything under `backend/app/backtest_runner/`, `routes/backtest.py`, `api/websocket/channels.py`, `routes/broker.py`, `finnhub_data.py`, `market_data.py` — this task's own explicit exclusion list.
- No frontend file — this task was backend-only.

## Honest limitation — stated plainly, not overclaimed

Could not force a live reproduction of the cluster's actual failure in this sandbox, before **or** after the fix. Two full-suite baseline runs pre-change (770/770 passed, 0 failed each) and 10 repeated fresh-DB runs of the four cluster-relevant test files together (133 tests, 0 failures across all 10) never caught it — consistent with this cluster's own long-documented "0–3 failures depending on run" intermittency (see decisions #131, #132, #135, #136's own reported baselines), not evidence against the mechanism. Confidence rests on: the standalone reproduction of the exact `task.cancel()`-doesn't-wait mechanism; the two new regression tests' deterministic 3/3-fail-then-reliable-pass verification against old/new code; and decision #84's own prior corroborating analysis for the identical bug in a sibling engine — not on watching a live failure disappear in this run.

## Tests

Real Postgres, DB wiped and recreated between every run, per this project's own established practice.

- **Before** (untouched pre-change tree — confirmed byte-identical to current `main`'s backend via `diff -rq` against a fresh pull taken after the six parallel deliveries landed): 3 full-suite runs, 770/770/770 passed, 0 failed each.
- **After** (this delivery's fix): 3 full-suite runs, 772/772/772 passed (770 + 2 new regression tests), 0 failed each.
- Targeted: `test_feature_engine.py` + `test_intelligence_routes.py` + `test_candle_recorder.py` + `test_level_interaction_engine.py` together, 10 repeated fresh-DB runs, 133/133 passed every time.
- Negative control: both new regression tests independently reverted to the old cancel-based `stop()` and re-run 3x each — deterministic failure every time (~0.0000s elapsed vs. the expected ~0.15–0.3s), then restored and re-confirmed passing.

**Unrelated sandbox observation, noted for transparency, not this delivery's bug:** Postgres stopped unexpectedly once mid-session (an explicit "received fast shutdown request" in its own log, cause not identified as this delivery's own code — not a crash). Matches decision #131's own already-documented Postgres instability in a memory-constrained sandbox container; restarted cleanly, no data implications, not investigated further as out of this task's scope.
