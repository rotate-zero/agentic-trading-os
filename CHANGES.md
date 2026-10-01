<!-- BEGIN DELIVERY SECTION: position-monitor-engine-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `position-monitor-engine-test-waits`

Based on `main` `26ba01b` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, schema, migration or frontend file was edited**, and
`backend/tests/test_simulated_eod_integration.py` was not touched or run (owned by another session). **No new decision
number:** nothing new is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched. The only code
file changed is `backend/tests/test_position_monitor_engine.py`.

- **Problem.** Nine EventBus-driven tests in this file synchronized with a fixed `await asyncio.sleep(0.1)` (the
  idempotency test twice). Under a slow hand-off the positive tests asserted before the intent existed, and the three
  "nothing happened" assertions (session-close event, unheld symbol, no second intent) could pass without the published
  inputs ever having been processed.
- **Edited** `backend/tests/test_position_monitor_engine.py` (new test-local helpers; no production seam added):
  - `_wait_until(predicate, description, ...)` — polls (5 ms) for a positive signal up to a 5 s failure ceiling and
    reports what was observed on timeout.
  - `_wait_for_intents(monitor, count, symbol=None)` — bounded wait on the public `get_exit_intents()`.
  - `_settle(bus, monitor, *envelopes)` — publishes the envelopes and returns only once they were fully processed:
    (1) a temporary wildcard probe on the real `EventBus` records each envelope (by object identity) once its lane has
    dispatched it, which also proves the monitor's synchronous subscriber ran, including for an unheld symbol it drops
    before queuing; then (2) `monitor._queue.join()` waits for the worker to finish everything the subscriber enqueued.
    The probe is unsubscribed in `finally`.
  - **Tests expecting an intent** (stop long, target short, tie, filter-by-symbol, multiple positions, first half of
    idempotency) wait with a bound for the expected number of intents before asserting details. The tie and
    multiple-positions tests also run `_settle` first, because their assertions are exact ("one intent, stop";
    "only the tight-stop position").
  - **Tests expecting no intent / no second intent** (session-close events, unheld symbol, idempotency second half)
    call `_settle` and assert absence only afterwards.
- **Unchanged.** The real `EventBus`, the fake position reader, the fixed `MarketClock`, the pure `_evaluate` tests and
  every substantive assertion (stop/target reason and trigger price, EX-8 stop-wins-tie, no `eod_flatten` from events,
  idempotent single intent, unheld-symbol and no-stop/target absence, symbol filter, independent positions). No
  assertion was removed or weakened and none was added.
- **Uncovered product defect:** none found.
- **Reliable completion signals that do not exist (reported, not changed).**
  1. `EventBus` has no public flush/idle signal; `queue_depths()` reaches 0 while a handler is still running. `_settle`
     therefore uses a wildcard probe, and `PositionMonitor` has no public "queue drained" signal, so it reads
     `monitor._queue` (the same private signal `test_position_monitor_eod.py` uses). If the subscriber ever stopped
     being synchronous (offloaded or awaited), stage (1) would no longer imply it had enqueued and the barrier would
     weaken silently; a delay injected into the subscriber itself demonstrates this.
  2. There is no positive "evaluated, nothing to do" signal for a processed event, so absence is asserted after the
     input is known processed, not after a branch is known taken.
  3. Idempotency compares `second == first` by value. A re-registered intent with identical fields would not be seen;
     the existing assertion is kept as-is (a latch-removal mutation is still caught because the later 200.0 tick yields
     a different reason).
<!-- END DELIVERY SECTION: position-monitor-engine-test-waits -->

<!-- BEGIN DELIVERY SECTION: execution-engine-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `execution-engine-test-waits`

Based on `main` `cbf3735` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, schema, migration or frontend file was edited**, and
`backend/tests/test_main_execution_pipeline.py` was not touched (owned by another session). **No new decision number:**
nothing new is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched. The only code file
changed is `backend/tests/test_execution_engine.py`.

- **Problem.** Nine orchestration tests in this file synchronized with fixed `asyncio.sleep` guesses: eight with
  `sleep(0.1)` (the duplicate test twice) and `test_critical_lane_not_blocked_by_slow_venue_call` with `sleep(0.05)`,
  `sleep(0.1)` and a final `sleep(1.0)`. Under slow ledger commits the positive tests asserted before the order was processed, and the two "nothing
  happened" tests (`close` dropped, malformed ID dropped) could pass without the input ever having been handled.
- **Edited** `backend/tests/test_execution_engine.py` (new test-local helpers; no production seam added):
  - `_wait_until(predicate, description, ...)` — polls (5 ms) for a positive signal up to a 5 s bound and fails with
    what was observed on timeout.
  - `_track_processing(engine)` — wraps that engine **instance's** `_process_one` and records the `order_id` of every
    queued `OrderApproved` the worker has finished handling (returned or raised). It is the positive "this exact input
    was processed" signal for tests whose expected outcome is nothing.
  - `_wait_order_processed(bus, processed, order_id, count=1)` — barrier: waits for the engine to finish `count`
    item(s) for `order_id`, then joins both bus lanes. `asyncio.Queue` counts an envelope finished only after every
    subscriber returned, so any `OrderStatusChanged` the engine published has reached its subscribers.
  - **Positive tests** (happy path, authorization gate, no venue, mode not supported, venue rejection ack) wait for the
    expected ledger update or published event, then run the barrier, then make the unchanged assertions. The happy-path
    "no `OrderStatusChanged`" and the mode-not-supported "never routed" assertions now run only after the barrier.
  - **Duplicate test.** Waits for the first delivery to be fully processed, publishes the duplicate, and waits until
    the engine has finished **two** items for that ID before asserting one venue call and one ledger row.
  - **Absence tests** (`close` dropped, malformed ID dropped). Assert absence only after the engine has finished that
    exact input and the bus is flushed.
  - **`_FakeVenue`.** The unused-elsewhere `delay` parameter is replaced by an optional `release` event plus an
    always-present `entered` event set when `place_order()` is reached; with `release`, placement blocks until the test
    sets it.
  - **`test_critical_lane_not_blocked_by_slow_venue_call`.** Waits for `entered` (placement provably in flight),
    asserts `release` unset and no ledger status update, publishes `PlanRejected`, waits for its delivery, then asserts
    it was delivered **while the venue was still blocked** and within 0.5 s of publishing (the prompt-delivery bound
    is kept, now measured from the publish call). Then it sets `release`, waits for the ledger `submitted` update
    (replacing the final `sleep(1.0)`), and `finally` sets `release` again before `engine.stop()` / `bus.stop()` so
    teardown cannot hang on a blocked venue call.
- **Unchanged.** Every substantive assertion (ledger rows and status updates, event payload reasons, one venue call for
  a duplicate, `place_order_calls == []`, `PlanRejected` delivery and its 0.5 s bound) and the
  `test_execution_engine_package_imports_no_concrete_broker_module_at_module_scope` test. No assertion was removed or
  weakened. Added assertions are limited to the critical-lane test (venue still blocked, ledger untouched while
  blocked, `submitted` update after release).
- **Uncovered product defect:** none found. The barrier relies on private attributes (`engine._queue`, `bus._critical_queue`,
  `bus._normal_queue`, `engine._process_one`); the file already reads engine privates (`_venue_provider`).
- **Observed, not changed (related follow-up only).** `ExecutionEngine.stop()` is a poison-pill drain that awaits the
  worker with no timeout, so a venue `place_order()` that never returns would hang shutdown. The critical-lane test
  therefore releases its fake venue in `finally`. No production change was made.
<!-- END DELIVERY SECTION: execution-engine-test-waits -->

<!-- BEGIN DELIVERY SECTION: main-pipeline-restart-rollback-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `main-pipeline-restart-rollback-test-waits`

Based on `main` `b0a09ad` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, schema, migration or frontend file was edited**, and
`backend/tests/test_execution_engine.py` was not touched (owned by another session). **No new decision number:**
nothing new is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched. The only code file
changed is `backend/tests/test_main_execution_pipeline.py`.

- **Problem.** Two tests in this file drove the real FastAPI lifespan but synchronized with fixed `asyncio.sleep`
  guesses: `test_orphaned_submitted_order_is_expired_on_restart_and_pipeline_resumes` (0.3 s ending the first
  lifespan, 0.3 s before checking resumed operation) and `test_partial_startup_rolls_back_before_serving_requests`
  (0.2 s before its absence assertions). With 0.4 s injected per DB commit the unmodified restart test failed (see
  `TESTING.md`); the rollback test's absence checks could pass without the probe events ever having been dispatched.
- **Edited** `backend/tests/test_main_execution_pipeline.py`, reusing the file's existing `_wait_for` helper
  (bounded, 10 s, reports what was observed on timeout):
  - **Restart test, process #1.** Before the first lifespan ends, waits until the first Trade is `approved` and its
    entry Order is `submitted`. Ending earlier could leave no order to orphan.
  - **Restart test, process #2.** After publishing the second price and Opportunity, waits until a second Trade row is
    committed **and** a verdict event (`OrderApproved` or `PlanRejected`) has been published for it. Only then does
    the unchanged `ORDER_APPROVED in types` assertion run, so a pipeline that did not resume fails on the assertion
    with the observed events, not on a timer.
  - **Rollback test.** New local `bus_idle()` barrier: both bus lanes have `_unfinished_tasks == 0`. The bus calls
    `task_done()` only after every subscriber for an envelope has returned, so this proves all published envelopes are
    fully dispatched. It is waited (1) right after entering the lifespan, so the Opportunity injected into the normal
    lane while startup was failing has settled before any assertion, and (2) after publishing the probe price and
    Opportunity, together with an identity check on a wildcard-subscribed recorder proving those exact two envelopes
    were dispatched. The trade/order absence assertions run only after (2).
- **Unchanged.** The real `TestClient` lifespan, the restart (`_reset_singletons`, fresh venue) and rollback paths
  (`PositionMonitor.start` failure injection) and every substantive assertion. No assertion line was removed or
  weakened; the diff removes exactly the three sleep lines, the rollback test's two `bus.publish` calls now publish
  named probe envelopes (same events), and `types` now copies `list(published)`.
- **Cannot be synchronized without production changes (reported only).**
  - `bus_idle()` proves dispatch of what was published, not of events other subscribers derive from them, and it reads
    the bus's private queue attributes (already read by this test for the worker queues).
  - The restart test proves the second Opportunity was *decided and its verdict published*; it does not wait for the
    second entry order to reach the venue, because it never asserted on it. Adding that would extend the test.
  - Process-exit teardown (workers draining on lifespan exit) is awaited by the `TestClient` context manager itself;
    there is no test-visible signal beyond that.
<!-- END DELIVERY SECTION: main-pipeline-restart-rollback-test-waits -->

<!-- BEGIN DELIVERY SECTION: position-monitor-pipeline-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `position-monitor-pipeline-test-waits`

Based on `main` `867846d` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, schema, migration or frontend file was edited**, and
`backend/tests/test_feature_engine.py` was not touched (owned by another session). **No new decision number:**
nothing new is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched. The only code file
changed is `backend/tests/test_main_execution_pipeline.py`.

- **Problem.** `test_position_monitor_places_durable_exit_and_closes_on_later_tick` (both parametrized cases, stop and
  target) drove the real FastAPI lifespan and pipeline but synchronized with five fixed `asyncio.sleep` guesses
  (0.3, 0.3, 0.1, 0.8, 0.4 s). Any slower commit in the entry, fill, exit or close path made it assert on a half-finished
  pipeline. With only 0.2 s injected per DB commit the unmodified test failed (see `TESTING.md`).
- **Edited** `backend/tests/test_main_execution_pipeline.py`:
  - New module helper `_wait_for(predicate, what, *, describe=None, timeout=10.0, interval=0.02)`: polls from the test
    thread (the app runs on the TestClient portal's own loop thread, so polling never blocks the pipeline) and, on
    timeout, fails with what it waited for and what the pipeline had actually reached.
  - Each fixed sleep is replaced by a bounded wait for the milestone the next assertions need:
    1. entry **approved and submitted** (`Trade.decision == "approved"` and entry `Order.status == "submitted"`; the
       Execution Engine places the order at the venue *before* committing `submitted`, so this also means the venue can
       fill it);
    2. entry fill recorded, **position open**, one entry fill **and visible to the Position Monitor**, i.e.
       `world_view_portfolio_reader.get_snapshot()` shows the position (see finding below);
    3. **durable exit request** (`ExitRequest` row), the exit intent listed by `/intelligence/exit-intents`, and the
       **close order `submitted`**;
    4. **position closed** and **`PositionClosed` published**.
- **Unchanged.** The full lifespan, the real event path (`bus.publish` of the price/opportunity envelopes and
  `venue.ingest_tick`, as before), and every assertion, including realized P&L (-150 stop / +250 target), the exact
  `/intelligence/exit-intents` bodies, the `{trade_id}:exit:1` close order, fill and receipt counts, the closed trade
  and the shutdown checks. The diff removes exactly the five sleep lines and no assertion line. No pipeline component is
  called directly; the new waits only read the ledger, the HTTP route and the existing `app.state` reader.
- **Finding that shaped the wait (not a defect, no production change).** A DB `open` position is not yet enough to send
  the trigger tick. `PositionMonitor._on_market_event` reads positions through `PortfolioState.get_snapshot()`, which
  returns `None` until the Portfolio State worker has finished syncing the fill, and a tick seen while it is
  unavailable is logged and dropped, not retried. The old 0.3 s + 0.1 s sleeps covered this implicitly. A throwaway
  variant of the test waiting only on the DB row lost the trigger tick under a 0.15 s Portfolio State read delay and
  timed out waiting for the exit request; the monitor-visible snapshot is therefore part of milestone 2.
- **Not changed (reported only).** Fixed sleeps remain in other tests of this file
  (`test_orphaned_submitted_order_is_expired_on_restart_and_pipeline_resumes` 0.3 s x2,
  `test_partial_startup_rolls_back_before_serving_requests` 0.2 s); they were out of the named scope. They are the
  obvious next conversions, and the new `_wait_for` is reusable for them.
<!-- END DELIVERY SECTION: position-monitor-pipeline-test-waits -->

<!-- BEGIN DELIVERY SECTION: feature-engine-cold-start-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `feature-engine-cold-start-test-waits`

Based on `main` `91a3323` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, schema, migration or frontend file was edited**, and
`backend/tests/test_main_execution_pipeline.py` was not touched (owned by another session). **No new decision number:**
nothing new is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched. The only code file
changed is `backend/tests/test_feature_engine.py`.

- **Problem.** Two cold-start tests used fixed sleeps in both halves of the restart: `asyncio.sleep(0.3)` before
  stopping the first `CandleRecorder` (guessing its write-behind writer had committed the candles) and
  `asyncio.sleep(0.2)` after starting the fresh `FeatureEngine` (guessing its thread-offloaded compute had published).
  Too-short sleeps stop the recorder before rows land (the fresh engine then backfills less history) or assert on a
  partial event list.
- **Edited** `backend/tests/test_feature_engine.py`, reusing the file's existing helpers (no new helper):
  - `test_aggregated_timeframe_backfills_prior_bars_on_cold_start`: `_wait_until_candles_persisted(ticker, expected_count=10)`
    before `recorder.stop()`; after feeding the third 5m bucket, `_wait_until` five 1m results **plus** the 5m
    bucket-close result, so the exactly-one-5m assertion runs after the worker handled every fed candle.
  - `test_vwap_backfills_from_persisted_history_on_cold_start`: `_wait_until_candles_persisted(ticker, expected_count=2)`
    before `recorder.stop()`; `_wait_until(len(received) >= 1)` before asserting.
- **Unchanged.** The restart shape (real recorder, stop, fresh engine, same bus), every count, close, `sma_3` and VWAP
  assertion. The diff removes four sleep lines and no assertion line.
- **Not changed (reported only).** Other fixed sleeps remain in this file (e.g. the remaining `asyncio.sleep(0.1/0.2)`
  sites and `test_gap_backfills_regular_open_on_cold_start`'s `0.2` pair). Suggested next conversions.
<!-- END DELIVERY SECTION: feature-engine-cold-start-test-waits -->

<!-- BEGIN DELIVERY SECTION: daily-levels-test-cleanup (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `daily-levels-test-cleanup`

Based on `main` `91791ed` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, Feature Engine behavior, API contract, schema, migration or frontend file was edited**, and
`test_feature_engine.py` was not touched. **No new decision number:** nothing new is decided; `INDEX.md`,
`confirmed-decisions.md` and the archive list are untouched. The only code file changed is
`backend/tests/test_daily_levels.py`.

- **Problem 1 — leaked rows.** The `_clean_daily_levels_symbol` fixture only deleted rows *before* a test and said, in
  its own docstring, that leaving fresh rows after a passing run was fine. Every run therefore left its `symbols` and
  `daily_levels_state` rows behind: five `__TEST_DL_*__` symbols (and eight state rows) were present in a freshly migrated
  database after one run of the unmodified file. Nothing cleaned up after a failure either.
- **Problem 2 — fixed sleeps.** Six tests published a candle and then asserted after `asyncio.sleep(0.05)` or `0.1`,
  guessing how long the EventBus, the Feature Engine's serial worker and its thread-offloaded provider fetch + DB
  persistence take.
- **Edited** `backend/tests/test_daily_levels.py`:
  - *Teardown.* `_delete_daily_levels_rows_for` is replaced by `_delete_daily_levels_rows_for_tickers(tickers)`: one
    transaction that deletes `daily_levels_state` rows for the given tickers' symbol ids **first**, then the `symbols` rows
    (the table has a composite `(symbol_id, is_backtest)` FK to `symbols`), with rollback on error. Only the listed tickers
    are touched; there is no broad reset. `_count_rows_for_tickers` reports what remains.
  - *Fixture.* `_clean_daily_levels_symbol` is replaced by `_daily_levels_symbols`, a per-test registrar built on the
    context manager `_tracked_daily_levels_symbols`. `track(ticker)` pre-cleans stale rows (so restart-survival cannot
    short-circuit on leftovers from an interrupted run) and records the ticker. Teardown runs in a `finally` — so it runs after
    a failing test body too — deletes exactly the recorded tickers, then **asserts none remain**, so a leak fails loudly
    instead of accumulating. The five DB-touching tests register the exact tickers they use (`__TEST_DL__`,
    `__TEST_DL_LKBK__`, `__TEST_DL_RCN__`, `__TEST_DL_RST__`, `__TEST_DL_FLKY__`); the no-provider test registers
    `__TEST_DLNOPRV__` so "leaves nothing behind" is verified rather than assumed.
  - *Bounded waits.* New private helper `_wait_for_features_updated(received, expected_count, *, what, timeout=5.0)`, the
    same shape as the one in `test_vwap_ext.py`, wrapping `tests.test_feature_engine._wait_until` (already used by sibling
    test files) and reporting what was actually received on timeout. Every `asyncio.sleep` is replaced by a wait for the
    specific number of `FeaturesUpdated` events the test published candles for. The restart-survival test now also
    subscribes on the first engine and waits for its event before "restarting": that event is published only after the
    daily-levels fetch **and** `_reconcile_and_persist_daily_levels` have committed, so the persisted state the second
    engine must restore is guaranteed to exist. The lookback test gained a `received` subscription for the same reason.
    The flaky-provider test's back-dating session is now closed in a `finally` and its function-local imports were
    removed (the names are already imported at module level). The file contains no `asyncio.sleep` and no longer imports
    `asyncio`.
  - *Two new tests (the cleanup guarantee itself).*
    `test_teardown_deletes_tracked_rows_even_when_the_test_body_fails_and_spares_others` raises inside the tracked context
    after persisting a real level and checks the tracked rows are gone while an untracked "bystander" ticker's rows survive;
    `test_teardown_removes_all_state_rows_for_a_symbol_before_its_symbols_row` persists an active and an archived state row
    for one symbol and checks both and the parent are deleted without an FK violation. Both clean up their own tickers
    (`__TEST_DL_TDF__`, `__TEST_DL_KEEP__`, `__TEST_DL_FKO__`) in `finally`.
- **Assertions preserved verbatim.** Clustering math (tier 1 is untouched), the once-per-day `1d` fetch count, level
  strength/count/price/`level_id` shape, lookback re-clustering and clamping, identity carry-forward / archive / mint,
  restart-survival (poisoned provider never called for `1d`, same `level_id` and price, event payload `level_id`), and the
  failure-retention check (`flaky.calls == 2`, one level still published after the failed refetch). Waits only wait for
  arrival; they never replace an assertion.
- **Not changed (reported only).** `asyncio.sleep(0.x)` waits remain in `test_feature_engine.py` and other test files; they
  were out of scope. The first engine in the restart test and the flaky test still assume the premarket 1m fetch is
  harmless (unchanged behavior, noted in the existing comments).
<!-- END DELIVERY SECTION: daily-levels-test-cleanup -->

<!-- BEGIN DELIVERY SECTION: vwap-ext-test-bounded-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `vwap-ext-test-bounded-waits`

Based on `main` `4137a16` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, indicator behavior, API contract, schema, migration or frontend file was edited**, and
`test_feature_engine.py` was not touched. **No new decision number:** nothing new is decided; `INDEX.md`,
`confirmed-decisions.md` and the archive list are untouched. The only code file changed is
`backend/tests/test_vwap_ext.py`.

- **Problem.** Five of the six tests in `test_vwap_ext.py` still published candles and then asserted after a fixed
  `asyncio.sleep(0.1)`, `0.2` or `0.3`, guessing that the EventBus, the Feature Engine's serial worker (thread-offloaded
  compute, plus a cold-start history read on a symbol's first candle) and `CandleRecorder`'s write-behind writer had all
  finished. The sixth test (`..._identical_across_1m_and_5m_featuresets_...`) was already converted to a bounded wait by
  `feature-engine-test-featureset-wait`; it is unchanged. After this change the file contains **no** `asyncio.sleep`.
- **Edited** `backend/tests/test_vwap_ext.py`:
  - *Four event-count tests* (`..._present_during_premarket_...`, `..._continues_across_the_930_boundary_...`,
    `..._resets_at_next_trading_day_...`, `..._absent_after_hours_...`): the sleep is replaced by a bounded wait (5 s) for
    the exact number of `FeaturesUpdated` events the test publishes candles for (1, 2, 3 and 2 respectively).
  - *Cold-start test* (`..._backfills_pre_market_history_on_cold_start`), both halves: before the recorder is stopped and
    the fresh engine started it now waits for the pre-market row to actually be **persisted**
    (`_wait_until_candles_persisted(ticker, expected_count=1)`), then, after publishing the 9:30 bar to the fresh engine,
    waits for that engine's own `FeaturesUpdated`.
  - *One new private helper* in the test file, `_wait_for_features_updated(received, expected_count, *, what, timeout=5.0)`:
    a thin wrapper over the existing `_wait_until` that, on timeout, re-raises with what was actually received
    (symbol / timeframe / `candle_ts` of each event) so a failure is diagnosable instead of a bare "condition not met".
    `_wait_until` and `_wait_until_candles_persisted` are imported from `tests.test_feature_engine`, the module this file
    already imports its other helpers from. The unused `asyncio` import was removed.
- **What the waits do and do not do.** A wait only blocks until the events (or the persisted row) *arrive*. Every existing
  assertion is kept verbatim and still runs afterwards: the exact `len(received)` checks, every `vwap`, `vwap_ext` and
  `session_volume_ext` value, the day-2 reset, the after-hours absence, the 1m/5m parity, and the cold-start
  `mean(50, 150) == 100.0` check. A wrong or missing result still fails: if an event never arrives the wait times out
  with a named message; if it arrives with the wrong value the original assertion fails (verified by mutation checks in
  `TESTING.md`).
- **Not changed (reported only).** `asyncio.sleep(0.x)` publish-then-assert waits remain in `test_feature_engine.py` and
  other test files (e.g. `test_daily_levels.py`); they were out of scope here and are the suggested next conversions.
  Five `__TEST_DL_*__` symbols from `test_daily_levels.py` were observed left in the sandbox test database after its
  runs (not touched, not related to this change).
<!-- END DELIVERY SECTION: vwap-ext-test-bounded-waits -->

<!-- BEGIN DELIVERY SECTION: market-clock-next-session-boundary (backend + tests + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `market-clock-next-session-boundary`

Based on `main` `93f6d2a` (re-checked against `origin/main` before packaging: no newer commits). **No new decision
number:** this corrects `MarketClock.next_session_boundary()` to behave as decisions #92 and #44's contract and the
verified 2026-2028 calendar already imply; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched.
No Context Engine, scheduler, API, schema, migration or frontend file was edited; **no caller defect was found.**

- **Defect.** `next_session_boundary()` built every window start/end as same-day wall-clock times and only walked
  forward once *none* were left today. On a weekend or verified holiday (session CLOSED all day) it therefore still
  returned same-day times such as 04:00/09:30/…/20:00 that are not session changes, and on a configured half-day it
  returned 14:30/16:00/20:00 even though the session closes at 13:00 and never reopens, never returning the actual
  13:00 close.
- **Fix** (`backend/app/core/market_clock.py`): a new private `_boundary_times(d)` returns the real session-state
  change times for a date, mirroring `current_session()`: weekend or `is_holiday()` -> none; `is_half_day()` -> 04:00,
  09:30, 11:30, 13:00; otherwise -> 04:00, 09:30, 11:30, 14:30, 16:00, 20:00. `next_session_boundary()` now walks day
  by day and returns the first boundary strictly after `ts`; after a day's last boundary the next one is the next
  trading day's 04:00 pre-market open. Normal-day boundaries, the strictly-after rule (exactly at a boundary returns
  the following one), clock-zone timezone-aware results, and the naive-`ts` `ValueError` are unchanged. One private
  constant `_HALF_DAY_CLOSE = time(13, 0)` was added; the existing literal `time(13, 0)` uses elsewhere in the file
  were left alone.
- **Caller impact (`ContextEngine._loop`, `backend/app/context_engine/engine.py`, unchanged).** The loop sleeps until
  `next_session_boundary()` and re-evaluates. It no longer wakes at meaningless times on closed days, and on a
  half-day it now wakes at the 13:00 close (previously it kept the stale pre-close state until 14:30). The loop's
  `datetime.now(boundary.tzinfo)` / `max(..., 0)` arithmetic is correct for the aware result.
- **Unverified years (not extended, not claimed).** `_VERIFIED_CALENDAR_YEARS` is still `{2026, 2027, 2028}`;
  `has_calendar_for_year(2029)` is still `False`. For an unverified year `is_holiday()`/`is_half_day()` still answer
  `False` and never raise, so `next_session_boundary()` there skips weekends only: e.g. from Fri 2028-12-29 20:00 ET
  it returns Mon 2029-01-01 04:00 although 2029-01-01 is in reality an NYSE holiday. That limit is the same one the
  earlier `market-clock-2027-2028-coverage` note recorded; callers needing a trusted answer still check
  `has_calendar_for_year()` first.
- **Docs:** `docs/architecture/system-design.md` §4.3 (signature takes optional `ts`; contract paragraph plus a
  data-flow/internal-flow diagram including the Context Engine loop) and `docs/architecture/execution-engine-design.md`
  (the calendar diagram's "next_session_boundary (unchanged ...)" note corrected). Earlier `CHANGES.md` history is
  left as written.
- **Tests** (`backend/tests/test_market_clock.py`, 8 new): covered holiday, weekend, normal-day sequence, half-days
  2026-11-27, 2026-12-24, 2027-11-26, 2028-07-03 and 2028-11-24, covered year crossings (2027->2028 and 2026->2027),
  timezone-aware/UTC-input/DST results, a minute-by-minute oracle over six covered windows, and the 2029 unverified
  behavior. Every case checks the session immediately before and at the returned boundary. Results in `TESTING.md`.
<!-- END DELIVERY SECTION: market-clock-next-session-boundary -->

<!-- BEGIN DELIVERY SECTION: eod-partial-fill-test-order (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `eod-partial-fill-test-order`

Based on `main` `66426eb` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, schema, migration or frontend file was edited**, and neither the Feature Engine nor
the VWAP test files were touched. **No new decision number:** nothing new is decided; `INDEX.md`,
`confirmed-decisions.md` and the archive list are untouched. **No production partial-fill defect was found.**

- **Root cause: an unordered read-back in the test, not a synchronization race and not a production defect.**
  `rows(pid)` in `backend/tests/test_simulated_eod_integration.py` loaded the fills with `select(Fill).where(...)` and
  **no `ORDER BY`**; `test_partial_venue_fills_keep_one_close_until_real_remaining_fill` then asserts
  `[f.qty for f in fills] == [3, 2]`. Without `ORDER BY` PostgreSQL returns rows in physical heap order, which is not
  insertion order once earlier cleanup deletes and vacuum/page pruning free slots: the second fill (qty 2, higher
  `ledger_seq`) can land in an earlier slot than the first (qty 3). The failing full-suite run reported
  `assert [2, 3] == [3, 2]` **after** `Position.status == "closed"` and the one-order/two-fill checks had already passed,
  i.e. every downstream step had completed and only the *order* of the list was wrong.
- **The queue joins are sound.** The chain was traced in source and exercised under injected stalls (see `TESTING.md`):
  `SimulatedVenue.ingest_tick` -> `_apply_fill` -> `_dispatch` calls `ExecutionEngine._on_venue_update`, which
  `put_nowait`s onto the engine queue **synchronously** (so `engine._queue.join()` cannot return before the work exists);
  the engine item is not `task_done` until `record_fill` has committed and `OrderFilled` is in the EventBus critical
  queue; the critical consumer calls Portfolio State's `_on_event`, which `put_nowait`s before the bus `task_done`; and
  Portfolio State's item is not `task_done` until `_synchronize` has committed the fill and the position. The test's
  `engine -> bus critical -> portfolio` join order therefore covers all downstream work. No fixed sleep or extra wait
  was added because none is needed.
- **Edited** `backend/tests/test_simulated_eod_integration.py`: in the shared `rows()` helper the fills query gains
  `.order_by(Fill.ledger_seq)` plus a three-line comment. `ledger_seq` is the `fills` primary key and, per
  `app/models/execution_ledger.py`, the strictly-monotonic ledger order. The assertions are unchanged: one close order,
  fills `[3, 2]`, position closed once. The orders query already had `ORDER BY Order.id`. No other test in the file
  asserts multi-fill order (the others check `fills == []`, a single fill, or counts), so the change is behavior-neutral
  for them.
- **Not changed (reported only):** (1) `ExecutionEngine._worker_loop` runs `_service_exits()` on a 0.5 s idle timeout
  outside any queue, so it can overlap a test's inline `await engine._service_exits()` and is not covered by
  `queue.join()`. Stalling each of 11 steps in the chain by 0.7 s did **not** break this test, so it is not the cause
  here; it is a latent shape worth knowing about if a future EOD test shows a different flake. (2) Other tests that read
  multiple rows without `ORDER BY` were not audited outside this file.
<!-- END DELIVERY SECTION: eod-partial-fill-test-order -->

<!-- BEGIN DELIVERY SECTION: feature-engine-test-featureset-wait (backend tests + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `feature-engine-test-featureset-wait`

Based on `main` `41aafc0` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, indicator behavior, API contract, schema, migration or frontend file was edited**, and neither
`conftest.py` nor `test_simulated_eod_integration.py` was touched. **No new decision number:** nothing new is decided;
`INDEX.md`, `confirmed-decisions.md` and the archive list are untouched.

- **Root cause: a timing assumption in the tests, not a computation defect.** Both tests published five 1m candles and
  then asserted after a fixed `asyncio.sleep(0.1)`. The Feature Engine's worker is strictly serial and does a
  thread-offloaded compute (`asyncio.to_thread(self._compute_one, ...)`, with a cold-start history read on the first
  candle of a symbol) per candle, then publishes through the EventBus. Five candles must yield six `FeaturesUpdated`
  events (five 1m + the 5m bucket-close set). On a loaded machine the 100 ms guess can expire first, so the assertions
  see a partial list: `kama_2` is not yet in the latest 1m set / no 5m entry exists (`KAMA` test), or
  `set(by_timeframe) == {"1m"}` instead of `{"1m", "5m"}` (`vwap_ext` test).
- **Edited** `backend/tests/test_feature_engine.py` (`test_kama_only_computed_for_its_configured_timeframe`) and
  `backend/tests/test_vwap_ext.py` (`test_vwap_ext_is_identical_across_1m_and_5m_featuresets_on_the_same_close`): the
  fixed sleep is replaced by the file's existing bounded poll `_wait_until` (5 s timeout) on the exact condition the
  assertions need — at least five `1m` events and one `5m` event received. `test_vwap_ext.py` imports `_wait_until` from
  `tests.test_feature_engine`, the same module it already imports its other helpers from. **No new helper, no
  production change, every timeframe and value assertion is unchanged.** A real defect still fails: if the 5m set is
  never published the wait times out with a named message instead of passing.
- **Not changed (reported only):** other tests in these and neighboring files still use fixed `asyncio.sleep(0.1)`
  waits after publishing candles (see `TESTING.md`); `test_simulated_eod_integration.py::test_partial_venue_fills_keep_one_close_until_real_remaining_fill`
  failed once in a post-change full run for an unrelated reason (fill row order, see `TESTING.md`).
<!-- END DELIVERY SECTION: feature-engine-test-featureset-wait -->

<!-- BEGIN DELIVERY SECTION: outcome-status-repeatable-read-test (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `outcome-status-repeatable-read-test`

Based on `main` `b271733` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, response shape, population rule, schema, migration or frontend file was edited.**
`test_execution_outcome_status_recorder_integration.py` (Claude 1's file) was not touched. **No production defect was
found:** the route's `REPEATABLE READ` promise holds, so no route fix was needed. **No new decision number:** nothing new
is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched.

- **Edited test file** `backend/tests/test_execution_outcome_status_route.py`: one new fixture, one helper and one test
  parametrized over two concurrent changes (2 test cases; the module goes 22 -> 24). Plus `event` added to the existing
  `sqlalchemy` import. No existing test or helper was changed.
- **What it proves:** `GET /intelligence/execution-outcome-status` documents that its aggregate and its recent-trades
  list are read in one `REPEATABLE READ` transaction. PostgreSQL takes that snapshot at the first statement, so the test
  pauses the route's worker-thread read *after the aggregate has executed and before the list query*, commits a change over
  an independent connection, then lets the route finish:

  ```
  test (event loop)                  route worker thread                independent connection
  -----------------                  -------------------                ----------------------
  seed 2 trades, read DB baseline
  GET (asyncio task) ------------->  _fetch_execution_outcome_status
                                      aggregate SELECT executes
                                      -> hook: aggregate_done.set()
  await aggregate_done  <-----------  hook blocks on release.wait()
  assert executed == ["aggregate"]
  commit change  --------------------------------------------------->  INSERT new eligible newest trade
                                                                         or UPDATE NULL -> 'recorded'
  release.set()  ------------------>  hook returns; list SELECT runs
  response  <-----------------------  counts + list  (one snapshot)
  assert counts == pre-change baseline AND list excludes/does not reflect the change
  fresh unpaused GET: change IS now visible (proves the commit was real)
  ```

- **Two variants:** `insert` (a new eligible, newest trade: under `READ COMMITTED` it would top the list while the counts
  omit it) and `transition` (an existing NULL-status trade moved to `recorded`: under `READ COMMITTED` the list would show
  `recorded` while the counts still say pending). Both assert counts equal the pre-change database baseline, the list is
  the same snapshot, and a fresh read afterwards does see the change.
- **Pause mechanism (no sleeps, no global patch):** the route is pointed at a **private engine** through the same
  `monkeypatch` of `db_session_module.SessionLocal` the file already uses, and an `after_cursor_execute` listener is
  attached to **that engine only**. It fires once, on the first `FROM trades` statement, and blocks on a
  `threading.Event` with a 5 s timeout (a stuck test fails with `TimeoutError`, it does not hang). The listener also
  records `["aggregate", "list"]` so the test asserts the pause really sat between the two statements. Teardown always
  releases the event, removes the listener and disposes the engine, even on failure.
- **Independent writer:** uses the test module's own `SessionLocal` (the shared engine, untouched by the monkeypatch) with
  `SET LOCAL lock_timeout = '3s'` on the update, so a route-side lock would fail fast instead of hanging.
- **Isolation:** expectations are read straight from the database (the route cannot be used for a baseline because the
  hook would pause it), so they are deltas that tolerate unrelated qualifying rows. Every row carries `_STRATEGY_NAME`;
  the existing autouse `_cleanup` fixture removes only those rows.
- **Known limits:** the test pins the snapshot point to the aggregate statement (PostgreSQL's first-statement snapshot),
  which is the route's own documented shape; it does not cover a third concurrent change type (e.g. a trade leaving the
  population) or concurrent writers during the *first* statement.
<!-- END DELIVERY SECTION: outcome-status-repeatable-read-test -->

<!-- BEGIN DELIVERY SECTION: outcome-recorder-event-path-integration (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `outcome-recorder-event-path-integration`

Based on `main` `55e8678` (re-checked against `origin/main` before packaging: no newer commits). Test and docs only:
**no production code, API contract, schema, migration or frontend file was edited, and no existing test was edited.**
No production defect was found. **No new decision number:** nothing new is decided; `INDEX.md`,
`confirmed-decisions.md` and the archive list are untouched (latest number is still 186) and decision #186 is not altered.

- **New test file** `backend/tests/test_outcome_recorder_event_path_integration.py` (1 test, real PostgreSQL). The
  existing `test_execution_outcome_status_recorder_integration.py` calls `OutcomeRecorder.record_trade()` directly; this
  test proves the event-driven wake-up path (#186: `PositionClosed` only wakes the worker; the ledger supplies the facts):

  ```
  seeded closed trade        EventBus (critical lane)         OutcomeRecorder
  (tests.test_outcome_  ---> publish(PositionClosed) -------> _on_close -> _enqueue("close", trade_id)
   recorder._seed)                                                 |
                                                                   v  worker task: record_trade(trade_id)
                                  GET /intelligence/            _close_candidate -> _record_locked
                                  execution-outcome-status <--- trades.outcome_status='recorded'
                                  (httpx.ASGITransport)         + strategy_outcomes row + trades.outcome_id
  ```

  Sequence: start a real `EventBus` and a real `OutcomeRecorder` **before any test trade exists** (so the startup scan
  cannot be what records it) -> seed the closed eligible trade -> route shows it `pending` (NULL status) and the worker
  idle -> publish a valid `PositionClosed` (built from the seeded position) -> the worker returns `recorded` -> the route
  shows `recorded` +1 with `outcome_id` equal to both `trades.outcome_id` and the single real `strategy_outcomes` row ->
  publish a **duplicate** `PositionClosed` -> the worker returns `skipped`, still one outcome row, same `outcome_id`,
  route counts unchanged.
- **Bounded synchronization, no fixed sleeps:** `record_trade` on the recorder *instance* is wrapped so each real worker
  result lands on an `asyncio.Queue`; the test awaits it with `asyncio.wait_for(timeout=10)`. The wrapper calls the real
  method and returns its real result.
- **Isolation:** the recorder's startup scan is a database-wide query and would otherwise record any unrelated qualifying
  trade in a shared database. The instance's `_pending_rows` is therefore wrapped so the real scan still runs (asserted:
  exactly once, before seeding) but its rows are not acted on, and `sweep_interval_seconds=3600` keeps the sweeper out.
  Counts are deltas against a route baseline. Cleanup deletes only the seeded `trade_id`s' rows (children first; the
  `trades.outcome_id` link is cleared before outcome rows go). The recorder and then the bus are stopped in a `finally`
  with a bounded `stop()`.
- **Known limits:** the fixture is the recorder suite's hand-seeded ledger, not an execution run (authorizer -> venue ->
  portfolio is out of scope); the startup scan and sweep recovery paths are not exercised here; the duplicate is published
  after the first record completed (coalescing of a duplicate that arrives while still queued is not asserted); exit
  snapshots are the real engine's cold-start result, not asserted.
<!-- END DELIVERY SECTION: outcome-recorder-event-path-integration -->

<!-- BEGIN DELIVERY SECTION: execution-outcome-status-recorder-integration (backend test + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `execution-outcome-status-recorder-integration`

Based on `main` `fc8e2c9` (re-checked against `origin/main` before packaging; `main` advanced once during the task from
`7ab2522` and the work was re-verified on the new base). Test and docs only: **no production code, API contract,
schema, migration or frontend file was edited**, and no production defect was found. **No new decision number:**
nothing new is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched (latest number is still
186) and decision #186 is not altered.

- **New test file** `backend/tests/test_execution_outcome_status_recorder_integration.py` (3 tests, real PostgreSQL).
  It closes the gap between two existing suites: `test_execution_outcome_status_route.py` hand-inserts
  `outcome_status` values and never runs the recorder, while `test_outcome_read_path_integration.py` runs the real
  recorder but never calls this route. Here the ledger rows come from `tests.test_outcome_recorder._seed` (reused, not
  rebuilt), statuses are changed by the real `OutcomeRecorder.record_trade()`, and every observation is a real
  `GET /intelligence/execution-outcome-status` call (`httpx.ASGITransport`, no app lifespan).
  - `test_null_status_is_pending_then_recorded_with_real_outcome_link`: a closed eligible trade before recording has
    stored NULL status, appears as JSON `null` and moves `pending` by +1; after `record_trade()` it moves to
    `recorded` (+1, `pending` back to baseline) and the listed `outcome_id` equals both `trades.outcome_id` and the
    one real `strategy_outcomes` row for that trade.
  - `test_permanently_blocked_trade_is_counted_blocked_without_link_or_reason`: a trade without `thesis.evidence`
    is blocked (`evidence_unavailable`, confirmed in the captured log); the route shows `blocked` +1, `outcome_id`
    null, no `strategy_outcomes` row, response keys exactly `counts`/`trades`, and neither the reason code nor the
    word "reason" appears anywhere in the response body.
  - `test_transient_recorder_failure_is_durably_pending_retry_then_recovers`: only
    `record_strategy_outcome_in_session` is injected to raise `SQLAlchemyError` after its insert. The recorder returns
    `pending_retry`; a fresh route request shows `pending_retry` +1, no link, and the failed insert rolled back. With
    the real writer restored, a second `record_trade()` records it and the route shows `recorded` +1 with
    `pending_retry` back to baseline.
- **Isolation:** all count assertions are deltas against a baseline read through the route, so unrelated rows do not
  make the tests brittle. Cleanup deletes only rows the test created, identified by the seeded `trade_id`s (children
  first; the `trades.outcome_id` link is cleared before outcome rows are removed). It deliberately does not reuse the
  recorder suite's `strategy_name`-keyed autouse cleanup.
- **Known limits:** the fixture is the recorder suite's hand-seeded ledger, not an entry-to-exit execution run; the
  blocked case covers one permanent reason (`evidence_unavailable`), not every reason code; the `limit` and ordering
  behavior of the route stays covered by its own suite.
<!-- END DELIVERY SECTION: execution-outcome-status-recorder-integration -->

<!-- BEGIN DELIVERY SECTION: outcome-status-doc-sync (docs + comments only; integrate alongside other sections, do not merge them) -->
# CHANGES — `outcome-status-doc-sync`

Based on `main` `7ab2522` (re-checked against `origin/main` before packaging: no newer commits). Documentation and
schema-description text only: **no API, database, migration, frontend behavior or trading-logic change.** **No new
decision number:** nothing new is decided; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched
(latest number is still 186) and no existing decision entry was edited. This corrects living documents and comments
after the `execution-outcome-status-route` and `execution-panel-outcome-status` deliveries, both already on `main`.

- **`docs/roadmap/phase-roadmap.md`:** the execution "Read surfaces" bullet now lists `execution-outcome-status` and
  names its Execution panel section, "Simulated outcome recording" (simulated `OutcomeRecorder` progress, #186; not a
  live portfolio or a real-money result).
- **`docs/architecture/execution-engine-design.md`:** §6.7.1 K no longer says a UI "is built separately" or shows
  "Execution panel UI (separate parallel task)"; it now says the route's one consumer is the merged §L section, and its
  "backend only" label is scoped to the route. The §6.8 `trades` row now names the route (§K) and panel section (§L) that
  expose `outcome_status`. §L was already accurate and is unchanged. The historical "fourth ledger read route" text
  in §6.6 is left as written.
- **`backend/app/schemas/performance.py` (docstring and `Field` descriptions only; fields, types, defaults, validators
  and population rules untouched, verified by an AST comparison that ignores string constants):**
  - "a future Execution Engine/Position Monitor fill handler will call" `capture_strategy_outcome_snapshots` → its
    callers are the Backtest Runner (#128) and the simulated `OutcomeRecorder` (#186).
  - The D17 paragraph keeps its history (Backtest Runner resolution in #128; the live path open at the time) but no
    longer says in the present tense that the live-path caller does not exist; it adds a dated current-state sentence
    (the `OutcomeRecorder` is that caller for simulated auto trades, via EX-12's nullable-plus-reason route, simulated and
    not paper or real-money) and states that all four snapshots are required for **backtest** rows only.
  - `execution_mode` description: no longer "today's two real callers" / "once a live writer exists"; it names the
    Backtest Runner and isolation tests as users of the default and says the `OutcomeRecorder` passes the fields
    explicitly. `market_state_at_entry` note names the `OutcomeRecorder` as the EX-7 writer.
- **`docs/architecture/strategy-engine-design.md`:** a concise "Current-state correction" blockquote after each of the
  two #137/#162 Live/Backtest diagrams (the ones with the "no Execution Engine" empty state). The diagrams and their
  decision-era meaning are unchanged; the notes point to the as-built note that follows and state that the Live view now
  shows simulated outcomes, not paper or real-money trading.
- **Further drift found and deliberately not fixed** (outside the four approved items): see `TESTING.md`.
<!-- END DELIVERY SECTION: outcome-status-doc-sync -->

<!-- BEGIN DELIVERY SECTION: execution-panel-outcome-status (frontend + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `execution-panel-outcome-status`

Based on `main` `4e40a79` (re-checked against `origin/main` before packaging: no newer commits). Frontend and docs
only. **No new decision number:** a read-only consumer of the `execution-outcome-status-route` backend and decision
#186's recorder; `INDEX.md`, `confirmed-decisions.md` and the archive list are untouched and the latest number is
still 186. **Dependency:** this branch compiles against the agreed contract; the route itself is already on `main`
(`execution-outcome-status-route`, §6.7.1 K) and this delivery was checked against its as-built response, but it
was not run against a live backend (see `TESTING.md`). No backend file was edited.

- **New "Simulated outcome recording" section in the Execution panel** (`ExecutionLifecyclePanel.tsx`,
  `SimulatedOutcomeRecording`), mounted when expanded after "Recent simulated positions" and before the event feed.
  It shows the five counts (Pending, Pending retry, Blocked, Recorded, Other) and a bounded recent list of up to 50
  trades, each with symbol · strategy, the time the trade record last changed, a status label and "outcome linked" /
  "no outcome link" (the outcome id is not printed).
  - **Status display:** `null` → "Pending"; `pending_retry`, `blocked`, `recorded` → their labels; **any other value
    is shown verbatim as `Unexpected status "<value>"`** in the error tone and is never called recorded (this
    includes the literal `"pending"`, which the server counts under Other, so list and counts agree).
  - **Wording:** closed simulated auto trades only, not a live portfolio or a real-money result; pending may still
    be recovered; a blocked reason is in the server logs, not this API; counts cover every such trade while the list
    is the most recently changed; loaded on expansion and Refresh, not a live feed. When the list is shorter than
    the population it prints "Showing the N most recently changed of M".
  - **States and Refresh** follow "Recorded exit requests": loading, error (never shown as empty), empty, populated;
    manual Refresh stays enabled mid-flight; the effect cleanup discards a superseded, late or post-collapse
    response. No polling, no control, no WebSocket merge.
- **`api-client.ts`:** `fetchExecutionOutcomeStatus(limit = 50)` plus wire types
  `ExecutionOutcomeStatusCountsWireShape`, `ExecutionOutcomeStatusTradeWireShape`,
  `ExecutionOutcomeStatusWireShape` (`outcome_status: string | null`, deliberately not a closed union).
- **Panel header comment** updated to list the new snapshot.
- **Docs:** `docs/architecture/execution-engine-design.md` gains §6.7.1 subsection **L** (as-built frontend reader,
  data-flow and internal-flow diagrams, limits). Subsection K (another delivery's text) is untouched.
- **Shared-doc follow-up at merge (not edited here):** `docs/roadmap/phase-roadmap.md` "Read surfaces" bullet and the
  execution-engine-design read-route table can name the route and this section; §K still says "a UI is built
  separately" and "separate parallel task" and can point at §L once both are merged.
- **Known limits:** the row time is `trades.updated_at` (last change), not a close or outcome time; `outcome_id` and
  `outcome_status` are shown as stored and not cross-checked; no paging beyond 50; no per-trade detail.
<!-- END DELIVERY SECTION: execution-panel-outcome-status -->

<!-- BEGIN DELIVERY SECTION: execution-outcome-status-route (backend + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `execution-outcome-status-route`

Based on `main` `2d11108` (re-checked against `origin/main` before packaging: no newer commits). Backend and docs only.
**No new decision number, no migration:** the route is a read of `trades.outcome_status`, whose vocabulary and writer
are already fixed by #186 and `execution-engine-design.md` §6.7.1 D. `INDEX.md`, `confirmed-decisions.md` and the
archive list are untouched.

- **New route `GET /intelligence/execution-outcome-status?limit=50`** (`backend/app/api/routes/intelligence.py`,
  helper `_fetch_execution_outcome_status`). Read-only view of the `OutcomeRecorder`'s progress. Response:
  `{"counts":{"pending","pending_retry","blocked","recorded","other"},"trades":[{"trade_id","symbol","strategy_name","outcome_status","outcome_id","updated_at"}]}`.
  - **Population (fixed, not parameters):** approved, closed, `execution_mode = 'simulated'`, `origin = 'auto'` trades.
  - **`counts` cover the whole population regardless of `limit`.** SQL NULL is `pending`; `pending_retry`, `blocked`,
    `recorded` are their own buckets; any other non-NULL value (including the never-written literal `"pending"`) is
    `other`. `trades` is the bounded recent list, `updated_at` descending then `trade_id` descending, `limit` 1–100
    (default 50, else 422).
  - **`outcome_status` is returned as stored** (NULL stays `null`). **No blocked reason is exposed** (#186 keeps
    reasons in logs). `updated_at` is normalised to UTC.
  - Counts and list are read in one `REPEATABLE READ` transaction; the read runs through `asyncio.to_thread` like the
    sibling `execution-*` routes. It writes nothing, takes no lock, triggers no recovery and changes no recorder
    behavior.
- **Tests:** new `backend/tests/test_execution_outcome_status_route.py` (22 tests, real PostgreSQL 16).
- **Docs:** `docs/architecture/execution-engine-design.md` gains §6.7.1 subsection **K** (as-built rules, data-flow and
  internal-flow diagrams, limits). `TESTING.md` updated.
- **Shared-doc follow-up at merge (not edited here, to avoid colliding with the parallel UI task):**
  `docs/roadmap/phase-roadmap.md` (its "Read surfaces" bullet lists the five existing `execution-*` routes) and the
  execution-engine-design read-route table near line 2456 can name this route once the UI lands.
- **Known limit:** `updated_at` is "last time the trade row changed through the ORM" (close, recorder status write), not
  a close or outcome time, and a raw-SQL re-arm that does not set it will not move the row.
<!-- END DELIVERY SECTION: execution-outcome-status-route -->

<!-- BEGIN DELIVERY SECTION: frontend-world-view-simulated-column (frontend + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `frontend-world-view-simulated-column`

Based on `main` `c91ad01` (contains `fb9462a`; re-checked against `origin/main` before packaging: no newer commits).
Branch `frontend-world-view-simulated-column`. Frontend and docs only. **No new decision number:** this is a
consumer-side wording correction after decision #186; `INDEX.md`, the `confirmed-decisions.md` tail and the archive
list are untouched and the latest number is still 186.

- **`WorldViewSummary` (`InfoTab.tsx`) describes its `is_backtest=false` column accurately.** The column header
  changes from "Live" to "Simulated execution", and the empty message from "No live trades yet." to "No simulated-execution
  trades recorded yet." The subtitle now reads "Simulated execution and backtest, shown separately — … Simulated
  execution is not real-money trading." The Backtest column (label and "No backtest trades yet.") is unchanged and stays
  a separate population.
- **Unchanged on purpose:** the backend's `performance.live` / `performance.backtest` keys and the two
  `is_backtest=False` / `True` queries; `aggregateWinRate`/`aggregateExpectancy`; the Portfolio block; manual Refresh;
  the loading and error rendering; `useWorldView`'s request-id ordering and clear-on-failure behavior. No polling,
  WebSocket, metric, backend, or new view. The wire key stays `live`; only the displayed wording changed.
- **Current-state comments corrected** in `InfoTab.tsx` (new note above `aggregateWinRate`) and `useWorldView.ts`
  (docstring): `performance.live` is the `is_backtest=false` population, and since #186 the simulated `OutcomeRecorder`
  is the only writer of those rows — no paper/live/manual outcome writer exists. Neither file previously claimed that
  no writer exists; the only stale wording was the two display strings above.
- **`docs/architecture/trading-intelligence-architecture.md` §15** (frontend surfacing): states the simulated-execution
  meaning and new labels; the internal-flow diagram now names the two columns. Historical text (decision #154 wording,
  `docs/decisions/archive/134-160.md`) is left as written.
- **Not changed, reported:** the two earlier CHANGES entries that quote "No live trades yet." are history and were kept;
  `api-client.ts`'s World View comments and `schemas/performance.py`'s `execution_mode` description are outside the
  requested files (the latter is already reported as a follow-up by `outcome-read-path-integration`).
<!-- END DELIVERY SECTION: frontend-world-view-simulated-column -->

<!-- BEGIN DELIVERY SECTION: outcome-read-path-integration (backend tests + docs/comments; integrate alongside other sections, do not merge them) -->
# CHANGES — `outcome-read-path-integration`

Based on `main` `fb9462a` (re-checked against `origin/main` before packaging: no newer commits). Branch
`outcome-read-path-integration`. **No new decision number:** this verifies and documents what decision #186 already
built; `INDEX.md`, the `confirmed-decisions.md` tail and the archive list (ends at 184, with 185/186 in the live log)
agree the latest is 186. `OutcomeRecorder`, the `strategy_outcomes` schema and decision #186 are untouched.

- **New test `backend/tests/test_outcome_read_path_integration.py`** (real PostgreSQL). The real
  `OutcomeRecorder.record_trade()` writes two simulated rows from ledger rows seeded by the existing
  `tests.test_outcome_recorder._seed` (reused, not rebuilt — no new execution pipeline fixture); only
  `capture_strategy_outcome_snapshots` is stubbed, as in the recorder's own tests. Trade A has no entry market-state
  snapshot (`engine_cold_start`) but an entry context with `calendar.session = power_hour`; trade B has both entry
  snapshots NULL (`recorder_unavailable`). Two backtest rows (EDT 14:05Z and EST 15:05Z, both 10:05 Eastern; session
  `power_hour` R=-1.0 and `open` R=+2.0) are written through `record_strategy_outcome()`.
  One test asserts all four read paths:
  - `GET /intelligence/strategy-outcomes`: default == `is_backtest=false`; each population contains only its own rows
    and modes; recorder values arrive exactly (price/qty/P&L/R/exit reason, unknown commission stays `null`,
    `signal_confirmed_at` `null`); NULL snapshots are `null` (not `{}`) with the exact `snapshot_missing_reasons`.
  - `GET /intelligence/win-rate-by-hour` and `/expectancy-by-session-type` (scoped by `strategy_name` for exact
    populations): simulated hours match an independent Python ET conversion; backtest EDT/EST rows share hour 10 with
    win rate 0.5; the NULL-context row is its own `session_type = null` group; backtest `power_hour` (-1.0) does not
    leak into simulated `power_hour` (+1.3125).
  - `GET /intelligence/world-view` `performance`: before/after deltas (shared-DB safe) equal the same buckets per
    population, and each population equals the corresponding aggregate route output.
  - Reading wrote nothing (row count and P&L unchanged).
- **Mutation-checked:** dropping the `is_backtest` filter in the aggregates or in the route, bucketing by UTC hour,
  swapping World View's populations, and breaking the session JSONB key each fail the test.
- **No reader defect found**, so no reader behavior changed.
- **Stale current-state claims corrected (comments/docstrings/docs only):** `GET /strategy-outcomes` and
  `GET /win-rate-by-hour` route docstrings (no longer say no live writer / zero live rows / no Execution Engine);
  `performance_queries.py` module docstring; `useBacktestOutcomes.ts` and `BacktestResultsPanel.tsx` comments;
  `trading-intelligence-architecture.md` (said `OutcomeRecorder` "remains unwired" — it is wired in `main.py`);
  `execution-engine-design.md` gains §6.7.1 J with the read-path diagram. Historical decision entries and the
  "Historical" §1 inventory were left as written.
- **Deliberately not changed:** the `execution_mode` field description in `schemas/performance.py` still says a
  "live writer" may not exist and names only Backtest Runner + tests as callers; it is part of the outcome schema
  module, so it is reported as a follow-up rather than edited.
<!-- END DELIVERY SECTION: outcome-read-path-integration -->

<!-- BEGIN DELIVERY SECTION: frontend-performance-panel-refresh (frontend + docs; integrate alongside other sections, do not merge them) -->
# CHANGES — `frontend-performance-panel-refresh`

Based on `main` `5079a08` (re-checked against `origin/main` before packaging: no newer commits). Frontend
and docs only. **No new decision number:** the repository check found no new decision — this is a
consumer-side correction of #137/#138/#162 after #186, and the latest number in `INDEX.md`, the
`confirmed-decisions.md` tail and the archive list is still 186.

- **Truthful Live empty state.** `StrategyPerformanceSummary` (`InfoTab.tsx`) no longer says "no
  Execution Engine exists to write it". The empty message now depends on the selected view *and*
  strategy: Live says no simulated-execution outcome has been recorded (naming the strategy if one
  is selected); Backtest wording is unchanged. The Live subtitle says the data is simulated, not
  real-money trading. The Live/Backtest labels, the Backtest default (#138) and the strict
  `is_backtest` filters are unchanged.
- **Manual Refresh** in the panel header re-runs the two existing aggregate requests for the current
  selection; a "last loaded" time is shown. No polling, no WebSocket, no endpoint, no new statistic,
  no trading control.
- **`usePerformanceAnalytics.ts` rewritten around request order and population identity.**
  - Monotonic request id (the `useStrategyOutcomes.ts` pattern): a superseded response — success or
    failure — is dropped, for both filter changes and repeated `refetch()` calls. The previous
    `cancelled` closure was unreachable from `refetch()`.
  - Results and failures are tagged with the `(isBacktest, strategyName, strategyVersion)` they were
    fetched for and returned only when they match the current filters, derived at render time — rows
    for one population/strategy are never returned under another's label, not even for one render.
  - Loading, genuinely empty and failed stay distinct. New return fields: `hasLoaded`,
    `lastLoadedAt`. A failed request still clears rows (deliberately unlike `useStrategyOutcomes`);
    a same-population Refresh keeps its rows visible while re-fetching.
- **Comments corrected to current state** in `InfoTab.tsx` (new "CURRENT STATE" paragraph; older
  decision paragraphs kept as history), `usePerformanceAnalytics.ts` (docstring replaced) and
  `api-client.ts` (`fetchWinRateByHour` docstring no longer says no writer exists).
- **Docs:** as-built note with data-flow and internal-flow diagrams added to the performance-read
  section of `docs/architecture/strategy-engine-design.md` (after the #162 note); #137/#162 notes
  untouched as history.
- **Reported, not changed (related follow-ups, outside scope):** `InfoTab.tsx` `WorldViewSummary`
  still renders "No live trades yet." for its Live column; `useWorldView.ts`, `useBacktestOutcomes.ts`,
  `BacktestResultsPanel.tsx` comments and the `GET /strategy-outcomes` backend docstring still carry the
  "no live writer" wording.
<!-- END DELIVERY SECTION: frontend-performance-panel-refresh -->

<!-- BEGIN DELIVERY SECTION: outcome-recorder-zero-position-recovery (backend-only; integrate alongside other sections, do not merge them) -->
# CHANGES — `outcome-recorder-zero-position-recovery`

Narrow `OutcomeRecorder` recovery fix under decision #186. **No decision number assigned** (slug only);
#186 and the outcome contract are unchanged. Built on `main` `c7c8550` (contains `9c69d91`), branch
`outcome-recorder-zero-position-recovery`. No schema, migration, endpoint, trading behavior or frontend change.

**Gap.** §6.7.1 C8 says a closed trade with zero `positions` rows becomes `blocked` (`multi_position_trade`),
but `_pending_rows()` inner-joined `positions`, so after a lost `PositionClosed` neither the startup scan nor the
sweep could ever discover such a trade. The old query also returned one row per position (a multi-position trade
consumed several page slots), and its `(closed_at, trade_id)` keyset silently dropped NULL `closed_at` rows
(`NULL > x` is unknown).

**Fix (`outcome_recorder.py`, `_pending_rows` only).** The query now starts from `trades`, left-joins `positions`
pre-aggregated to one row per trade, and orders/pages on `(coalesce(min(closed_at), 1970-01-01), trade_id)`.
Same filters as before (approved, closed, simulated, auto, no outcome, not `blocked`; `pending_retry` still
eligible). Discovery only: `record_trade` / `_build` are untouched, so a zero-position trade reaches the existing
C8 check and is blocked with no outcome row and no fabricated position.

```
lost PositionClosed ─X─►                       (no event needed)
startup _startup_scan ─┐
periodic  scan()  ─────┴─► _pending_rows(cursor)
                            trades ⟕ (positions GROUP BY trade_id → min(closed_at))
                            WHERE approved∧closed∧simulated∧auto∧outcome_id NULL∧¬blocked
                            ORDER BY (coalesce(min_closed_at, epoch), trade_id) LIMIT batch
                              │  one row per trade, never-NULL key
                              ▼
                     _enqueue("close", trade_id) ─► record_trade ─► _close_candidate ─► _record_locked ─► _build
                                                                          1 closed position ─► outcome recorded (once)
                                                                          0 / >1 / not closed ─► blocked: multi_position_trade
```

Discovery order changed slightly: trades with no usable close time now sort first (previously NULL sorted last,
and was unreachable past page one). Ordinary trades keep oldest-close-first order.

**Also touched:** `docs/architecture/execution-engine-design.md` §6.7.1 C7 (as-built recovery description) and the
Verification sentence; `backend/tests/test_outcome_recorder.py` (+7 test functions, 9 cases; the NULL-`closed_at` discovery test already passed before the fix at default batch size, the page-boundary tests catch the paging loss).
<!-- END DELIVERY SECTION: outcome-recorder-zero-position-recovery -->

<!-- BEGIN DELIVERY SECTION: frontend-outcomes-simulated-reader (frontend-only; integrate alongside the backend task's section, do not merge the two) -->
# CHANGES — `frontend-outcomes-simulated-reader` (frontend for decision #186)

Frontend-only follow-up to decision #186's simulated `OutcomeRecorder`. **No decision number was
assigned** (slug only); the reader is a consumer of #186 and changes no architecture. Whether it
deserves its own numbered decision is for Saqib to decide at merge, after re-checking `main`.
Built on `main` `9c69d91` on branch `frontend-outcomes-simulated-reader`. No backend file,
endpoint, migration, dependency, polling or WebSocket event was added, and there is no trading
control and no trade-detail view.

- **"Recent Closed Trades" now reads as simulated execution.** `InfoTab.tsx`'s
  `RecentClosedTrades` keeps its strict `is_backtest=false` request (limit 10) and adds a
  "Simulated" badge plus the subtitle "Simulated execution results — not real-money trading."
  Each row also prints its own `execution_mode · execution_venue`, so a future paper/live row
  could not be mislabelled by the section header.
- **Manual Refresh and distinct states.** Loading (first request), Error (first request failed —
  never shown as "no trades"), Empty (a request succeeded with zero rows) and Populated. A failed
  Refresh keeps the previously loaded rows and shows a banner with the time of the last good load;
  a later success replaces the rows and clears the banner.
- **Stale-response safety.** `useStrategyOutcomes.ts` gives every load a monotonically increasing
  request id and drops any response (success or failure) that is no longer the newest, so an older
  request cannot replace a newer refresh's rows or bring back a stale error. The old cancel
  closure was unreachable from `refetch()` callers; that gap is closed. The hook now returns
  `outcomes`, `loading`, `error`, `hasLoaded`, `lastLoadedAt`, `refetch`.
- **`StrategyOutcomeWireShape` corrected** against `schemas/performance.py`: added
  `execution_mode`, `execution_venue`, `snapshot_missing_reasons` (`Record<string, string> | null`);
  the four `market_state_*` / `context_*` snapshots are `Record<string, unknown> | null`.
  `StrategyOutcomeRow` carries mode, venue and missing-snapshot reasons; rows whose snapshots were
  unavailable show "N snapshots unavailable" (reasons in the tooltip).
- **Required knock-on type edit:** `BacktestResultsPanel.tsx`'s JSON-blob field list type was widened
  to `Record<string, unknown> | null` so the nullable snapshots compile. No behavior change.
- **Stale comments corrected** where they describe this reader, the wire shape or
  `fetchStrategyOutcomes()` (`api-client.ts`, `useStrategyOutcomes.ts`, `InfoTab.tsx`): the "no live
  writer / one-shot because nothing writes" claims are replaced with the as-built #186 behavior.
- **Docs:** `execution-engine-design.md` §6.7.1 I (as-built frontend reader, data-flow and internal-flow
  diagrams); additive correction note in `backtest-runner-design.md` beside its "no writer yet"
  diagram. `confirmed-decisions.md` and `INDEX.md` are untouched.
- **Reported, not changed (related follow-ups):** other frontend comments and one user-visible string
  still say no live/simulated writer exists — `InfoTab.tsx` `StrategyPerformanceSummary` ("no Execution
  Engine exists to write it", "No live trades yet."), `BacktestResultsPanel.tsx`, `useBacktestOutcomes.ts`,
  `usePerformanceAnalytics.ts`, `useWorldView.ts`, and `fetchWinRateByHour`'s docstring. The backend
  route docstring for `GET /strategy-outcomes` (`intelligence.py`) says the same. Separate sections/files,
  outside this task's scope.
<!-- END DELIVERY SECTION: frontend-outcomes-simulated-reader -->

# CHANGES — decision #186, `outcome-recorder-contract` (EX-12 option a)

Saqib approved the dedicated, ledger-driven `OutcomeRecorder` for simulated,
strategy-attributed auto trades. The Governor's strict evidence persistence
was already merged at `79650ad` and is reused here.

- Added `record_strategy_outcome_in_session()` to stage the complete outcome
  without committing or closing its caller's session. Backtest Runner retains
  its existing own-session wrapper and persisted behavior.
- Added the best-effort first-entry-fill snapshot hook and the recorder's
  position-scoped receipt replay, 60-second exit-snapshot bound, startup scan,
  60-second bounded recovery sweep, and atomic outcome insert/trade link.
  Missing required evidence, R basis or exit reason blocks the trade with a
  reason-coded log; recoverable database failures remain retryable.
- Wired the recorder after successful execution reconciliation. Recorder
  startup failure logs CRITICAL while execution readiness stays `ready`, and
  shutdown stops it after Portfolio State.
- Added PostgreSQL integration tests and updated the execution architecture,
  related status documents, decision log and `TESTING.md`. No migration,
  paper/live writer, manual writer or backtest policy change is included.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-status-doc-sync`

Documentation correction plus two small follow-ups. The first pass (the claim table below) was
written against `eca3573` and has landed as `c2ea927` ("Execution status doc sync"). This
follow-up is verified against `main` `79650ad`, the tip at the final fetch. Between the two,
only `79650ad` ("Governor approval evidence") landed; it touches `governor/`, its own tests
and one additive paragraph in `execution-engine-design.md` §6.2, and none of the four files
changed here or any claim in the first-pass table.
No migration, dependency, trading-behavior or decision-log file was edited, and **no decision
number was assigned** (slug only): every behavior described below is already built and covered
by decisions #170–#185 or by the slug-only deliveries that extend #185's approval. Nothing here
authorizes new trading behavior or the EX-12 `OutcomeRecorder`, which stays proposed.

## Follow-up fixes (test and comment only)

- **`backend/tests/test_exit_ledger_postgres.py` no longer depends on the wall clock.** The
  module's `NOW = datetime.now(timezone.utc)` is now a fixed instant, Wed 2026-09-16 15:00Z
  (11:00 ET, inside the 09:30–16:00 ET session), and a `make_ledger()` helper passes
  `clock=lambda: NOW` to all four `PostgresExitLedger` instances. Before, three tests failed
  whenever pytest ran outside US regular hours because the session guard added by
  `simulated-protective-session-retry` (`eca3573`) makes `prepare()` return `None` then. All
  existing assertions are unchanged, and the production session guard, `PostgresExitLedger`
  and `MarketClock` are untouched.
  - *Consistency with the injected time.* The seeded position's `opened_at` is `NOW − 1 min`;
    the venue-rejection retry timestamp the ledger writes is `NOW + 5 s` (so
    `prepare()` correctly returns `None` for the bounded retry), and the test then sets
    `retry_after = NOW − 1 s` so attempt 2 is reserved; the intent's `trigger_ts` and the
    late fill's `venue_ts` are `NOW`. All of these fall in the same regular session.
  - *New guard test.* `test_fixed_now_is_a_regular_session` asserts `NOW` is timezone-aware
    and that `NOW`, `NOW − 1 min` and `NOW + 5 s` are all regular-session instants per
    `MarketClock`, so a calendar change cannot silently turn the fixed instant into an
    out-of-session one.
- **`backend/app/core/config.py` comment corrected.** The comment on
  `execution_eod_flatten_lead_seconds` said "the timer and ledger consumers are later tasks".
  It now says the lead (1..900 s) is read by Position Monitor's EOD timer and by
  `PostgresExitLedger`, which stores the `[close − lead, close)` window on the EOD row. The
  setting, its default and its validator are unchanged.

The living status text still described the execution path as it stood before decision #171
(roadmap) or before the EOD deliveries (execution design, system design). Each claim below was
checked against the code at `eca3573` and, where noted, a test that exercises it.

## Corrected stale claims

| # | Where | Stale claim | Now says | Code evidence |
|---|---|---|---|---|
| 1 | `phase-roadmap.md`, Phase 5–6 status | Portfolio State, Execution Engine and Position Monitor "not started as application modules" | Built for `execution_mode = simulated` only; lists entry, stop/target, EOD, calendar, session-aware retries, read surfaces | `app/portfolio_state/`, `app/execution_engine/`, `app/position_monitor/`, `app/main.py` (pipeline wiring) |
| 2 | same | "no live Execution/Position Monitor writer exists"; World View Portfolio State slot "honestly returns `null`" | No live `StrategyOutcome` writer (still true, now tied to the unbuilt `OutcomeRecorder`); the slot reads the running Portfolio State and is `null` when the pipeline did not start or the snapshot is unavailable | `app/main.py` (`world_view_portfolio_reader = portfolio_state`), `app/world_view/composite.py`, `tests/test_world_view_portfolio.py` |
| 3 | same | "no Governor rule engine exists" implied nothing between the schema and a real Governor | A provisional simulated-only authorizer stub (rules 0–6) is built; a real Governor rule engine is not | `app/governor/engine.py` (subscribes to `OpportunityCreated`), `app/governor/rules.py` |
| 4 | same, diagram | Execution Engine, Position Monitor, Portfolio State all `[not started]` | Re-drawn: built / proposed / not built per box | as above; no `OutcomeRecorder` class exists |
| 5 | same, header | "Status as of decision #164" for the whole section | Phase 5–6 re-verified at `eca3573`; Phases 1–4 explicitly not re-audited | — |
| 6 | `execution-engine-design.md`, header and §0 | No current-status statement; §0 items 1–5 read as the live state | §0 item 6 gives current built / proposed / not-built status | as above |
| 7 | same, §1 | Inventory rows "not built" for ledger tables, Portfolio State, Execution Engine, Position Monitor | Labelled historical (main through #167); points to §0 item 6 | `alembic/versions/0012…0016`, modules above |
| 8 | same, EX-5 (table row, heading, "Approved EOD policy") and §7 intro / §7.1 item 1 | "EOD order path unbuilt"; "the monitor, durable fallback/dispatch path and order placement still need implementation" | EX-5 resolved **and built** for simulated stop/target and EOD; manual/paper/live exits still have no policy | `exit_ledger.py` (`EOD_FLATTEN`, `FALLBACK_ALREADY_STORED`), `position_monitor/engine.py`, `alembic/versions/0016_simulated_eod_exit_state.py`, `tests/test_simulated_eod_integration.py` |
| 9 | same, EX-5 "As-built resolution" | "This does not implement EOD…" (present tense) | Kept, marked as historical wording at `simulated-protective-exits` | as above |
| 10 | same, §6.6 `simulated-eod-monitor-handoff` note | "This is the monitor half only. No EOD order can be placed…" | Kept as a delivery-time limit, marked superseded by the ledger, visibility and integration deliveries | `app/main.py` (`restore_observation`), `execution_engine/engine.py` (`on_observation`) |
| 11 | same, §6.5 Portfolio State | "No … forced EOD exit … exists here" | Clarifies EOD flatten is Position Monitor/Execution policy, not Portfolio State | — |
| 12 | same, §6.7 | Presented as the live-half design without a status | Banner: design sketch, **not built**; EX-12 open; `BacktestRunner` is `record_strategy_outcome()`'s only caller | `app/backtest_runner/runner.py:414`; no `class OutcomeRecorder` |
| 13 | same, §6.7.1 A10 | "exit path writes `exit_reason` as `stop` or `target`; the approved EOD policy has no executable order path yet" | `stop`, `target` or `eod_flatten` | `exit_ledger.py`, `tests/test_simulated_eod_integration.py` |
| 14 | same, §6.7.1 status | Verified at `f517834` | Adds re-verification note (A1, A5 re-checked; A10 corrected; others not re-audited) | — |
| 15 | same, §8 World View bullet | "simulated stop/target observations now also enter the durable execution path" | stop/target **and EOD** | `exit_ledger.py` |
| 16 | same, §9 | Acceptance criteria read as a status list | Labelled a historical proposal, not audited | — |
| 17 | `system-design.md`, companion-doc line | "forks still open (… EOD aspect of EX-5)" | EX-5 removed; EX-12 noted as proposed, not built | — |
| 18 | same, §4.8 implementation status | "Governor … Position Monitor remain the target shape only, not yet built" | Update: authorizer stub and Position Monitor-lite built; ranking, Decision Engine, Trade Planning, real Governor and thesis logic not | `app/governor/`, `app/position_monitor/` |
| 19 | same, §4.9 "As built" | "EOD flatten … remain separate work" | EOD built (best-effort, `[close − lead, close)`, 2026–2028 only); session-aware protective retries; still not built: paper/live, broker-side protection, manual exits, `OutcomeRecorder` | `core/session_window.py`, `exit_ledger.py`, `core/market_clock.py` |
| 20 | same, §3 diagram | Position Monitor → Performance Intelligence edge shown without qualification | Caveat added: target shape; writer proposed, not built | — |
| 21 | same, §4.13 tables | "no Execution Engine/Position Monitor exists yet to write one" | The engine and monitor exist (simulated); the `OutcomeRecorder` does not | as row 12 |
| 22 | same, §8 folder tree | Lists `order_manager.py`, `execution_router.py`, `mode.py`, `approval_queue.py`, `governor.py`, `position_sizing.py`, `risk_rules.py`, `monitor.py` | Note: original target layout; lists the as-built execution modules; those eight files do not exist | `find backend/app` |

**Preserved unchanged:** every decision-log entry (including #185's wording that EOD timer,
ledger and orders "remain unbuilt", which was true when written), the §6.6 "Historical baseline
before decision #185 integration" and "Historical staged implementation footprint" text, and
the Phase 1–4 roadmap bullets.

## Findings reported, not fixed (AGENTS.md §9)

- **Decision-log question, no entry written.** Decision #185's log text says the EOD timer,
  ledger, orders, recovery and UI "remain unbuilt". Later deliveries built them under #185's
  approval without a new number, and this delivery documents that in the living docs. The log is
  immutable; whether a correction entry recording the as-built state (and the regular-hours
  condition `simulated-protective-session-retry` added to the fallback wording) is wanted is
  your call at merge. No number is needed for this delivery.
- **Related follow-up — other docs.** `strategy-engine-design.md` (lines ~386 and ~444) still
  quotes the "no Execution Engine" Live-tab message. It is historical wording and is left
  for a separate review; not audited or edited here.

## Files

First pass (landed as `c2ea927`): `CHANGES.md`, `TESTING.md`, `docs/roadmap/phase-roadmap.md`,
`docs/architecture/execution-engine-design.md`, `docs/architecture/system-design.md`.
This follow-up: `CHANGES.md`, `TESTING.md`, `backend/tests/test_exit_ledger_postgres.py`,
`backend/app/core/config.py`.

---

# CHANGES — `simulated-protective-session-retry`

Stops a simulated stop/target close from minting a new attempt every five seconds while the
venue is closed. GitHub `main` was `b6d1e57` at start and at the final fetch (no newer
commits). No decision number was assigned (slug only), no migration, no dependency. Not
touched: `MarketClock`, `core/session_window.py`, their tests, calendar data, frontend.

**Defect reproduced first.** `SimulatedVenue` rejects every order outside regular hours; the
ledger answered any rejected close with `retry_after = now + 5 s`, and `prepare_exit()` had
no session check. Ledger-level reproduction (real PostgreSQL, injected clock, rejecting
stand-in venue): 400 service passes over 40 minutes -> 400 venue calls, 400 distinct
`<trade>:exit:N` IDs, all but five after 16:00 ET. The same loop applied to an original
stop/target observed after hours and to the fallback of an expired EOD row (a fallback is
first actionable at or after the bell).

- `execution_engine/exit_ledger.py`: a stop/target close (original request or EOD fallback)
  is neither reserved nor dispatched while `MarketClock.is_regular_session(now)` is false,
  using the ledger's injected clock. New `PrepareDisposition.WAIT_OUTSIDE_REGULAR_SESSION`
  and `ClaimDisposition.WAIT_OUTSIDE_REGULAR_SESSION`. The durable request, `exit_attempt`,
  order rows and rejection history are untouched, so IDs stay monotonic. The guard sits
  after the active/uncertain-close, pending-fill and retry-delay checks (they keep their
  behavior; a submitted or dispatch-marked close stays exclusive) and replaces only a new
  reservation or a `SUBMIT`. An unsent approved reservation is held and reused (same ID)
  at the open. `claim_dispatch` re-checks before writing any dispatch marker. `eod_flatten`
  and its `[flatten_at, close_at)` placement rule are not affected.
- `execution_engine/engine.py`: the worker treats both new dispositions as a quiet return
  (no error log, no venue call). Resumption is the existing worker pass; there is no new timer.
- Docs: `docs/architecture/execution-engine-design.md` (new §6.6 subsection with component
  data-flow diagram, prepare/claim internal-flow diagram and timeline, consequences/limits;
  updated summary, transition table, result-type table and the existing flow diagram).
- Tests: new `test_simulated_protective_session_retry.py` (32). Five cases in four existing
  tests in `test_exit_ledger_eod_postgres.py` placed an EOD fallback at 16:00:30 ET; only that
  placement step moved to the next open (`NEXT_OPEN`, 2026-09-17 13:30Z) and a wait
  assertion was added. Their other assertions are unchanged.

**Behavior consequences to review.** (1) An EOD fallback cannot place on the entry day; its
first chance is the next regular open, adding a regular-hours condition to decision #185's
wording (no numbered entry written; add one at merge if you treat this as a policy change).
(2) Working-entry cancellation is not a close and is not gated. (3) A legacy #184 unsent
reservation with no dispatch marker is also held outside hours. (4) No fork arose about an
already-submitted order; its existing handling is preserved. (5) The two clocks (ledger vs
venue) can disagree at the boundary; the cost is one venue rejection under the existing delay.

---

# CHANGES — `market-clock-2027-2028-coverage`

Extends `MarketClock`'s verified NYSE equity calendar from 2026 to 2026–2028. GitHub `main`
was `0390ef1` at start and at packaging; no new decision, migration or dependency was needed
(the coverage extension is the data-only change the module's own scope note anticipated, and
decision #185's helper contract is unchanged; #185's "2026 coverage" sentence is history).

**Official schedule checked:** NYSE "Holidays & Trading Hours",
https://www.nyse.com/trade/hours-calendars (fetched 2026-09-30; its table lists 2026, 2027
and 2028 with footnotes for early closes).

- Full-day closures. 2026 (unchanged): Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19, Jul 3
  (observed), Sep 7, Nov 26, Dec 25. **2027**: Fri Jan 1, Mon Jan 18, Mon Feb 15, Fri Mar 26
  (Good Friday), Mon May 31, Fri Jun 18 (Juneteenth observed), Mon Jul 5 (Independence Day
  observed), Mon Sep 6, Thu Nov 25, Fri Dec 24 (Christmas observed). **2028**: Mon Jan 17,
  Mon Feb 21, Fri Apr 14 (Good Friday), Mon May 29, Mon Jun 19, Tue Jul 4, Mon Sep 4,
  Thu Nov 23, Mon Dec 25. **2028 has no New Year's Day holiday** (footnote: Saturday
  Jan 1, 2028 is not observed).
- 1:00 p.m. ET equity early closes. 2026 (unchanged): Nov 27, Dec 24. **2027**: Fri Nov 26.
  **2028**: Mon Jul 3 and Fri Nov 24. The page lists no early close on Fri 2027-07-02 or
  Thu 2027-12-23, and none was added. The options 1:15 p.m. close and other venues' 5:00 p.m.
  late sessions are not modelled.
- `backend/app/core/market_clock.py`: added `_HOLIDAYS_2027/2028` and `_HALF_DAYS_2027/2028`
  beside the untouched 2026 sets, their unions `_HOLIDAYS`/`_HALF_DAYS` (what `is_holiday()`
  and `is_half_day()` now read) and `_VERIFIED_CALENDAR_YEARS = {2026, 2027, 2028}`, the single
  source for `has_calendar_for_year()`, which now reports exactly those years. Session methods
  keep their behavior outside them (no holidays or early closes, no exception). The module
  docstring and TODO were corrected to say what is and is not verified.
- `backend/app/core/session_window.py` is **unchanged**: with the calendar extended it already
  yields `[flatten_at, close_at)` for 2027–2028 trading days, `None` on covered
  holidays/weekends, and `UnsupportedEodCalendarError` for any other entry year. The
  inclusive/exclusive contract and lead validation are untouched. Backtest Runner's
  `regular_session_close_utc` also reads `is_half_day()`, so parity is inherent and tested.
- Tests that legitimately assumed 2027 was unsupported now use 2029 (only the year changed,
  assertions intact): `test_exit_ledger_eod_postgres.py` (`unsupported_eod_calendar` reason)
  and `test_position_monitor_eod.py` (bounded warning + protective exit still works).
  `test_session_window.py`'s unsupported-year parametrization dropped 2027 for 2029.
- Docs: `docs/architecture/execution-engine-design.md` (replaced the "2026-only coverage"
  paragraph, added a calendar data-flow diagram next to the existing window diagrams, updated
  the A18 row) and `system-design.md` §4.3.

Known limits: 2029 onward is unverified and fails closed for EOD; `next_session_boundary()`
walking past 2028-12-31 treats unverified 2029-01-01 as an ordinary weekday. Not changed here:
`CalendarProvider`'s 2026-only FOMC dates and the "2026" comments in files this task does not
own (the `_HOLIDAYS_2026`/`_HALF_DAYS_2026` names are kept, so they remain accurate).

---

# CHANGES — `simulated-eod-flatten-integration`

Connects decision #185's already-built monitor handoff, PostgreSQL exit state machine,
migration `0016`, and read-only request view to the running simulated execution path.
GitHub `main` was `f9b6d77` at start; no new decision or migration was needed.

- `ExecutionEngine` drains monitor observations in sequence. The callback is only a wake-up;
  a slot is acknowledged after `observe_exit()` commits. Database failure retains the slot,
  and a later observation for the same position cannot overtake it. Distinct window, closed,
  invalid and wait dispositions are handled. The legacy stop/target callback is not wired
  alongside this handoff.
- The worker services `pending_exit_position_ids()` using `prepare_exit()` and
  `claim_dispatch()`. Only `CLAIMED` places a close at `SimulatedVenue`. It cancels unfinished
  entries and collects reported fills, records actual venue rejection reasons, and leaves
  EOD-lifecycle claims with exceptions or lost status commits uncertain. Each position is isolated
  so one failure does not stop the others.
- Reconciliation blocks startup when an approved close has a dispatch marker but the venue
  has no report. A clean retained venue can resolve the same order ID; a fresh venue with
  an open ledger position still blocks activation. After clean reconciliation and Portfolio
  State refresh, startup advances persisted EOD expiry and restores the original/fallback
  monitor slots before authorizer/Execution/monitor subscriptions. If expiry cancels a
  proven-unsent order, Portfolio State refreshes again before those subscriptions.
  PostgreSQL timestamp offsets are
  converted to UTC for the monitor's `ExitIntent` contract.
- Lifespan shutdown stops the monitor before draining Execution. The existing entry path,
  stop/target flow, read-only `/health/execution-startup`, and simulated-only mode gate remain
  in effect. The order and fill readers retain their separate meanings: a request is durable
  intent; a reserved order is not venue acceptance; venue acceptance is not a fill; only a
  committed fill receipt can reduce or close the position.
- Added real PostgreSQL, EventBus, SimulatedVenue and three-start lifespan tests; extended
  reconciliation tests for both missing and retained venue evidence. Updated §6.6's as-built
  diagrams and acceptance status, plus this file and `TESTING.md`.

EOD is a best-effort placement attempt. An accepted order can fill after hours or remain
unfilled indefinitely. EX-12/OutcomeRecorder and paper/live execution are unchanged.

---

# CHANGES — `simulated-eod-exit-request-visibility` (read path only)

Shows the durable EOD request state Task 2 introduced in the existing
`GET /intelligence/execution-exit-requests` route and the Execution panel's "Recorded exit
requests" section. Built on `main` `c1d09e4` (Task 2, `simulated-eod-ledger-handoff`,
migration `0016`); column names and nullability come from that model and migration, not
from the §6.6 proposal. No decision number assigned (#185 records the policy).

- `backend/app/api/routes/intelligence.py`: each row gains six always-present nullable
  fields passed through as stored: `eod_flatten_at`, `eod_close_at`, `eod_expired_at`,
  `fallback_reason`, `fallback_trigger_price` (exact decimal string) and
  `fallback_trigger_ts`. `exit_reason` may now be `eod_flatten`. Query, `simulated`
  scoping, exact `symbol`, `limit` bounds, `trigger_ts`/`position_id` ordering, exact
  decimals and the worker-thread read are unchanged; nothing is derived and no clock is read.
- `frontend/src/services/api-client.ts`: wire type extended (`eod_flatten` reason, six
  nullable fields).
- `frontend/src/components/execution/ExecutionLifecyclePanel.tsx`: an `eod_flatten` row
  shows its stored window, "Placement eligibility ended <time>" or "No expiry recorded",
  and either the first stored stop/target observation (exact price and time) or "No stop or
  target fallback stored". Stop/target rows are unchanged. The section note now says an
  expiry only ends placement eligibility (not proof an order was cancelled or the position
  closed), a fallback is an observation (not a working protective order), and orders/fills
  are on their own views. No polling, no controls, existing Refresh unchanged.
- `docs/architecture/execution-engine-design.md` §6.6: as-built block with a field table
  and data-flow/internal-flow diagrams; the route/UI text and diagrams that said only
  stop/target are persisted or shown were corrected in place; the §6.8 `exit_requests` row
  was updated.

**Not true yet:** the ledger that writes `eod_flatten` rows is not wired into a running
system, so today these fields are `null` and no EOD row exists outside tests. The read path
is verified against hand-inserted rows only.

**Shared documentation the final integrator must merge** (all in
`docs/architecture/execution-engine-design.md`, plus this file and `TESTING.md`):
the new as-built block placed directly above the `simulated-eod-monitor-handoff` block;
in-place edits in the earlier `execution-exit-requests-route` /
`execution-panel-exit-requests` text (comparison-table "Includes EOD observations" cell,
"Curated fields" paragraph, "does not claim" paragraph, the route internal-flow diagram,
the frontend "Each row shows" sentence, the panel internal-flow diagram); and the §6.8
`exit_requests` row. Keep every other task's entries.

Package: `simulated-eod-exit-request-visibility.zip`, root-relative, seven files listed in
`TESTING.md`.

---

# CHANGES — `simulated-eod-ledger-handoff` (exit-ledger EOD state machine; UNWIRED)

Task 2 of the parallel EOD split, built on decision #185's foundation and rebased onto `main`
`5fc4dfb` (the merged Position Monitor half; migration head still `0015`). No new decision
number assigned; #185 already records the policy. It implements the
**durable half** only: the EOD request, immutable first protective fallback, reservation,
expiry and dispatch-claim state machine in `PostgresExitLedger`, its schema, and tests.
**No caller uses the new methods, so no EOD order can execute and the full EOD path is not
claimed to work.** The Position Monitor sibling, Execution worker, reconciliation, `main.py`,
readers and frontend are untouched.

- `backend/app/execution_engine/exit_ledger.py`: rewritten with two surfaces over the
  existing serialization lock. The legacy surface (`observe`, `pending_position_ids`,
  `prepare`, `confirm_recovery_exit`, `set_status`) keeps its signatures and stop/target
  behavior for the unchanged worker and never surfaces an EOD row. The explicit surface
  (`observe_exit`, `prepare_exit`, `claim_dispatch`, `advance_eod_expiry`,
  `pending_exit_position_ids`, `slot_state`) returns typed dispositions so a later
  integration can distinguish a normal expiry from an unsafe ledger. Consumes
  `core.session_window.eod_session_window()` to validate EOD bounds from the committed
  `positions.opened_at`; supplied bounds and quantity are never trusted.
- `backend/app/models/execution_ledger.py` and migration
  `backend/alembic/versions/0016_simulated_eod_exit_state.py` (parent `0015`): `eod_flatten`
  reason, immutable EOD bounds, durable expiry, first-wins fallback slot,
  `orders.exit_dispatch_started_at`, field-group CHECKs, immutability triggers, and a
  downgrade that refuses to discard EOD/fallback/dispatch evidence.
- `docs/architecture/execution-engine-design.md` §6.6: scoped as-built description,
  result-type table, data-flow and internal-flow diagrams; one intro sentence corrected.
  Reconciliation's marker-blind approved-close handling is recorded as an integration gap.
- Two existing migration tests (`test_authorization_ledger_postgres.py`,
  `test_position_ledger_postgres.py`) asserted the head was the literal `"0015"` after a
  refused downgrade. A new migration necessarily breaks that, so they now compare against
  the script directory's head. Their refusal assertions are unchanged. This is the only edit
  outside the requested file boundary.

Not done (integration task): monitor timer/handoff, Execution worker wiring and venue-side
handling of each disposition, reconciliation/marker awareness and restart hydration,
`main.py` ordering, exit-request reader and frontend. A stale unsent reservation whose
quantity no longer matches the position is reported `UNSAFE`, not auto-replaced.

Package: `simulated-eod-ledger-handoff.zip`, root-relative, the ten files listed in `TESTING.md`.

---

# CHANGES — `simulated-eod-monitor-handoff` (Position Monitor half of decision #185)

Scope: Position Monitor only. Started from GitHub `main` `1c927db` (re-fetched before packaging: no newer commits). No new decision number; #185 already exists.

- `backend/app/position_monitor/engine.py`: EOD moved off the event path into a timer-driven pulse enqueued on the **same queue** as price events, with an injectable wall clock and explicit configured lead passed to `eod_session_window()` / `EodSessionWindow.contains()`. Valid `PriceUpdated` ticks are cached in monotonic exchange-time order (first equal-time tick kept) before held-symbol filtering. An EOD label needs an entry-day tick with `opened_at <= exchange_ts <= wall now`; candles never label EOD. Queued events are processed before a pulse; stop/target are checked first on the eligible tick; EOD never suppresses later protective observations.
- `ExitIntent` gained optional `eod_flatten_at` / `eod_close_at` (UTC, EOD only); stop/target construction is unchanged.
- New `backend/app/position_monitor/handoff.py` and monitor methods `pending_observations()`, `get_observations()`, `acknowledge_observation()`, `release_observation()`, `enqueue_pulse()`, plus optional `on_observation`. Callback/queue success is never treated as a commit; slots stay `PENDING` until acknowledged.
- `docs/architecture/execution-engine-design.md` §6.6: scoped as-built block with data-flow and internal-state diagrams and the integration API.
- Tests: new `test_position_monitor_eod.py`; `test_position_monitor_engine.py` updated (old exact-close EOD event expectation replaced; `_evaluate` no longer takes a clock).

Not done: no EOD order, exit-ledger/migration/Execution/reconciliation/`main.py`/route/frontend change. **The full EOD path does not work yet**: nothing consumes or acknowledges an EOD observation. Note for integration: `main.py` builds the monitor with defaults, so once this merges the 1 s timer runs in the app and may create a pending EOD slot (visible as the first intent in `GET /intelligence/exit-intents`); it is inert.

Merge note: `CHANGES.md`/`TESTING.md` are edited by the parallel exit-ledger task too; keep both sections.

Package: `simulated-eod-monitor-handoff.zip` (folder `simulated-eod-monitor-handoff/`, root-relative contents).

---

# CHANGES — `simulated-eod-flatten-contract` shared foundation (#185)

Saqib approved the four §6.6 policies on 2026-09-29. This delivery builds only the
shared EOD window contract: `backend/app/core/session_window.py` computes a covered
entry-day UTC interval `[flatten_at, close_at)`; `MarketClock.has_calendar_for_year()`
exposes 2026 coverage without changing existing sessions; Settings defaults the validated
lead to 60 seconds (`1..900`). The new tests cover boundaries, calendar gaps, half-days,
DST and parity with Backtest Runner's 2026 close derivation.

`docs/architecture/execution-engine-design.md` now separates approved policy, built
foundation and the still-unbuilt executable EOD path; it adds foundation data/internal
flow diagrams and boundaries for the two subsequent parallel tasks. Decision #185 was
appended after the latest GitHub `main` recheck. The over-100KB open decision log was
rolled over under `docs/decisions/README.md`: #161–#184 moved verbatim to
`archive/161-184.md`, their index locations changed, and the open log now starts at #185.
No existing decision body changed. This file and `TESTING.md` document the delivery.

No Position Monitor timer/handoff, exit-ledger state machine, migration, Execution wiring,
reconciliation or frontend behavior was added. EOD orders cannot execute yet; approved
flatten behavior remains best-effort even after later implementation.

Package: `simulated-eod-flatten-foundation.zip`, root-relative, contains exactly the
ten changed/new files listed in `TESTING.md`.

---

# CHANGES — `simulated-eod-flatten-contract` revision (UNAPPROVED)

Design/documentation only, requested by Saqib. Inspected and re-fetched GitHub `main`
at `67ef81d`; reused the committed implementation and revised the existing §6.6 proposal
in `docs/architecture/execution-engine-design.md`. No code, migration or test implementation.

- Replaced permanent EOD suppression with proposed durable original-EOD/fallback slots,
  placement expiry and explicit monitor/Execution acknowledgement behavior.
- Specified transitions for missing requests, unsent reservations, reject/cancel, submitted
  and partial orders, later stop/target and restart; preserved one-active-close/reduce-only.
- Added a durable dispatch claim to distinguish proven-unsent from uncertain placement,
  including crash/recovery and concurrent-claim acceptance cases.
- Specified post-opening tick-only labels, monotonic cache and queue ordering, supported
  calendar bounds, after-hours/non-fill limits and best-effort EOD wording.
- Replaced component/internal diagrams and added acceptance matrix A1–A20.
- Identified four unapproved policy choices: authorization/timing, fallback precedence,
  accepted-order behavior at close and label freshness, with recommendations/alternatives.

Updated this file and `TESTING.md`. Canonical decision index/log/archive remain unchanged:
latest confirmed decision is #184; the temporary slug has no number and is not approved.
EX-12 and its separate proposal are unchanged. Previous delivery records follow verbatim.

---

# CHANGES — `outcome-recorder-contract` (PROPOSAL — NOT APPROVED, NOT IMPLEMENTED)

## Current delivery

Design only. `docs/architecture/execution-engine-design.md` gains **§6.7.1 "Proposed
`OutcomeRecorder` contract"** (between §6.7 and §6.8) and one pointer paragraph under EX-12.
No application code, migration, test, frontend file, decision-log entry or decision number was
written or assigned. EX-12 stays OPEN: it needs Saqib's confirmation before anything is built.

**Provenance.** Written against backend code at `f517834`. `main` advanced to `3c23701`
(`simulated-eod-flatten-contract`, documentation only) before packaging; this delivery is
rebased on it and does not touch that proposal's text. Every "as-built" row in §6.7.1 cites a
file and symbol, and every proposed behavior is labelled Proposed.

**What §6.7.1 contains.**

- **A. As-built facts (A1–A11).** Nothing calls `record_strategy_outcome()` except the Backtest
  Runner; `trades.status = 'closed'` is already committed atomically with the closing fill; the
  receipts hold every fill's exact inputs, so a closed position can be replayed from the ledger
  alone; `PositionClosed` is a lossy wake-up carrying no entry facts, thesis or evidence.
- **B. Field-by-field source map** for every `StrategyOutcome` field: persisted, derived, NULL, or
  the exact missing source. Four gaps (M1–M4), one of which blocks: `Opportunity.evidence` is
  never persisted, and `strategy_outcomes.evidence` is `NOT NULL`.
- **C. Writer behavior** (C1–C8): partial reductions, commissions, missing snapshots, missing R
  basis, duplicate closure notifications, failed writes, restart recovery, trade-to-position
  cardinality. A trade that cannot be attributed honestly becomes `blocked` with a logged reason;
  it is never recorded with placeholders and never dropped.
- **D. Atomic linkage.** One transaction: lock the `trades` row, insert the outcome, set
  `outcome_id` and `outcome_status = 'recorded'`, commit. No migration: `outcome_status` has no CHECK.
- **E. Diagrams.** Component data flow and the recorder's internal flow, in the repository's ASCII style.
- **F. EX-12 options against current code.** (a) recommended, refined; (b) kept only as a wake-up
  hint; (c) rejected.
- **G. Build footprint and acceptance criteria** (P1–P4), all proposed.
- **H. The one decision to confirm** (below).

**The decision Saqib must confirm.** Confirm EX-12 option (a) in the form §6.7.1 specifies:
`OutcomeRecorder` is the only writer of non-backtest `strategy_outcomes` rows; the ledger drives it
and events are wake-up hints only; `strategy_outcomes` stays strict, so an unattributable trade is
`blocked`. That includes two footprint items outside `trading_intelligence/`: P1 (the Governor
persists `evidence` and `confirmed_at` at acceptance) and P3 (a same-session variant of
`record_strategy_outcome()`). Defaults unless overruled: 60 s snapshot-lag bound, 60 s sweep,
`blocked` reasons in logs only, first opening fill defines `entry_filled_at`, the closing fill's
order defines `exit_reason`.

**Deviations from §6.7's earlier sketch (called out, not silently changed).** (1) `PositionClosed`
enqueues a `trade_id`; it is not the data source. (2) Insert and link are one transaction rather
than "ok then set `outcome_id`". (3) A fourth `outcome_status` value, `blocked`. (4) The exit
snapshot is captured only within a lag bound; a later recovery pass records `NULL` plus
`recorder_unavailable`. §6.7's text is left as written.

## Findings (recorded, not acted on)

- **Blocker for the build, not for this proposal:** `evidence` is not persisted anywhere durable.
  Trades approved before P1 ships would all be `blocked` (`evidence_unavailable`).
- **P1 risk:** `PostgresTradeLedger._record_data` serializes with `allow_nan=False`; a non-JSON-safe
  `Opportunity.evidence` would fail the authorization commit. The build must sanitize first.
- `strategy_outcomes` has no uniqueness on `opportunity_id` (scratch run inserted two rows for one
  ID). The row lock prevents it in practice; a partial unique index would make it structural (a
  migration, left as a follow-up).
- `positions` has no uniqueness on `trade_id`; one position per trade rests on procedure
  (cancel-entry before close), so the recorder blocks on zero or several positions.
- `BacktestRunner` writes `schema_version = 1` while §6.8 and decision #170 say new-shape writers
  write 2. Not touched; noted so it is not mistaken for a recorder bug.
- `record_strategy_outcome()` builds its ORM row without `execution_mode`, `execution_venue` or
  `snapshot_missing_reasons`; a NULL-snapshot row fails the DB CHECK today.
- §1.3 of the design still lists `trades` and `orders` as not built; that inventory is marked
  historical in the document header and was left alone.

## Boundary

No backend, migration, frontend, test or decision-log change. No decision number assigned. §6.7,
§6.6 (including the concurrent EOD proposal), §7.1 and every other section are unchanged apart from
the one EX-12 pointer paragraph. At merge time the build task must re-check `main` and the
canonical decision log before assigning a number; if EX-12 is confirmed, §6.7.1 is rewritten from
proposed to as-built in that delivery. Not merged, not pushed.

<!-- Previous delivery record retained below. -->

# CHANGES — `simulated-eod-flatten-contract` (PROPOSAL — NOT APPROVED, NOT IMPLEMENTED)

## Current delivery

Design only. `docs/architecture/execution-engine-design.md` §6.6 gains "Proposed —
simulated EOD flatten contract (UNAPPROVED)", and EX-5 gains one pointer paragraph. No
application code, migration, test, frontend file, decision-log entry or decision number was
written or assigned. EX-12 and `OutcomeRecorder` (§6.7) are untouched.

**Provenance.** Written against `main` at `f517834`. Decision #184 left EOD flatten as an
in-memory observation with no authorization policy (EX-5 open for EOD). This proposal
designs the smallest path from that observation to a simulated reduce-only close.

**What the proposal says.**

- **Trigger.** Clock-driven, not tick-driven: a Position Monitor poll fires inside a
  wall-clock window `[close − L, close)` on the position's entry day (`L` proposed 60 s;
  half-day aware). A tick at the boundary is not needed; late or after-hours ticks cannot
  cause or block an EOD.
- **Precedence.** First durable reason wins; `exit_requests` is one row per position, one
  active close per position (existing partial unique index). Stop is evaluated before EOD.
- **Reuse.** Quantity from committed `positions.qty`, `<trade_id>:exit:<n>` reservations,
  cancel-entry and fill-receipt waits, `retry_after`, `confirm_recovery_exit()`, fill
  ingestion, Portfolio State closure, startup reconciliation.
- **Schema.** One constraint: `ck_exit_requests_reason` widened to allow `eod_flatten`
  (migration `0016`, same name). `orders.exit_reason` has no CHECK and already fits.
- **Venue.** Guarantees and non-guarantees, and what stays unsafe after a restart with a
  lost venue position, are listed in the section.
- Diagrams (component data flow, monitor poll, ledger path, timeline), the file list, and 12
  real-lifespan plus pure/monitor/ledger acceptance cases are in the section.

**The one confirmation needed from Saqib:** policy EOD-A — simulated EOD needs no Governor
decision (EX-5 option (a)), triggered at `close − 60 s` by wall clock, no next-day
catch-up, no cancellation of a submitted close at the bell.

## Findings (recorded, not acted on)

- **The current EOD instant cannot be acted on** (executed): the venue rejects a close at
  exactly 16:00:00 ET.
- **The EOD observation latches the position and silences later stops** (executed): a 16:05
  price of 80 against a stop of 90 produced no intent after a 16:00 EOD observation.
- **Stop/target retries are unbounded outside the session** (read, not executed): every 5 s
  a new order is created and refused by the venue until the next open.
- `ExecutionEngine._service_exits()` aborts its pass for later positions when one
  reservation raises (read).
- `MarketClock` knows 2026 holidays and half-days only; from 2027-01-01 the EOD window would
  be wrong, so the proposal adds a coverage guard instead of trusting the calendar.
- Backtest parity: the runner exits at the close of the first candle stamped at or after the
  close (an after-hours minute in `1m-ext` data); live would place about a minute earlier
  and fill on the next tick. The delta is recorded, not hidden.

## Boundary

Only `docs/architecture/execution-engine-design.md`, `CHANGES.md` and `TESTING.md` change.
The design-doc change is **insertion-only** (two blocks, no existing line altered), so it
can be integrated beside the other Claude's edit to the §6.7 `OutcomeRecorder` section:
apply `simulated-eod-flatten-contract-design.patch` with `git apply --3way` on top of
whichever version has landed, rather than unzipping the whole file over it. `CHANGES.md`
and `TESTING.md` are prepended blocks; if the other delivery landed first, keep both blocks
(newest first). `confirmed-decisions.md` and `INDEX.md` are deliberately untouched;
assign a number only when this is approved, implemented and ready to merge, after
re-checking `main`. A migration revision `0016` may also be wanted by parallel
OutcomeRecorder work — re-check at implementation. Not merged, not pushed.

## Package

`simulated-eod-flatten-contract.zip` contains three files, root-relative:
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.
A separate `simulated-eod-flatten-contract-design.patch` carries the design-doc change alone.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-panel-exit-requests`

## Current delivery

Added a compact "Recorded exit requests" section to the Execution panel
(`ExecutionLifecyclePanel.tsx`) and a typed `fetchExecutionExitRequests()` client
(`api-client.ts`). Frontend only.

**Provenance.** `GET /intelligence/execution-exit-requests` was added by
`execution-exit-requests-route` (commit `a8c4b84`) over the durable `exit_requests`
table from decision #184. Its record says "No frontend consumer". This task is that
missing caller, the exit-request counterpart of the orders, fills and positions
panel sections. It is not a contract change, and no backend file was touched.

- `frontend/src/services/api-client.ts`: new `ExecutionExitRequestWireShape`,
  `ExecutionExitRequestsWireShape` and `fetchExecutionExitRequests()`. It requests
  the bare route (server default 50 rows) and throws `ApiError` on a non-OK
  response, like its siblings. `trigger_price` is typed as a string and never
  converted to a number; `retry_after` is `string | null`.
- `frontend/src/components/execution/ExecutionLifecyclePanel.tsx`: new
  `RecordedExitRequests` section, mounted directly after `ObservedExitTriggers`.
  It reuses the file's existing `EXIT_REASON_LABEL` and `formatTriggerTime`. Nothing
  else changed except the imports, one header comment sentence, and the mount line.

**Behavior.**

- Fetches when the panel expands (the section mounts) and on the section's own
  Refresh. No polling, no filter, no `limit` argument, no buttons other than
  Refresh, and no trading action.
- Rows render in server order (`trigger_ts` descending), keyed by `position_id`;
  nothing re-sorts.
- Each row: symbol · Stop|Target, trigger time, `Trigger price <exact string>`,
  `Position <status> · remaining qty <n>`, and `Retry after <time>` only when
  `retry_after` is non-null.
- The section says a recorded request does not prove an order was placed or that
  the position is protected, and that position status and remaining quantity are
  current, not as of the trigger.
- Distinct loading, empty ("No exit requests recorded yet.") and error states.
- Each effect run has an `active` flag cleared by its cleanup, so a response or
  failure that arrives after collapse/unmount, or after a newer Refresh, is
  discarded.
- Separate from "Observed exit triggers" (in-memory `/exit-intents`, different
  title, note and endpoint), from orders and fills (none is read or implied), and
  from the WebSocket feed. Refreshing it refetches no other section.

**One deliberate choice, same as the positions section:** Refresh stays enabled
while a request is loading (the orders, fills and observed-triggers sections
disable theirs). Discarding a response superseded by Refresh is only reachable if
Refresh can be pressed mid-flight, and this section has no input that could change
under a pending request. It is a one-attribute change if you prefer the disabled
pattern.

Documentation: `execution-engine-design.md` §6.6 gains a "Frontend read path (as
built, `execution-panel-exit-requests`)" note with data-flow and internal-flow
diagrams, directly after the exit-requests route section. That section's sentence
"No frontend consumer" was true only of the route delivery, so it now says so and
points to the new note; the `exit_requests` row in §6.8 gains one sentence naming
the panel section. No other existing text was changed. `TESTING.md` records the
verification.

Decision number: none assigned. This delivery runs in parallel with other work, so
the slug `execution-panel-exit-requests` is the temporary identifier;
`confirmed-decisions.md` and `INDEX.md` are deliberately untouched. The route and
ledger this consumes are already covered by decision #184 and the route's own
record; whether a UI-only consumer needs its own number is left to integration,
after re-checking `main` and the canonical logs (`origin/main` at packaging:
`c2f66e5`; decision log and INDEX end at #184).

## Findings (recorded, not acted on)

- The list is capped at the route's 50 rows with a scrolling `max-h-48` box. There
  is no "showing latest 50" note and no paging; older requests are not visible.
- `retry_after` is displayed as stored. The backend never clears it once set
  (already recorded by `execution-exit-requests-route`), so a past timestamp can
  remain beside a position that has since closed. The UI does not interpret it.
- Rows show the exact trigger-price string, so `10.123400` keeps its trailing
  zeros, as the fills and positions sections do. A friendlier format would be a
  separate presentation choice.
- The repo still has no frontend test runner or `test` script. Verification used a
  scratch harness that is not shipped (see `TESTING.md`).
- The panel places this section next to "Observed exit triggers" on purpose (same
  topic, different source). If the two still read as duplicates in use, moving this
  section below the ledger sections is a layout-only change.

## Boundary

No backend, migration, ledger write, position accounting, exit-policy or
trading-control change. `RecentSimulatedOrders`, `RecentSimulatedFills`,
`RecentSimulatedPositions`, `StartupStatusLine`, `ObservedExitTriggers`, the
WebSocket hook and every existing API function are untouched. `package.json`,
`package-lock.json` and `tsconfig.tsbuildinfo` are unchanged. This file sits on top
of `execution-panel-position-history` (`c2f66e5`); the previous delivery records are
retained below.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-panel-position-history`

## Current delivery

Added a compact "Recent simulated positions" section to the Execution panel
(`ExecutionLifecyclePanel.tsx`) and a typed `fetchExecutionPositions()` client
(`api-client.ts`). Frontend only.

**Provenance.** `GET /intelligence/execution-positions` was added by
`execution-positions-route` (commit `9dcb879`) over the `positions` table built by
decision #172. Nothing in the frontend called it; its record says "No frontend
consumer". This task is that missing caller, the positions counterpart of the
orders and fills panel sections. It is not a contract change, and no backend file
was touched.

- `frontend/src/services/api-client.ts`: new `ExecutionPositionWireShape`,
  `ExecutionPositionsWireShape` and `fetchExecutionPositions()`. It requests the
  bare route (server default 50 rows) and throws `ApiError` on a non-OK response,
  like its siblings. `avg_price`, `stop`, `target` and `realized_pnl` are typed as
  strings (`stop`, `target`, `realized_pnl` nullable) and never converted to
  numbers.
- `frontend/src/components/execution/ExecutionLifecyclePanel.tsx`: new
  `RecentSimulatedPositions` section (mounted after the fills section), a
  `PositionRow`, and a small `pnlSign()` helper. Nothing else in the file changed
  except the imports, one header comment sentence, and the mount line.

**Behavior.**

- Fetches when the panel expands (the section mounts) and on the section's own
  Refresh. No polling, no filter, no `limit` argument.
- Rows are rendered in server order, keyed by `position_id`; nothing re-sorts.
- Each row: symbol · side, status, `Qty <n>`, `Avg entry <exact string>`,
  `Stop`/`Target` only when non-null, and `Gross realized P&L` only when non-null.
  A known `0.000000` P&L is shown, in the neutral colour; `null` is omitted, never
  shown as zero.
- A closed position with quantity zero reads "Qty 0 — closed, nothing held" and
  is dimmed. The wording needs both `status === "closed"` and `qty === 0`; a
  closed row with a non-zero quantity shows the real quantity.
- Distinct loading, empty ("No simulated positions recorded yet.") and error
  states.
- Each effect run has an `active` flag cleared by its cleanup, so a response or
  failure that arrives after unmount/collapse, or after a newer Refresh, is
  discarded.
- The section says it is a persisted snapshot, not the live portfolio, that qty is
  what is currently held, and that P&L is gross (before commissions).
- Separate from the WebSocket activity feed and from the live World View
  portfolio: it neither reads nor feeds either, and Refreshing it does not refetch
  the other sections.

**One deliberate difference from the orders/fills sections:** Refresh stays
enabled while a request is loading (those sections disable their controls). The
task requires discarding a response superseded by Refresh, which is only
reachable if Refresh can be pressed mid-flight, and this section has no input
that could change under a pending request. Say so if you prefer the disabled
pattern; it is a one-attribute change.

Documentation: `execution-engine-design.md` §6.3 gains a "Frontend read path (as
built, `execution-panel-position-history`)" note with data-flow and internal-flow
diagrams, directly after the positions route section. That section's sentence "No
frontend consumer" was true only of the route delivery, so it now says so and
points to the new note; no other existing text was changed. `TESTING.md` records
the verification.

Decision number: none assigned. This delivery runs in parallel with other work,
so the slug `execution-panel-position-history` is the temporary identifier;
`confirmed-decisions.md` and `INDEX.md` are deliberately untouched. The routes
and ledger this consumes are already covered by their own records; whether a
UI-only consumer needs its own number is left to integration, after re-checking
`main` and the canonical logs (`origin/main` at packaging: `a8c4b84`).

## Findings (recorded, not acted on)

- Positions rows show the server's exact strings, so a value such as `185.100000`
  keeps its trailing zeros, matching the fills section's treatment of `price`. A
  friendlier display format would be a separate presentation choice.
- The list is capped at the route's 50 rows with a scrolling `max-h-48` box. There
  is no "showing latest 50" note and no paging; older positions are simply not
  visible in the panel.
- The repo still has no frontend test runner or `test` script. Verification used a
  scratch harness that is not shipped (see `TESTING.md`).

## Boundary

No backend, migration, ledger write, position accounting, exit or trading-control
change. `RecentSimulatedOrders`, `RecentSimulatedFills`, `StartupStatusLine`,
`ObservedExitTriggers`, the WebSocket hook and every existing API function are
untouched. `package.json`, `package-lock.json` and `tsconfig.tsbuildinfo` are
unchanged. This file sits on top of `execution-exit-requests-route` (`a8c4b84`);
the previous delivery records are retained below.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-exit-requests-route`

## Current delivery

Added read-only `GET /intelligence/execution-exit-requests`: the first HTTP view
of the persisted `exit_requests` rows (decision #184). Backend only.

**Provenance.** `exit_requests` and `PostgresExitLedger` already exist on `main`
(decision #184, restored to the record by `restore-protective-exits-record`).
Nothing outside that ledger read the table. This task adds the missing reader; it
does not change how requests are created, retried or acted on.

**Not `/intelligence/exit-intents`.** That route reports the running Position
Monitor's in-memory observations and is empty after a restart. The new route reads
only PostgreSQL, works with no monitor, and never consults it. Neither route's
behavior changed.

- `backend/app/api/routes/intelligence.py`: new module-level
  `_fetch_execution_exit_requests(symbol, limit)` (opens and closes its own
  `Session` inside the worker; patchable by tests) and the async route
  `get_execution_exit_requests`, which calls it through `asyncio.to_thread` —
  the same convention as the orders, fills and positions routes.
- `backend/tests/test_execution_exit_requests_route.py` (new): 25 tests.

**Behavior.**

- `exit_requests` INNER JOIN `positions` on `position_id`; hard-scoped to
  `Position.execution_mode == "simulated"` (not a parameter — an
  `execution_mode` query value is ignored). The join drops nothing: the request
  is keyed one-to-one by a foreign key.
- Optional exact `symbol` (on the position; no case-folding, no partial match).
  `limit` 1-100, default 50; out of range or non-integer returns 422.
- Order: `trigger_ts` descending, then `position_id` descending. The pair is a
  strict total order, so repeated reads and a `limit` inside a tie return the
  same rows. The tie-break is stable, not chronological (`position_id` is a
  random uuid4).
- Fields: `position_id`, `symbol`, `exit_reason`, `trigger_price` (exact decimal
  string), `trigger_ts`, `retry_after` (null when unset), `created_at`, plus the
  position's **current** `position_status` and `remaining_qty` (read at request
  time, not as of the trigger).
- Not returned or inferred: order status, any protection guarantee, retry
  outcome. `retry_after` is the stored timestamp only.
- Empty result (empty table, other-mode rows only, no match): `{"exit_requests": []}`, 200.
- Read-only; POST/PUT/PATCH/DELETE return 405.

Documentation: `execution-engine-design.md` §6.6 gains an as-built note with a
comparison against `/exit-intents`, a data-flow diagram and the route's internal
flow diagram; §6.8 gains the `exit_requests` table row (the table was not listed
there). `TESTING.md` records verification.

Decision number: none assigned. This delivery runs in parallel with other work,
so the slug `execution-exit-requests-route` is the temporary identifier;
`confirmed-decisions.md` and `INDEX.md` are deliberately untouched. Whether a
read-only route needs its own number, or falls under #184, is left to
integration after re-checking `main` and the canonical logs (`origin/main` at
packaging: `e1814fd`; log and index both end at #184).

**Integration note.** This delivery prepends its own record to `CHANGES.md` and
`TESTING.md`, as do sibling deliveries. Apply sequentially: keep every sibling's
record and this one, newest on top, and do not replace either file wholesale.

## Findings (recorded, not acted on)

- `retry_after` is set only when a close order is rejected or cancelled and is
  never cleared, so a non-null value can outlive a later successful close. The
  route reports it as stored; it is not a "retry pending" signal.
- `exit_requests` has no index on `trigger_ts`; the sort scans the
  mode/symbol-filtered join. Fine at this ledger's diagnostic volume (one
  concurrent position per EX-4); a migration is out of scope.
- No frontend consumer. The Execution panel still shows only `/exit-intents`.
  Showing persisted requests would be a separate task.
- The design doc's §6.8 table did not list `exit_requests`; the row added here
  describes it and the new route only.

## Boundary

No migration, frontend, exit-policy, Position Monitor, Execution Engine,
placement or trading-control change. `PostgresExitLedger`, `/exit-intents` and the
sibling routes are untouched.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-fills-symbol-filter-implementation`

## Current delivery

Added an independent symbol filter to the Execution panel's "Recent simulated
fills" section (`ExecutionLifecyclePanel.tsx`) and the optional `symbol`
argument to `fetchExecutionFills()` (`api-client.ts`). Frontend only.

**Provenance.** The backend already supported this: `GET
/intelligence/execution-fills` (decision #183) has accepted an optional exact,
case-sensitive `symbol` since its own delivery. Nothing in the frontend ever
passed it. Commit `a00dbd0` is named `execution-fills-symbol-filter` but
changed neither frontend file (it changed `.gitignore` and five documentation
files; see `restore-protective-exits-record` above), so this behavior did not
exist before this delivery. This task is the missing caller, the fills
counterpart of `execution-orders-symbol-filter` below; it is not a contract
change.

- `frontend/src/services/api-client.ts`: `fetchExecutionFills(symbol?)` appends
  `?symbol=<encodeURIComponent(symbol)>` when a non-empty symbol is passed and
  requests the bare path otherwise — the same ternary `fetchExecutionOrders`
  uses. `price` and `commission` remain exact decimal strings; the wire type is
  unchanged.
- `frontend/src/components/execution/ExecutionLifecyclePanel.tsx`:
  `RecentSimulatedFills` gains its own `symbolInput` / `appliedSymbol` state,
  a compact input with Apply and Clear, and `emptyFillsMessage()` (a sibling of
  `emptyOrdersMessage`). It reuses the existing `normalizeSymbolFilter()`
  unchanged (trim, uppercase, empty becomes `undefined`) rather than adding a
  second copy. Nothing is shared with `RecentSimulatedOrders`: filtering one
  section never refetches or alters the other.

**Behavior** (deliberately identical to the orders filter):

- The input uppercases as typed; Apply and Enter trim and uppercase before
  fetching. Empty or whitespace-only clears the filter and never sends
  `symbol=`.
- Refresh re-fetches with the last applied symbol; typed-but-unapplied text is
  not used.
- Clear empties the input and returns to the unfiltered default; it is
  disabled when there is nothing to clear.
- Input, Apply, Clear and Refresh are disabled while loading.
- Empty result: "No simulated fills recorded yet." with no filter, "No
  simulated fills for XYZ." with one.
- Stale responses: the effect is keyed on `[refreshKey, appliedSymbol]` and its
  cleanup flips an `active` flag, so a response for a superseded filter (or
  after collapse) is ignored.
- Applying the same symbol again, or applying an empty box when nothing is
  applied, changes no state and therefore triggers no request; Refresh is the
  way to re-read. This matches the orders section.

Documentation: `execution-engine-design.md` §6.3 gains an as-built note with
two diagrams, directly after the fills read-path diagrams. `TESTING.md` records
the verification.

Decision number: none assigned. This delivery runs in parallel with other work,
so the slug `execution-fills-symbol-filter-implementation` is the temporary
identifier; `confirmed-decisions.md` and `INDEX.md` are deliberately untouched.
The existing decisions #181 and #183 already cover the routes this consumes;
whether a UI-only filter needs its own number is left to integration, after
re-checking `main` and the canonical logs (`origin/main` at packaging:
`a725d5d`).

## Findings (recorded, not acted on)

- `execution-engine-design.md`'s orders read-path section ("Frontend read path
  (as built, `execution-panel-order-history`)") does not mention the orders
  symbol filter that `execution-orders-symbol-filter` added; the orders filter
  is documented only in `CHANGES.md`/`TESTING.md`. Related follow-up, not done
  here.
- The panel has no test runner; the repo has no `test` script or frontend test
  files. Verification used a scratch harness that is not shipped (see
  `TESTING.md`). Adding a permanent frontend test setup would be a separate
  decision.

## Boundary

No backend, migration, ledger write, trading-control or trading-behavior change.
`RecentSimulatedOrders`, `normalizeSymbolFilter`, `emptyOrdersMessage`, the
fill row rendering and the `ExecutionFillWireShape` type are untouched.
`package.json`, `package-lock.json` and `tsconfig.tsbuildinfo` are unchanged.

<!-- Previous delivery record retained below. -->

# CHANGES — `restore-protective-exits-record`

## Current delivery

Documentation-only repair. No application code, migration, test, or
`.gitignore` change; no decision number assigned or altered.

**What went wrong.** Commit `a00dbd0` (labeled `execution-fills-symbol-filter`)
landed on top of `4aea47f` (`simulated-protective-exits`, decision #184). Five
shared files in it are byte-identical to their `cbca16c` (decision #183) state
— `CHANGES.md`, `TESTING.md`, `docs/decisions/INDEX.md`,
`docs/decisions/confirmed-decisions.md`, and
`docs/architecture/execution-engine-design.md` — consistent with a stale-base
overwrite that discarded everything those files had gained since `cbca16c`. The
#184 code (migration `0015`, `exit_ledger.py`, the Position Monitor hand-off,
startup ordering) stayed on `main`, so the record described an older system
than the code. `models/execution_ledger.py` still cited "decision #184", a
number absent from the log.

```
cbca16c #183 ──► f0a6249 ──► 3fdaacf ──► 4aea47f #184 ──► a00dbd0 ──► 9dcb879
                 orders      fills        protective      5 shared      positions
                 symbol      panel        exits           docs reset    route docs
                 filter                   (+code)         to cbca16c    (built on a00dbd0)
                    │           │             │               │              │
                    └───────────┴─────────────┴──── lost ─────┘              │
                        records/text, restored here from 4aea47f             │
                                                      preserved as-is ───────┘
```

**What was restored, all from `4aea47f`:**

- **Decision log.** Row 184 in `INDEX.md` and the `### 184.` entry at the true
  end of `confirmed-decisions.md`, byte-identical to `4aea47f`. #184 keeps its
  original number; no other decision was touched.
- **`execution-engine-design.md`.** Passages `a00dbd0` reverted, re-applied as
  that commit's inverse (not a file replacement): the header status; invariant
  I2; §6.3's fills-panel "Frontend read path (as built,
  `execution-panel-fill-history`)" with its two diagrams, replacing "No
  frontend consumer yet"; §6.5's "not wired" paragraph; §6.6's observer
  paragraph, the "As built (`simulated-protective-exits`)" section with both
  diagrams and the startup/reconciliation paragraph, and the Output bullet; §7's
  intro, the EX-5 table row, heading and as-built resolution; §7.1's EX-5 item
  and J4; and the World View bullet in the deferred list.
- **`CHANGES.md` / `TESTING.md`.** Three delivery records per file, verbatim:
  #184 (`simulated-protective-exits`), `execution-panel-fill-history`, and
  `execution-orders-symbol-filter`. The last two were not #184 material but
  were lost by the same overwrite. They sit below the `execution-positions-route`
  record and above #183, keeping newest-first order.

**Preserved:** everything `9dcb879` added — the `execution-positions-route`
as-built section, both diagrams and the `positions` row in §6.8 of the design
doc, and its `CHANGES.md`/`TESTING.md` records. The design doc now equals
`4aea47f` plus exactly those additions.

**Contradictory status statements repaired.** Restoring the design doc replaces
the reverted statements that EX-5 was still open, that no exit hand-off existed
(§6.6), that fills had "No frontend consumer yet", and J4's pre-#184 recovery
text. The `execution-positions-route`
record's two findings that said #184 was "not restored here" and the fills note
"still says No frontend consumer yet" were true of that delivery; their text is
unchanged and each now carries a one-line "Resolved afterwards by
`restore-protective-exits-record`" pointer.

## Findings (recorded, not acted on)

- `a00dbd0` also added six lines to `.gitignore` (`*.sqlite`, `*.db`,
  `pgdata/`, `.*-validation/`, plus a comment). Not documentation and not part of
  this task; left as is. `.*-validation/` overlaps the existing
  `.exit-validation/` entry.
- `execution_engine/engine.py` `_process_one` still says the `OrderApproved`
  close branch "needs EX-5, still open". That branch (an authorizer-approved
  close) is still unbuilt, but the wording predates EX-5's simulated
  stop/target resolution. Code untouched.
- `INDEX.md` rows 80–90 point to `confirmed-decisions.md`, while
  `archive/080-090.md` exists. Pre-existing and unrelated to #184; not
  touched.
- The design doc's status header cites `simulated-protective-exits` by slug and
  "Decisions #171–#183", while J4 cites "#184". Both refer to the same
  decision, as they did at `4aea47f`; not harmonized here.
- No `execution-fills-symbol-filter` work is present on `main` (the fills
  route already had `symbol` in #183). If that task exists elsewhere, it was
  built on a `cbca16c` base and should be re-based before it is applied.

## Boundary

Five files change: `CHANGES.md`, `TESTING.md`,
`docs/decisions/INDEX.md`, `docs/decisions/confirmed-decisions.md`, and
`docs/architecture/execution-engine-design.md`. The diff against `9dcb879` is
insertions only apart from the design doc's reverted passages and two
annotation lines.

## Package

`restore-protective-exits-record.zip` contains those five files, root-relative.
Base: `9dcb879` (`origin/main`, unchanged at packaging). Not merged or pushed.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-positions-route`

## Current delivery

Added `GET /intelligence/execution-positions`, a read-only diagnostic view of
persisted simulated positions — the third HTTP view over the execution ledger
after `execution-orders` (#181) and `execution-fills` (#183), and the first
over `positions` (Portfolio State's durable projection of `fills`). Internal
code already reads the table (Portfolio State restore/Session API, startup
reconciliation, the Execution Engine's exit ledger); no HTTP route exposed the
persisted rows.

- `backend/app/api/routes/intelligence.py`: new `_fetch_execution_positions()`
  (module-level, patchable, opens/closes its own `Session` inside the worker)
  and the route, run through `asyncio.to_thread` like the sibling routes.
  Hard-scoped to `Position.execution_mode == "simulated"` (not a parameter; no
  join needed because `Position` has its own mode column). Optional exact
  `symbol`, `limit` bounded `[1, 100]` default 50, honest `{"positions": []}`
  when empty.
- Ordering: `opened_at` descending, then `position_id` descending. The pair is
  a strict total order (`position_id` is the primary key), so repeated reads
  and a `limit` cutting through tied rows are stable. The tie-break is
  deterministic but not chronological (`position_id` is a random `uuid4`).
- Response fields: `position_id`, `trade_id`, `symbol`, `side`, `qty`,
  `status`, `avg_price`, `stop`, `target`, `opened_at`, `closed_at`,
  `realized_pnl`. `avg_price`/`stop`/`target`/`realized_pnl` are exact decimal
  strings; unset `stop`, `target`, `closed_at`, `realized_pnl` are `null`.
  Field meanings are Portfolio State's own: `qty` is the quantity currently
  held (0 once closed), `avg_price` the weighted-average entry cost,
  `realized_pnl` lifetime gross realized P&L before commissions.
- `backend/tests/test_execution_positions_route.py` (new, 23 tests): mode
  isolation (backtest/paper/live excluded; an `execution_mode` query parameter
  cannot widen scope), exact symbol filter, newest-first ordering, `opened_at`
  ties and a limit cutting through a tie, limit cap/default/bounds, empty
  results, exact-decimal and null serialization, partially reduced and closed
  rows, read-only behavior, and an event-loop responsiveness regression.
- `docs/architecture/execution-engine-design.md` §6.3: as-built note plus
  component data-flow and route internal-flow diagrams; §6.8 `positions` row
  annotated.

Decision number: none assigned. This delivery runs in parallel with another
task, so the slug `execution-positions-route` is the temporary identifier;
`confirmed-decisions.md` and `INDEX.md` are deliberately untouched. The final
number is to be assigned at integration after re-checking `main` and the
canonical logs (`origin/main` at packaging: `a00dbd0`, unchanged since the
task started).

Findings (recorded, not worked around):

- The tests do not boot the app lifespan. They insert `positions` rows with no
  fills behind them; booting the lifespan with such a row logs a
  `PositionLedgerError: positions do not match durable fill history` from
  `portfolio_state/postgres.py` `load_state()` (the row is left unmodified).
  Requests go through `httpx.ASGITransport`, which skips the lifespan.
- In that same experiment `GET /health/execution-startup` reported `ready`
  although Portfolio State's restore had raised. Seen once; not investigated.
- Commit `a00dbd0` (labeled `execution-fills-symbol-filter`) removed the
  decision #184 entries from `confirmed-decisions.md`/`INDEX.md`, the #184
  sections of `execution-engine-design.md`, and the top records of `CHANGES.md`
  and `TESTING.md`, while the #184 code (migration `0015`, `exit_ledger.py`)
  remains on `main`. Not restored here (out of scope; it needs a decision-log
  action). **Resolved afterwards by `restore-protective-exits-record`** (top
  record of this file).
- `execution-engine-design.md`'s fills note still says "No frontend consumer
  yet", although the frontend fill-history panel commit exists on `main`.
  **Resolved afterwards by `restore-protective-exits-record`**: the
  fill-history read path is restored to §6.3.

## Boundary

No writes, migrations, frontend, position accounting, exit placement, or
trading-control changes. `governor/`, `execution_engine/`,
`portfolio_state/`, `models/execution_ledger.py`, and `main.py` untouched. No
mark price, unrealized P&L, exposure or daily total is computed; rows come from
the table, not `PortfolioState.get_snapshot()`'s cache. The `opened_at` sort has
no supporting index (only `(symbol, status)` and `trade_id` exist); acceptable
at diagnostic volume, not fixed here.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #184: simulated protective exits

## Current delivery

Finished the existing simulated stop/target exit work. Position Monitor now
hands those observations to Execution Engine, which persists a position-bound
request, cancels unfinished entries, waits for fill accounting, and reserves
one reduce-only close order for the committed remaining quantity. Failed close
attempts retry with a new deterministic attempt ID. Simulated venue fills use
the existing fill ledger and Portfolio State path; a full close marks the
trade closed. EOD flatten remains observed only.

Migration `0015` adds `exit_requests`, `orders.position_id`, and a partial
unique index preventing two active closes for one position. Startup applies
pending fills before reconciliation. Reconciliation validates approved exits
without placing them; the execution worker rechecks safety immediately before
submission. A fresh simulated venue with a missing position blocks execution
on discrepancy.

Updated the execution and trading architecture, system design, decision
index/log, and `TESTING.md`. Added PostgreSQL reservation and real-lifespan
stop/target tests. Decision #184 resolves EX-5 for simulated stop/target exits
only; live outcome writing and other exit modes remain separate work.

## Package

`simulated-protective-exits.zip` contains the changed application files,
migration, tests, architecture and decision records, `CHANGES.md`, and
`TESTING.md`, all root-relative. The local `.exit-validation/` database is
excluded.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-panel-fill-history`

## Current delivery

Added a typed `fetchExecutionFills()` client for decision #183's existing
`GET /intelligence/execution-fills` route and a compact "Recent simulated
fills" section to the Execution panel. Opening the panel fetches the
unfiltered default 50 simulated fills; the section's Refresh fetches again.
Rows retain the route's descending `ledger_seq` order and show symbol,
quantity, exact price string, venue time, known exact commission, and any
anomaly. Unknown commission is omitted. Loading, empty, and request error
states are distinct, and late responses after collapse or refresh are ignored.

The persisted fills section remains separate from the transient WebSocket
activity feed. There is no backend, database, polling, or trading-control
change. The existing execution design now describes this as-built frontend
path. No new decision was needed: decision #183 establishes the read contract,
and this consumer follows the existing panel's persisted-order read pattern.

## Package

`execution-panel-fill-history.zip` contains the two frontend files,
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, and
`TESTING.md`, all root-relative.

# CHANGES — `execution-orders-symbol-filter`

## Current delivery

Added a usable symbol filter to the Execution panel's "Recent simulated
orders" section (`ExecutionLifecyclePanel.tsx`). The backend route this
calls, `GET /intelligence/execution-orders` (decision #181), already
accepted an optional, exact-match `symbol` query parameter with no
frontend caller ever passing it — this delivery is that missing caller,
not a contract change.

`fetchExecutionOrders` (`api-client.ts`) now takes an optional `symbol`
and appends `?symbol=<encoded>` when supplied, via the same
ternary/`encodeURIComponent` pattern `fetchOpportunities` already uses —
omitted entirely when absent, matching the backend's own "no `symbol` ->
every symbol" default.

The panel section gained a compact input, "Apply" and "Clear" controls
(styled and behaviorally matching `ScannerPanel.tsx`'s universe-add
input: uppercase-as-typed, trim-on-submit, Enter submits). Two pure
helpers do the actual work, alongside this file's existing
`formatTime`/`formatNum`/`describeEvent`:

- `normalizeSymbolFilter` — trims and uppercases the typed value; empty
  after trimming clears the filter (`undefined`) instead of sending
  `symbol=`, which would exact-match nothing and return zero rows
  instead of "no filter."
- `emptyOrdersMessage` — distinct empty-state text: "No simulated orders
  recorded yet." with no filter vs. "No simulated orders for SYMBOL."
  with one applied.

The active filter is tracked separately from the existing manual-refresh
counter, so pressing Refresh keeps whatever filter is currently applied
(refresh and filter both just feed the same `useEffect`, which already
re-fires on either changing). The existing stale-request-cancellation
`active`-flag pattern is unchanged in shape, now scoped by both
`refreshKey` and the applied symbol, so switching the filter quickly (or
Refresh firing mid-flight) still lets a superseded response arrive and be
silently discarded rather than overwriting newer data.

No backend route, database model, event feed, or order-placement code
changed. No architecture decision needed — the backend contract this
uses was already approved and shipped under decision #181; this is a
frontend caller catching up to an existing capability, the same posture
`count-lower-bound-validation`/`scanner-override-ticker-validation` took
for their own no-decision-number deliveries.

## Boundary

Exactly two application files change: `frontend/src/services/api-client.ts`
(`fetchExecutionOrders` signature and its leading comment only) and
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`
(`RecentSimulatedOrders` and two new module-scope pure helpers only —
`ExecutionLifecycleBody`, `StartupStatusLine`, `ObservedExitTriggers`, and
every other component/export in the file are untouched). Plus this file
and `TESTING.md`. No backend file, migration, test, or documentation
outside these four changes.

# CHANGES — decision #183: `execution-fills-route`

## Current delivery

Added `GET /intelligence/execution-fills`, a read-only diagnostic view of
persisted simulated fills — the `fills` counterpart to decision #181's
`GET /intelligence/execution-orders`, and the first HTTP route over the
`fills` ledger (Portfolio State, the governor's `PortfolioStateReader` and
`fill_ledger.py` already read it internally).

- `backend/app/api/routes/intelligence.py`: new `_fetch_execution_fills()`
  (module-level, patchable, opens/closes its own `Session` inside the worker)
  and the route. `fills` is `INNER JOIN`ed to `orders` on `client_order_id`
  because `Fill` has no `execution_mode` or `symbol` column; the read is
  hard-scoped to `Order.execution_mode == "simulated"` (not a parameter).
  Optional exact `symbol` (on the order), `limit` bounded `[1, 100]` default
  50, newest `ledger_seq` first, honest `{"fills": []}` when empty.
- Response fields: `ledger_seq`, `client_order_id`, `trade_id`, `symbol`,
  `execution_venue` (the fill's own), `venue_fill_id`, `qty`, `price`,
  `venue_ts`, `commission`, `anomaly`, `created_at`. `price` and `commission`
  are exact decimal strings; unknown `commission` is `null`.
- `backend/tests/test_execution_fills_route.py` (new, 15 tests): ordering,
  exact-symbol filtering (case/partial rejected), backtest-mode isolation via
  the join, limit bounds and default, empty result, curated serialization,
  null and exact-string commission, anomaly passthrough, and an event-loop
  responsiveness regression (blocked read vs `/health`).
- `docs/architecture/execution-engine-design.md` §6.3: as-built note,
  component data-flow diagram and route internal-flow diagram; §6.8 `fills`
  row annotated.
- `docs/decisions/confirmed-decisions.md` + `INDEX.md`: decision #183
  (observed next number 183, assigned 183; `origin/main` re-checked at
  packaging, unchanged).

Findings (recorded, not worked around): `TestClient(app)` runs the real
`lifespan()`, which reconciles hand-inserted simulated fills into
`positions`/`position_fill_receipts`, so the new tests' cleanup covers those
tables; a fill larger than its order is retroactively flagged `overfill`, so
test quantities are kept consistent; opening `TestClient(app)` twice in one
test function fails at the second shutdown (`bus.stop()`, "bound to a
different event loop") — no other test does this, so the tests use one boot
per function and the bus was not modified.

## Boundary

No writes, migrations, trading controls, or frontend. `governor/`,
`execution_engine/`, `portfolio_state/`, `models/execution_ledger.py`, and
`main.py` untouched. Flagged, not acted on: `confirmed-decisions.md` is now
~200KB, well past the ~100KB rollover threshold (a standing follow-up).

<!-- Previous delivery record retained below. -->

# CHANGES — `backtest-isolation-flake-fix`

## Current delivery

Fixed the intermittent failure in
`test_two_separate_runs_isolate_level_interaction_state_and_events`
(`backend/tests/test_backtest_routes.py`). `level_interaction_state`
legitimately tracks each `level_key` once per timeframe (this scenario's
replay produces independent `1m`/`5m`/`15m`/`1h` rows for `level_key
== "vwap"`), but the test's own comparison query selected and ordered by
`level_key` without `timeframe`, then picked "the" vwap row with
`next(row for row in ... if row["level_key"] == "vwap")`. Four rows tied
on that predicate per run; SQL gives no guaranteed order among tied rows
without an explicit tiebreaker, so the row returned first — and therefore
compared — could differ from run to run independent of any real state
divergence. Reproduced on a freshly migrated database, first attempt, 4
times out of 5 consecutive attempts, always with the same
`14:30`/`16:02` signature; confirmed by direct column dump that the two
runs' actual per-timeframe state was identical every time, and that only
the query's tied-row selection varied.

Not a production defect: `LevelInteractionEngine`/`FeatureEngine` are
constructed fresh per backtest run with run-scoped DB reads/writes
(decision #160/D20), VWAP's cold-start backfill only ever reads *live*
(non-backtest) candle history, and production's own `get_snapshot()`
already nests its results by timeframe correctly. Checked
`test_backtest_sweep_route.py`, `test_level_interaction_engine.py`,
`test_replay_state_producer.py`, and `test_symbol_namespace.py` for the
same pick-one-of-several-ties pattern — found nowhere else.

Fix, in `backend/tests/test_backtest_routes.py` only: added
`lis.timeframe` to the state query's `SELECT`/`ORDER BY`, and filtered the
`vwap_rows` lookup to `timeframe == "1m"` (the timeframe every v1 strategy
actually reads, decision #99) in addition to `level_key == "vwap"`. The
test's meaningful contract — two identical replays must produce
independent, equivalent persisted state — is unchanged; it now checks that
contract against one well-defined row instead of an arbitrarily-selected
one among four legitimate rows. No assertion weakened, no sleep added, no
production file touched.

Also corrected three prior `TESTING.md` entries
(`count-lower-bound-validation`, `execution-authorizer-and-engine`,
`execution-ledger-and-venue`) that had each independently observed this
same failure and mischaracterized it — twice as this project's own
long-documented `#119` `FeatureEngine`-warmup cluster (a different,
unrelated set of tests), once as "non-reproducible against a pristine
database" (it reproduces on one 80% of the time, first attempt). Corrected
in place with dated footnotes rather than rewritten, so both the original,
honestly-reported-at-the-time conclusion and this correction remain
visible. No decision number assigned — a test-query correction, not an
architecture change.

## Boundary

Application code is entirely untouched. The only file changed is
`backend/tests/test_backtest_routes.py` (two `SELECT`/`ORDER BY` query
lines and the `vwap_rows` lookup filter, plus an explanatory comment), and
documentation (`TESTING.md`, this file). No other test file, route,
engine, or migration changed.

<!-- Previous delivery record retained below. -->

# CHANGES — `count-lower-bound-validation`

## Current delivery

`GET /market/candles` and `GET /intelligence/series` both declared their
`count` query parameter as `Query(240, le=1000)` — an upper bound with no
lower one. `count=0` or a negative `count` previously passed FastAPI's own
request validation untouched and reached each route's retrieval logic with
an invalid range: `start = end - timedelta(minutes=... * count)` produces a
zero-width (`count=0`) or inverted (`count<0`) `start`/`end` window, and the
final `[-count:]` slice on the resulting candle list has its own misleading
behavior at those values — `count=0` means `[-0:]`, a genuine Python slice
quirk that means "from index 0," i.e. the WHOLE list, not "the last zero
items," so a caller asking for zero candles got back everything instead;
`count<0` is a positive-index slice at that point (`recorded[-(-1):]` ==
`recorded[1:]`), an unrelated and equally misleading result.

Both routes now declare `count: int = Query(240, ge=1, le=1000)` — the same
`Query(..., ge=1, le=N)` bounding convention `GET /scanner/state`'s `top_n`
and `GET /intelligence/execution-orders`' `limit` already use. `count=0` or
negative is now a clean `422` at the request-validation layer, before any
retrieval logic runs. The pre-existing upper bound (`le=1000`), the default
(`240`), and every other query parameter, retrieval path, and response
shape on both routes are unchanged — including candle-store/aggregator
lookup order and external-provider fallback on `GET /market/candles`, and
the Feature-Engine-scoped, no-fallback retrieval on `GET /intelligence/series`.

No new product or architecture decision was needed — this closes a gap in
an existing parameter's validation range, the same "reuse an existing
bounding convention at a new call site" shape `scanner-override-ticker-
validation` and `scanner-route-db-offload` already used — so no decision
number was assigned and `docs/decisions/confirmed-decisions.md`/`INDEX.md`
are untouched.

`backend/README.md` updated: the `GET /market/candles?symbol=&count=&
timeframe=` bullet now states the `[1, 1000]` bound and the failure mode it
closes; the `test_market_routes.py` test-table row now mentions the new
`count`-bound coverage. Verification is recorded in `TESTING.md`.

## Boundary

Application code changes are confined to the `count` parameter declaration
on `GET /market/candles` (`backend/app/api/routes/market.py`) and `GET
/intelligence/series` (`backend/app/api/routes/intelligence.py`) — one line
each, plus an explanatory comment. No other query parameter, function
signature, retrieval logic, or response shape on either route changed.
Candle retrieval, series computation, and provider behavior are byte-for-
byte unchanged for every `count` value that was already valid (`1`–`1000`).

<!-- Previous delivery record retained below. -->

# CHANGES — `layout-import-fault-isolation`

## Current delivery

Fixed `importLayouts()` in `frontend/src/state/WorkspaceContext.tsx`: it
previously mapped an entire imported "export layouts" JSON array in one
`.map()` call — the exact same shape `loadSavedLayouts()` had before its own
fix (`saved-layouts-restore-isolation`, retained below). One malformed entry
in the imported file (a missing or non-array `subWindows` field, or a
sub-window shape that made `normalizeSubWindow()` itself throw) threw out of
that single `.map()`, was caught by the function's outer try/catch, and
rejected every other, otherwise-valid layout in the same file along with it.

The parsing logic is now a new module-level `normalizeImportedLayouts()`,
extracted from the `importLayouts()` closure the same way `loadSavedLayouts()`
is already its own module-level function — this makes it directly callable
for verification, not just reachable through a React state setter. Each
imported entry is now normalized inside its own try/catch, the identical
per-entry isolation `loadSavedLayouts()` already uses, so one bad entry in
the file is skipped and every other valid entry still imports. Accepted
entries are given a fresh `id` (the imported file's own ids are never
reused) with every other field preserved as normalized — unchanged from
before. The outer try/catch is unchanged and still covers unparsable JSON
and a non-array top level for the file as a whole, returning no entries in
either case, exactly as before. `importLayouts()` itself now just calls
`normalizeImportedLayouts()` and appends whatever it returns to
`savedLayouts`; a file that yields nothing valid leaves `savedLayouts`
untouched, the same net effect the old all-or-nothing outer catch produced
for that case.

Updated `docs/architecture/system-design.md` §4.11 with a "Layout import
fault isolation" note plus a data-flow and an internal-flow diagram,
directly below the existing "Saved layout restoration resilience" note it
mirrors. Verification is recorded in `TESTING.md`. This follows the same
per-entry fault-isolation convention `loadSavedLayouts()` itself now uses;
no new product or architecture decision was needed, and no decision number
was assigned.

## Boundary

Application code changes are confined to `frontend/src/state/WorkspaceContext.tsx`:
`importLayouts()` (now a thin wrapper) and the new `normalizeImportedLayouts()`
function. `loadSavedLayouts()`, `normalizeSubWindow()`, `normalizeMainWindow()`,
`loadSession()`, and every other saved-layout or session interaction
(save, load, delete, export) are unchanged. No `SavedLayout` field or
saved-field contract changed.

<!-- Previous delivery record retained below. -->

# CHANGES — `daily-levels-lookback-offload`

## Current delivery

Moved `GET /intelligence/state`'s optional `daily_levels_lookback_days`
reclustering path off the event loop. That path used to call
`FeatureEngine.get_daily_levels()` synchronously, in-line, inside the
async route handler — genuine CPU-bound work (`cluster_daily_levels()`,
no `await` inside it), not I/O, but the same event-loop-blocking symptom
as a blocking DB read. Measured directly: ~2.6ms for a realistic 360-candle
cache at the server's own default lookback (180 days), but ~21ms under a
pathological near-uniform-price 360-candle cache and ~157ms at a
1000-candle custom lookback under the same shape — real enough to stall
concurrent requests, `/health` included. The call now runs via
`await asyncio.to_thread(_compute_daily_levels_lookback, symbol,
daily_levels_lookback_days)`, a new module-level helper following this
file's existing `_fetch_execution_orders`/`_fetch_strategy_outcomes`/
`_fetch_backtest_runs` offload convention. Response shape, cached-input
behavior, and the default (no-lookback) path are all unchanged; the
default path never reaches this code and pays none of this, before or
after. Added a deterministic blocked-reclustering concurrency regression
(confirmed to fail against the pre-fix synchronous call before being
confirmed to pass against the fix).

Updated `docs/architecture/trading-intelligence-architecture.md` §3 with
an as-built note plus component data-flow and route internal-flow
diagrams. This uses the existing read-route offload pattern (also used by
`performance-analytics-route-read-offload` and
`intelligence-history-read-offload` below), so — following that same
precedent — no new architectural decision or decision number was needed.
Verification is recorded in `TESTING.md`.

## Boundary

Only `backend/app/api/routes/intelligence.py` (the one route branch plus
the new helper) and `backend/tests/test_intelligence_history_read_
concurrency.py` (one new test) changed, plus this delivery's own
`CHANGES.md`/`TESTING.md`/architecture-doc records. No clustering rule,
no database schema, and no other route changed — including `GET
/intelligence/state`'s own default (no-lookback) path.

<!-- Previous delivery record retained below. -->

# CHANGES — `saved-layouts-restore-isolation`

## Current delivery

Fixed `loadSavedLayouts()` in `frontend/src/state/WorkspaceContext.tsx`: one
malformed saved layout (missing or non-array `subWindows`, or a sub-window
shape that made `normalizeSubWindow()` itself throw) previously threw out of
the single `.map()` call over the whole array, was caught by the function's
own outer try/catch, and discarded every other, otherwise-valid saved layout
along with it. Each saved layout is now normalized inside its own try/catch,
so one bad entry is skipped and every other valid layout still restores. The
outer try/catch is unchanged and still covers storage access and unparsable
JSON for the collection as a whole; `normalizeSubWindow()` itself, the
`SavedLayout` shape, `loadSession()`/`normalizeMainWindow()`, and every other
saved-layout interaction (save, load, delete, export, import) are unchanged.

Updated `docs/architecture/system-design.md` §4.11 with a "Saved layout
restoration resilience" note plus a data-flow and an internal-flow diagram,
matching the existing Info panel/Feature Engine panel restoration write-ups
in the same section. Verification is recorded in `TESTING.md`. This follows
the same per-entry fault-isolation convention this codebase already uses at
the collection level (`loadSession()`'s and `importLayouts()`'s own outer
try/catch); no new product or architecture decision was needed, and no
decision number was assigned.

## Boundary

The only application code change is in
`frontend/src/state/WorkspaceContext.tsx` (`loadSavedLayouts()` only — no
other function in that file changed). No other saved-layout field, workspace
interaction, or session-format change.

<!-- Previous delivery record retained below. -->

# CHANGES — `info-panel-session-restore`

## Current delivery

Older workspace sessions now receive `makeMainWindow()`'s existing Info
panel defaults in `normalizeMainWindow()`: `infoCollapsed: false` and
`infoWidthPx: 300`. Each missing field is backfilled independently with
`??`, preserving explicitly saved collapsed and expanded states and custom
widths. The Info panel's interactions and other saved fields are unchanged.

Updated `docs/architecture/system-design.md` §4.11 with Info restoration
data-flow and internal-flow diagrams. Verification is recorded in
`TESTING.md`. This follows the established Scanner and Feature Engine
restoration pattern; no new product decision or decision number was needed.

## Boundary

The only application code change is in `frontend/src/state/WorkspaceContext.tsx`.
The earlier uncommitted analytics delivery in this workspace remains intact.

<!-- Previous delivery record retained below. -->

# CHANGES — `performance-analytics-route-read-offload`

## Current delivery

Moved the synchronous query calls in `GET /intelligence/win-rate-by-hour`
and `GET /intelligence/expectancy-by-session-type` to `asyncio.to_thread`.
Both routes retain their three filters, strict live/backtest selection in
the query layer, `ValueError` to HTTP 400 mapping, and response envelopes.
Added a deterministic blocked-query concurrency regression for each route.

Updated `docs/architecture/trading-intelligence-architecture.md` §14 with
component data flow and route internal flow diagrams. This uses the existing
read-route offload pattern, so no new architectural decision was required.
Verification is recorded in `TESTING.md`.

## Boundary

Only the two analytics route calls, one concurrency test, the canonical
architecture record, and this delivery's change and test records changed.
The query SQL and other intelligence routes are unchanged.

<!-- Previous delivery record retained below. -->

# CHANGES — `feature-engine-panel-session-restore`

## Current delivery

Older workspace sessions now receive the Feature Engine panel defaults in
`normalizeMainWindow()`: collapsed `true`, width `300`, and symbol
`DEFAULT_SYMBOL`. Each missing field is filled independently with `??`, so
an explicitly expanded panel (`false`), custom width, and selected symbol
survive restoration. This follows the existing Scanner backfill pattern and
changes no panel interaction or saved field contract.

Updated `docs/architecture/system-design.md` §4.11 with the as-built data
flow and internal flow diagrams, and added a current-state pointer in
`docs/architecture/scanner-design.md`. Verification is recorded in
`TESTING.md`. No new decision was needed: this repairs session restoration
using established defaults.

## Boundary

Application code changes only in `frontend/src/state/WorkspaceContext.tsx`.
No panel, backend, API, or other saved field changes.

<!-- Previous delivery record retained below. -->

# CHANGES — `intelligence-history-read-offload`

## Current delivery

Completed the in-progress offload of `GET /intelligence/strategy-outcomes`
and `GET /intelligence/backtest-runs`. Both routes retain their existing
validation, query semantics, and JSON contracts while their synchronous
database reads and row serialization run in worker threads. Each helper owns
its database session. Added a concurrency regression for both routes.

Updated the backtest architecture record with the current route flow and
corrected the execution architecture record's now-stale comparison. This
follows the established scanner and execution-orders offload pattern; no new
architecture decision was needed.

## Boundary

Only the two history routes in `backend/app/api/routes/intelligence.py`, their
route-test wording, one new concurrency test, the two affected architecture
documents, and this delivery's `CHANGES.md`/`TESTING.md` records change.

<!-- Previous delivery record retained below. -->

# CHANGES — `execution-panel-order-history` (decision #182)

## Current delivery

Added a read-only Recent simulated orders section to the Execution panel.
The typed API client requests the existing simulated-only
`GET /intelligence/execution-orders` route without query parameters, retaining
its default newest-first 50-row cap. The section loads on panel expansion
and manual Refresh, and shows symbol, side, open/close effect, quantity,
status, venue, updated time, and any exit or rejection reason. Loading,
request failure, and an empty ledger each have their own visible state.
Persisted orders stay separate from the transient WebSocket activity feed.

Updated `docs/architecture/execution-engine-design.md` §6.3 with the
as-built frontend behavior and component data-flow and internal-flow
diagrams; corrected its deferred-frontend note. Appended decision #182 to
`docs/decisions/confirmed-decisions.md` and its index entry. Verification is
recorded in `TESTING.md`.

## Boundary

Application changes are limited to `frontend/src/services/api-client.ts` and
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`. No backend,
migration, order action, polling, or global state change.

<!-- Previous delivery record retained below. -->

# CHANGES — `scanner-panel-session-restore`

## Current delivery

Fixed Scanner panel state when loading a workspace session saved before the
Scanner panel existed. `makeMainWindow()` defaults `scannerCollapsed` to
`true` and `scannerWidthPx` to `300` for a freshly created Main Window, but
`normalizeMainWindow()` — the function that back-fills every field missing
from an older localStorage session (same pattern already applied to
`lastBacktestRunId`/`lastBacktestSweepId`, decisions #134/#163) — never
gained an equivalent backfill for these two fields when §12 of
`scanner-design.md` introduced them, a gap that section's own text already
flagged as inherited rather than fixed. A session written before the Scanner
panel existed left both fields `undefined` at runtime: `ScannerPanel.tsx`
then rendered in its expanded branch (`undefined` is falsy) with an
`undefined` width, and the drag-resize handle's width arithmetic produced
`NaN`, permanently breaking that Main Window's resize handle until reload.

`normalizeMainWindow()` now backfills `scannerCollapsed ?? true` and
`scannerWidthPx ?? 300` — the same defaults `makeMainWindow()` itself uses,
and the same `??`-based pattern already used two lines above for the
backtest fields. `??` rather than `||` is required for `scannerCollapsed`
specifically so an explicitly saved `false` (panel left expanded) survives
the backfill untouched rather than being silently re-collapsed. No other
field, component, API call, poll, or panel changed.

Verified by direct execution of the changed function against five
representative fixtures (fully-old session, explicit non-default values,
explicit default values, partial old session, and unrelated fields) — see
`TESTING.md` — plus a clean `tsc -b` and `vite build`. Updated
`docs/architecture/scanner-design.md` with a new §15 documenting the fix
(as-built notes plus two diagrams) and a one-clause forward-reference added
to §12's own text.

## Boundary

Exactly one application file changes: `frontend/src/state/WorkspaceContext.tsx`
(two lines added inside `normalizeMainWindow`, plus their comment — nothing
else in the file touched). Docs: `docs/architecture/scanner-design.md`
(new §15, one clause added to §12), plus this file and `TESTING.md`.
Untouched: `ScannerPanel.tsx`, `normalizeSubWindow`, `loadSession`, every
API call, poll, and universe-editing code path, every other panel
(`featureEngineCollapsed`/`featureEngineWidthPx` keep the identical,
still-unfixed gap), and every backend file. No decision number assigned —
see `scanner-design.md` §15's closing note for why.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #181: `execution-orders-route`

## Current delivery

Added `GET /intelligence/execution-orders`, a bounded, read-only endpoint over
the `orders` ledger (decision #172) — the first reader of that table anywhere
in this codebase; every existing import of `Order` (`governor/`,
`execution_engine/`, `portfolio_state/`) is a write. Returns the most recent
rows for `execution_mode == "simulated"` (hard-scoped, not a query parameter —
the only mode any row can honestly carry until a real venue exists), ordered
by `orders.id` (the ledger's own monotonic primary key) descending, with an
optional exact `symbol` match and `limit` bounded `[1, 100]` (default 50).
Response fields are curated, not a full-row dump: order identity (`id`,
`client_order_id`), `trade_id`, `symbol`, `side`, `position_effect`, `qty`,
`status`, `execution_venue`, `exit_reason`, `reject_reason`, `created_at`,
`updated_at`. An empty table, or a `symbol` with no matches, returns
`{"orders": []}`, never an error — the same honest-empty convention every
route in this file already follows.

The synchronous SQLAlchemy read runs through a new module-level
`_fetch_execution_orders()` helper, wrapped in `asyncio.to_thread` at the
route boundary — `scanner-route-db-offload`'s established convention for
keeping a blocking DB read off the event loop, rather than the inline,
loop-blocking pattern this file's own older `GET /strategy-outcomes` and
`GET /backtest-runs` routes still use (flagged, not silently repeated).
`_fetch_execution_orders` opens and closes its own `Session` entirely inside
the worker thread.

New `backend/tests/test_execution_orders_route.py` (13 focused tests, real
Postgres, hand-inserted `trades`/`orders` rows): descending-ledger-id
ordering; exact-symbol filtering (including that a lowercase or substring
query does not match); hard exclusion of `backtest`-mode rows even though the
DB's own mode/venue CHECK allows them to exist; `limit` bounds (422 below/
above, both edges accepted, default confirmed); an honest empty collection
for an unmatched symbol; curated-field response shape with UUID/timestamp
serialization verified by parsing them back, and confirmation that
`order_type`/`limit_price`/`venue_order_id`/`execution_mode` are absent from
the response; a nullable `reject_reason` populated on a rejected row; and a
concurrency regression (mirroring `test_scanner_route_concurrency.py`'s own
deterministic `threading.Event` technique) proving a blocked read does not
block a concurrent `/health` request. Updated
`docs/architecture/execution-engine-design.md` §6.3 with an as-built note
plus a data-flow diagram and an internal-flow diagram, and annotated §6.8's
`orders` row as now read.

A file-disjoint sibling, `scanner-override-ticker-validation`, merged to
`main` first, mid-task; this delivery was rebased onto that `main` with zero
file overlap confirmed directly. Full backend suite on that baseline: 1122
passed; with this delivery: 1135 passed (exactly +13, the new tests), zero
regressions. Observation only, exactly as scoped: no order placement, no
ledger write, no schema migration, and no frontend consumer — `orders.status`
still has no UI widget, unchanged from the design doc's own deferred
"Frontend" prerequisite.

## Boundary

Exactly two application files change: `backend/app/api/routes/intelligence.py`
(edited, additive only — one new route, one new module-level helper) and
`docs/architecture/execution-engine-design.md` (edited — §6.3 as-built note
plus two new diagrams, §6.8 table annotation). New —
`backend/tests/test_execution_orders_route.py` — plus this file, `TESTING.md`,
`confirmed-decisions.md`, and `INDEX.md`. Untouched: every `governor/`,
`execution_engine/`, `portfolio_state/`, scanner, and frontend file;
`models/execution_ledger.py`; any migration; EX-5/EX-12.

<!-- Previous delivery record retained below. -->

# CHANGES — `scanner-override-ticker-validation`

## Current delivery

`GET /scanner/state`'s ad hoc `?symbols=` override now enforces the exact
same ticker-format rule `POST /scanner/universe` already enforces
(`is_valid_ticker_format` — 1-5 letters, optional share-class suffix like
`BRK.B`), instead of only stripping/uppercasing each comma-separated entry.
A new `_parse_symbols_override()` helper in `app/api/routes/scanner.py`
trims and uppercases each entry, returns HTTP 400 for a whole-empty
override, an empty entry from a stray comma, or a format-invalid ticker,
and deduplicates valid entries preserving first-seen order. The omitted-
parameter path (`symbols` key absent from the query string entirely) is
untouched — it still reads the persisted universe via `DbUniverseProvider`
with the same `TEST_UNIVERSE` fallback, exactly as before. Scoring,
ranking, `top_n`, and every universe CRUD route are unchanged; no new
size limit is introduced on the override.

New `backend/tests/test_scanner_state_route.py` (11 focused HTTP-route
tests, direct ASGI transport, no lifespan needed for 10 of the 11 — the
omitted-parameter test is the one that reads real Postgres). Verified as
a genuine regression guard by temporarily reverting the fix and confirming
7 of 11 tests failed, then restoring it and confirming 11/11 passed.
Corrected a now-stale claim in `test_scanner_runner.py`'s own docstring
that said the route had "nothing route-specific to get wrong." Updated
`docs/architecture/scanner-design.md` with new §14 (before/after
request-flow and internal-parser-flow diagrams). Full backend suite:
1111 → 1122 passed (exactly +11, the new tests), zero regressions. No new
architectural decision — this reuses an existing, already-decided
validation rule at a second call site to close a consistency gap; universe
semantics, scoring, ranking, and the success-path API contract are
unchanged. Universe CRUD, `run_scan`, `main.py`, and every frontend/
execution file remain untouched, exactly as scoped.

## Boundary

Exactly four files change: `backend/app/api/routes/scanner.py` (edited),
`backend/tests/test_scanner_runner.py` (docstring correction only),
`backend/tests/test_scanner_state_route.py` (new), and
`docs/architecture/scanner-design.md` (edited, new §14) — plus this file
and `TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — `scanner-route-db-offload`

## Current delivery

The Scanner's four synchronous database calls — `GET /scanner/state`'s
default-universe read and `GET`/`POST`/`DELETE /scanner/universe` — now run
via `asyncio.to_thread` instead of directly on the event loop, so a slow
database read or write no longer holds up every other request this process is
serving for its duration. Each wrapped `app/scanner/universe.py` function
already opened and closed its own `Session`; only where it runs changed.
`run_scan()` and `FeatureEngine.get_snapshot()` stay on the event loop —
inspection confirmed the latter's own docstring is accurate: a pure in-memory
dict read with no I/O, nothing blocking to move.

Validation, the `TEST_UNIVERSE` fallback, response shapes, and the POST
route's `ValueError`→400 mapping are all unchanged. New
`backend/tests/test_scanner_route_concurrency.py` proves a blocked universe
call no longer blocks `GET /health`, using a `threading.Event`-controlled fake
rather than a sleep for deterministic timing; the test was verified to
genuinely catch the regression by temporarily reverting the fix and watching
it fail (time out) first. Updated `docs/architecture/scanner-design.md` §13
with before/after data-flow and internal-flow diagrams. Full backend suite:
1109 → 1111 passed (exactly +2, the new tests), zero regressions. No new
architectural decision — universe semantics, validation, scoring, and the API
contract are all unchanged; this is an operational fix at the route boundary.
Execution, `main.py`, frontend API files, and the continuous
`MarketActivityScanner`/promotion/cadence path remain untouched, exactly as
scoped.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #180: `execution-startup-status`

## Current delivery

The running UI can now tell which of decision #179's three real startup
outcomes actually happened, instead of only inferring "not ready" from
`/intelligence/world-view`'s `portfolio: null` or `/intelligence/exit-intents`'
`monitor_status: "unavailable"`. `main.py` tracks an explicit
`app.state.execution_startup_status` through the existing execution-pipeline
try/reconcile/else/except/finally sequence — `"ready"`, `"reconciliation_blocked"`
(with a plain discrepancy count), or `"startup_failed"` (a fixed reason code,
never the caught exception's own text) — set at the same points that already
determine the outcome. The `finally` block resets it on shutdown, the same
reset `world_view_portfolio_reader`/`position_monitor` already get, so a route
hit with no active lifespan, before startup finishes, or after shutdown reports
`"unavailable"`.

Added read-only `GET /health/execution-startup`, returning that status (or the
unavailable shape when unset). Its docstring states plainly that this is a
startup diagnostic, not a live trading-readiness check: `"ready"` is not proof
a given opportunity will pass Governor's rules, that Portfolio State will stay
ready, or that an open position's exit is protected. `/health` itself and all
entry behavior are unchanged.

The Execution panel gains a compact "Startup status" line above "Observed exit
triggers," fetched on expand and manual Refresh only (no polling, no new
WebSocket subscription) — same loading/error/unavailable shape that section
already established. It shows the status label and, when blocked, the
discrepancy count, plus the same "not a readiness guarantee" disclaimer as the
route's own docstring.

Updated `docs/architecture/execution-engine-design.md` §6.9 with cross-component
and internal status-flow diagrams. No entry rule, exit placement, scanner file,
or EX-5/EX-12 change; no new architectural decision beyond this one.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #179: `execution-startup-fail-closed`

## Current delivery

An exception after partial execution-pipeline startup now rolls back before
FastAPI serves requests. `main.py` clears the execution venue role and the
World View / Position Monitor app references, closes the authorizer and
execution callbacks, then stops the started workers and disconnects the venue.
A reconciliation discrepancy also disconnects its already-connected venue.
Market-data and intelligence routes retain their soft-start behavior; successful
startup and the existing normal shutdown order remain intact.

The bus can remove these lifecycle subscriptions. A callback already copied for
dispatch sees a stopped component and cannot enqueue new work; authorizer and
execution workers discard pending items during rollback. The simulated venue
also drops its update callbacks on disconnect. A real-lifespan fault test raises
after the authorizer, execution engine, and monitor start, then verifies health,
no approved trade or order, cleared readers/registry role, and stopped workers.
The canonical execution architecture record now diagrams successful startup and
rollback. Decision #179 records this correction to #176's startup contract.
No trading rule, exit placement, status UI, or EX-5/EX-12 change.

<!-- Previous delivery record retained below. -->

# CHANGES — `observed-exit-intents-ui`

## Current delivery

The Execution panel now reads decision #178's existing `GET /intelligence/exit-intents` response when expanded and on manual Refresh. A separate “Observed exit triggers” section distinguishes unavailable monitor, running monitor with no intents, fetch error, and observed intents. Intent rows show symbol, stop/target/EOD reason, side, quantity, trigger price, and trigger time. The section states that observation has not placed an exit order or closed the position; the order-lifecycle event list remains separate.

Added the exact backend response types to the frontend API client and corrected stale entry-pipeline comments. Updated `docs/architecture/execution-engine-design.md` §6.6 to show the new read-only UI connection. No backend, Position Monitor, execution, WebSocket, EX-5/EX-12, or new architectural decision change.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #178: `position-monitor-observer-wiring`

## Current delivery

`main.py` now starts the existing Position Monitor with the existing `PortfolioStatePositionReader` after clean execution reconciliation, restored Portfolio State, and successful entry-pipeline startup. It stops the monitor during lifespan shutdown and clears the app-owned reference. A blocked pipeline leaves it unavailable.

Added read-only `GET /intelligence/exit-intents`. Its `monitor_status` distinguishes `unavailable` from `running`, `intent_status` labels every response `observed_only`, and `exit_intents` lists existing intent fields (position ID, symbol, side, quantity, reason, trigger price, timestamp). It delegates the optional symbol filter to `get_exit_intents()` and sorts results deterministically. A real-lifespan simulated-entry test confirms one stop intent, continued one-intent latching after another crossing, and no exit order, fill, or position closure.

Updated `docs/architecture/execution-engine-design.md` §6.6 with cross-component and internal-flow diagrams, `docs/architecture/trading-intelligence-architecture.md` §13 with the as-built boundary, and the Position Monitor package/port descriptions. Appended and indexed decision #178 after the final GitHub main/log recheck. This observer only reacts to received price/candle events and keeps intents in memory; it does not protect or flatten a position. EX-5/EX-12 remain open.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #177: `world-view-portfolio-read`

## Current delivery

World View now reads the running, restored Portfolio State instance through an explicit lifespan dependency. The reader is exposed only after clean reconciliation and successful execution-pipeline startup, then cleared on shutdown. A missing or stale snapshot remains `portfolio: null`; a restored flat snapshot has an empty positions list.

The typed portfolio response contains execution mode, snapshot time, open position ID/symbol/side/remaining quantity/average entry/stop/target, and in-flight order count. Prices are decimal strings. The World View symbol still scopes only Market State and Context. The existing World View panel retains both performance columns and now displays the position count and compact position rows, with manual Refresh.

Updated `docs/architecture/trading-intelligence-architecture.md` with cross-component and internal read-flow diagrams, and corrected the World View follow-up in `docs/architecture/execution-engine-design.md`. Added focused backend response, serialization, scope, startup, and shutdown coverage. No exit path, order control, accounting change, Position Monitor wiring, or EX-5/EX-12 decision is included.

Appended decision #177 at the true end of `docs/decisions/confirmed-decisions.md` and indexed it in `docs/decisions/INDEX.md` after the final GitHub main/log recheck.

<!-- Previous delivery record retained below. -->

# CHANGES — `position-monitor-portfolio-reader`

## Current delivery

Added `position_monitor/portfolio_state_reader.py`, a synchronous `PositionReader`
adapter over the real Portfolio State instance. It reads only the detached
snapshot's positions with remaining quantity, including accounting status
`closing` after a partial reduction. It excludes in-flight entry orders and
converts Decimal stop/target prices to float for `PositionView`. An unrestored,
stale, or blocked snapshot raises `PositionSnapshotUnavailable`; a restored
empty portfolio returns `()`.

Updated the existing Position Monitor package and port descriptions and
`docs/architecture/execution-engine-design.md` §6.6 with as-built data-flow
and internal adapter-flow diagrams. Added focused adapter tests. No new
architecture decision was needed: this implements the read seam already
reserved by decision #175. Position Monitor remains unwired in `main.py`;
no exit orders or new events are produced, and EX-5/EX-12 remain open.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #176: Entry-order lifecycle wired to real Postgres (`entry-lifecycle-wiring`)

## Current delivery

Closes the gap #171 and #172 both left explicitly open: the full entry pipeline (authorizer, Execution Engine, ledger, `SimulatedVenue`, Portfolio State) was fully built and fully tested as of #172 — **against fakes only**. Nothing persisted to a real database in a running process, and `main.py` started none of it. This delivery wires the real thing together for the entry side of the lifecycle (exits, Position Monitor's own exit-order placement, `StrategyOutcome` writing — EX-5/EX-12 — remain untouched, exactly as scoped from the start).

**Renumbered once.** Reserved #175 via this task's own three-source re-check; a re-pull immediately before packaging found a file-disjoint sibling, `position-monitor-lite`, had landed first and correctly taken #175 — that task's own entry explicitly anticipated this exact collision and pre-committed to deferring, which it did. This delivery is **#176**.

**Found two-and-a-half of three scoped adapters already on `main`, undocumented, when this task began.** `backend/app/execution_engine/postgres.py` (`PostgresOrderLedger`, implementing both `OrderLedgerPort` and `DecisionAuthorizationPort`) and `backend/app/governor/postgres.py` (`PostgresTradeLedger`, implementing `TradeLedgerPort`) were real, tested code (`test_authorization_ledger_postgres.py`) with **zero** decision-log entry anywhere — a genuine process gap, not a design fork, reported to Saqib before writing any code. `docs/architecture/execution-engine-design.md` had already been edited to describe this as "As built (#175)," a decision number that never existed in `INDEX.md`/`confirmed-decisions.md`. Directed by Saqib to verify and adopt rather than rebuild: 153/153 pre-existing tests passing against a real, freshly migrated Postgres 16 before this task changed a single line; neither file is edited by this delivery. A third such file, `backend/app/portfolio_state/postgres.py` (`PostgresPositionLedger`), was found and adopted the same way.

**Built.**
- `FillLedgerPort` + `PostgresFillLedger` (new file, `execution_engine/fill_ledger.py`) — fill-ingestion persistence (design doc §6.3 step 6), kept as a new, separate port rather than widening `OrderLedgerPort`/`PostgresOrderLedger` per Saqib's explicit reuse-not-rebuild direction.
- Fill processing wired into `ExecutionEngine` (additive) — registers `OrderVenue.on_order_update()` once at `start()` when a `FillLedgerPort` is supplied (optional, `None`-default — no existing caller/test affected); shares the engine's existing single-worker queue with `OrderApproved` processing, which is what guarantees ordering against the very order a fill belongs to.
- `PortfolioStateAdapter` (new file, `governor/portfolio_state_reader.py`) — the third and last concrete `PortfolioStateReader`, a thin translation over the live `PortfolioState` event-worker instance `main.py` now wires. Implements I14's "halt new entries on an unresolved fill anomaly" by raising, which `AuthorizerStub`'s existing fail-closed handling already turns into exactly that halt — no new table or flag.
- `main.py`'s real startup wiring — the §6.9 sequence (rebuild → connect → reconcile → resume), called from a running process for the first time; a reconciliation discrepancy leaves the execution pipeline entirely unwired (logged `CRITICAL`) rather than proceeding; the rest of the app still boots. Symmetric, `None`-guarded shutdown.
- Two stale docstrings fixed (`get_execution_engine()`/`get_authorizer_stub()` both previously said "main.py is NOT wired to call this").

**Documentation gap corrected, per Saqib's explicit direction (the one approved exception to this task's own "don't touch other architecture docs" boundary).** Every phantom "#175" citation in `execution-engine-design.md` (the banner, two "As built" callouts, and four smaller inline citations a first pass missed) rewritten to name this task's own slug instead of a decision that never existed. A second, differently-shaped error found the same way: `position_fill_receipts` was attributed to **#174**, which is frontend-only and built no table — corrected, with the mistake stated inline.

**Testing.** Real Postgres 16, no mocks, throughout. 15 new tests: 7 for `PostgresFillLedger` (dedup, overfill, unknown-order, monotonic status advance), 4 for `PortfolioStateAdapter` (mode mismatch, not-ready, I14 halt, snapshot translation), 3 end-to-end `OpportunityCreated → real open Position` integration tests, 1 restart-recovery test through `main.py`'s **real** `lifespan()` (submit an order, exit the process, re-enter with a fresh, non-durable `SimulatedVenue`, confirm it's marked `expired`/`venue_lost_state_on_restart` and the pipeline resumes). **1066 → 1081** passing on this task's own branch; **1091** combined with `position-monitor-lite` (confirmed file-disjoint, re-run together). Zero regressions. Repeated 3x for timing flakiness (a four-engine async queue-hop chain) — stable every time.

**Not done, stated precisely.** EX-5/EX-12 untouched. No cancel/expire path beyond restart reconciliation. The "a durable venue reports a fill the dead process never persisted" branch of restart recovery is covered at the function level by #172's own `test_reconciliation.py`; it cannot be reproduced at the process level against `SimulatedVenue`, which is not durable across a restart by design.

**Heads-up, not acted on.** `confirmed-decisions.md` is now well past the ~100KB rollover trigger (flagged at #171, #172, #173, #174) — flagged again.

## Boundary

New: `backend/app/execution_engine/fill_ledger.py`, `backend/app/governor/portfolio_state_reader.py`, four new test files. Edited, additive only: `backend/app/execution_engine/engine.py`, `backend/app/governor/engine.py` (docstring only), `backend/app/main.py`, `docs/architecture/execution-engine-design.md` (Saqib's explicit exception). Untouched: `backend/app/broker_adapters/**`, `backend/app/models/execution_ledger.py`, `backend/app/portfolio_state/**`, `backend/app/services/broker_registry.py` (called, not edited), any migration, `backend/tests/conftest.py` (no new module-level singleton is introduced by this task — `PortfolioState`, both new adapters, and the reconciliation-mode instance are all local to `main.py`'s own `lifespan()`), `backend/app/execution_engine/postgres.py`, `backend/app/governor/postgres.py` (both found pre-built, reused unmodified). Confirmed by `diff -rq` against a freshly re-pulled `main`, done twice (once before, once after discovering the `position-monitor-lite` collision).

<!-- Previous delivery record retained below. -->

# CHANGES — decision #175: Position Monitor-lite built (`position-monitor-lite`)

## Current delivery

New package `backend/app/position_monitor/` — the in-process exit-intent decision layer EX-11's recommendation calls for, and decision #171's own marked extension point ("Position Monitor-lite's own task"), named directly.

- `ports.py`: this module's own narrow, frozen-dataclass `PositionReader` Protocol (`get_open_positions() -> tuple[PositionView, ...]`) and `PositionView` (`position_id`, `symbol`, `side`, `qty`, `stop`, `target`, `opened_at` — no `avg_price`, no P&L). Deliberately its own shape, not governor's `PortfolioStateReader`/`OpenExposure` (no `target` field there; mixes open positions with in-flight entries) — read as a pattern reference only. No concrete adapter ships here, same "ports, no adapter" precedent `governor/ports.py`/`execution_engine/ports.py` already set.
- `engine.py`: `PositionMonitor` — same subscribe→own-queue→worker shape every engine in this codebase uses (decision #84's pattern). Subscribes to `PriceUpdated`/`CandleClosed` (held symbols only — filtered before enqueue, rechecked at processing, mirroring decision #173's own Portfolio State worker). Precedence exactly matches `fill_simulator`'s convention (EX-8): stop checked before target (same-bar tie → stop wins); EOD-flatten keyed to the position's own entry trading day via a local, deliberate duplicate of `fill_simulator.regular_session_close_utc()`'s ET/half-day formula (not an import — EX-8's un-taken option (b) would've expanded the footprint into `backtest_runner/`). One `ExitIntent` per position, ever — an in-memory idempotency latch, no ledger-backed re-arm-after-restart yet (§6.9 step 5, out of this task's scope).
- **Deliberately stops at the `ExitIntent`.** No event published, no order placed, `schemas/events/execution.py`/`execution_engine`/`governor` all untouched. EX-5 (protective-exit authorization — still open, explicitly NOT on the "proceeds on recommendation" list, unlike EX-11) is left for a later task, once Saqib confirms it — this task stays inside the half of the problem EX-5 doesn't touch (deciding *when/why* to exit, not *how* to place the exit).
- No `main.py` wiring, no module-level singleton getter (there's no concrete `PositionReader` yet to default-construct one against) — a later wiring task adds both together.

**Verified:** 10 new tests, all passing in isolation (1.82s) and as part of the full suite (616/616 passing overall — was 606 on the untouched baseline; same 48 failed/93 errored pre-existing Postgres-connection tests either side, all from the `entry-lifecycle-wiring` sibling's own unwired adapters, zero relation to this task). `diff -rq` against a fresh, independently-pulled, untouched clone confirms only `backend/app/position_monitor/**` (new) and `backend/tests/test_position_monitor_engine.py` (new) changed — nothing else in the tree touched. Full detail in `TESTING.md`.

## Boundary

Created only: `backend/app/position_monitor/__init__.py`, `backend/app/position_monitor/ports.py`, `backend/app/position_monitor/engine.py`, `backend/tests/test_position_monitor_engine.py`. Everything else — `backend/app/{portfolio_state,execution_engine,governor}/*.py`, `backend/app/db/**`, `backend/alembic/**`, `backend/app/main.py`, `backend/app/schemas/events/*.py`, `backend/app/api/**`, `frontend/**`, every architecture doc, every existing decision entry — untouched, confirmed by `diff -rq`.

**Heads-up, not acted on (flagged a fifth time).** `confirmed-decisions.md` remains well past the ~100KB rollover trigger (flagged at #171, #172, #173, #174) — archive rollover stays outside this task's own append-only decision-entry boundary, not performed here either.

**Heads-up, new this round.** `docs/architecture/execution-engine-design.md` §6.8's persistence-sketch table already informally cites `"#174"`/`"#175"` for two tables belonging to the still-undocumented `entry-lifecycle-wiring` sibling's own migrations — read, not edited (outside this task's file boundary), and not treated as a real reservation: this delivery's own `#175` is assigned strictly from `INDEX.md`/`confirmed-decisions.md`'s own tails. A collision with that sibling's own eventual packaging (it may also expect #174/#175) is likely and would mean renumbering this delivery, the same way #172→#173→#174 each already renumbered earlier in this session.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #174: First frontend consumer of the order-lifecycle events wired (`execution-lifecycle-frontend`)

## Current delivery

First frontend consumer anywhere in this repo of any order-lifecycle event — closes the "Frontend" line in `execution-engine-design.md` §8's deferred-prerequisites list.

**Renumbered twice.** Built and packaged as #172; renumbered to #173 when `execution-ledger-and-venue` merged first and took #172; renumbered again to **#174** when `portfolio-state-engine` merged next and took #173. Zero file overlap with either sibling, confirmed both times (`portfolio-state-engine`'s own stated boundary explicitly excludes websocket channels and frontend edits; both editable files are byte-identical, by hash, to this task's original baseline throughout).

**This round required a real code update, not just a renumber.** `portfolio-state-engine` added a real `PositionClosed` payload model to `execution.py` and a `CRITICAL_EVENT_TYPES` entry to `envelope.py`. This task's own five originally-guessed fields (chosen defensively from a docs sketch, before any real model existed) matched the real model exactly. `PositionClosedWire` in `useOrderLifecycle.ts` updated to mirror the real model's further optional fields; the panel now shows `fees` on the row (genuinely new information) — `realized_profit`/`realized_loss` are read but not separately shown (they decompose the `realizedPnl` figure already on the row); `r_multiple_missing_reason` is read but deliberately not surfaced per-row, since it's expected to be true of every closure for now and would just be noise repeated on every line.

**Still cannot arrive in a running system today** — `portfolio-state-engine`'s own words: "no production implementation of this Protocol ships here," adapter/startup wiring "not wired." `channels.py`'s routing line needed no change; only its explanatory comment did, since its prior "no payload model yet" claim is now false.

**Verified:** `npx tsc -b && npm run build` clean, re-run a third time. `channels.py` re-verified by real import, including a new assertion (`POSITION_CLOSED in CRITICAL_EVENT_TYPES`) not meaningful before this merge. `PositionClosed` normalization re-exercised against both a full real-shape fabricated message and a minimal one — both correct. Full detail in `TESTING.md`.

**Everything else** — the channel-split judgment call, the hook's overall design, the panel, the `App.tsx` wiring — unchanged from the #173 packaging.

## Boundary

Unchanged from the #173 packaging: `frontend/src/hooks/useOrderLifecycle.ts`, `frontend/src/components/execution/ExecutionLifecyclePanel.tsx` (new); `backend/app/api/websocket/channels.py` (comment-only change this round), `frontend/src/App.tsx` (unchanged this round). Confirmed by `diff -rq` against a freshly re-pulled `main` (post-#173).

**Found and fixed again:** `TESTING.md`'s #171-and-earlier history, restored once already in the #173 packaging but never merged (that fix was only ever handed over as a zip), was found still missing on this pull and restored again — `portfolio-state-engine`'s own section correctly preserved #172's above it, so the gap didn't grow, but it also hadn't shrunk on its own.

**Heads-up, not acted on:** `confirmed-decisions.md` is now 144,321 bytes, well past the ~100KB rollover trigger (flagged at #171, #172, #173) — still not performed; flagged a fourth time for Saqib.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #173: Portfolio State Engine (`portfolio-state-engine`)

## Current delivery

Extends the Portfolio State package already merged in #172, with Saqib's explicit approval after reporting the ownership overlap. Initial clean `main` and final comparison baseline: `c341a2c2f3e2e38901fe2ce10a430b826201be11`.

- Shared pure Decimal accounting for long/short positions, weighted adds, partial reductions, and full closes. Invalid quantities, incompatible fills, and unintended reversals are rejected without changing the position.
- An EventBus → queue → worker path reads authoritative fills through a narrow local `PositionLedgerPort`. Fill identity is `(execution_venue, venue_fill_id)`; position, realized-fill attribution, and cursor must commit atomically before cache installation or closure publication. No production Protocol adapter is included.
- Synchronous, detached snapshots expose positions, remaining in-flight entry exposure, held-symbol marks, and separate daily **profit, loss, and fees**. Unknown startup/order/history/fee state stays unknown. No consumer-package imports.
- Position identity and lifetime accounting survive arbitrary holding periods. Day trading remains the primary use, but no daily flatten/reset or holding-duration limit is introduced. Each partial realization and fill fee stays on its own MarketClock day and mode, even when closure occurs months later.
- Additive `PositionClosed` payload and critical-lane membership. Closure reports lifetime gross P&L and aggregate exit VWAP; R is nullable with a missing reason because no immutable planned-risk contract currently exists. No outbox or guaranteed event delivery is claimed.
- Existing reconciliation APIs remain callable. The Session path uses the same arithmetic, installs cache state only after commit, reconstructs daily totals on restart, retains partial-order remainders, and no longer mistakes a precommitted terminal order status for proof that its fill was already accounted. Invalid overfills remain persisted and flagged, leave position arithmetic unchanged, and block snapshots. Reconciliation reports unavailable accounting as a discrepancy.

## Validation and remaining integration

**161 focused checks passed, no skips**, including 45 pure/fake-ledger worker cases and 22 real-PostgreSQL Session/reconciliation cases. Isolated PostgreSQL 18.6, migrated through unchanged `0012`; the new Protocol adapter was not tested because it is not built. Related execution/governor/ledger/venue/bus/clock regressions also passed. `TESTING.md` records the command and limits.

Final handoff revalidation on 2026-09-23 repeated the documented suite: **161 passed, no skips, in 4.85s**. Freshly fetched GitHub `main` still matched the original baseline. `portfolio-state-engine.zip` delivers the 16 modified/new task files using repository-relative paths; database data, validation logs, caches, and virtualenvs are excluded.

Current `OrderFilled` has no stable fill ID/sequence and no application publisher; it is only a wake-up for ledger reads. Current `OrderStatusChanged` represents rejection only; cancellation/expiration becomes visible through explicit refresh or startup read-back, not a new live publisher. Production adapter safe-prefix/concurrency guarantees, startup wiring, consumer adapters, numeric R basis, outcome recovery, and corporate-action/late-fee inputs remain integration work. The commit-to-publish crash window can lose a closure notification; committed state must be recovered independently.

## Boundary

Changed only `backend/app/portfolio_state/**`, focused portfolio tests, additive execution-event schema/envelope changes, the relevant execution design, appended decision/index entry, and delivery documentation. Models, migrations, broker/registry, governor/execution engine, websocket channels, main startup, and frontend remain untouched. Existing decision bodies are unchanged. Archive rollover was already due on baseline and remains outside this task's decision-entry boundary.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #172: Execution ledger + `OrderVenue`/`SimulatedVenue` + Portfolio State built (`execution-ledger-and-venue`)

## Current delivery

The ledger/venue half of decision #170's Slice A design — sibling to, and merged after, decision #171 (the authorizer stub + entry-order Execution Engine). Everything #171's own code was built against narrow local `Protocol`s in anticipation of.

- **`OrderVenue` port + `SimulatedVenue`:** the interface from design doc §6.4 verbatim, and its only implementation — market/limit fills on tick, deterministic `venue_fill_id`, session-guarded, not durable by design (a fresh instance has no memory of a prior one, which IS the restart behavior §6.9 needs), injectable tick source/clock/partial-fill planner. `BrokerAdapter` untouched.
- **`execution` registry role:** a third, separately-typed global in `broker_registry.py`, following that file's own existing pattern; fails closed if a venue's `supported_modes` excludes the configured `execution_mode`.
- **Execution ledger:** `trades`/`orders`/`fills`/`positions`/`portfolio_state_cursor` (migration `0012`), DB-level `UNIQUE` on `client_order_id` and `(execution_venue, venue_fill_id)`, mode/venue pairing CHECKs repeated across every table that carries both columns.
- **`strategy_outcomes` EX-2/EX-7:** `execution_mode`/`execution_venue` added NOT NULL (backfilled), four snapshot columns relaxed to nullable, new `snapshot_missing_reasons`, four new CHECK constraints — the migration counts and **aborts** on any pre-existing `is_backtest = false` row rather than guessing a label. Mirrored additively into the ORM and Pydantic contracts, with matching validators.
- **Portfolio State:** `apply_fill()` (idempotent, opens/adds/closes positions, realized P&L, cursor advance, all one transaction), `rebuild_from_ledger()` (ledger wins over any in-memory disagreement, logged), and `reconcile_with_venue()` — the full §6.9 step-3 ladder (cancelled-stale-entry / resubmitted-exit / expired-lost-state / advanced-with-missing-fills), placed here per this task's own scope rather than in the sibling's `execution_engine/`.
- **Config:** `execution_mode: str = "simulated"`, fails closed on anything else.

**Two real bugs found by testing against real Postgres, not just written and trusted:** a Postgres CHECK-constraint-vs-NULL gap (`x ? 'key'` on a NULL jsonb evaluates to NULL, which CHECK treats as passing) and a SQLAlchemy JSONB `None`-vs-JSON-`null` gap (`none_as_null=False` by default would have silently defeated EX-7's entire nullable-snapshot mechanism). Both fixed and reverified with a full migration down/up round-trip.

**Judgment calls (five, stated precisely — full reasoning in the decision entry):** J1, one import line in `db/base.py` (outside "may edit," needed for the ORM registration that file's own docstring requires). J2, `PositionClosed`'s actual publish left as a documented seam (`apply_fill()`'s return value signals a closure) — the payload model and `CRITICAL_EVENT_TYPES` entry live in files outside this task's boundary. J3, the mode/venue pairing CHECK repeated beyond just `strategy_outcomes`. J4, `execution_mode`/`execution_venue` defaults derived from `is_backtest` (Pydantic `before`-validator + a context-sensitive SQLAlchemy default) since `record_strategy_outcome()` is outside this task's boundary and doesn't forward the new fields. J5, `orders.status` progression added to `apply_fill()` since nothing else maintains it and AC #13's anomaly detection depends on it.

**Sibling merge handled mid-session:** decision #171 landed on `main` partway through this task (confirmed via the GitHub API, not just `diff -rq`). None of this task's four editable files were touched by #171 except `core/config.py`'s shared append point — resolved as the "trivial merge, not a conflict" both tasks' own prompts anticipated. `execution_engine/ports.py`'s local `Protocol`s were read directly and confirmed structurally compatible with this delivery's real classes (Python duck typing) — no code on either side needed further change.

**Verified:** 37 new tests (10 `SimulatedVenue`, 4 registry, 7 ledger-constraint, 9 Portfolio State, 7 reconciliation — see `TESTING.md` for the acceptance-criterion mapping), all against real Postgres 16, no mocks. Combined with #171's own 885: **922 passed**, one run surfacing the same pre-existing #119-cluster wall-clock flake #171 already documented (confirmed independently, reproduces on unmodified code). Migration round-trip (`downgrade 0011` → `upgrade head`) clean.

**Not done, stated precisely:** `PositionClosed`'s real publish (J2). `main.py` startup wiring of the restart sequence (steps 2-3 are built and tested individually; the orchestration is outside this task's file boundary). EX-5/EX-12 remain untouched, exactly as this task's own scope established from the start.

## Boundary

New: `backend/app/broker_adapters/{order_venue,simulated_venue}.py`, `backend/app/models/execution_ledger.py`, one Alembic migration, `backend/app/portfolio_state/**`, 5 new test files. Edited: `backend/app/services/broker_registry.py`, `backend/app/models/trading_intelligence.py` (additive), `backend/app/schemas/performance.py` (additive), `backend/app/core/config.py` (append, merged with #171's own block), `backend/app/db/base.py` (J1, one line). Confirmed by `diff -rq` against a freshly re-pulled `main` (post-#171) — nothing else touched.

**Heads-up, not acted on:** `confirmed-decisions.md` is now past the ~100KB rollover trigger (both #170 and #171 already flagged approaching it). Following the same established precedent, the rollover is deliberately not performed here — flagged for Saqib.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #171: Authorizer stub + entry-order Execution Engine built (`execution-authorizer-and-engine`)

## Current delivery

First code (not just design) for decision #170's Slice A: two new packages, `backend/app/governor/` (the authorizer stub, §6.2) and `backend/app/execution_engine/` (entry-order placement only, §6.3), plus additive-only edits to three existing files.

- **Governor:** pure `rules.py` (rules 0-6 — execution-mode gate, regular session, actionable, pre-trade snapshot gate, slots/duplicates, reference price + stop geometry + fixed-notional sizing, the daily-loss gate exactly as documented in I15) and `engine.py`'s `AuthorizerStub` (subscribe → own queue → worker, same pattern every prior engine here uses). Commits every decision (approved or rejected) before publishing anything. A rejected decision publishes `PlanRejected` alone; an approved one mints `opportunity_id`/`client_order_id` **only at acceptance** (EX-9) and publishes `TradePlanned` → `GovernorDecision(approved)` → `OrderApproved`.
- **Execution Engine, entry orders only:** `engine.py`'s `ExecutionEngine` consumes `OrderApproved` (critical lane, own queue — I7/AC #20), checks an authorization gate against the committed decision (I2/AC #19 entry-gate half) before writing anything, performs an idempotent ledger insert (AC #7 client-order-id-mint half), checks the configured venue supports the order's mode (AC #5 venue-refusal half), and calls `OrderVenue.place_order()`. Fill processing (`on_order_update`, `OrderFilled`) is **not built** — a flagged judgment call, out of this task's owned AC list and coupled to Portfolio State, which this task doesn't own.
- **Schema/envelope, additive only:** `OrderApproved.position_effect` (required, EX-14); new `TradePlanned` (R2 — reconciled from `TradePlan`'s field set, two judgment calls: no `symbol` on the payload, `long`/`short` stays the planning-layer vocabulary); new `OrderStatusChanged` event + `EventType` member + critical-lane membership (EX-9's recommended venue-level rejection event, distinct from plan-level `PlanRejected`) — the one approved exception to this task's file-boundary "may edit" list, confirmed against a fresh `main` pull immediately before editing.
- **Config:** the exact three-setting block (`execution_max_concurrent_positions`/`execution_fixed_notional_usd`/`execution_daily_loss_cap_usd`, defaults 1/1000.0/100.0), each validated positive via a new `field_validator` (this file's first). `execution_mode` deliberately not added — the sibling task's own block.
- **Ownership fork (Saqib, 2026-09-22):** the real `orders`/`trades` ledger tables + migration, `SimulatedVenue`, and `broker_registry`'s `execution` role all belong to the sibling `execution-ledger-and-venue` task (this task's file boundary forbids `models/**`/Alembic/`broker_registry.py`). This delivery is built against five narrow local `Protocol`s instead (`TradeLedgerPort`, `PortfolioStateReader`, `OrderLedgerPort`, `DecisionAuthorizationPort`, `ExecutionVenueProvider`/`OrderVenue`), tested against in-memory fakes — an explicit, confirmed departure from "real Postgres 16, never mocks," scoped to exactly this seam.
- **`opportunity_id` minting note:** the design doc's citation to "decision #128" doesn't match #128's actual current text (likely stale renumbering drift) — flagged, not silently followed; proceeded on the independently well-corroborated requirement itself (`opportunity_id` as a `uuid4()`, matching `strategy_outcomes.opportunity_id`'s UUID column).

**Verified:** 75 new tests (pure rule-pipeline cases covering AC #17's full daily-loss-gate table; config validators; `AuthorizerStub`/`ExecutionEngine` orchestration against a real `EventBus` and fake ports, including an AC #20 critical-lane-isolation timing test; schema/envelope cases), all passing repeatedly on their own. Full suite: 885 collected, first run 885/885; a second run surfaced one intermittent, wall-clock-time-sensitive failure in `test_backtest_routes.py`, confirmed pre-existing (reproduces identically on a freshly re-pulled, untouched `main` — 809 passed/1 failed there, 809 + 75 = 884, matching this delivery's own second-run count) — this project's own long-documented #119 cluster, unrelated to this delivery. Zero regressions.

**Not done, stated precisely:** no exit path, no reduce-only guard, no `StrategyOutcome` writing (EX-5/EX-12 still open); no fill processing; no real ledger/venue/registry-role (sibling task's scope); `main.py` not wired to start either engine (outside this task's file boundary — both `get_authorizer_stub()`/`get_execution_engine()` are ready for that wiring).

## Boundary

New: `backend/app/governor/**`, `backend/app/execution_engine/**`, 5 new test files. Edited, additive only: `backend/app/schemas/events/envelope.py`, `backend/app/schemas/events/execution.py`, `backend/app/core/config.py`, `backend/tests/conftest.py` (2 singleton-reset lines). Confirmed by `diff -rq` against a freshly re-pulled `main` — nothing else touched.

**Heads-up, not acted on:** `confirmed-decisions.md` is now 98,049 bytes, close to (but still under) the ~100KB rollover trigger `docs/decisions/README.md` documents. Following this project's own established precedent (decision #170's own note on the same subject), the rollover is deliberately not performed as part of this delivery — flagged for Saqib.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #170: Execution Engine design amended — Slice A approved in principle (`execution-engine-design-amendment`)

## Current delivery

Amends decision #168 (which stays exactly as merged — decision content is immutable) and revises `docs/architecture/execution-engine-design.md` in place, so the simulated-venue automatic path (Slice A) is now the implementation specification. **Nothing is built.**

- **Resolved by Saqib:** EX-1 Execution first with a stub authorizer that is technically restricted to simulated execution and fails closed for paper/live (four layers); EX-2 separate `execution_mode` (`backtest | simulated | paper | live`) and `execution_venue` (`simulated | ibkr | …`), `is_backtest` kept temporarily; EX-3 a new narrow `OrderVenue` interface and an `execution` registry role, `BrokerAdapter` not enlarged; EX-4 one stub with initial limits of 1 concurrent position, $1,000 notional per trade, and a $100 daily loss cap, all configurable; EX-6 Portfolio State owns accounting, in-flight orders, and daily P&L, and `PositionClosed` is published on the critical lane only after its commit; EX-7 a pre-trade snapshot gate, a reported fill never discarded, nullable snapshots plus a missing-data reason.
- **Requirements added:** stable client-order IDs; deduplicated order and fill updates; restart recovery that reconciles non-terminal orders with the venue; an authoritative database ledger with reconstructable Portfolio State; persist-before-publish for fills and closures; a daily-loss gate that counts unrealized loss and open risk.
- **Documented precisely:** the critical lane gives ordering and handler-failure isolation, not persistence, delivery guarantees, crash recovery, or failure propagation to the publisher (verified against `bus.py`).
- **Diagrams revised:** system data flow; authorizer stub gates and daily-loss formula; Execution Engine flow and order state machine; Portfolio State; `OutcomeRecorder`; new restart-recovery flow.
- `system-design.md`: the companion-doc entry and the §4.6/§4.9 pointer paragraphs updated to match. Decision #170 and its `INDEX.md` row record the amendment.

Still open: EX-5 and EX-12 need confirmation; five judgment calls (J1–J5) are listed for confirmation in §7.1. No backend or frontend code, schema, migration, configuration key, or test changed.

## Boundary

Exactly six files change: the design doc, `system-design.md` (pointers only), the two decision-log files, `CHANGES.md`, and `TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #169: Phase 4 scale/load investigation (`phase4-scale-load-measurement`)

## Current delivery

Added `backend/scripts/measure_live_pipeline_scale.py`, an opt-in measurement
harness (not pytest-collected) that finally measures Phase 4's exit
criterion — "100-symbol streaming with a `FeatureSet` per symbol and no
dropped ticks" — which decision #164 recorded as never having been
demonstrated. The harness wires the real live-path objects (`FeatureEngine`,
`LevelInteractionEngine`, `MarketStateEngine`, `ContextEngine`,
`StrategyScheduler`, `OpportunityCache`, `CandleRecorder`, `LiveTickRelay`),
in the same classes and start order `main.py`'s `lifespan()` uses, against a
real scratch PostgreSQL 16 database, driven by a synthetic in-process tick/
candle provider — never a real feed. It ramps N = 1, 10, 25, 50, 100
synthetic symbols through a tick-ingestion stage and a candle-burst stage,
using `queue.join()` (the same primitive `MarketStateEngine.settle_replay()`
already uses) for authoritative per-stage drain detection rather than
polling published-event counts, which would have been wrong for
`LevelInteractionEngine` specifically (it only publishes on a zone
transition, not once per item processed — verified directly against
`level_interaction_state`'s own `updated_at` timestamps during the harness's
own smoke test).

**Result: at N=100 with a 16-candle burst, both engines fully drained with
exact 100/100 per-symbol coverage and no drops — FeatureEngine in 2.74s,
LevelInteractionEngine in 4.19s, both 14–20x inside the 60-second
per-candle-minute production budget.** A supplementary stress point (same
N=100, a 60-candle burst — beyond the requested ramp, added because it was
cheap and directly answers "how much margin") still held 100/100 coverage at
9.11s/14.03s. `MarketStateChanged` coalescing under a fast synthetic burst
(400 of a possible 1,600 at N=100/K=16) is `DebounceScheduler` (decision
#10/#155) working exactly as designed, not evidence of a drop.

Three real methodology bugs were found and fixed during the harness's own
development — documented as findings in the decision entry rather than
silently patched: (1) an event-count-based drain check that would have
declared "done" while `LevelInteractionEngine` still had real backlog; (2) a
tight burst-publish loop that gave downstream worker tasks zero chance to
run between bursts, because an unbounded `asyncio.Queue.put()` never
actually suspends the coroutine; (3) this harness's own synthetic
historical `candle_ts` colliding with `TickIngestBridge`'s real-wall-clock
stale-bucket safety net, producing a harmless but noisy spurious
duplicate-candle warning — never occurs in production, where `candle_ts`
always tracks real time.

`docs/roadmap/phase-roadmap.md`'s Phase 4 status paragraph: the exit-criterion
sentence rewritten from "has not been demonstrated in the repository record"
to a measured statement citing decision #169's numbers and what remains
unmeasured (real feed, real tick burstiness, provider symbol caps).

**Deliberately NOT touched:** `docs/architecture/scanner-design.md`'s §7
"100-symbol concurrency prerequisite" bullet — that bullet is about Finnhub's
free-tier WebSocket symbol-count ceiling (a data-*provider* question, still
genuinely open, unrelated to and unverified by this backend-processing
measurement) — reported as a follow-up in the decision entry rather than
edited, since this measurement's synthetic in-process provider never
exercised a real feed. Investigation only: no fix implemented, no option
chosen — four options laid out for Saqib in the decision entry. Zero
`backend/app/**`, `frontend/**`, or `backend/tests/**` changes.

## Boundary

Exactly five files change: the new harness script, `docs/roadmap/phase-roadmap.md`
(one sentence), the two live decision-log files, `CHANGES.md`, and
`TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #168: Execution Engine & Portfolio State design doc landed (`execution-engine-design`)

## Current delivery

Added `docs/architecture/execution-engine-design.md`, a DRAFT design pass for
the Execution Engine and Portfolio State — the modules that gate D17's live
half and the whole Decision/Governor/Planning tail. It contains a verified
built / partial / not-built inventory of the downstream pipeline (every claim
cited to `path:symbol` and machine-checked); thirteen places where the as-built
code disagrees with the prose design; a field-by-field map of what one live
`StrategyOutcome` needs; three candidate first slices compared (simulated-venue
auto path recommended; manual-first and IBKR paper analysed); component designs
with data-flow and internal-flow diagrams for the Execution Engine, a simulated
venue, Portfolio State, a minimal Position Monitor, and an `OutcomeRecorder`;
fourteen open forks `EX-1…EX-14`; deferred prerequisites; and proposed
acceptance criteria for a later build task.

`docs/architecture/system-design.md` gains pointers only (companion-doc entry,
one paragraph each under §4.6 and §4.9). Decision #168 and its `INDEX.md` row
record the delivery. **Nothing is decided, built, or migrated**; every fork is
left for Saqib.

No backend or frontend application code, schema, migration, event model, or
test changed.

## Boundary

Exactly six files change: the new design doc, `system-design.md` (pointers
only), the two live decision-log files, `CHANGES.md`, and `TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #167: close the two low-risk documentation status-drift follow-ups

## Current delivery

Closed the two low-risk documentation drift follow-ups recorded by decision
#164. The `docs/README.md` folder table now identifies the existing standalone
`trading-intelligence-overview.md` diagram while leaving the accurate `api/`
placeholder unchanged. Phase 3 in `milestone-tracker.html` now reflects the
verified Finnhub streaming, Polygon historical/fallback, and manually connected
IBKR both-role provider architecture; its exit criterion and three-item shape
are unchanged.

No backend or frontend application code changed.

## Boundary

Exactly six documentation files change: `docs/README.md`,
`milestone-tracker.html`, the two live decision-log files, `CHANGES.md`, and
`TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #166: explicitly exclude the deferred GridPresetPicker sketch

## Current delivery

Restored a clean active frontend TypeScript/build baseline by explicitly excluding
the unreachable, deferred `frontend/src/components/workspace/GridPresetPicker.tsx`
sketch from `frontend/tsconfig.json`. The sketch remains untouched and workspace
preset save/export remains deferred; no live frontend source, backend code, or
dependencies changed.

Updated the frontend-build note in `backend/README.md`, Future Ideas entry 18, the
decision index/log, and this task's testing record. The active program now passes
`npx tsc -b`, and `npm run build` passes its TypeScript and Vite stages.

## Boundary

Exactly seven files change: `frontend/tsconfig.json`, the scoped frontend note in
`backend/README.md`, Future Ideas entry 18, the two live decision-log files,
`CHANGES.md`, and `TESTING.md`. No backend tests are run because no backend code
changes.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #165: first-class sweep outcome filtering

## Current delivery

Added first-class `sweep_id` filtering to `GET /intelligence/strategy-outcomes`
and removed the sweep-results frontend fan-out. The route validates UUIDs,
requires `is_backtest=true`, joins through `backtests.run_id`, preserves
global newest-first ordering and limit semantics, and AND-combines with
`backtest_run_id`. The sweep hook now makes exactly two requests per refresh:
run metadata plus all sweep outcomes. The runs response remains visible so
zero-outcome runs are not hidden.

Updated the route regression coverage, API-client documentation, architecture
record, decision log, and task-specific testing record. Sweep execution,
outcome persistence, rendering, schemas, models, and migrations are unchanged.

## Boundary

Exactly nine files change in the completed delivery: the existing route and
route test, the API client and sweep hook, the backtest-runner architecture
record, the two live decision-log files, `CHANGES.md`, and `TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #164: documentation status synchronization

## Current delivery

Documentation-only synchronization of the two living status surfaces that had
fallen behind the implementation and decision record. No architecture or
product decision changes, and no backend or frontend files change.

- `docs/roadmap/phase-roadmap.md` — updates only `Status (living)`: refreshes
  Phase 2–4 facts, replaces the false Phase 5–6 “not started” statement with
  verified built/partial/not-started boundaries, records that the Phase 4
  100-symbol/no-dropped-ticks exit criterion is not demonstrated, and adds the
  delivery's single plain-text status diagram.
- `docs/architecture/scanner-design.md` — changes only the header `Status` line
  to distinguish the real on-demand scanner/universe/UI implementation from
  the continuous cadence, promotion, discovery, and spread work that remains
  unbuilt. The `DRAFT` label stays unchanged.
- `docs/decisions/INDEX.md` — removes the stale hardcoded upper bound from the
  introduction and adds this delivery's row at final numbering.
- `docs/decisions/confirmed-decisions.md` — appends one documentation-sync
  decision; existing entries remain immutable.
- `TESTING.md` — fresh task-specific evidence, collision, continuity, link,
  footprint, and archive verification record.

Read-only drift findings outside this exact boundary are reported in
`TESTING.md` and the new decision entry; none were corrected here.

## Boundary

Exactly six files change: this file, `TESTING.md`, the roadmap, the scanner
design header, and the two live decision-log files. No other documentation,
application code, test code, migration, archive, Git history, or existing
decision content changes.
