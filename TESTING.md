# TESTING — decision #157: deterministic fast backtest replay

## Current delivery verification

Environment for the new timing measurement: WSL2 Linux 6.6.114.1, 12 logical CPUs, 7.7GiB RAM, Python 3.14.4, PostgreSQL 18.6 (`fsync=on`, `synchronous_commit=on`, `shared_buffers=128MB`). Command:

```
/usr/bin/time -f 'WALL_SECONDS=%e CPU_PERCENT=%P MAX_RSS_KB=%M' \
  env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=trading_workspace \
  POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/pytest \
  tests/test_backtest_routes.py::test_run_backtest_volume_gated_strategy_returns_honest_zero \
  -q --tb=short
```

Result: 1 passed; pytest 1.47s; process wall 1.97s; 119 Market State rows for the 119 candles admitted by the fixture provider. Decision #155's previous 118.16s measurement used a different 1-vCPU/4GB/PostgreSQL-16.15 (`fsync=off`) environment, so no hardware-independent ratio is claimed.

Focused results so far:

- Debounce Scheduler + pure scoring + timeframe/Acceleration regressions: 41 passed.
- Replay State Producer + Market State Engine against real PostgreSQL: 12 passed.
- Required focused set (scheduler, Market State, replay, runner/routes, D18/D19, IBKR route, Momentum): 127 passed in 12.57s.
- Manual existing-scenario check: `volume_gated_baseline` + Momentum now records zero outcomes; 119 rows, acceleration range 50.00–54.22, below threshold 65. This is the intended consequence of 60-second source-time intervals; decision #155's wall-clock path recorded one BUY.

Complete backend suite before the final GitHub-main reconciliation: 783 passed in 55.67s, no failures, deselection, retries, marker exclusions, or timeout changes.

Post-reconciliation/final-numbering rerun:

- Required focused set: 127 passed in 12.78s.
- Complete backend suite: 783 passed in 55.50s.
- `npx vite build`: passed, 99 modules, 1.77s.

Frontend verification after correcting stale timing copy: `npx vite build` passed (99 modules). `npm run build` remains blocked by the four pre-existing `GridPresetPicker.tsx` TypeScript errors documented by prior deliveries (`GRID_PRESETS`, `preset`, `setPreset`, implicit `any`); none is in a changed file.

## Prior delivery record — decisions #155 and #156

Both deliveries append to `docs/decisions/confirmed-decisions.md`/`INDEX.md` (no conflict there — both entries present) and replace this file plus `CHANGES.md`. #156 landed second and combined both deliveries' content here as sections, per #155's own manual-merge notes (§6 of its part, below), rather than overwriting #155's content.

## Part A — Backtest replay timing investigation (decision #155)

**Docs-only delivery. Nothing was implemented or changed; nothing was decided.** Status: awaiting Saqib's direction. No `backend/`, `frontend/` or test file is touched, so no test suite needs re-running for this change. "Verification" here means reproducibility: every number below has the command and environment that produced it. Sections are separated on purpose: **1 Measured facts → 2 Options → 3 Recommendation → 4 Not measured → 5 Verification of this delivery → 6 Manual-merge notes.**

## 1. Measured facts

### 1.1 Environment (it changes timings)

| Item | Value |
|---|---|
| Hardware | 1 vCPU, 4 GB RAM sandbox, Ubuntu 24.04, Python 3.12.3 |
| Postgres | 16.15 (Ubuntu apt), one cluster, local socket/TCP `localhost:5432` |
| **Postgres tuning** | **`fsync = off` only** (via `conf.d/sandbox.conf`; the same setup #153 documented). `synchronous_commit = on`, `shared_buffers = 128MB`, everything else default. Confirmed with `SHOW fsync` |
| DB | `trading_workspace`, freshly created, `alembic upgrade head` → `0010` before the baseline run |
| Python deps | `pip install --break-system-packages -r backend/requirements.txt` |
| Sandbox notes | The nodesource apt source returned 403 and was moved aside so `apt-get update` could install Postgres (unrelated to timings). Sandbox clock reads Saturday 2026-09-19 |

Setup commands, in order:

```
mv /etc/apt/sources.list.d/nodesource.sources /home/claude/     # only because it returned 403
apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16 postgresql-client-16
printf 'fsync = off\n' > /etc/postgresql/16/main/conf.d/sandbox.conf
pg_ctlcluster 16 main start
su postgres -c "psql -c \"CREATE USER trading WITH SUPERUSER PASSWORD 'trading';\""
su postgres -c "psql -c 'CREATE DATABASE trading_workspace OWNER trading;'"
cd backend && pip install --break-system-packages -r requirements.txt && alembic upgrade head
```

### 1.2 Baseline full-suite run

```
cd backend
setsid nohup python3 -m pytest -p no:cacheprovider --durations=0 -v tests > suite_baseline.log 2>&1 &
# followed with bounded polling, never a long blind sleep:
for i in $(seq 1 24); do grep -q END suite_baseline.meta && break; sleep 5; done
```

Result: **776 collected, 776 passed, 787.29 s** by pytest (787.95 s wall, 18:33:46Z → 18:46:54Z). The last session reported 787–789 s on the same 776 tests.

### 1.3 The 30 slowest tests (call time; setup/teardown are all < 5 ms)

| # | Seconds | File | Test | Drives BacktestRunner / replay producer |
|---|---|---|---|---|
| 1 | 236.33 | `test_backtest_runner_regression.py` | `test_backtest_runs_keep_independent_daily_level_rows` | replay |
| 2 | 236.30 | `test_backtest_runner_regression.py` | `test_backtest_runner_namespace_preserves_live_levels_and_repeat_runs_keep_nonzero_regime_scores` | replay |
| 3 | 128.23 | `test_backtest_routes.py` | `test_run_backtest_first_pullback_scenario_fires_and_persists` | replay |
| 4 | 118.16 | `test_backtest_routes.py` | `test_run_backtest_volume_gated_strategy_returns_honest_zero` | replay |
| 5 | 3.06 | `test_backtest_runs_route.py` | `test_route_reflects_a_real_backtest_runner_write` | replay |
| 6 | 3.05 | `test_backtest_runner.py` | `test_full_fixture_run_records_real_outcome_at_correct_instants` | replay |
| 7 | 3.05 | `test_backtest_runner_regression.py` | `test_runner_discards_signal_when_fill_time_snapshot_is_none` | replay |
| 8 | 3.05 | `test_backtest_runner_regression.py` | `test_runner_discards_signal_when_exit_time_snapshot_is_none` | replay |
| 9 | 3.05 | `test_backtest_runner_regression.py` | `test_runner_persists_outcome_when_both_snapshots_present` | replay |
| 10 | 3.05 | `test_backtest_runner.py` | `test_is_backtest_isolation_reuses_existing_invariant` | replay |
| 11 | 2.61 | `test_vwap_strategy.py` | `test_end_to_end_same_zone_repeat_after_established_window_is_suppressed` | no |
| 12 | 2.04 | `test_replay_state_producer.py` | `test_advance_to_settles_state_to_the_replayed_candle_ts_not_wall_clock` | replay |
| 13 | 1.31 | `test_market_state_engine.py` | `test_second_observation_populates_acceleration` | no |
| 14 | 1.21 | `test_first_pullback_strategy.py` | `test_end_to_end_day_rollover_allows_a_fresh_first_touch` | no |
| 15 | 1.04 | `test_ibkr_backtest_route.py` | `test_successful_route_uses_preloaded_real_provider_and_persists_run` | replay |
| 16 | 1.01 | `test_first_pullback_strategy.py` | `test_end_to_end_second_touch_rejected_does_not_fire` | no |
| 17 | 1.01 | `test_vwap_strategy.py` | `test_end_to_end_new_trading_day_resets_last_fired_zone` | no |
| 18 | 1.01 | `test_vwap_strategy.py` | `test_end_to_end_genuine_alternation_fires_both_directions` | no |
| 19 | 1.01 | `test_reversal_strategy.py` | `test_end_to_end_fires_at_most_once_per_day` | no |
| 20 | 1.01 | `test_reversal_strategy.py` | `test_end_to_end_second_touch_conquest_still_fires_unlike_first_pullback` | no |
| 21 | 0.61 | `test_strategy_integration_contract.py` | `test_market_state_snapshot_carries_the_market_composite_for_any_symbol` | no |
| 22 | 0.61 | `test_market_state_engine.py` | `test_cross_symbol_state_persists_sentinel_row_with_correct_shape` | no |
| 23 | 0.61 | `test_market_state_engine.py` | `test_cross_symbol_state_synthesizes_once_all_three_report` | no |
| 24 | 0.61 | `test_vwap_strategy.py` | `test_end_to_end_conquered_below_with_neutral_trend_fires_sell` | no |
| 25 | 0.61 | `test_first_pullback_strategy.py` | `test_end_to_end_no_established_trend_does_not_fire` | no |
| 26 | 0.61 | `test_reversal_strategy.py` | `test_end_to_end_rejected_touch_does_not_fire` | no |
| 27 | 0.61 | `test_vwap_strategy.py` | `test_end_to_end_conquered_above_with_neutral_trend_fires_buy` | no |
| 28 | 0.61 | `test_reversal_strategy.py` | `test_end_to_end_established_uptrend_conquered_fires_sell` | no |
| 29 | 0.61 | `test_first_pullback_strategy.py` | `test_end_to_end_first_touch_rejected_in_uptrend_fires_buy` | no |
| 30 | 0.61 | `test_vwap_strategy.py` | `test_end_to_end_established_trend_blocks_firing_even_on_a_real_conquest` | no |

### 1.4 Where the time goes

| Slice | Seconds | Share of summed test time (784.25 s) |
|---|---|---|
| 4 tests: `test_backtest_runs_keep_independent_daily_level_rows` 236.33, `…namespace_preserves_live_levels…` 236.30, `test_run_backtest_first_pullback_scenario_fires_and_persists` 128.23, `test_run_backtest_volume_gated_strategy_returns_honest_zero` 118.16 | 719.02 | 91.7% |
| 8 smaller replay-driving tests (1.0–3.1 s) | 21.39 | 2.7% |
| **All 12 replay-driving tests ≥ 1 s** | **740.41** | **94.4%** |
| All six replay-related files (60 tests) | 741.28 | 94.5% |
| Everything else (716 tests) | 42.97 | 5.5% |

The four heavy tests are #1, #2, #46 and #47 in run order. Every one of the 12 fits **wall ≈ (replayed candles − 1) × 1.0 s + ~0.2 s** (118.16 s for 119 replayed candles, 128.23 s for 129, 3.05 s for 4, 2.04 s for 3). Non-replay slow tests are fixed sleeps inside test bodies (0.1–1.1 s each; 26 tests ≥ 0.5 s outside the six files total 20.81 s).

### 1.5 Mechanism, confirmed with an instrumented single replay (throwaway; not delivered)

The probe script lives only in the sandbox. What it does, so it can be re-created: runs `BacktestRunner` on scenario `volume_gated_baseline` with strategy Reversal (or Momentum), built exactly as `test_backtest_runner_namespace_preserves_live_levels_and_repeat_runs_keep_nonzero_regime_scores` builds it; wraps `EngineBackedReplayStateProducer.advance_to/_settle_market_state/_drain_bus`, `ContextEngine.evaluate_all/evaluate_for_symbol`, `FeatureEngine._compute_one`, `MarketStateEngine._compute/_persist`, `LevelInteractionEngine._process_one`, and replaces `asyncio.sleep` with a wrapper that logs (calling coroutine, requested, actual); reads `market_state_history` and `strategy_outcomes` afterwards. Per-pace changes are monkeypatches on `app.market_state_engine.engine._MIN_INTERVAL_SECONDS` (and, in R3, the producer's `settle_poll_interval_seconds` default).

| Run | Floor / poll | `run()` wall | CPU | advance_to calls | Rows |
|---|---|---|---|---|---|
| R1 real (Reversal) | 1.0 s / 0.05 s | 118.159 s | 0.767 s | 119 | 119 |
| R6 real, repeat | 1.0 s / 0.05 s | 118.136 s | — | 119 | 119 |
| R2 | 0.05 s / 0.05 s | 6.102 s | 0.434 s | 119 | 119 |
| R3 | 0.001 s / 0.005 s | 0.995 s | 0.409 s | 119 | 119 |
| R4 real (Momentum) | 1.0 s / 0.05 s | 118.168 s | — | 119 | 119 |
| R5 (Momentum) | 0.05 s / 0.05 s | 6.140 s | — | 119 | 119 |
| R7 (Momentum) | 0.001 s / 0.005 s | 0.951 s | — | 119 | 119 |

R1 detail: `advance_to` first call 0.068 s; calls 2–119 mean **1.0004 s** (min 0.955, max 1.040); 99.7% of `advance_to` is inside `_settle_market_state` (117.78 s of 118.12 s); 118 debounce sleeps requested a mean of 0.970 s; 2,345 settle-poll sleeps (19.7 per candle); bus drains 0.081 s total (357 calls), Context Engine 0.013 s, FeatureEngine compute 0.048 s, Market State compute 0.005 s and persist 0.293 s (2.5 ms/candle), Level Interaction 0.196 s. The ceiling loop slept 12 times and never fired. **Nothing else adds a real wait.**

Data effects: across R1/R2/R3, trend, volatility, volume and VWAP scores were identical in all 119 rows; `acceleration_score` differed in 80 of 119 rows (max |Δ| 3.59 at 0.05 s, 35.18 at 0.001 s); R1 and R6 (both real pace) were identical. Momentum recorded the same single BUY (102.34 → 103.70, `target`, entry 2026-02-02 15:09Z) in R4/R5/R7, with persisted `market_state_at_entry.acceleration_score` 49.93 / 48.75 / 40.08. Cause, by reading: `MarketStateEngine._compute` divides Δtrend by the real `time.monotonic()` gap since the previous compute.

### 1.6 Whole-suite emulations (throwaway pytest plugin outside the repo, patching only the six replay files)

| Run | Configuration | Time | Result |
|---|---|---|---|
| baseline | none | 787.29 s | 776 passed |
| emulated (a) | floor 0.05 s | **84.78 s** | 775 passed, **1 failed:** `test_backtest_routes.py::test_run_backtest_first_pullback_scenario_fires_and_persists` (`elapsed > 60`) |
| emulated (a) | floor 0.001 s, poll default 0.005 s | **52.70 s** | 774 passed, **2 failed:** the same test and `test_replay_state_producer.py::test_replay_settle_timeout_raised_honestly_not_stale_state_returned` |
| no patch | four heavy tests `--deselect`ed | **67.45 s** (68.0 s wall) | 772 passed, 4 deselected |

The `--deselect` run was: `python3 -m pytest -p no:cacheprovider -q --durations=0 tests --deselect tests/test_backtest_runner_regression.py::test_backtest_runs_keep_independent_daily_level_rows --deselect tests/test_backtest_runner_regression.py::test_backtest_runner_namespace_preserves_live_levels_and_repeat_runs_keep_nonzero_regime_scores --deselect tests/test_backtest_routes.py::test_run_backtest_first_pullback_scenario_fires_and_persists --deselect tests/test_backtest_routes.py::test_run_backtest_volume_gated_strategy_returns_honest_zero`.

### 1.7 Read directly, not measured

- `elapsed > 60` is asserted at `test_backtest_routes.py` (~line 131) and its module docstring says the coupling is deliberate. That file is not excluded from the parallel wall-clock audit.
- `FixtureCandleProvider.get_historical` filters `start <= candle_ts < end`; `backtest.py:329` and the regression tests pass `end=candles[-1].candle_ts`, so the last fixture candle is not replayed (120 → 119). The comment near `test_backtest_runner_regression.py:595–610` describes a different cause for a one-row shortfall; left as found.
- D17 in `strategy-engine-open-decisions.md` still reads open ("no code anywhere resolves it either way yet") while #128 records runner-level as-built handling (snapshots at `entry_filled_at`/`exit_filled_at`, `None` ⇒ `DiscardedSignal`). Not edited.
- Live `acceleration_score` (by reading): computed over the gap since the previous compute, and the 10 s ceiling loop recomputes when no candle arrives — so replay (~1 s window) and live differ; magnitude unmeasured. An earlier interim note's "~60×" was wrong.

## 2. Options (design only — detail and file lists are in the entry)

| Option | Measured / estimated saving | Main cost |
|---|---|---|
| (a) test-side floor patch | **Measured emulation** 787 → 84.8 s (0.05 s), 52.7 s (0.001 s, 2 failures); four-tests-only ≈ 105 s (estimate) | Loses real-pace end-to-end replay coverage unless canaries are kept; `elapsed > 60` must be replaced; floor must stay above the 0.001 s settle-timeout test |
| (b1) injectable interval (production) | Suite as (a); 120-candle run 118 s → 6.1 s / 1.0 s (measured via monkeypatch) | Changes replayed acceleration values; touches `market_state_engine/engine.py`; revisits the Unit 2 brief |
| (b2) flush / force path (production) | Estimate ≈ R3 (~1 s per 120 candles) | Touches shared `DebounceScheduler`; replay stops exercising real debounce |
| (c) replay-side clock | Estimate ≈ (b2) | Largest surface; changes replayed acceleration to a third definition (neither today's nor live's) |
| (d) slow tier | **Measured** 67.45 s default tier (`--deselect`, no repo change) | Isolation guards leave the default run |
| (e) do nothing | 0 | 787 s per full verification |

## 3. Recommendation — for Saqib's decision, not a decision

(1) Now, no repo change: the four-test `--deselect` (67.5 s) while iterating on code that does not touch replay; full suite before merging anything that does. (2) If a durable suite-time fix is wanted: option (a), narrowly scoped to the four heavy tests, floor ≈ 0.05 s, real-pace canaries kept (the eight small replay tests and `test_replay_state_producer.py`), `elapsed > 60` replaced by a candle-count proof. (3) Treat production-side speed-up ((b)/(c)) as a separate decision gated on two forks: what replay's `acceleration_score` should mean, and whether faster replay for users is in scope.

## 4. Not measured

A full-suite run at every candidate configuration (only the two emulations and the `--deselect` run exist); the four-tests-only (a) variant; estimates for (b2) and (c); real-IBKR replay length and acquisition time (the ~960-bar / ~16 min figure is an assumption from a 16 h extended session); live-path acceleration behaviour; scenarios other than `volume_gated_baseline`, strategies other than Reversal and Momentum; other hardware; Postgres with durable `fsync`. All timings above share the `fsync = off` setting.

## 5. Verification of this delivery

Footprint confirmed by `diff -rq` between a freshly pulled clone and the same clone with this delivery unzipped on top:

```
Files fresh3/CHANGES.md and applied/CHANGES.md differ
Files fresh3/TESTING.md and applied/TESTING.md differ
Files fresh3/docs/decisions/INDEX.md and applied/docs/decisions/INDEX.md differ
Files fresh3/docs/decisions/confirmed-decisions.md and applied/docs/decisions/confirmed-decisions.md differ
(4 files: two root docs replaced, two decision-log files appended to; nothing else)
```

Decision-number check, run immediately before the entry was written: `INDEX.md` last row #154, `confirmed-decisions.md` tail #154, archive files `001-060 … 122-133` — all agree; next free number **#155**. Nothing under `backend/` or `frontend/` and no test file differs from `main`; no existing decision was edited.

## 6. Manual-merge notes (four shared files with `test-wall-clock-audit`)

- `docs/decisions/confirmed-decisions.md` — append-only. If the other instance landed first, keep its entry and place this one after it; renumber this entry to the next free number (header line `### 155.`), and note the collision inline.
- `docs/decisions/INDEX.md` — append-only, one row. Keep the other row(s); renumber this row to match the entry header.
- `CHANGES.md` and `TESTING.md` — both deliveries replace the whole root file. If the other one landed first, do not overwrite it: keep both as sections in each file, or rename this pair to `CHANGES-backtest-replay-timing-investigation.md` / `TESTING-backtest-replay-timing-investigation.md`. After renumbering, update the "decision #155" mentions in both.
- Content overlap to watch: `test_backtest_routes.py::…first_pullback…` carries a wall-clock assertion (`elapsed > 60`) that the audit instance may also flag; this delivery changes no test.

## Part B — Wall-clock-dependent test audit (decision #156)

**Docs-only delivery. No test or production file changed; nothing was fixed because nothing proven-broken was found.** Sections: **1 Methodology → 2 Full inventory table → 3 Controlled-clock proof (the two genuine candidates) → 4 Read-only review of `test_backtest_runner_*.py` → 5 What was not covered → 6 Verification of this delivery.**

## 1. Methodology

Per the task's own instruction, occurrence is not defect: `datetime.now()`/`utcnow()`/`date.today()`/`time.time()` appearing in a test only matters if the test's **outcome** — not just the literal value computed — can change depending on which real moment it runs at. Two ways that can happen:

- **Direct**: the test itself calls one of the four functions and the result influences something asserted.
- **Indirect**: test or production code under test calls a `MarketClock` method with no explicit `ts` argument, so it defaults to real `datetime.now()` internally (every method's signature is `ts: datetime | None = None`, confirmed in `app/core/market_clock.py`).

First confirmed, by reading `feature_engine/engine.py`'s `_compute_one`/`_compute_aggregated` in full, that **the engine itself never touches the wall clock** — every session/timing decision (`MarketClock.session_bounds`, `.trading_day`, etc.) is driven off the caller-supplied `candle_ts`, never real `now()`. This means the defect class decision #153 found is structurally confined to test code, not production code — a test is only at risk if *it* supplies a `now()`-derived timestamp into the system under test.

Direct-risk inventory: `grep -rn "datetime\.now(\|utcnow(\|date\.today(\|time\.time()" backend/tests/` (excluding `test_feature_engine.py`), then every hit read in its surrounding test to classify as comment vs. real, and every real hit traced through the actual code path it feeds — not guessed from the pattern alone.

Indirect-risk inventory: `grep -rn` for zero-argument calls to each `MarketClock` method (`.is_market_open()`, `.current_session()`, `.session_bounds()`, `.trading_day()`, `.minutes_since_open()`, `.next_session_boundary()`, `.is_regular_session()`) across both `backend/tests/` and `backend/app/`. Zero hits in tests. Two in production: `GET /health` (`market_session`, `market_open`) and `GET /market/feed-status` (`market_session`). Neither is a defect: no test file references `/health` at all (`grep -rln "/health\b" backend/tests/` — empty), and the one test hitting `/market/feed-status` (`test_market_feed_status_reports_staleness_for_a_recorded_candle`, `test_market_routes.py`) asserts only `"market_session" in body`, never its value. `ContextEngine._loop`'s implicit-now `next_session_boundary()` call was also checked (it sizes a background sleep, cancelled by every test's `.stop()` well before it could elapse) — read the method's full fallback branch (walks forward to the next non-holiday weekday if nothing is left "today") and confirmed it cannot raise for any input, so even an unbounded real-clock value passed through this path is inert.

## 2. Full inventory table — every real (non-comment) direct call site

36 real sites across 16 files (`test_daily_levels.py` has 10 raw hits, all inside comments describing 5 anchors already fixed to literal `datetime(2026, 8, 12, 15, 0, ...)` timestamps — 0 real sites, no action needed).

| File | Real sites | Outcome | Why (code path actually traced) |
|---|---|---|---|
| `test_tick_ingest.py` | 1 | No | `exchange_ts` is an opaque per-tick value; `TickIngestBridge` forwards `PRICE_UPDATED` per tick regardless of session (grepped for `MarketClock` in `tick_ingest.py` — zero hits) |
| `test_backtest_runner_fixtures.py` (read-only) | 2 | No | L52: unknown-symbol lookup raises before any date filtering; L167: asserts `NotImplementedError` on a documented stub, argument value irrelevant |
| `test_candle_recorder.py` | 4 | No | `CandleRecorder`/`candle_store` have zero `MarketClock` import; DB writes/reads are plain range queries by `candle_ts`. One real lead chased and ruled out: the `candles` table is monthly range-partitioned and migration `0001` only seeds July/August 2026 — today (2026-09-19/20) is outside that range, which looked like a live bug. `app/db/partitions.py` auto-creates the *write's own* month keyed off that row's `candle_ts` before every insert (confirmed by reading `candle_recorder.py:179`), not off wall-clock `now` — so this never fails regardless of the real date. File run live: 4/4 passed |
| `test_backtest_runner_regression.py` (read-only) | 3 | No | L330: `get_aggs` unconditionally raises `BadResponse`, start/end never inspected; L548/L650: `default_registry(datetime.now(...))` only sets `StrategyConfig.active_from`, which decision #116/D14 confirms is stored but never enforced anywhere in the scheduler — the value is inert |
| `test_live_tick_relay.py` | 3 | No | `LiveTickRelay` has zero `MarketClock` import (grepped); `exchange_ts` only builds OHLC bars, no session dependency |
| `test_strategy_integration_contract.py` | 2 (+2 comment) | No | Both are a "not coincidentally close to real now" sanity check on a timestamp fixed years in the past (`_TS`) — can only ever be true |
| `test_ibkr_backtest_route.py` | 1 | No | `acquired_at=datetime.now(...)` is passed into `IBKRReplayDataset` and never asserted anywhere in the test |
| `test_news_flag_provider.py` | 1 | No | `time.time()` used for a *relative* delta (`minutes_ago * 60`) consumed synchronously by `_summarize()`'s own `time.time()` call milliseconds later — not the session/calendar defect class; all `minutes_ago` values used are far from the 15m importance-threshold edge |
| `test_candle_aggregator.py` | 3 | No | All three `datetime.now(_ET)` values are `start`/`end` filter bounds passed to a `monkeypatch`ed `candle_store.get_recorded_candles` (a `lambda` that ignores its args and returns fixed data), or feed a path that raises `ValueError` before any date logic runs |
| `test_intelligence_routes.py` | 3 (+6 comment) | 2× No (proven, §3) / 1× No (by design) | L261, L318: see §3. L476 (`test_intelligence_series_reflects_real_persisted_candles`): already defensive by construction — its own comment explains it asserts only that the `vwap` key exists, never its value, precisely because `base_ts` might not land in a regular session |
| `test_polygon_provider.py` | 5 | No | All five are `start`/`end` args passed to a `get_aggs` `lambda` that ignores kwargs entirely and returns fixed fake data (confirmed `get_historical`'s body: `start`/`end` flow straight into `get_aggs` and nowhere else) |
| `test_fundamentals_provider.py` | 1 | No | `now` populates DB columns (`profile_updated_at` etc.) that the test never asserts on — only `sector`/`industry`/`market_cap`/`revenue_ttm`/`financials_period`/`next_earnings_date` are checked |
| `test_finnhub_provider.py` | 1 | No | `get_historical` unconditionally raises `HistoricalDataUnavailableError` (free-tier contract) regardless of the start/end passed |
| `test_ibkr_adapter.py` | 1 | No | Qualification is mocked to always fail, raising `SymbolNotFoundError` before `now`/start/end are ever used |
| `test_fundamentals_refresh.py` | 2 | No | Self-referential: both the input data (`soon`/`later`) and the expected result derive from the *same* `date.today()` call in the same test — always `soon < later`, robust to any real date |
| `test_market_routes.py` | 3 | No | L235/L413 self-recorded-candle tests: candle_ts (`now − 1min`) always falls inside the route's own `[now − N·count, now]` window by a huge margin (route's own `now()` call is at most ~1s after the seed's) — not the session-dependent defect class. L333 (`staleness_seconds`): plain elapsed-time subtraction with a ±generous tolerance, asserted with `assert known_age_seconds - 5 <= ... <= known_age_seconds + 30`, no session/day dependency. All three routes: `market_session`/similar keys, when present, are never asserted on value |

## 3. Controlled-clock proof — the two genuine candidates

`test_intelligence_state_merges_feature_and_level_interaction_data` and `test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction` (both `test_intelligence_routes.py`) derive `base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=20|30)`, then publish 9/17 consecutive 1m candles through the real EventBus into a real running app (`app.router.lifespan_context`), exercising the real Feature Engine and Level Interaction Engine. Reading alone couldn't rule this out (a straddled ET midnight could plausibly reset `LevelInteractionEngine`'s daily touch counter mid-run), so it was proven, not assumed.

Command (repeated verbatim per instant/rep, only `FAKETIME` changes):

```
cd backend
FAKETIME="@<instant>" LD_PRELOAD=/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1 \
  python3 -m pytest \
    tests/test_intelligence_routes.py::test_intelligence_state_merges_feature_and_level_interaction_data \
    tests/test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction \
    -q -p no:cacheprovider
```

| Instant (UTC) | What it is | Reps | Result |
|---|---|---|---|
| 2026-09-18 14:45:00 | Weekday regular session | 3 | 3/3 pass |
| 2026-09-18 09:00:00 | Weekday pre-market | 3 | 3/3 pass |
| 2026-09-18 21:00:00 | Weekday after-hours | 3 | 3/3 pass |
| 2026-09-19 14:45:00 | Saturday (closed) | 3 | 3/3 pass |
| 2026-09-07 14:45:00 | Labor Day (holiday, closed) | 3 | 3/3 pass |
| 2026-09-18 04:15:00 | Just past ET midnight (00:15 ET) — `base_ts` straddles the ET trading-day boundary | 3 | 3/3 pass |
| 2026-09-18 00:15:00 | Just past UTC midnight | 3 | 3/3 pass |
| 2026-03-08 06:50:00 | 2026 US DST spring-forward edge | 3 | 3/3 pass |
| 2026-11-01 06:20:00 | 2026 US DST fall-back edge | 3 | 3/3 pass |
| 2026-09-30 23:59:00 | Month rollover | 3 | 3/3 pass |

**30 pytest invocations, 60 individual test passes, 0 failures.** Why safe, confirmed by reading the assertions: both tests reach their result via `_wait_until_published`/HTTP polling of `GET /intelligence/state`, scoped to `body["timeframes"]["1m"]`, and assert specific SMA/VWAP/level-interaction *values*, never an event *count*. The extra 5m/15m/1h `FeaturesUpdated` publishes #153's bug produces during a real session land under the `"5m"`/`"15m"`/`"1h"` keys of the same response — a different, unasserted part of the payload — so they cannot perturb these two tests' outcome regardless of session or day.

## 4. Read-only review of `test_backtest_runner_*.py`

Per the file boundary, these two files (5 real sites — `test_backtest_runner_fixtures.py` L52/L167, `test_backtest_runner_regression.py` L330/L548/L650) were read and classified (table above) but not touched. **No suspected wall-clock defect found to report** to the parallel `backtest-replay-timing-investigation` (#155) session.

That session's own `TESTING.md` flagged one overlap concern for this audit to check: `test_backtest_routes.py`'s `assert elapsed > 60` (line 131). Checked directly: `elapsed = time.monotonic() - t0` around an HTTP POST — a real-duration *stopwatch* measurement, not a calendar timestamp. `grep -n "datetime\.now(\|utcnow(\|date\.today(\|time\.time()" test_backtest_routes.py` returns zero hits; the file has no occurrence of this audit's defect class at all. Confirmed no actual overlap — that assertion's flakiness risk (if any) is about real execution speed, exactly #155's own subject, not which calendar day/session it runs in.

## 5. What was not covered

- The controlled-clock matrix (§3) was run only against the two genuine candidates, not against all 36 real sites — the rest were ruled out by tracing their actual code path (table in §2), per the task's own "occurrence is not defect, find out deterministically" framing, which does not require sweeping every harmless site under `libfaketime` (the task explicitly warns against sweeping the full suite under many clocks for exactly this reason — cost without signal).
- Production wall-clock reads outside the test suite (e.g. `app/services/tick_ingest.py`'s own `datetime.now()` calls for stale-bucket flushing, noted in passing while tracing `test_tick_ingest.py`) were read but not independently audited — out of scope; this task covers test-suite defects, not a production wall-clock audit.
- No new regression tests were added, because nothing was found to regress-test against.

## 6. Verification of this delivery

Full suite re-run once, at the real natural clock, after the audit (a no-regression *count* check — no controlled-clock re-run was needed since zero files changed):

```
cd backend
setsid nohup python3 -m pytest -q -p no:cacheprovider tests > full_suite.log 2>&1 &
# followed with bounded polls, never a blind sleep
```

Result: **776 passed, 0 failed, 786.25s** (the mid-run gap in this sandbox's own logs — Postgres briefly went down between tool turns and was restarted before this run — did not affect the result; the run reported here is a single, complete, successful invocation). Matches the 776-passed baseline #149–#155 have all reported; no regression.

`diff -rq` against a fresh clone pulled immediately before packaging: only `docs/decisions/confirmed-decisions.md` (appended), `docs/decisions/INDEX.md` (appended), `CHANGES.md` (replaced), `TESTING.md` (replaced) differ. Nothing under `backend/` or `frontend/`, no test file, no existing decision entry edited.

Decision-number check, run immediately before the entry was written: `INDEX.md` last row **#155**, `confirmed-decisions.md` tail **#155**, archive files `001-060 … 122-133` (unchanged) — all agree; next free number **#156**.
