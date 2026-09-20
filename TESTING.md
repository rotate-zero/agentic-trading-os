# TESTING — Backtest replay timing investigation (decision #155)

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
