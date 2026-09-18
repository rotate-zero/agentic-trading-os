# TESTING — pending decision (temp id: `flaky-test-cluster-rootcause`) — Root-caused the #119 flaky test cluster

Backend-only delivery. `docs/` and this file/`CHANGES.md` are the only other changes. No frontend files touched, so no `npx tsc -b`/`npx vite build` run applies.

## Required reading done, in order

`docs/decisions/README.md`; `docs/decisions/INDEX.md`'s last several rows; decision #119 in full (`docs/decisions/archive/107-121.md`) as the rigorous prior run-down of this exact cluster; decision #84 in full (`docs/decisions/archive/080-090.md`) — turned out to be the single most important piece of required reading, since it's where this delivery's actual root cause was already half-diagnosed; `backend/app/feature_engine/engine.py` and `backend/app/services/candle_recorder.py` in full; `backend/app/trading_intelligence/level_interaction_engine.py` in full, as the sibling engine decision #84 already fixed; `backend/tests/conftest.py`; all four named tests' own source in full; `test_intelligence_routes.py`'s `_wait_until_published`/`_wait_until_candles_persisted` helpers as the bounded-wait precedent this task's own prompt pointed to.

## Per-test summary (as the task asked)

| Test | Root cause found | Fixed? | How |
|---|---|---|---|
| `test_feature_engine_backfills_from_persisted_history_on_cold_start` | Two fixed `asyncio.sleep()` calls guessing at (a) CandleRecorder's write-behind writer landing 2 rows, (b) FeatureEngine's cold-start backfill compute finishing — both genuinely variable-latency, worsened by the shutdown-race bug below leaking cross-test contention into the shared thread pool. | **Yes** | Both sleeps replaced with bounded, deterministic polls (DB row count; `received`-list length). Root engine-level bug (below) also fixed. |
| `test_vwap_publishes_even_while_sma_is_still_warming_up` ("most consistently-failing") | Its own `asyncio.sleep(0.1)` — the tightest margin of any test in the cluster — raced an *undocumented*, unconditional `asyncio.to_thread` DB round trip in `_maybe_refresh_daily_levels`'s restart-survival check, which fires on every never-before-seen symbol regardless of whether Daily Levels is relevant to the test at all. | **Yes** | Sleep replaced with the same bounded `received`-list poll. Module docstring's stale "no DB required" claim for this test's tier corrected in place. |
| `test_daily_levels_carry_level_interaction_once_touched` | Traced (via decision #84's own corroborating analysis, confirmed still applicable) to a real shutdown-race bug: `CandleRecorder.stop()`/`FeatureEngine.stop()` used a `task.cancel()` + `await task` pattern that returns before in-flight `asyncio.to_thread` work actually finishes, letting orphaned background work from one test's engines contend for the shared default `ThreadPoolExecutor`/Postgres pool with a later test's own. Decision #84 already fixed the identical bug in the third engine (`LevelInteractionEngine.stop()`) and explicitly flagged these two as "very likely" carrying it too — deferred at the time, never done. | **Yes, at the engine level** | `CandleRecorder.stop()`/`FeatureEngine.stop()` fixed with the same poison-pill drain #84 already proved. **No change to this test's own body** — it already used bounded polling, not a fixed sleep; there was no test-code anti-pattern to fix. |
| `test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction` | Same shared root cause as the row above — same file, same real app lifespan, same engines. | **Yes, at the engine level** | Same engine-level fix. **No change to this test's own body**, same reasoning as above. |

## Root cause, in one paragraph

Decision #84 diagnosed and fixed a genuine bug: `task.cancel()` on an asyncio Task that's suspended awaiting `asyncio.to_thread(...)` returns almost immediately, regardless of whether the real OS thread underneath (actually running inside the executor) has finished — so `await task` after cancelling doesn't actually wait for the real work. That entry fixed it for `LevelInteractionEngine.stop()` via a poison-pill drain (enqueue a sentinel instead of cancelling, let the FIFO queue guarantee everything ahead of it finishes first) and explicitly flagged `CandleRecorder.stop()`/`FeatureEngine.stop()` as "very likely" carrying the identical bug — deliberately deferred at the time as follow-up work. Direct inspection confirmed that follow-up was never done, ~60 decisions later. This delivery applies the same, already-proven fix to both.

## Verification performed

1. **Standalone reproduction**, before touching any app code, mirroring decision #84's own verification method: a worker loop wrapping a real blocking call in `asyncio.to_thread`, cancelled mid-flight, returns in ~0ms while the real work keeps running detached from the caller — versus a poison-pill drain, which genuinely blocks until the real work finishes. Confirmed the mechanism in isolation first.
2. **Two new deterministic regression tests**, mirroring decision #84's own `test_stop_waits_for_an_in_flight_persist_before_returning`:
   - `test_candle_recorder.py::test_stop_waits_for_an_in_flight_write_before_returning`
   - `test_feature_engine.py::test_stop_waits_for_an_in_flight_compute_before_returning`

   Each wraps the real thread-offloaded method with an artificial 0.3s delay, feeds one item directly onto the engine's own queue, then asserts `stop()` blocks for close to that delay AND that the work genuinely completed (no `ForeignKeyViolation` on an immediate `DELETE`; a real published event). **Verified in both directions**, not just that they pass: each `stop()` was temporarily reverted to the old cancel-based pattern and its new test re-run 3x — deterministic failure every time (~0.00004s elapsed vs. an expected ~0.15s+ floor). Restored the fix and re-confirmed 3/3 passes. This is the strongest evidence in this delivery: not "the fix looks right" but "the exact bug this fix targets is provably present in the old code and provably gone in the new code."
3. **All four originally-named tests** run individually and together — pass.
4. **10 repeated fresh-DB runs** of `test_feature_engine.py` + `test_intelligence_routes.py` + `test_candle_recorder.py` + `test_level_interaction_engine.py` together (133 tests): 0 failures across all 10.
5. **3 full-suite runs before the fix** (untouched tree): 770/770/770 passed, 0 failed each, ~13 min each.
6. **3 full-suite runs after the fix**: 772/772/772 passed (770 + 2 new tests), 0 failed each, ~13 min each.
7. Real Postgres 16 throughout, DB schema dropped and recreated (`alembic upgrade head` from scratch) between every run in steps 4–6, per this project's own established practice.

## What this delivery could NOT show — stated honestly

**Never caught the cluster actually failing in this sandbox, before or after the fix.** Both pre-change baseline runs and all 10 targeted repeated runs came back clean. This matches the cluster's own long-documented "0–3 failures depending on run" intermittency (decisions #131, #132, #135, #136 all report the same test suite passing clean on some runs and failing on others) — it is not evidence the root cause is wrong, but it does mean this delivery cannot claim "N failures before, 0 after" from a live catch. The evidence this fix rests on instead: the standalone mechanism reproduction, the two new regression tests' deterministic (not probabilistic) fail-then-pass verification, and decision #84's own prior corroborating analysis of the identical bug in a sibling engine. If a future session's own full-suite run does catch one of these four failing again after this fix, that would be a genuine surprise worth investigating fresh — not dismissed as "the known cluster."

## Unrelated sandbox observation — not this delivery's bug

Postgres stopped unexpectedly once mid-session (its own log shows an explicit "received fast shutdown request", not a crash — cause not identified). Matches decision #131's own already-documented finding of Postgres instability in a memory-constrained sandbox container. Restarted cleanly; no data implications; not investigated further as genuinely out of this task's scope. Mentioned here only so a future session doesn't waste time re-diagnosing it as new.

## Footprint

Confirmed by `diff -rq` against a freshly re-pulled untouched clone, taken immediately before packaging (after this delivery's own four files were rebased onto the six parallel deliveries that landed on `main` mid-session — confirmed zero overlap, all frontend/docs/`backend/app/backtest_runner`-scoped):

- `backend/app/services/candle_recorder.py`
- `backend/app/feature_engine/engine.py`
- `backend/tests/test_candle_recorder.py`
- `backend/tests/test_feature_engine.py`
- `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, this file, `CHANGES.md`

Nothing under `backend/app/backtest_runner/`, `backend/app/api/routes/backtest.py`, `backend/app/api/websocket/channels.py`, `backend/app/api/routes/broker.py`, `finnhub_data.py`, `market_data.py`, or any frontend file touched — per this task's own explicit boundary list.

## Manual merge notes for parallel-session file conflicts

None expected. This delivery's four backend files (`candle_recorder.py`, `feature_engine/engine.py`, `test_candle_recorder.py`, `test_feature_engine.py`) were not touched by any of the six parallel deliveries that landed during this task's session — confirmed by `diff -rq` and by each of those deliveries' own footprint statements. `docs/decisions/confirmed-decisions.md`/`INDEX.md` are append-only from this delivery's side (one new entry, one new row) — if another session's own entry lands between this delivery's packaging and merge, append after theirs, not in place of anything here, same as this delivery's own `INDEX.md` row already had to do relative to the six PENDING entries ahead of it. `CHANGES.md`/`TESTING.md` are, per this project's own established pattern, wholesale-replaced by whichever delivery merges next — nothing to hand-merge there by design.
