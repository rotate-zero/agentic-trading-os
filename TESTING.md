<!-- BEGIN DELIVERY SECTION: late-tick-candle-diagnostics -->
# TESTING — `late-tick-candle-diagnostics`

Base: pushed `main` `fb478f2`. Controlled fake providers/buses and a controlled local WebSocket/HTTP server only; no real provider credentials, no full suite. The sandbox had no PostgreSQL at first, so five DB-dependent files failed identically on an untouched `fb478f2` clone (13 in `test_market_routes.py`, 1 each in `test_provider_subscription_status.py`, `test_protected_feed_reconciliation.py`, `test_tick_bridge_retirement.py`); a local PostgreSQL 16 was then installed (`trading`/`trading_workspace`, `alembic upgrade head`) and the files were rerun with the change applied. Serial results from `backend/`:

- `tests/test_tick_bridge_diagnostics.py` — **20 passed** (new): fresh zero snapshot and distinct instance ids; older-than-active counted once, candle OHLCV unchanged and raw `PriceUpdated` still published; already-closed after rollover wins precedence over older-than-active; late tick after a wall-clock flush counted without reopening or duplicating a candle; same-minute source regression stays in the candle and is not counted; per-symbol separation and sorting; an excluded tick behind a paused `CandleClosed` publish is counted once only after it settles; ticks after `stop()` neither work nor count and counters stay readable; a handler retired while waiting for the lock does not count; snapshots are frozen and isolated from later counting; storage stays bounded under 200 accepted and 50 excluded ticks; reading publishes/changes nothing; registry reader `None` without a bridge; route counts, identity, replacement then clear; route makes no provider call; snapshot failure is `unavailable`, not 500; exactly one GET route; the real route body is accepted by the measurement reducer and yields a delta.
- `tests/test_streaming_coverage_runtime.py` — **56 passed** (existing plus bridge cases): end-minus-start delta with monitored subset; interval labelled as diagnostic reads (`matches_websocket_window` false); comparable zero is an available zero; replaced bridge, reset counters (three shapes), missing endpoint on an older backend (exit 0, window completes), no registered bridge, bridge appearing/disappearing, malformed/failed end reads — all unavailable, never zero; failed setup never reads the bridge. Existing assertions were updated only for the two added GETs (`http_paths`, Basic-auth header count).
- `test_tick_ingest.py` **8 passed**, `test_tick_bridge_retirement.py` **24 passed**, `test_broker_registry.py` **7**, `test_market_routes.py` **17** (+4 skipped as before), `test_provider_subscription_status.py` **30**, `test_protected_feed_reconciliation.py` **6**, `test_streaming_coverage.py` **63**, `test_streaming_coverage_contract.py` **1**, `test_live_tick_relay.py` **6**, `test_main_execution_pipeline.py` **8** — all passed. Existing exclusion behavior is unchanged: the pre-existing late-tick cases pass untouched.
- `git diff --check` clean. The final ZIP was extracted onto a clean checkout of `fb478f2` and reproduced the working tree (see handoff).

These tests establish counter behavior for controlled sequences. No real feed was measured with the new counters, so no real-feed exclusion rate is claimed. Database effect: the DB-dependent files used the local sandbox PostgreSQL only.
<!-- END DELIVERY SECTION: late-tick-candle-diagnostics -->

<!-- BEGIN DELIVERY SECTION: streaming-tick-regression-minute-breakdown -->
# TESTING — `streaming-tick-regression-minute-breakdown`

Base: pushed `main` `8c89833`. Targeted checks ran serially from `backend/`: `.venv/bin/pytest -q --disable-warnings tests/test_streaming_coverage.py` — **63 passed**; `tests/test_streaming_coverage_runtime.py` — **37 passed**; `tests/test_streaming_coverage_contract.py` — **1 passed**. **101 passed across three files**; no full suite or frontend build. New controlled cases verify same-minute versus earlier-minute tick regressions after UTC normalization, per-symbol totals, high-water timestamp samples and first per-symbol/class examples, zero-count monitored symbols, and counts continuing after the 20-sample cap. Existing completed/interrupted report and production gateway contract checks still pass.

The previous 2026-10-08 Finnhub report has 22 tick regressions but its samples contain no source timestamps; it cannot be reclassified. One first attempt to run the new CLI inside the network sandbox never connected a measurement WebSocket or wrote a report; it was interrupted (exit 130). The following real-feed runs used the existing configured Finnhub key outside that sandbox, with Polygon auto-connect and scanner observation disabled and simulated execution `ready`. Each run requested AAPL/MSFT/NVDA through three successful `POST /finnhub/subscribe` calls; the adapter's local inventory then listed all three. That inventory is request evidence, not provider acknowledgement.

- **Initial 180-second classification window:** 2026-10-08 17:05:33–17:08:33 UTC, status `completed`, CLI exit **0**, normal WebSocket close 1000, no interruption. The ignored local report is `backend/.finnhub-validation/coverage-regressions-2026-10-08.json`. At `/ws`: AAPL **472 ticks / 3 closed 1m candles / 3 1m feature updates**, MSFT **170 / 3 / 3**, NVDA **473 / 3 / 3**; totals **1,115 / 9 / 9**, no zero-event symbols. It counted **250 tick source-time regressions: 240 same-minute and 10 earlier-minute** (AAPL 107/9, MSFT 41/1, NVDA 92/0); candle and feature regressions were zero. All 20 generic samples filled with same-minute events within the first 14 seconds, and 230 later samples were dropped. This exposed the need for the separate first example per symbol/class, added before the final repeat window. A read-only query of local development PostgreSQL `localhost/trading_workspace` found **3 new 1m candle rows per symbol**, **0 new symbol rows, 0 trades and 0 orders** created in this window; no cleanup or deletion was performed.

- **Final 180-second example-retaining window:** 2026-10-08 17:14:39–17:17:39 UTC, status `completed`, CLI exit **0**, normal WebSocket close 1000, no interruption. The ignored local report is `backend/.finnhub-validation/coverage-regression-examples-2026-10-08.json`. At `/ws`: AAPL **188 ticks / 3 closed 1m candles / 3 1m feature updates**, MSFT **50 / 3 / 3**, NVDA **273 / 3 / 3**; totals **511 / 9 / 9**, no zero-event symbols. It counted **81 tick source-time regressions, all same-minute** (AAPL 40, MSFT 5, NVDA 36), with zero earlier-minute, candle or feature regressions. The report retained the first source/high-water timestamp pair for each symbol: AAPL `17:15:48.128`/`17:15:48.130` UTC, MSFT `17:15:22.157`/`17:15:22.244` UTC, NVDA `17:15:28.129`/`17:15:28.137` UTC. The 20 generic samples filled and 61 later samples were dropped; the first examples remained available. No earlier-minute example was observed in this repeat window; the controlled test verifies that a later earlier-minute first example is retained after the generic cap fills. A read-only query of local development PostgreSQL `localhost/trading_workspace` found **3 new 1m candle rows per symbol and no new symbols**. During the window, the existing simulated strategy path also created **one approved MSFT trade, one filled entry order, one fill and one open one-share simulated position**. These records were left intact; no cleanup or deletion was performed. The backend logged an `OutcomeRecorder` entry-snapshot failure because a `datetime` in `fundamentals.profile_updated_at` could not be serialized to JSON; the trade's entry snapshot was not captured. This is an unrelated follow-up, not a measurement failure.

Both completed classification windows had connected Finnhub/FinnhubAdapter beginning and end diagnostics with all three symbols in local inventory. Neither showed malformed messages, unmonitored events, WebSocket interruption, or candle/feature source-time regressions. The initial window proves that earlier-minute regressions occurred in the observed stream, but its capped samples cannot supply their timestamp pairs; the final window supplies real same-minute pairs and did not observe the earlier-minute class. The observations cannot attribute ordering to a specific upstream or backend component or establish lossless delivery. No full suite was run.
<!-- END DELIVERY SECTION: streaming-tick-regression-minute-breakdown -->

<!-- BEGIN DELIVERY SECTION: late-tick-candle-ordering -->
# TESTING — `late-tick-candle-ordering`

Base: pushed `main` `db8cb18`. Controlled provider and bus only for the new bridge tests; no real provider or full suite. Before the bridge fix, the two deterministic older-minute cases failed: a 10:30 tick arriving after the 10:31 bucket opened prematurely closed 10:31 and later repeated 10:30, while a 10:30 tick after the wall-clock close reopened and repeated that candle. The timestamp assertion was normalized to UTC before the final baseline rerun, so these failures reflect extra or split candles rather than string formatting.

- From `backend/`, serial targeted checks: `.venv/bin/pytest -q --disable-warnings tests/test_tick_ingest.py` — **8 passed** (including the two baseline failures and two paused-publication cases); `tests/test_tick_bridge_retirement.py` — **24 passed**; `tests/test_finnhub_reconnect.py` — **19 passed**; `tests/test_candle_recorder.py` — **4 passed**; `tests/test_feature_engine.py` — **81 passed**; `tests/test_main_execution_pipeline.py` — **8 passed**. **144 passed across six files**, each file run serially; the bridge file was rerun after the paused-publication cases were added.
- The bridge cases verify that all raw `PriceUpdated` events still publish, a source-time regression inside the same minute remains in that candle, older-minute ticks neither close a newer bucket nor reopen an already closed minute, and paused close publication does not duplicate a candle, corrupt the next bucket's OHLC or let a later minute's close overtake an earlier one.
- `test_candle_recorder.py` used the configured local PostgreSQL development/test connection (its four tests passed, with no skip) and its isolated `__TEST_ZZZZ__` rows were removed by the test fixture. No migration or task-specific persistent data was added. `git diff --check` passed at final verification.

These tests establish the corrected bridge behavior for controlled sequences. The prior real Finnhub report retained only bounded anomaly samples and did not record regression source timestamps, so this delivery does not claim that any of its 22 regressions crossed a minute boundary or that the provider's trade order has been diagnosed.
<!-- END DELIVERY SECTION: late-tick-candle-ordering -->

<!-- BEGIN DELIVERY SECTION: real-finnhub-streaming-coverage-trial -->
# TESTING — `real-finnhub-streaming-coverage-trial`

Base: pushed `main` `059d9b4`. Real provider validation on Thursday 2026-10-08, 16:25:23–16:28:23 UTC (12:25–12:28 EDT), within the [NYSE core session](https://www.nyse.com/trade/hours-calendars). The configured Finnhub key was used without printing or storing it. The temporary local backend ran with `EXECUTION_MODE=simulated`, `POLYGON_API_KEY=` and `SCANNER_OBSERVATION_ENABLED=false`; it connected Finnhub and reported execution startup `ready` for the simulated venue. No IBKR or live execution venue was connected.

- Three sequential `POST /finnhub/subscribe?symbol=...` requests for `AAPL`, `MSFT`, `NVDA` each returned 200. `GET /market/subscription-status` then listed all three as local requests on connected `finnhub/FinnhubAdapter`; this is request evidence, not provider acknowledgement.
- From `backend/`: `.venv/bin/python scripts/measure_streaming_coverage.py --backend-url http://127.0.0.1:8000 --symbols AAPL,MSFT,NVDA --duration 180 --json-report .finnhub-validation/coverage-2026-10-08.json` — exit **0**, status `completed`, 180.0-second window. The report file is ignored local evidence, not a checked-in source file.
- Observed through the production `/ws` boundary: AAPL **100 ticks, 3 closed 1m candles, 3 1m feature updates**; MSFT **29, 3, 3**; NVDA **84, 3, 3**. Totals: **213 ticks, 9 candles, 9 feature updates**; no zero-event symbol in any category. Beginning and end diagnostics both showed connected `finnhub/FinnhubAdapter`, available local inventory count 3 and all monitored symbols locally listed. The identity snapshots matched; they do not prove continuity between reads. The measurement WebSocket closed normally (1000), without interruption.
- Anomalies: **22 tick source-timestamp regressions** against each symbol's preceding high-water mark (20 retained samples, 2 samples dropped); zero candle or feature regressions, malformed messages, duplicate candle/feature timestamps, unmonitored events, unexpected channels, `_meta` errors, or pre/post-window events. These regressions are observations, not a diagnosed provider or backend fault.
- Local development PostgreSQL target `localhost/trading_workspace`: a read-only post-trial query found **3 new 1m candle rows per monitored symbol** created during the run, with **0 new symbol rows, 0 trades and 0 orders** in the trial interval. The temporary backend shut down cleanly. No database cleanup or deletion was performed.
- Separate runtime observation: Position Monitor logged retained price-window loss with cause `unowed_expiry` for AAPL, MSFT and NVDA while old ticks expired without a visible position. Its journal expiry path marks this as possible delayed-fill evidence loss (`backend/app/position_monitor/engine.py`); the trial did not establish a streaming failure or test delayed-fill recovery. No trades or orders were created during the window.

This one three-symbol session confirms observed real Finnhub trades reached the backend WebSocket and produced minute candles and Feature Engine updates. It does **not** verify lossless upstream delivery, provider acknowledgement, account capacity, the distinct scanner/manual/protected full union, continuous coverage, or the Phase 4 100-symbol exit criterion. Polygon was disabled, so Daily Levels and pre-market volume-ratio features needing a historical provider were unavailable; the observed `features.updated` counts still reflect the actual live 1m Feature Engine events. No full test suite was run for this validation-only delivery.
<!-- END DELIVERY SECTION: real-finnhub-streaming-coverage-trial -->

<!-- BEGIN DELIVERY SECTION: streaming-isolation-coverage-corrections -->
# TESTING — `streaming-isolation-coverage-corrections`

Base: GitHub `main` `9e486aa`. Targeted checks ran serially; no full backend suite, frontend build, real provider, or real-feed trial. Controlled providers and the measurement's synthetic local server were used. The real lifespan shutdown check may attempt optional startup reads against the configured local development PostgreSQL database; it creates no task-specific persistent records.

- `tests/test_tick_bridge_retirement.py` — **24 passed**. Added failed-takeover candidate retirement and no publish, IBKR/Polygon route cleanup, and real-lifespan shutdown retirement after a provider disconnect error; existing late callback, retained historical provider, bucket, reconnect and idempotence checks still pass.
- `tests/test_streaming_coverage.py` — **61 passed**. Updated the source-time regression assertion to require the last received event's source time, while keeping regression detection; malformed diagnostics dictionaries are rejected.
- `tests/test_streaming_coverage_runtime.py` — **37 passed**. Malformed beginning diagnostics fail setup before opening a WebSocket; malformed end diagnostics are recorded while the completed-window exit remains `0`.
- `tests/test_streaming_coverage_contract.py` — **1 passed** against the production gateway and route shapes with a controlled provider.
- Affected existing checks, serial: `tests/test_broker_registry.py` — **7 passed**; `tests/test_finnhub_reconnect.py` — **19 passed**. `git diff --check` checked during implementation.

Final targeted verification: **149 passed across six files**, each run serially; `git diff --check` was clean. One earlier bridge-file run encountered a transient local database connection error during optional lifespan startup; an isolated shutdown test and then the complete bridge file passed on rerun and in final verification. These tests do not establish live provider delivery or capacity.
<!-- END DELIVERY SECTION: streaming-isolation-coverage-corrections -->

<!-- BEGIN DELIVERY SECTION: streaming-coverage-measurement -->
# TESTING — `streaming-coverage-measurement`

Base: GitHub `main` `13678c6`, unchanged at packaging. Targeted checks only (no full backend suite, no frontend, no real provider key or feed). Local PostgreSQL 16 (`trading_workspace`, migrated to head) was running for the adjacent existing tests; the new tests need no database.

**Synthetic verification (reported separately from any real-feed trial).**

- `tests/test_streaming_coverage.py` — **61 passed** (no I/O): input validation (both/neither symbol source, non-positive/NaN/inf/bool duration, bad URL/scheme/query, bad tickers, duplicates), credential redaction, acknowledgement-gated window with monotonic ack latency, deterministic per-symbol values with explicit clocks, source vs receipt time kept distinct, 1m-only filtering, unmonitored symbols (bounded sample, exact case-sensitive match), 13 malformed shapes by kind (including deep nesting and binary frames), unexpected channel/`_meta` error/setup rejection, deadline cut-off, regression/duplicate/naive/`Z` timestamps, bounded retained state over 60,000 events, diagnostics reduction (no capacity/delivery fields) and identity comparison, deterministic report/console output, atomic JSON writer and report-path checks.
- `tests/test_streaming_coverage_runtime.py` — **35 passed**, against a controlled local WebSocket+HTTP server speaking the real protocol shapes: interleaved symbols, timeframe filtering, zero-event symbols, malformed messages, pre-window events, a delayed event (monotonic offset), honest zero-event completion, saved-universe capture exactly once, begin/end snapshots (identity change, unreadable end snapshot), empty/unusable universe and unreadable start diagnostics (no WebSocket connection made), missing acknowledgement timeout and subscription rejection (clean close), handshake failure, connection loss and server close code 1011 (partial counts kept), task cancellation and stop request (clean close code 1000), a 20,000-tick flood with state growing by exactly the one anomaly sample, credentials sent as Basic auth yet absent from reports/console/errors, in-process argument and report-write-failure handling, and the **real script end to end as a subprocess**: console table plus JSON report, invalid input exit 2 with no backend contact, empty universe exit 3 with a failure report, SIGINT and SIGTERM exit 4 with a partial report and clean close, connection loss exit 4, no credential in output, `--help`.
- `tests/test_streaming_coverage_contract.py` — **1 passed**: the production `/ws` router, `WebSocketGateway`, `EventBus`, `make_envelope` payload models and the real `GET /market/subscription-status` and `GET /scanner/universe` routes under uvicorn (provider double and universe read substituted) are parsed with zero malformed/unexpected messages, 1m/other-timeframe and unmonitored handling as expected, and the unsubscribed `market.tick.snapshot` channel never delivered.
- Commands (from `backend/`): `pytest tests/test_streaming_coverage.py tests/test_streaming_coverage_runtime.py tests/test_streaming_coverage_contract.py` — **97 passed in about 17 s**, repeated three times with the same result.
- Affected existing checks, serial: `pytest tests/test_websocket_channels.py tests/test_provider_subscription_status.py tests/test_scanner_observation_status.py tests/test_scanner_route_concurrency.py tests/test_finnhub_reconnect.py tests/test_context_universe_hot_add.py tests/test_scanner_universe_feed_request.py` — **114 passed**; run together with the three new files in one process: **211 passed**. Rerun again from the extracted ZIP on a clean checkout of the base (reported with the handoff). `git diff --check` clean.

Interpretation of these results: they show the tool classifies and reports correctly against protocol-shaped synthetic traffic and the production gateway code. **Not run: any real-feed trial** (no Finnhub key, no live backend/provider, no market-hours session), the full backend suite and the frontend. Real-feed behavior, provider capacity and lossless delivery are unmeasured; the tool never claims them.
<!-- END DELIVERY SECTION: streaming-coverage-measurement -->

<!-- BEGIN DELIVERY SECTION: retired-tick-bridge-isolation -->
# TESTING — `retired-tick-bridge-isolation`

Base: GitHub `main` `95490d0`, unchanged at packaging. Checks ran serially against a scratch local PostgreSQL 16 (`trading_workspace`, migrated to head) for the DB-backed lifespan tests; no full suite, real provider key or broker connection. The new tests use controlled providers, a recording/gated bus and fake WebSocket sockets only.

- New file `tests/test_tick_bridge_retirement.py` — **21 passed**. Covers late callback after `stop()` (no task created); handlers queued but not started; a handler paused inside a publish (cancelled; `aclose()` returns without releasing a stuck bus); pending flush (not yet woken, and paused mid-publish); partial bucket discarded; retained historical provider silent after takeover while still connected and registered; queued old handlers dropped at takeover; old bridge `RETIRED` before the new provider is visible at the protected-feed wake; immediate synchronous clear with cleanup awaited later; same-provider replacement delivering each tick once; correct new-provider candles with no leak of the old partial bucket; failed old disconnect leaving registry and old bridge unchanged; shutdown retirement without role change; repeated stop/aclose/clear/takeover idempotence; callback removal only via the optional capability (and a failing capability); constructor failure leaving no task; Finnhub same-instance reconnect (one callback, bridge stays `ACTIVE`, one event per trade) and a retained Finnhub adapter silent after takeover.
- Affected set, serial: `pytest tests/test_tick_ingest.py tests/test_tick_bridge_retirement.py tests/test_broker_registry.py tests/test_execution_registry.py tests/test_finnhub_reconnect.py tests/test_protected_feed_reconciliation.py tests/test_protected_feed_event_wake.py tests/test_protected_feed_status.py tests/test_provider_subscription_status.py tests/test_scanner_universe_feed_request.py tests/test_market_routes.py tests/test_ibkr_adapter.py tests/test_main_execution_pipeline.py tests/test_intelligence_routes.py tests/test_simulated_eod_integration.py tests/test_feature_engine.py` — **276 passed** (the last four files exercise the real lifespan shutdown). Bridge plus reconnect tests repeated three times: 40 passed each.
- **Baseline proof.** The new file was replayed on an untouched checkout of `95490d0` with a harness that only adds shim attributes for the new API (`state`, `aclose`, registry settle functions) and tolerates teardown on a closed loop: **17 failed, 4 passed**. The failures reproduce the defect behaviourally: events reach the bus from the retired/retained provider's old bridge after `stop()`, after takeover and after a synchronous clear; queued handlers run after `stop()`; a paused handler resumes and publishes; same-provider replacement delivers each tick twice; the retained old partial bucket rolls into a candle. The 4 passing there (flush-not-yet-woken, flush paused mid-publish, failed old disconnect, constructor failure) are guards that the baseline already satisfied; some of the 17 fail on the shimmed state assertion rather than on published events.
- Clean-checkout verification of the ZIP is reported with the handoff. `git diff --check` clean.
- Not run: full backend suite, frontend (untouched), real Finnhub/Polygon/IBKR connections. Boundary tested: events already on the bus are not retracted; only new publishes from a retired bridge are prevented. `remove_tick_callback` is tested only against a controlled provider because no shipped provider offers it.
<!-- END DELIVERY SECTION: retired-tick-bridge-isolation -->

<!-- BEGIN DELIVERY SECTION: scanner-universe-feed-request -->
# TESTING — `scanner-universe-feed-request`

Base: GitHub `main` `e84934a`; the fetched tip remained unchanged before assigning decision #193. Focused checks ran serially; no full suite, real provider key or broker connection. The new database tests create only `symbols` and `scanner_universe_symbols` in a scratch PostgreSQL schema, drop it after each case, and never edit the public saved universe, candle or ledger tables.

- Backend: `.venv/bin/pytest -q --disable-warnings tests/test_scanner_universe_feed_request.py tests/test_scanner_universe.py tests/test_scanner_state_route.py tests/test_scanner_route_concurrency.py tests/test_finnhub_reconnect.py tests/test_protected_feed_reconciliation.py` — **53 passed**. The new tests cover empty persisted set, offloaded session ownership, actual POST route, locally present requests, failed inventory read, sequential returned/failed requests with safe error class, missing/disconnected provider, failed provider check, read failure, provider takeover (including inside the last subscribe) and disconnect mid-batch, duplicate-batch rejection, saved-universe edit after capture, and replay contention both before and during the database read. A scoped SQL statement recorder confirms the request's database work is SELECT only; controlled providers record and assert no unsubscribe calls.
- Frontend: a temporary jsdom 26.1.0 / React `act` harness outside the repository bundled the actual `UniverseTab` and API client and replaced only `fetch` — **passed**. It exercised the POST, repeated same-tick clicks, pending state, a partial result, a universe removal during the request, preservation of the submitted symbol set, another request after settlement and late completion after unmount. The harness is not a repository dependency. `npm run build` (`tsc -b && vite build`) — **passed** with Vite's existing large-chunk advisory; generated `tsconfig.tsbuildinfo` was restored to its tracked contents.
- `git diff --check` — checked at final review. No automatic full suite was run.

These checks establish local request ordering and response projection. They do not establish provider acknowledgement, capacity, real-feed delivery or continuous coverage. A provider takeover during an already awaited `subscribe()` can let that one call finish; the next symbol uses the rechecked owner and is left unattempted when it changes.
<!-- END DELIVERY SECTION: scanner-universe-feed-request -->

<!-- BEGIN DELIVERY SECTION: scanner-observation-lifespan -->
# TESTING — `scanner-observation-lifespan`

Base: GitHub `main` `5087874`. Directly affected checks ran serially; no automatic full suite or external provider was used. Real FastAPI lifespan tests used controlled MarketClock times, worker timers/universe reads and local `trading_workspace` PostgreSQL startup dependencies. They did not place a real broker order or create a scanner subscription.

- `.venv/bin/pytest -q --disable-warnings tests/test_scanner_observation_lifespan.py tests/test_scanner_observation_worker.py tests/test_scanner_observation_status.py tests/test_scanner_observation_source_timestamps.py tests/test_scanner_state_route.py tests/test_scanner_route_concurrency.py tests/test_finnhub_reconnect.py tests/test_execution_startup_status_route.py` (from `backend/`) — **117 passed**.
- New lifecycle cases cover disabled startup; enabled selected and excluded sessions; settings rejection for empty/invalid/duplicate/closed labels and unsupported execution mode; the documented JSON environment setting; holiday, half-day and uncovered-year admission; blocked/failed execution startup; partial observer-start rollback; reader installation/removal and the real `/scanner/observation` response; shutdown draining a blocked universe read; repeated lifespan entry; a busy replay/connection slot skipping a due point; and a selected session ending during a blocked universe read.
- The replay race holds an actual offloaded universe read while `install_replay_engines()` temporarily replaces process-wide singletons. The worker publishes no result while replay holds the slot; after restoration, its next admitted cycle reports the live FeatureEngine 1m candle timestamp through the real observation endpoint. Existing replay and scanner route/worker checks pass unchanged.
- Frontend: `npm run build` — passed (`tsc -b` and Vite). Its generated `tsconfig.tsbuildinfo` was restored to the original tracked contents; build output is excluded. Vite emitted its existing large-chunk advisory.

These tests establish lifecycle ownership and local replay isolation under controlled timing. They do not measure real-feed delivery, capacity, freshness or scanner-universe coverage. No full suite was run.
<!-- END DELIVERY SECTION: scanner-observation-lifespan -->

<!-- BEGIN DELIVERY SECTION: protected-feed-event-wake -->
# TESTING — `protected-feed-event-wake`

Base: GitHub `main` `bbb43f3`. Tests ran serially from `backend/`; controlled providers and WebSocket transports used no real credentials, feed or broker. PostgreSQL integration used isolated unique `trading_workspace` test rows, removed after each case. No migration or full-suite run.

- Baseline sensitivity: the new immediate-wake regression failed against an isolated archive of untouched `bbb43f3` with missing `request_reconcile()`. The provider-takeover and same-instance Finnhub-reconnection regressions both timed out against that archive; all three pass with this delivery.
- Affected tests: `.venv/bin/pytest -q --disable-warnings tests/test_protected_feed_event_wake.py tests/test_protected_feed_reconciliation.py tests/test_protected_feed_status.py -k 'not lifespan and not reader_is_not_installed and not rollback_when_the_owner' tests/test_broker_registry.py tests/test_finnhub_reconnect.py` — **59 passed, 3 deselected**. The deselected cases are `TestClient` lifespan cases whose local Python 3.14/Starlette portal hung in the prior delivery; status projection and owner behavior were exercised without that portal here.
- Exit ledger: `.venv/bin/pytest -q --disable-warnings tests/test_exit_ledger_eod_postgres.py` — **41 passed**. It asserts that a newly committed close reservation carries the new marker; existing reservation/retry state-machine checks remain green.
- New deterministic cases cover prompt exposure/provider wakes without advancing the periodic clock, a 100-request burst, a wake during blocked subscription causing exactly one follow-up, failed-read cooldown and omitted-notification periodic recovery, current-owner takeover, restart/stop and late-wake rejection, responsive critical EventBus dispatch while a subscription blocks, committed order and position rows visible to the protected query, new-versus-reused exit reservation signaling, and same-instance Finnhub reconnect. Existing status retention, request evidence, replay and registry regressions are included in the affected run.

These checks prove local request scheduling and PostgreSQL commit visibility under controlled ordering. A commit-to-signal crash window remains, recovered on a later periodic/startup cycle. They do not verify real provider acknowledgement, capacity, feed delivery or continuous protection.
<!-- END DELIVERY SECTION: protected-feed-event-wake -->

<!-- BEGIN DELIVERY SECTION: finnhub-stream-reconnect -->
# TESTING — `finnhub-stream-reconnect`

Base: GitHub `main` `dcbb730`. All WebSocket transports were controlled local fakes; no real Finnhub key, external feed or broker connection was used. No database changes or full-suite run.

- Baseline sensitivity: the first five new reconnect regressions against untouched `dcbb730` failed (5/5) with `TypeError: FinnhubAdapter.__init__() got an unexpected keyword argument 'connect_ws'`. This demonstrates the new lifecycle coverage did not pass against baseline.
- Final affected tests, serial: from `backend/`, `.venv/bin/pytest -q --disable-warnings tests/test_finnhub_reconnect.py tests/test_finnhub_provider.py tests/test_provider_subscription_status.py::test_finnhub_snapshot_tracks_changes_sorted_and_sends_nothing_itself tests/test_provider_subscription_status.py::test_finnhub_failed_send_is_not_recorded tests/test_provider_subscription_status.py::test_same_instance_reconnect_can_request_finnhub_and_ibkr_again` — **30 passed**.
- Controlled cases include exceptional and clean remote close, failed retries and capped delays, eventual restoration, unsubscribe during outage, partial restore, current-only inventory with unknown capacity/delivery, obsolete-socket messages, callback/bridge uniqueness, repeated connect, disconnect during initial connect/backoff/restore, registry takeover, replay rejection during retry and before singleton installation, and replay permitted after settled disconnect.
- Existing TestClient-based `test_provider_subscription_status.py` cases were attempted serially but the local Python 3.14/Starlette `TestClient(app).get(...)` portal hung even for `test_no_streaming_provider_is_an_explicit_unavailable_state`, which uses no Finnhub adapter. The pure route was verified directly in the new controlled test. These TestClient cases are not claimed as passing.

These checks establish local lifecycle and replay-guard behavior only. They cannot establish Finnhub acknowledgement, real-feed delivery or subscription capacity.
<!-- END DELIVERY SECTION: finnhub-stream-reconnect -->

<!-- BEGIN DELIVERY SECTION: protected-feed-reconciliation-status -->
# TESTING — `protected-feed-reconciliation-status`

Base: GitHub `main` `c5313d7ad8230b733794b588117b25f318426c98`. Local PostgreSQL 16 at `localhost:5432` (`trading_workspace`, migrated to head `0017`). Targeted tests were run serially; no full suite, no real broker or feed session.

- **New backend tests:** `pytest -q tests/test_protected_feed_status.py` — 21 passed. Real `ProtectedFeedReconciler` cycles with controlled providers/readers and the real app routes: no reconciler installed versus installed-never-attempted (and first attempt in progress); successful empty set (distinct from never-read); no streaming provider; disconnected provider; connection-check failure (safe code); local inventory hits with a returned request and two failed requests (`timeout` and `other`) and the retry cycle; inventory-unavailable provider; read failure after a success (last set and its `read_at` kept, never empty) and a first read failure (never-read, not empty); takeover to a replacement provider; provider replaced and provider disconnected mid-cycle (`no_outcome` for the rest); running/in-progress flags and shutdown mid-request recorded as `interrupted` with nothing admitted afterwards; periodic counters; frozen dataclasses, tuple-only containers and no mutable container anywhere in the snapshot, and an earlier snapshot unchanged by a later cycle; the response being a fresh projection; status reads (never attempted, after a cycle, and while a cycle holds the lock) causing no reconciliation, database read, `is_connected`, inventory read, subscribe or task start; a raising reader giving `snapshot_read_failed`; the real lifespan installing the reader for the running owner and clearing it on shutdown, not installing it when startup is blocked, and rollback after an owner start failure leaving no reader and a stopped owner. Injected exception text containing a DSN and password never appears in any response.
- **Affected existing tests:** `pytest -q tests/test_protected_feed_status.py tests/test_protected_feed_reconciliation.py tests/test_execution_startup_status_route.py tests/test_main_execution_pipeline.py tests/test_provider_subscription_status.py tests/test_market_routes.py tests/test_broker_registry.py` — 94 passed; `tests/test_intelligence_routes.py` — 16 passed. The existing reconciler tests are unchanged.
- **Environment note:** one earlier run showed errors in `test_main_execution_pipeline.py` because the local PostgreSQL had stopped (connection refused); the same errors occurred on a clean checkout of the base, so they were environmental. After restarting the database the files above passed.
- **Backend mutation checks (temporary, reverted, files verified identical):** a failed read overwriting the retained set with an empty one, raw exception text in the error class, the reader not cleared at shutdown, `get_snapshot()` starting a cycle, and a mutable list in the snapshot each failed the suite. The restored code passed all 21.
- **Frontend build:** `npm run build` (`tsc -b && vite build`) succeeded with no TypeScript errors. `tsconfig.tsbuildinfo`, which is tracked and was regenerated by the build, was restored and is not part of the delivery.
- **Actual frontend behavior:** the repository has no frontend test runner, so a temporary jsdom 24.1.3 / React 18.3.1 `act` harness outside the repository bundled the real `BrokerPanel` (with its real `useBrokerStatus`, `useSubscriptionStatus`, `useProtectedFeedStatus` and `api-client.ts`), replacing only `fetch` (individually resolvable held status requests). **55 assertions passed with no unexpected `console.error`.** Covered: no request while the panel or section is collapsed and exactly one on expanding; every state (not installed, never attempted and never read, first attempt in progress, successful empty set, no provider, disconnected with retained request outcomes labelled with their own time, read failure after success showing the retained symbols with the successful read's time, first read failure not shown as an empty set, partial request failures with per-symbol outcomes, unknown future state shown as its code); provider, connection and timestamps; the interval-delay and not-delivery explanations; no verified/delivered wording; Refresh never disabled; an older response arriving after a newer one ignored, a hung request's late failure invisible, a newer failure not overwritten by an older success; a failed Refresh keeping the last reading with its server read time and a later success clearing the notice; an initial HTTP failure shown as a request failure, distinct from "not installed" and from worker failure states; markup in an error or symbol rendered literally; late success or failure invisible after collapsing the section, collapsing the panel and unmounting the root, with re-expanding loading afresh; the two diagnostics sections requesting only their own routes and Connect touching only broker routes.
- **Frontend mutation checks (temporary, reverted, files verified identical):** removing the superseded-response guards failed 4 assertions; rendering a never-read set as an empty set failed 2; dropping the retained-read label failed 1; disabling Refresh while loading broke the held-request scenarios. As in earlier deliveries, the unmount invalidation is defensive parity: React 18 ignores a state update after unmount, so it is not independently provable through this harness.
- **Not run:** the full backend suite; any real-provider, IBKR or live-feed session; a browser against the running app.

What a pass establishes and does not: see `docs/architecture/scanner-design.md` §18.14. The status shows request evidence from the owner's last attempt; it is not provider acknowledgement, proof of tick delivery or confirmed protection. The reproduction on a clean checkout of the base (apply, `git diff --check`, rerun) is reported in the handoff.
<!-- END DELIVERY SECTION: protected-feed-reconciliation-status -->

<!-- BEGIN DELIVERY SECTION: candle-to-simulated-trade-acceptance -->
# TESTING — `candle-to-simulated-trade-acceptance`

Base: GitHub `main` `9527854f8e3b9af8f2485e0c98d465a1b8c46fc9`. Local PostgreSQL 16 at `localhost:5432`; every acceptance run used a fresh, disposable, migrated (`alembic upgrade head`, revision `0017`), empty database. No real provider connection was made (Finnhub/Polygon keys blanked by the command); no full-suite run was performed.

Command (from `backend/`):

```
POSTGRES_DB=<disposable_db> python scripts/candle_to_simulated_trade_acceptance.py --database <disposable_db>
```

- **Acceptance command, PostgreSQL:** `RESULT: PASS — 43 milestones` on every fresh-database run (P.1–P.4 preconditions, C1.1–C1.30 positive path, C2.1–C2.9 contrast); about 4 s each, exit code 0. Recorded in the handoff: the final run on a clean checkout of the base with the ZIP applied.
- **Directly affected tests:** `pytest -q tests/test_candle_to_simulated_trade_acceptance.py tests/test_simulated_mvp_acceptance.py` — 35 passed (16 new, 19 existing; the existing simulated-MVP file is unchanged). Run against a migrated database.
- **Sensitivity (temporary mutation, not delivered):** `FeatureEngine._update_gap` was temporarily changed to `return {}`. The positive acceptance then failed with exit code 1 at milestone `C1.10` ("calculated gap features equal the values recomputed from the supplied candles"; detail showed `regular_open`, `gap_dollars`, `gap_pct` absent while `pdc` was present). The first attempt failed as an unexpected `TypeError` from an eagerly formatted milestone note; the note formatting was made absence-safe so the failure names the milestone. `backend/app/feature_engine/engine.py` was restored with `git checkout` and `git status` confirmed it unchanged; it is not in the ZIP.
- **Existing simulated-MVP command:** `python scripts/simulated_mvp_acceptance.py --database <disposable_db>` still passes 88 milestones on a fresh database (baseline before and after).
- **Not run:** the full test suite; any real-provider, IBKR or live-feed check. Frontend untouched.

What a PASS establishes and does not: see `docs/architecture/execution-engine-design.md` §6.13. It is a synthetic candle-to-trade path with calculated features recomputed from supplied candles; it is not tick acquisition, real-feed coverage, profitability or broker execution. The database is left as evidence; recreate a fresh migrated database before rerunning.
<!-- END DELIVERY SECTION: candle-to-simulated-trade-acceptance -->

<!-- BEGIN DELIVERY SECTION: protected-feed-reconciliation -->
# TESTING — `protected-feed-reconciliation`

Base: GitHub `main` `a63dd0f`; targeted tests run serially with the repository's `backend/.venv`. Local PostgreSQL `trading_workspace` at `localhost:5432` was used for the isolated projection fixture and existing execution startup checks; test-owned trade/order/position rows were removed by fixture cleanup. No real provider connection or full suite.

- `backend/.venv/bin/pytest -q backend/tests/test_protected_feed_reconciliation.py` — 6 passed. The projection test covers restored open/closing positions, each approved/submitted/partially-filled/unknown working status, terminal orders, closed positions, mode isolation and deduplication. Controlled providers cover manual-symbol retention after needs shrink, partial request failure, failed reads, unavailable inventory repeat requests, disconnect/retry, provider takeover, exposure changes on later cycles, overlap, shutdown cancellation, and the timer-driven next cycle. An in-process Event Bus, TickIngestBridge, PositionMonitor and SimulatedVenue check tick delivery for a protected symbol absent from a controlled scanner universe.
- `backend/.venv/bin/pytest -q --disable-warnings backend/tests/test_execution_startup_status_route.py backend/tests/test_main_execution_pipeline.py::test_world_view_stays_unavailable_when_startup_reconciliation_blocks_entries backend/tests/test_main_execution_pipeline.py::test_position_monitor_places_durable_exit_and_closes_on_later_tick` — 8 passed. Clean startup still reaches ready; reconciliation discrepancy remains blocked; the existing monitor/execution path still closes on a later tick.
- Provider regression run initially found two tests that asserted the older retained-record behavior after Finnhub/IBKR disconnect. Those assertions were updated for the required old-session reset, and a same-instance reconnect request check was added. The final serial command combining the six new tests, provider diagnostics/adapter tests, execution startup status, blocked reconciliation and existing monitor integration passed **62/62** (`--disable-warnings`; 3,031 dependency deprecation warnings). A first combined run exposed the new integration test patching the shared `MarketClock` instance; it was corrected to inject its own clock, and the same combined command then passed.

The first sandboxed PostgreSQL run could not open the local database connection; the identical targeted check passed outside that network sandbox. The fixture never touches live trading or external provider accounts.
<!-- END DELIVERY SECTION: protected-feed-reconciliation -->

<!-- BEGIN DELIVERY SECTION: scanner-observation-source-timestamps -->
# TESTING — `scanner-observation-source-timestamps`

Base: GitHub `main` `79041bc6c33f38fae90bd1be5c1e75f54dd656b8`. Python 3.13.16 (FastAPI 0.115.14, Starlette 0.46.2, httpx 0.27.2, pytest 8.4.2, pytest-asyncio 0.24.0, SQLAlchemy 2.0.54); Node 22.22.0, React 18.3.1, TypeScript 5.9.3, Vite 5.4.21; local PostgreSQL 16.15 on `localhost:5432` (`trading_workspace`, created fresh and migrated through Alembic `0017`), needed only by two neighbouring tests that read the real universe table (the new module needs no database). No provider credentials, no network access to any provider, no full-suite run (targeted, serial).

- **Baseline before changes:** `test_scanner_runner.py`, `test_scanner_observation_worker.py`, `test_scanner_observation_status.py`, `test_scanner_state_route.py`, `test_scanner_route_concurrency.py` — 49 passed on the untouched base once PostgreSQL was running (the same set showed 2 failures, both "connection refused" on the real-universe tests, before it was started — an environment gap, not a product failure).
- **New module:** `pytest -q tests/test_scanner_observation_source_timestamps.py` — 20 passed (three consecutive runs, no flakiness).
- **Directly affected, serial:** `test_scanner.py`, every `test_scanner_*.py` (runner, worker, status, state route, concurrency, universe, the new module) and `test_feature_engine.py` — 164 passed. The full suite was not rerun for this read-surface delivery.
- **Real path, controlled data:** the real `run_scan`, the real `ScannerObservationWorker` (injected clock, universe and weights) and the real routes over a **real `FeatureEngine`** whose `_latest` map (what the engine replaces on every closed candle) holds controlled `FeatureSet`s, so each timestamp travels through the genuine `get_snapshot()` ISO round trip; only the engine accessor is patched. Asserted: each symbol keeps its own source time (three different times across symbols); a scan completed "now" can carry three-day-old source data and the response shows scan completion and data time separately; scores equal `score_symbol` on the same inputs, order is descending score, a zero-input row is retained with its source time, and a symbol with no 1m row is skipped with no row and no timestamp.
- **Pairing under replacement:** exactly one `get_snapshot()` per symbol (call log asserted, none for an extra attach); an engine replaced right after each read leaves the first symbol with the old time and old inputs and the second with the new time and new inputs; an engine replaced while `score_symbol` runs cannot change the already-captured time, features or score.
- **Compatibility:** `ScanResult` constructed positionally and by keyword with four fields still works, equals, defaults `source_candle_ts` to `None` and stays frozen; `GET /scanner/state` over a real engine returns exactly `universe`/`results`/`total_scored`/`skipped` with rows of exactly `symbol`/`score`/`inputs_available`/`features`; the unavailable response keeps its shape and adds `read_at`; the route never touches the FeatureEngine (accessor patched to fail, not called) and performs one `get_snapshot()` per request.
- **Retained state:** after a successful cycle, a failing universe read and (separately) a failing scan leave the snapshot's `results` the identical tuple with the same source times, the response's rows byte-equal to the pre-failure read, `last_error` set and `latest_attempt: "failed"`; a later successful cycle replaces the times while the earlier snapshot object is unchanged; mutating a projected response (even clearing its rows) cannot reach the snapshot or the next response; frozen rows/snapshot reject assignment.
- **Unknown, empty, UTC, future:** rows built without a time, and a scan double with no attribute at all, serialize `null` (the key is always present); a successful empty result has no rows and no timestamps; a `+06:00` candle time serializes as `…14:29:00Z`, a naive datetime as the same UTC value (the codebase convention); a future source time is returned as stored (not clamped to `read_at`) with no stale/fresh key anywhere; `read_at` is the pinned server clock, one per request, advancing only when the server clock does.
- **Mutation checks (temporary, reverted, file hashes verified identical):** a second FeatureEngine read for the timestamp failed 3 tests; the worker dropping the field failed 5; the route omitting it failed 6; the route clamping a future time failed 2; `GET /scanner/state` leaking the new key failed 1. The restored code passed all 20.
- **Frontend build:** `npm run build` (`tsc -b && vite build`) passed. The build rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it was restored and is not in the ZIP.
- **Actual frontend behavior:** the repository has no frontend test runner, so a temporary jsdom 24.1.3 / React 18.3.1 `act` harness outside the repository bundled the real `ScannerPanel` (real `WorkspaceProvider`, `useScannerObservation`, `api-client.ts`), replacing only `fetch` (observation requests individually resolvable). **48 assertions across 12 scenario groups passed with no unexpected `console.error`.** Covered: no observation request while the panel or section is collapsed, exactly one on expanding; rows with an old (2d 17h), recent (2m 30s), null (unknown), future ("later than the server read time — age not shown", never zero or negative) and unreadable (`<b>…</b>` shown literally, not interpreted) source time; the summary with scan-completed and server-read times, the earliest–latest range with the unknown count and the completion-vs-data-time / not-a-live-counter / not-a-freshness-verdict note; no stale/fresh/healthy wording in the section apart from the pre-existing coverage caveat, which is preserved along with the existing last-success line; age following each new response's `read_at` rather than the browser clock; a missing `read_at` (older backend) giving "age unavailable" and a missing field giving "unknown"; a failed Refresh keeping the last read (source times included) with a notice that a later success clears; an older response arriving after a newer one ignored, a superseded request's late failure and a hung request's late failure invisible; a failed latest attempt keeping its retained rows' source times; successful-empty, never-succeeded and unavailable states showing no source times; an initial HTTP failure shown as a request failure, not "unavailable"; and late success/failure invisible after collapsing the section, collapsing the panel and unmounting the root, with re-expanding loading afresh and no console error.
- **Frontend mutation checks (temporary, reverted, hashes verified identical):** clamping a future source time to "age 0s" failed 2 assertions; measuring age from the browser clock failed 8; rendering an unknown time as blank failed 2; dropping the completion-vs-data-time note failed 1; dropping the "at server read" label failed 8. The restored code passed all 48. `useScannerObservation.ts` was not changed, and as in earlier deliveries its unmount invalidation is defensive: React 18 ignores a state update after unmount, so that guard is not independently provable through this harness.
- **Not tested:** a real FeatureEngine fed by a real provider, a real lifespan install of the worker, any real feed delivery or coverage (so nothing is said about why a candle is old), and the full backend suite.
- **Packaging checks:** performed on the final ZIP and reported in the delivery handoff (clean checkout of the stated base, `git status --short` against the manifest, `git diff --check`, the new module and the directly affected set, the frontend build, and a final `main` re-check).
<!-- END DELIVERY SECTION: scanner-observation-source-timestamps -->

<!-- BEGIN DELIVERY SECTION: provider-subscription-diagnostics -->
# TESTING — `provider-subscription-diagnostics`

Base: GitHub `main` `8097abd87d4f4bed9889d6bea2b34f3ea83949f8`. Python 3.12.3 (FastAPI 0.115.14, Starlette 0.46.2, httpx 0.27.2, pytest 8.4.2, pytest-asyncio 0.24.0, websockets 14.2, ib_async 2.1.0, SQLAlchemy 2.0.54); Node 22.22.2, React 18.3.1, TypeScript 5.9.3; local PostgreSQL 16.15 on `localhost:5432` (`trading_workspace`, created fresh and migrated through Alembic `0017`), needed only by the neighbouring lifespan-based suites and by one new test that runs the real lifespan. No real provider credentials, no network access to any provider, no full-suite run (targeted, serial).

- **Baseline before changes:** `test_market_routes.py`, `test_broker_registry.py`, `test_finnhub_provider.py`, `test_polygon_provider.py`, `test_ibkr_adapter.py` — 59 passed on the untouched base.
- **New module:** `pytest -q tests/test_provider_subscription_status.py` — 29 passed (three consecutive runs, no flakiness).
- **Directly affected, serial:** the new module plus `test_market_routes.py`, `test_broker_registry.py`, `test_finnhub_provider.py`, `test_polygon_provider.py`, `test_ibkr_adapter.py`, `test_ibkr_backtest_route.py`, `test_ibkr_historical.py`, `test_tick_ingest.py`, `test_live_tick_relay.py` and `test_stored_backtest_route.py` — 156 passed. The full suite was not rerun for this read-surface delivery.
- **Real adapters, mocked transports:** Finnhub over a recording fake WebSocket (patched `websockets.connect`; iterating it raises `ConnectionClosed` on demand), Polygon with a recording `get_aggs` and a one-hour poll interval, IBKR with a recording `ib_async.IB` (connect/disconnect/`isConnected`/qualify/`reqMktData`/`cancelMktData` replaced). Asserted: subscription changes reflected in ascending order; a duplicate subscribe and an unknown unsubscribe still send/request/cancel nothing (existing behavior); a failed Finnhub send and a failed IBKR qualification are not recorded, and symbols before a mid-list Finnhub failure are; snapshots are immutable tuples that are not the adapter's internal collection, an earlier snapshot never changes after later subscribes, and consumer-side copies/mutation cannot reach the adapter; repeated reads add nothing to any transport (sends, REST calls, `reqMktData`, `cancelMktData`, qualification).
- **Disconnect semantics:** after an explicit `disconnect()` all three adapters still return their retained local record (documented existing behavior), while the route reports `connected: false` and an `unavailable` inventory (`provider_not_connected`, `count`/`symbols` null — never `[]`); an unexpected Finnhub socket close behaves the same; a connected adapter with nothing recorded is a genuine empty inventory (`count: 0`).
- **Provider replacement:** Finnhub replaced by Polygon through the real `take_over_streaming` (Finnhub disconnected, its socket closed) reports Polygon and only Polygon's symbols; IBKR kept alive for the historical role and replaced for streaming by Finnhub reports Finnhub's symbols, with IBKR's subscriptions untouched (`cancelMktData` never called); replacement by an unsupported double, and clearing the streaming role, report `inventory_not_supported` and `no_streaming_provider` respectively.
- **Unsupported, missing and misbehaving providers:** no streaming provider (also under the real lifespan, where `conftest.py` blanks the API keys) → `status: "unavailable"`; the existing `_FakeConnectedAdapter`-style double and a minimal `MarketDataProvider` subclass without the capability → `inventory_not_supported` (not `[]`), `provider.id: "unknown"`; snapshots returning `None`, a string, ints, a list containing `None`, a dict or an arbitrary object, or raising, and an `is_connected()` that raises → 200 with `snapshot_failed` / `connection_state_unknown`; a non-conforming adapter returning its own list is copied and sorted without mutating the original.
- **Labelling:** every response, including with 120 recorded symbols, carries `basis: "locally_tracked_requests"`, `capacity: {status: "unknown", limit: null}`, `delivery: {status: "unknown"}` and the fixed note ("not provider acknowledgement" / "capacity and delivery are unknown"); repeated reads are byte-identical.
- **No side effects:** with the three adapter constructors, the registered adapter's `connect`/`disconnect`/`subscribe`/`unsubscribe`, `take_over_streaming`, `set_historical_provider`, `clear_streaming_provider`, `clear_historical_provider`, `socket.connect`, `connect_ex` and `create_connection` all patched to raise (scoped so fixture teardown is unaffected), three reads returned the expected inventory, the socket recorded no new send and was not closed, the adapter's set and both registry roles were identical, and the adapter stayed connected; the no-provider path was checked the same way. The route table gains exactly one GET route and still contains `GET /market/feed-status`, `POST /market/subscribe`, `GET`/`POST /market/active-symbols` and `GET /market/candles`.
- **Mutation checks (temporary, reverted, file hashes verified identical):** removing the route's disconnected guard failed 3 tests; returning `[]` instead of "not supported" failed 3; returning an adapter's internal set instead of a tuple failed 4. The restored code passed all 29.
- **Frontend build:** `npm run build` (`tsc -b && vite build`) passed (124 modules). The build rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it was restored and is not in the ZIP.
- **Actual frontend behavior:** the repository has no frontend test runner, so a temporary jsdom 24.1.3 / React 18.3.1 `act` harness outside the repository bundled the real `BrokerPanel` (with its real `useBrokerStatus`, `useSubscriptionStatus` and `api-client.ts`), replacing only `fetch` (individually resolvable held diagnostics requests; `/broker/*` answered immediately). **52 assertions across 9 scenario groups passed with no unexpected `console.error`.** Covered: no diagnostics request while the panel or section is collapsed; expanding makes exactly one request and shows loading; no-provider, not-connected (retained record not shown), unsupported, snapshot-failed, connection-unknown, an unrecognized future reason (shown as its code), available-but-empty (distinct from unavailable) and populated (backend order, count, provider and class labelled) states, each with the local-only caveat and "Capacity: unknown · Delivery: unknown"; Refresh never disabled and labelled while pending; an older success ignored after a newer one; a hung request replaced by Refresh with its late failure invisible; a newer failure not overwritten by an older success; a failed Refresh keeping the last reading with a labelled notice that a later success clears; an initial HTTP/network failure shown as a request failure, not as "unavailable"; markup in an error or symbol rendered literally; late success and failure invisible after collapsing the section, collapsing the panel and unmounting the root, with re-expanding loading afresh; and the existing panel unchanged — Connect hits only `/broker/connect`/`/broker/status`, Subscribe still posts and lists the symbol locally, the panel-local note is unchanged, and neither action triggers a diagnostics read until a manual Refresh.
- **Frontend mutation checks (temporary, reverted, hashes verified identical):** removing the superseded-request guards failed 4 assertions; disabling Refresh while loading failed; rendering "unavailable" as nothing failed 4. Removing the unmount invalidation was **not detected**: React 18 ignores a state update after unmount and a re-expanded section mounts a new hook instance, so that guard is defensive parity with `useScannerObservation`, not independently proven.
- **Not tested:** any real Finnhub, Polygon or IBKR session or account (so no provider acknowledgement, delivery, ownership or capacity behavior), a same-instance reconnect (no route performs one), and the full backend suite.
- **Packaging checks:** performed on the final ZIP and reported in the delivery handoff (clean checkout of the stated base, `git status --short` against the manifest, `git diff --check`, the new module and the directly affected set, the frontend build, and a final `main` re-check).
<!-- END DELIVERY SECTION: provider-subscription-diagnostics -->

<!-- BEGIN DELIVERY SECTION: scanner-observation-status -->
# TESTING — `scanner-observation-status`

Base: GitHub `main` `abeb2b99bbd114a38504d1b41e5e76a20797b071`. Python 3.13.16 (FastAPI 0.115.14, Starlette 0.46.2, httpx 0.27.2, pytest 8.4.2, SQLAlchemy 2.0.54); Node 22.22.0, React 18.3.1; local PostgreSQL 16 on `localhost:5432` (`trading_workspace`, created fresh and migrated through Alembic `0017`) for the neighbouring DB-backed suites. The new module itself needs no database.

- **New module:** `pytest -q tests/test_scanner_observation_status.py` — 25 passed (three consecutive runs, no flakiness).
- **Directly affected, serial:** `pytest -q` over `test_scanner_observation_status.py`, `test_scanner_state_route.py`, `test_scanner_route_concurrency.py`, `test_scanner_observation_worker.py`, `test_scanner_universe.py`, `test_scanner_runner.py`, `test_scanner.py` and `test_context_universe_hot_add.py` — 83 passed. The full suite was not rerun for this route/UI-only delivery.
- **State coverage (controlled readers):** unavailable (no reader, and reader set to `None`); initial snapshot (never attempted, not a successful empty result); successful empty (empty universe, and universe with every symbol skipped); populated (backend order preserved, zero-score/zero-input rows retained, UTC `Z` timestamps); pending cycle with previous rows retained; first cycle pending; failed latest attempt with retained results and the older success timestamp; failure with no success ever; loop-level error without an attempt timestamp; attempt invalidated by `stop()` (`interrupted`); naive and non-UTC datetimes normalized; a 5000-character error capped; NaN/Infinity rendered as `null` with a 200 response.
- **Real worker, injected clock/universe/scanner (no database):** a stopped worker whose reader is still installed returns its retained results with `running: false`; a second cycle that fails after a success reports `failed`, retains the earlier results and last-success time; a worker that never ran reports `none`/`none`.
- **No side effects:** with `Engine.connect`/`begin`, `SessionLocal` (route and worker modules), `run_scan`, `DbUniverseProvider`, `list_universe_symbols` and `socket.connect`/`connect_ex` all patched to raise, three reads against a real, never-started worker returned 200, left `get_snapshot()` the identical object, read no universe, ran no scan, never consulted eligibility and created no asyncio task; the unavailable path was checked the same way; exactly one `get_snapshot()` occurs per request; a reader with only `get_snapshot()` suffices. Mutating the projected dicts/lists (including features sourced from a plain mutable dict) and mutating one HTTP response left the source snapshot and the next response unchanged.
- **Neighbours:** the route table still contains `GET /scanner/state`, `GET`/`POST /scanner/universe` and `DELETE /scanner/universe/{symbol}`, and `/scanner/observation` is GET-only; `GET /scanner/state?symbols=` still scans independently of an installed reader.
- **Bug found by the tests and fixed:** comparing a naive `last_success_at` with an aware `last_attempt_at` raised `TypeError` (a 500); both are now normalized to UTC before comparison.
- **Frontend build:** `npm run build` (`tsc -b && vite build`) passed. The build rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it was restored and is not in the ZIP.
- **Actual frontend behavior:** the repository has no frontend test runner, so a temporary jsdom 24 / React 18 `act` harness outside the repository bundled the real `ScannerPanel` (with its real `ResultsTab`, `UniverseTab`, `useScannerState`, `useScannerUniverse`, `useScannerObservation` and `api-client.ts`), replacing only `fetch` (individually resolvable held observation requests), the workspace context and the 15 s interval timer (manually ticked). **100 assertions across 22 scenarios passed with no unexpected `console.error`.** Covered: collapsed by default with no observation request and the Results tab unchanged; expand makes exactly one request, shows loading, then the unavailable state without implying scanning; six manual ticks of the Results tab's own poll produce no observation request; every state — stopped/never attempted, running/never attempted, first scan in progress, successful empty, populated (order, labels, counts, UTC timestamps, thin-reading and null-score rendering, skipped tooltip, no recommendation or promotion wording), failed with retained results and last-success time, failed never-succeeded, stopped with retained results, interrupted, pending cycle keeping previous rows — plus the feed/coverage caveat in every available state; markup in an error shown literally with no element created; an older Refresh success ignored after a newer one; a hung request replaced by Refresh with its late failure invisible; a newer failure not overwritten by an older late success; a failed Refresh keeping the last read and a later success clearing the notice; an HTTP error shown as a request failure, distinct from a worker failure; Refresh never disabled; late success and failure invisible after the section is collapsed and reopening reloading afresh; collapsing the panel and unmounting the root while pending staying silent; and section Refresh touching only `/scanner/observation` while Results Refresh touches only `/scanner/state`, with the Universe tab's list and add controls intact and the section independent of the active tab.
- **Mutation checks (temporary, reverted):** removing the superseded-request guard in the hook failed 3 harness assertions. Removing the unmount invalidation was **not detected**: React 18 ignores a state update after unmount and a re-expanded section mounts a new hook instance, so that guard is defensive parity with `useScannerState`, not independently proven. The restored hook passed all 100 (file hash verified identical).
- **Not tested:** a real lifespan install (none exists), real FeatureEngine/provider data, and live feed delivery or coverage.
- **Packaging checks:** performed on the final ZIP and reported in the delivery handoff (clean checkout of the stated base, `git status --short` against the manifest, `git diff --check`, the new module and the directly affected set, the frontend build, and a final `main` re-check).
<!-- END DELIVERY SECTION: scanner-observation-status -->

<!-- BEGIN DELIVERY SECTION: context-universe-hot-add -->
# TESTING — `context-universe-hot-add`

Base: GitHub `main` `2a483e484e157bcb357e07fda5316b20689e17c3`. Python 3.12.3; PostgreSQL 16 on `localhost:5432` (`trading_workspace` created fresh and migrated through Alembic `0017`). The new module also creates and drops its own scratch database (migrated to head), so the shared universe is never touched. No external news/provider calls: providers are controlled fakes and `FINNHUB_API_KEY`/`POLYGON_API_KEY` stay blanked by `conftest.py`. Nothing was run in parallel and the full suite was not run.

- **New module:** `pytest -q tests/test_context_universe_hot_add.py` — 20 passed.
- **Directly affected, serial:** `pytest -q` over `test_context_universe_hot_add.py`, `test_context_engine.py`, `test_scanner_universe.py`, `test_scanner_route_concurrency.py`, `test_scanner_state_route.py`, `test_scanner_observation_worker.py`, `test_execution_startup_status_route.py`, `test_intelligence_routes.py`, `test_replay_state_producer.py`, `test_backtest_runner_fixtures.py`, `test_strategy_scheduler.py`, `test_world_view.py`, `test_main_execution_pipeline.py`, `test_outcome_recorder_lifespan_recovery.py`, `test_calendar_provider.py`, `test_websocket_channels.py` — 150 passed, 0 failed (one pre-existing Starlette `TestClient` DeprecationWarning), run serially from a clean checkout of the base with the ZIP applied, after wiping and recreating `trading_workspace` and migrating to `0017`; no scratch `ctx_hot_add_*` database was left behind. The baseline run of `test_context_engine.py`, `test_scanner_universe.py` and `test_scanner_route_concurrency.py` on the untouched base was 15 passed.
- **Coverage of the required behavior:** the added symbol receives its initial `ContextChanged(symbol=...)` and appears in `get_snapshot()` merged with the global calendar entry; the hot-added loop re-evaluates on the existing symbol timer (interval shrunk by monkeypatch; the real constant is asserted to remain 900 s); repeated refresh and four simultaneous refreshes holding the same universe produce one loop and one initial evaluation per symbol; existing loops, the global loop task and the calendar call count are unchanged by a refresh; removing a symbol from the universe does not stop its loop, which only normal `stop()` cancels; a refresh finishing while bootstrap's read is still held (and the reverse order, bootstrap reading before an addition) never duplicates or loses a loop; `stop()` during an in-flight refresh settles without waiting for the blocked read, the caller gets `[]`, and the late read creates nothing; refresh before `start()` or after `stop()` is a no-op that does not read; a stale lifecycle generation cannot track symbols after a restart; a cancelled caller does not lose a committed addition; an injected read failure keeps existing tracking, logs, and a later refresh retries.
- **Route and lifespan:** through the real FastAPI app over `httpx.ASGITransport`: the POST response contract is byte-identical (`{"symbol": "ZAB", "added": true}`) and triggers the refresh; an invalid ticker still returns 400 and does not read; an injected refresh failure returns the same successful response with the symbol committed, existing loops intact, both log lines present, and a second POST retries successfully; with no exposed engine the route works and `get_context_engine()`'s singleton stays `None`; with a stopped engine exposed it starts nothing; DELETE contract unchanged and the loop kept. A real `main.py` lifespan (with a controlled engine seeded as the singleton) shows `app.state.context_engine` is the running engine, a POST hot-adds a symbol into its snapshot, the attribute is `None` after shutdown, and a late POST commits without starting anything.
- **Mutation checks (temporary, reverted):** removing the "already tracked" skip failed 5 tests; removing the generation/running guard, the stop-time refresh cancellation, the route's exception handling, the route's use of `app.state` (switching to the singleton) or the lifespan clear each failed their targeted tests (1, 1, 1, 4 and 1 respectively); the restored code passed all 20.
- **Packaging checks:** the final ZIP was extracted over a clean clone of the stated base; `git status --short` matched the manifest exactly, `git diff --check` was clean, the new test module and the directly affected set passed from that checkout, and no generated, cache or packaging files are in the ZIP. `git ls-remote`/fetch of `main` immediately before packaging still reported `2a483e4`.

Limits: this proves context-loop behavior with controlled providers and a local PostgreSQL; it does not exercise real Finnhub news/fundamentals calls for a hot-added symbol, multi-process deployments, or a real provider feed. Not run: the full backend suite, the frontend (untouched).
<!-- END DELIVERY SECTION: context-universe-hot-add -->

<!-- BEGIN DELIVERY SECTION: scanner-observation-worker -->
# TESTING — `scanner-observation-worker`

Base: GitHub `main` `b64be4f`. Python 3.14; isolated PostgreSQL 18 cluster on `127.0.0.1:55432`, database `scanner_observation_test`, migrated through Alembic `0017`. The database test added and removed only ticker `ZZWKR`; it left the seeded universe intact. No broker, external provider, production database, full backend suite or real-minute wait was used.

- **Focused serial suite:** `pytest -q --disable-warnings tests/test_scanner_observation_worker.py tests/test_scanner.py tests/test_scanner_runner.py tests/test_scanner_universe.py tests/test_scanner_state_route.py tests/test_scanner_route_concurrency.py` with the isolated PostgreSQL environment — **38 passed** (8 new worker tests). Existing on-demand route, override, fallback, universe CRUD and runner tests passed unchanged.
- Controlled monotonic clock/wait tests cover an immediate first admitted cycle, fixed 60-second deadlines, ineligible due points, blocked reads, no overlap, coalescing missed boundaries, empty success, universe edits after capture, failed DB and scorer attempts with retained success, stop during an offloaded read, cancelled stop draining that read, restart generation isolation and a single timer. The real `run_scan` test checks configured weights, zero-input/zero-score rows, missing 1m snapshots, skipped symbols, full ranking, FeatureEngine access on the owning loop and caller mutation isolation.
- The PostgreSQL test uses the real `DbUniverseProvider` and worker-owned session factory. It commits an add and a remove between controlled cycles, sees `ZZWKR` appear and disappear in the captured universe, and verifies worker read sessions are opened off the event-loop thread.
- `git diff --check` passed. The final `git ls-remote origin refs/heads/main` still reported `b64be4f`; the decision index and canonical log end at #189 and the archive ends at #184. The isolated test cluster was stopped after validation. The worker has no application startup wiring or read route, so this suite verifies the reusable core rather than unattended operation. A stuck synchronous DB read may delay `stop()` because cancelling `asyncio.to_thread` cannot stop its thread.

<!-- END DELIVERY SECTION: scanner-observation-worker -->

<!-- BEGIN DELIVERY SECTION: continuous-scanner-design -->
# TESTING — `continuous-scanner-design`

Base: GitHub `main` `5459edfc482b915b576090e637773139b129c62f`. Documentation-only delivery. Inspected the current branch, clean initial worktree, recent commits, canonical scanner/system/strategy/roadmap docs and decision-log state before editing. `git ls-remote origin refs/heads/main` matched the local base before work and was rechecked before numbering #189 and at packaging.

- Verified scanner source contracts by reading `backend/app/scanner/{universe,scorer,runner}.py`, `backend/app/api/routes/scanner.py`, `backend/app/core/config.py` and the existing scanner design history: on-demand recomputation, empty-DB fallback, override validation, output slicing and zero-input scored rows.
- Verified separate provider, relay, scheduler and UI paths in `backend/app/{main.py,broker_adapters/{finnhub_provider,polygon_provider,ibkr_adapter}.py,services/live_tick_relay.py,strategy_engine/scheduler.py,api/routes/market.py,context_engine/engine.py}`, `frontend/src/hooks/useLatestPrices.ts` and the existing scanner UI hooks. Read `MarketClock` and `DebounceScheduler`; compared actual session boundaries with the draft schedule.
- Verified protection path in `backend/app/position_monitor/{engine,portfolio_state_reader}.py`, `backend/app/broker_adapters/simulated_venue.py`, `backend/app/governor/reference_price.py` and lifespan's restored execution startup/shutdown. The documented feed-retention gap is a code-inspection finding, not a live-provider experiment.
- Checked decision #3's variable-schedule text, then matched the tails of `INDEX.md`, `confirmed-decisions.md` and the archive filenames before appending #189. Checked the design's cited repository paths/symbols, `git diff --check`, package manifest and extraction onto a clean checkout of the stated base. No backend suite, database migration, provider connection or live order was run; no production code changed. Local doubles versus real-provider checks are separated in scanner-design.md §18.6.

Limits: this verifies architecture claims and delivery integrity, not real Finnhub/IBKR symbol capacity, reconnect behavior, Polygon polling budget, pre-market coverage or live-session correctness. Decision #189 confirms only the directions it names; §18.7's remaining choices are unapproved. No implementation behavior is claimed.
<!-- END DELIVERY SECTION: continuous-scanner-design -->

<!-- BEGIN DELIVERY SECTION: recorded-outcome-evidence-detail -->
# TESTING — `recorded-outcome-evidence-detail`

Base `81cbc3a` (`81cbc3a5edd1f7f16d7e141e996b5e4519240649`); Python 3.13, PostgreSQL 16 on `localhost:5432` (database `trading_workspace`, created fresh and migrated to Alembic `0017`), Node v22.22.0 with `npm ci` in `frontend/`. Tests inserted and removed only rows marked `__OUTCOME_DETAIL_TEST__` (including its `backtests` rows). No broker, provider or production database was contacted. The full backend suite was **not** run (scoped delivery); the affected files below were.

- **New PostgreSQL route/helper tests:** `pytest -q tests/test_strategy_outcome_detail_route.py` — **9 passed**. Fixtures covered: a recorder-style simulated row with NULL snapshots, NULL commission and `recorder_unavailable` codes returned exactly as stored; equality of the detail body with the same row from `GET /strategy-outcomes` for a simulated row with commission and all snapshots, a simulated row with unavailable snapshots and a backtest row (real `backtests` FK); nested evidence (markup-looking strings, unicode, nulls, empty containers, 8-deep nesting) returned verbatim; partially present snapshots with only their recorded codes; a backtest ID staying `is_backtest: true` / `execution_mode: "backtest"` with its `backtest_run_id` and a simulated ID staying simulated; only the requested row returned when two rows share an `opportunity_id`; unknown ID → 404 and malformed IDs → 422 without calling the helper; worker thread, `transaction_read_only = on` and `repeatable read` observed on every statement, the server refusing an `UPDATE` inside the helper's own transaction, and an unchanged table fingerprint; a blocked read not blocking `/health`.
- **Regression:** with `tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py`, `test_execution_trade_detail_route.py`, `test_outcome_read_path_integration.py`, `test_intelligence_history_read_concurrency.py`, `test_execution_outcome_status_route.py`, `test_performance_analytics_routes.py` and `test_backtest_selection_summary_route.py` — **95 passed** together with the 9 new tests.
- **Sensitivity (mutation) checks, backend:** each temporary source mutation made the new tests fail and the file was restored byte-identical: read-only option dropped (1 failure), backtest row relabelled simulated (2), NULL commission turned into 0 (2), unknown ID returned as 200 (2).
- **Actual frontend behavior:** a temporary jsdom 24 / React 18 `act` harness outside the repository bundled the real `InfoTab` (with its real `RecentClosedTrades`, `StrategyOutcomeEvidence`, `useStrategyOutcomeDetail`, `useStrategyOutcomes`, sibling hooks and `api-client.ts`), replacing only `fetch` (held, individually resolvable responses), the workspace context and an inert `WebSocket`. **117 assertions across 15 scenarios passed, with no unexpected `console.error`** (the list hook's own documented failure log is expected in the forced-list-failure scenario). Covered: list and sibling sections (Portfolio details, Strategy Performance) unchanged, list request unchanged and no detail request before a click; one GET per selection with no query string; loading state with the list still usable; simulated row with absent commission/slippage/snapshots (stated, never zero, reason codes listed); markup in evidence values and keys shown literally with no element or handler created, `"10"` vs `10`, empty list/object/text, depth-cap JSON fallback and `__proto__`/`constructor` keys; backtest and paper population labelling from the recorded row only, a recorded `0` commission staying `0`; selection races in both arrival orders (superseded response ignored, previous outcome never shown for the new one); failure after switching and recovery by Refresh; failed Refresh keeping the same outcome's evidence with a notice; older Refresh success and late failure after a newer success ignored, Refresh never disabled; 404 as "not found"; Hide while pending (late success and failure invisible, reopening not populated by the old completion); unmount while pending (silent, no extra request); list Refresh and list failure independent of the open evidence (including an outcome that left the list); View/Hide toggle with one panel; partial snapshots and unknown reason keys; only Refresh/Hide controls, no links and no trade ID; and a probe of the actual hook showing no render pairs a selection with another outcome's data.
- **Sensitivity (mutation) checks, frontend:** success stale-guard removed (2 failures), failure stale-guard removed (1), previous outcome leaking for one render (2, caught only by the hook probe), absent commission shown as 0 (2), evidence strings injected as HTML (4); each file restored byte-identical. Removing the extra `requestId` bump in `refresh()` changes nothing observable (the effect cleanup that runs when `refreshKey` changes already retires the request), so that mutant is equivalent and not claimed as covered.
- **Frontend build:** `npm run build` (`tsc -b` and Vite) passed with Vite's existing large-chunk advisory. The build-modified tracked `frontend/tsconfig.tsbuildinfo` was restored; `dist/` and `node_modules/` are excluded.
- **Delivery check:** the ZIP was extracted onto a clean checkout of the base; `git status --short` matched the manifest, `git diff --check` was clean, the new backend tests, the build and the harness were rerun there (see the delivery response for results).

**Limits:** jsdom with controlled responses, not a real browser or a frontend-to-PostgreSQL request; the harness is not part of the repository (it has no frontend test framework). Unmount silence is asserted by the absence of errors and extra requests (React 18 does not warn on late updates). Backend tests prove reads, isolation and serialization on synthetic rows, not profitability; precision beyond float serialization is not exercised because the list route has the same convention.
<!-- END DELIVERY SECTION: recorded-outcome-evidence-detail -->

<!-- BEGIN DELIVERY SECTION: backtest-selection-comparison -->
# TESTING — `backtest-selection-comparison`

Base `5377912` (`5377912c4b84348c4bd3dfa3f88137802be27599`). Frontend-only delivery; no backend, database or provider was used and the backend suite was not run (no backend file changed).

- **Actual frontend behavior:** a temporary jsdom 24 / React 18 `act` harness outside the repository bundled the real `BacktestSelectionComparison`, `useBacktestSelectionComparison`, `useBacktestSelectionSummary`, `selectionComparison.ts`, `BacktestResultsPanel` (with its real hooks, `RecentBacktestRuns`, `PerformanceSummaryCard` and CSV export) and `api-client.ts` with esbuild, replacing only `fetch` (with held, individually resolvable responses) and the workspace context (to record any write). **97 assertions across 19 scenarios passed, with no `console.error`/React warnings.** Covered: collapsed by default and no request on expand; malformed, empty, padded and upper-case IDs (nothing sent for invalid; trimmed lower-case value requested; `run_id` vs `sweep_id` parameter; exactly one of the two per request; only the summary endpoint ever requested); multiple groups with matching provenance and correct B − A values (`+10.00 pp`, `+0.1500 R`); same strategy with a different configuration hash, and same everything with a different data version, kept unmatched (A-only and B-only lists); null win rate/mean R on one side → no difference; zero-vs-zero aligned with no differences; exact count sums in the overview; known zero-outcome vs unknown vs request failure (HTTP and network rejection) shown distinctly and independently per side; one side failing while the other stays loaded, with recovery by Refresh and no re-request of the other side; reverse response order (superseded selection arriving last is ignored, B before A and A before B); three repeated Refreshes resolved in reverse order (only the latest shown, Refresh never disabled while pending, "Refreshing…" with the previous same-selection data); a late failure after a newer success ignored; re-Apply of an unchanged selection reloads it and a changed selection never shows the old selection's data; collapse while pending (late completion ignored, drafts and applied selections kept, re-expand re-requests and is not populated by the old completion); unmount while pending (late success and late failure silent); the disclaimer text, no evaluative wording and no selection/ranking control; and no workspace writes.
- **Existing behavior preserved:** the same harness rendered the real expanded Backtest Results panel for an applied run, recorded its text, request set and "Download loaded rows" CSV, then applied, refreshed and re-applied two comparison selections and collapsed the section. The panel text outside the comparison section was identical, the comparison issued no outcome-list, run-metadata or panel-summary request (the panel's own summary for its run was requested exactly once), the CSV and its filename were byte-identical before and after, and no `setLastBacktestRunId`/`setLastBacktestSweepId` call occurred.
- **Sensitivity (mutation) checks:** each temporary source mutation made the harness fail and the file was restored byte-identical afterwards: partial provenance key (4 failures), A − B sign flip (1), unmatched-B dropped (2), unknown treated as known (4), stale-response guard removed from `useBacktestSelectionSummary` (2), client-side ID validation removed (5), no reload on identical Apply (2). A no-op control mutation still passed (97/0).
- **Frontend build:** `npm run build` (`tsc -b` and Vite) passed with Vite's existing large-chunk advisory. The build-modified tracked `frontend/tsconfig.tsbuildinfo` was restored; `dist/` and `node_modules/` are excluded.
- **Delivery check:** the ZIP was extracted onto a clean checkout of the base; `git status --short` matched the manifest, `git diff --check` was clean, the build passed and the harness was rerun there (see the delivery response for results).

**Limits:** jsdom with controlled responses, not a real browser or a frontend-to-PostgreSQL request; the harness is not part of the repository (it has no frontend test framework). Unmount silence is asserted by the absence of errors (React 18 does not warn on late updates), so the unmount guarantee rests on the inherited counter-on-cleanup logic plus the collapse/re-expand test, which does show an old completion cannot populate a new mount. No backend behavior was exercised; the summary route's own tests are unchanged.
<!-- END DELIVERY SECTION: backtest-selection-comparison -->

<!-- BEGIN DELIVERY SECTION: execution-trade-detail -->
# TESTING — `execution-trade-detail`

Base `68b534e`; Python 3.12, PostgreSQL 16 on `localhost:5432` (database `trading_workspace`, created fresh and migrated to Alembic `0017`). Tests inserted and removed only rows marked `__TRADE_DETAIL_TEST__`. No broker, provider or production database was contacted. The full backend suite was not run.

- **New PostgreSQL route/helper tests:** `pytest -q tests/test_execution_trade_detail_route.py` — **11 passed**. Fixtures covered: rejected attempts with unsupported and null requested modes (empty collections, null reasons, null limits, all-null outcome); an approved-but-unfilled trade; partial entry fills plus a partially filled exit; multiple exit attempts (`exit:1` rejected, `exit:2` partially filled, `exit_attempt = 2`); a linked recorded outcome with exact decimals; a `blocked` outcome status with no linked row and no inferred reason; an EOD exit request with fallback observation and non-UTC inputs serialized as UTC; unrelated trades on the same symbol excluded from every collection; and a trade with 105 orders, 130 fills, 105 positions and 105 exit requests returned in full (above the 100-row caps of the recent routes) and in identical deterministic order on repeated reads. Also covered: 404 for an unknown trade; 422 for malformed UUIDs without calling the helper; every statement running off the event-loop thread in a `repeatable read`, `transaction_read_only = on` transaction; byte-identical table contents before and after a read; a writer committing a new order mid-read being invisible to that read's single snapshot (and visible to the next); and `/health` answering while the detail query is blocked.
- **Regression:** `pytest -q tests/test_execution_authorizations_route.py` (shares the extracted projection) — **8 passed**; with the sibling execution route tests and Instance 1's `tests/test_stored_sweep_route.py` re-run serially on the integrated tree — **158 passed** together with the 11 new tests (new, authorization, orders, fills, positions, exit-requests, outcome-status and stored-sweep route tests, serially).
- **Actual frontend behavior:** a temporary jsdom/React `act` harness outside the repository bundled the real `RecordedAuthorizations`, `TradeLifecycleDetail`, `useExecutionTradeDetail` and `api-client.ts`, replacing only `fetch`. **16 scenarios passed with no React warnings:** no request for a null selection; selection race (late response for a superseded trade ignored, previous trade never shown in the intervening render); failure after switching trades does not show the old trade; refresh failure keeps same-trade detail and recovers; stale refresh ignored; 404 shown as not-found, distinct from request failure; completion and failure after unmount ignored; rejected trade, approved-but-unfilled trade, partial fills with null commission and exact prices, and a blocked outcome with no invented reason; only Refresh/Hide controls; row View lifecycle fetches that trade, switching selection ignores the superseded response, the list stays; collapsing the section while pending ignores the late response; changing the decision filter clears the selection.
- **Frontend build:** `npm run build` (`tsc -b` and Vite) passed with Vite's existing large-chunk advisory. The build-modified tracked `frontend/tsconfig.tsbuildinfo` was restored; `dist/` is excluded.

**Limits:** the frontend harness used controlled HTTP responses and jsdom, not a real browser or a frontend-to-PostgreSQL request, and it is not shipped (the repository has no frontend test framework). Mid-read isolation was proven by one deterministic interleaving, not by a stress run. No trading policy, authorizer writer or broker path was exercised.
<!-- END DELIVERY SECTION: execution-trade-detail -->

<!-- BEGIN DELIVERY SECTION: stored-candle-symbol-sweep -->
# TESTING — `stored-candle-symbol-sweep`

Base `cf28e8a4c7a14bfb5788342ca3f79fd1c11a3789`. Isolated PostgreSQL 16 cluster on `127.0.0.1:55432`, database `stored_sweep_test` migrated by the repository's Alembic chain, Python 3.13. Fixtures use `ZSSW*` tickers and clean their own rows. No provider, broker, external database or full backend suite was used. The cluster's default `+06` timezone made one pre-existing test (`test_backtest_runner.py`, reproduced on an untouched base worktree) fail; setting the test database's `timezone` to UTC fixed that environment-only difference.

- **New route tests:** `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=rotate_zero POSTGRES_PASSWORD=<local-test-password> POSTGRES_DB=stored_sweep_test pytest -q tests/test_stored_sweep_route.py` — **27 passed**, covering: 11 rejection cases (empty/blank symbols, 21 distinct, naive/reversed/empty/over-24h windows) leaving no read, run or outcome; live-provider 409s; the cap counting distinct symbols; normalization and de-duplication order; half-open interval, UTC-offset normalization and warm-up counts; the exactly-24-hour boundary; namespace isolation with source rows and live derived state unchanged; a fresh strategy per symbol, sequential execution (one active run) and parity with a single `/run/stored`; mixed success/failure, zero outcomes and the shared `sweep_id` checked in the database; an all-failed sweep; database-unavailable isolation; a runner failure that creates an unreported run row (stage `during_replay`); the guard re-check mid-sweep; look-ahead bait at/after `end` and on same-day daily bars; and the existing sweep read-outs (`/intelligence/strategy-outcomes`, `/backtest-runs`, `/backtest-selection-summary`).
- **Negative controls:** 11 temporary mutations of the route (no de-duplication; cap over raw listings; missing pre- or post-read guard; shared strategy instance; abort on first failure; runner without the shared `sweep_id`; validating only the first symbol; invented `run_id`; mislabeled stage; zero outcomes reported as missing) each made at least one new test fail; the file was restored byte-identical afterwards.
- **Targeted serial regression:** `pytest -q tests/test_stored_sweep_route.py tests/test_stored_backtest_route.py tests/test_backtest_sweep_route.py tests/test_backtest_runner.py` — **80 passed** (27 new). A wider targeted run over backtest, history and read-route tests passed (165) before final documentation edits. Existing Python dependency deprecation warnings only.
- **Actual frontend behavior:** a temporary jsdom 24 / React 18 `act` harness in the scratchpad bundled the real `BacktestPanel`, `useStoredBacktestSweepRun` and `api-client.ts` with esbuild, replacing only `fetch` (and the workspace context, to record published IDs) — **74 assertions passed**. It checked scope switching and preserved single-symbol, coverage-preview, fixture and IBKR behavior; Run enabled/disabled for empty, 20-distinct-plus-duplicates, 21-distinct and reversed-window inputs; exactly one request on a double click; the repeated-`symbols` URL with a UTC window; running state and disabled inputs; per-symbol rows, run IDs, `before_replay`/`during_replay` text and zero-outcome text; submitted parameters unchanged after a form edit; `sweep_id` published once for a partial success and zero-outcome success, never for an all-failed 200 or for 422/400/409/500/network errors; Run usable again after each error; and no `console.error`. Mutating the publish condition or the synchronous guard made the harness fail (2 and 1 assertions); sources were restored. The harness is not part of the repository.
- **Frontend build:** `npm run build` (`tsc -b` and Vite) passed with the existing large-chunk advisory. The build-modified tracked `frontend/tsconfig.tsbuildinfo` was restored; `dist/` is excluded.
- **Delivery check:** the ZIP was extracted onto a clean checkout of the base; `git status --short` matched the manifest, `git diff --check` was clean, the build passed, and the new route tests and harness were rerun there (see the delivery response for results).

**Limits:** jsdom with controlled responses, not a real browser or a browser-to-PostgreSQL request. Measured timing (~1 s per symbol of ~119 candles locally) is an observation, not a guarantee. Backend fixtures are synthetic and prove plumbing only.
<!-- END DELIVERY SECTION: stored-candle-symbol-sweep -->

<!-- BEGIN DELIVERY SECTION: execution-authorization-history -->
# TESTING — `execution-authorization-history`

Base `37579f0`; Python 3.14 and PostgreSQL 18 on `localhost:55432`. A separate `execution_authorization_history_test` database was created and migrated to Alembic `0017`; the tests inserted and removed only their marked Trade fixtures. No live broker, provider or production database was contacted. The full backend suite was not run.

- **PostgreSQL route/query validation:** `POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_USER=rotate_zero POSTGRES_PASSWORD=<local-test-password> POSTGRES_DB=execution_authorization_history_test ./.venv/bin/pytest -q tests/test_execution_authorizations_route.py` — **8 passed**. Fixtures covered approved decisions; rejected decisions with null and unsupported requested modes; exact and combined symbol/decision filters; unknown/empty selection; tied timestamps and UUID tie-break; UTC conversion; default 50 and limits 1/500, with 0/501/invalid decision rejected before helper invocation; missing reasons/limits; a high-precision JSONB numeric limit returned as an exact string; curated fields; the worker thread and PostgreSQL `transaction_read_only=on`; rows unchanged after the read; and an intentionally blocked helper while `/health` remained responsive.
- **Targeted serial regression run:** the new test plus `test_execution_orders_route.py`, `test_execution_fills_route.py`, `test_execution_positions_route.py`, `test_governor_evidence_postgres.py`, and `test_governor_rules.py` — **124 passed**. Python 3.14 dependency deprecation warnings remained; there were no test failures.
- **Actual frontend behavior:** a temporary jsdom/React `act` harness in `/tmp` bundled the real `RecordedAuthorizations`, `useExecutionAuthorizations` and `api-client.ts`, replacing only `fetch` with controlled responses. **36 assertions passed:** no fetch while collapsed; loading versus populated and empty; faithful reason codes, UTC and exact limit text; same-filter rows retained on a failed Refresh; reverse-order filter and refresh completions ignored; collapse and unmount completions ignored. A second harness rendered the real `ExecutionLifecyclePanel` with a stub WebSocket and held unrelated fetches pending: **13 assertions passed**, including preservation of neighboring sections and authorization fetch only after its own expansion. Neither harness is part of the repository.
- **Frontend build:** `npm run build` passed (`tsc -b` and Vite). Vite's existing large-chunk advisory remains. The build-modified tracked `frontend/tsconfig.tsbuildinfo` was restored; generated `dist/` is excluded.

**Limits:** The frontend harness used controlled HTTP responses and jsdom, not a real browser or a frontend-to-PostgreSQL request. The route returns a bounded recent tail with no pagination. It displays persisted decisions only; an approved row is not evidence of an order or fill. No trading policy, authorizer writer or broker path was exercised by this delivery.
<!-- END DELIVERY SECTION: execution-authorization-history -->

<!-- BEGIN DELIVERY SECTION: strategy-to-simulated-execution-acceptance -->
# TESTING — `strategy-to-simulated-execution-acceptance`

Base `7a27e67`; local PostgreSQL 18 on `localhost:55432`, Python 3.14. Disposable databases were separately created and migrated to Alembic `0017`; the acceptance command's empty-database preconditions ran before any application worker. No provider, broker, credentials or full backend suite was used.

**Acceptance command** (from `backend/`, on a newly created and migrated empty fixture):

```bash
POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_USER=rotate_zero POSTGRES_PASSWORD=<local-test-password> POSTGRES_DB=strategy_simulated_acceptance_verified_test ./.venv/bin/python scripts/simulated_mvp_acceptance.py --database strategy_simulated_acceptance_verified_test
```

- Final code: `RESULT: PASS — 88 milestones passed` (S1–S5 preserved; S6 ×20), 64 public API reads across nine routes. S6 observed Gap v1 `OpportunityCreated`, a persisted approval and order, entry fill and open Portfolio State position, target observation and closing fill, and one linked Gap v1 outcome. The negative setup symbol produced no event, trade, order or position after the bounded queue barrier.
- Required fault check: temporarily changed only the scheduler's opportunity publish condition to false, then ran the same command on a separate fresh migrated database with `--timeout 10`. S1–S5 passed and S6 failed at `S6.6 Gap evaluate() causes StrategyScheduler to publish OpportunityCreated` with `events=0` (exit 1). The production scheduler source was restored and `git diff --exit-code -- backend/app/strategy_engine/scheduler.py` was clean.
- Directly affected tests, serially on a separate migrated test database: `pytest -q tests/test_simulated_mvp_acceptance.py tests/test_gap_strategy.py tests/test_strategy_scheduler.py tests/test_main_execution_pipeline.py tests/test_outcome_recorder_event_path_integration.py tests/test_outcome_recorder_lifespan_recovery.py` — **77 passed**. Existing Python 3.14 `pytest_asyncio`/FastAPI deprecation warnings only.
- An earlier diagnostic run placed S6 on S4's trading day; the Governor correctly rejected it with `projected_loss_exceeds_daily_cap`. S6 now uses the next regular day, preserving that risk rule.

**Limits.** Controlled feature and market-state payloads bypass candle acquisition and FeatureEngine calculations. ContextEngine is real, but provider keys are blank. The simulated venue, injected clocks, fixed entry snapshot and prior S4 restart book retention remain as documented in execution-engine-design.md §6.12. No live-feed, profitability, ranking or real broker claim follows from this run. Evidence databases are retained; the command refuses a rerun against them.
<!-- END DELIVERY SECTION: strategy-to-simulated-execution-acceptance -->

<!-- BEGIN DELIVERY SECTION: backtest-selection-performance-summary -->
# TESTING — `backtest-selection-performance-summary`

Base: GitHub `main` `a42984641e43d3f43b16e3e46d3d10a9e889b270`. Isolated PostgreSQL 18 cluster under `/tmp/stored-coverage-pg`, port 55432; separate database `backtest_selection_summary_test` migrated to `0017`. Fixtures use the dedicated `__TEST_BACKTEST_SELECTION_SUMMARY__` name and clean their own rows. No live or external database was touched.

- `pytest -q tests/test_backtest_selection_summary_route.py tests/test_backtest_runs_route.py tests/test_performance_analytics_routes.py`: **36 passed**. The new route tests prove that a sweep aggregate uses more rows than a deliberately limited outcome-list response, excludes an unrelated run and a simulated-execution row, separates version/configuration/data/feature groups, includes a zero-outcome run, and computes independently expected positive/negative/zero-R counts, win rate and mean R. They also cover unknown versus known-empty selections, malformed/missing/double UUID filters, and a worker-owned read-only repeatable-read transaction.
- `node /tmp/backtest-selection-summary-hook-check.mjs`: passed controlled hook checks for no request without a selection, run/sweep filter changes, reverse response order, request failure, Refresh supersession and unmount completion. The temporary harness is not packaged.
- `npm run build`: TypeScript and Vite passed (existing Vite chunk-size advisory). `git diff --check` and ZIP content verification are final packaging checks.

The frontend hook was checked with controlled responses, not a browser. Synthetic outcomes verify aggregation and isolation, not profitability or a ranking decision.
<!-- END DELIVERY SECTION: backtest-selection-performance-summary -->

<!-- BEGIN DELIVERY SECTION: stored-candle-coverage-preview -->
# TESTING — `stored-candle-coverage-preview`

Base: GitHub `main` `1f43c11ac42607f1f88947d1a5f53cc3c419788b`. Isolated PostgreSQL 18 cluster under `/tmp/stored-coverage-pg`, port 55432, database `stored_coverage_test`, migrated to `0017`; no live or external database was touched. The test fixtures insert and clean their own `ZSTORE1`/`ZSTORE1F` rows.

- `pytest -q tests/test_stored_backtest_route.py -k stored_coverage`: 9 passed after avoiding unrelated app lifespan startup in the read-only route tests. Covers live/backtest namespace separation, inclusive/exclusive bounds, unknown and empty results, warm-up selection, same-day daily exclusion, malformed parameters, and a read-only repeatable-read transaction with unchanged source rows and no backtest rows.
- `pytest -q tests/test_stored_backtest_route.py`: 35 passed, including existing replay tests after the shared selection-rule extraction. Both commands used the isolated PostgreSQL database. The initial attempt against the configured stopped service skipped; the first isolated run exposed a test-only repeated-lifespan event-loop collision, corrected before the passing runs.
- `node /tmp/stored-coverage-hook-check.mjs`: passed controlled hook checks for input invalidation, reverse response ordering and unmount completion (temporary harness, not packaged).
- `npm run build`: TypeScript and Vite passed. Vite reported its existing chunk-size advisory. `git diff --check` passed.

The frontend check used controlled responses rather than a browser. The backend tests used synthetic candles and do not prove continuous market coverage, valid replay outcomes or profitability.
<!-- END DELIVERY SECTION: stored-candle-coverage-preview -->

<!-- BEGIN DELIVERY SECTION: backtest-run-history (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `backtest-run-history`

Base `0ae188a47a24738f9f3c557cb10d4a2fb549ae58`, Node v22.22.2 with `npm ci` in `frontend/`. No backend code changed and the backend suite was **not** run (not requested). The repo has no frontend test runner and none was added.

- **Harness (outside the repository, not part of the delivery).** A jsdom + React 18 `act` harness bundled with esbuild the real `BacktestResultsPanel`, `RecentBacktestRuns`, `useRecentBacktestRuns`, `useBacktestRuns`, `useBacktestOutcomes`, `useBacktestSweepOutcomes`, `outcomesCsv.ts` and `api-client.ts`. Only two things are replaced: `fetch` (a controllable stub that records every URL and lets a test hold, answer, reorder or fail each request) and `WorkspaceContext` (a small store exposing `lastBacktestRunId`/`lastBacktestSweepId` and recording any call to `setLastBacktestRunId`/`setLastBacktestSweepId`). **77 checks passed, 0 failed.**
- **Covered.**
  - *Section:* collapsed by default; no history request until expanded; exactly one `limit=50` request on expand; collapsing hides rows.
  - *Rows:* server order preserved (a deliberately non-chronological list is not re-sorted); symbols, replay range and creation time shown; `fixture:…`, `stored:postgres:candles:1m-1d` and `ibkr:…` `data_version` strings shown verbatim; "Showing up to 50 recent runs"; no profit/loss/outcome-count text; no "real market data" wording.
  - *Selecting an older run while following latest:* one metadata and one outcomes request for that run, run_id tab active, "(manually set)", its 3 outcomes loaded, selected row marked **Viewing**, input holds the ID, `setLastBacktestRunId`/`setLastBacktestSweepId` never called and the workspace value unchanged.
  - *From sweep mode:* View results switches to the run_id tab and applies the run; no row is marked selected while in sweep mode.
  - *Zero-outcome run:* metadata still shown, honest "No backtest outcomes found for run_id …", "0 rows", CSV button disabled.
  - *Empty history:* "No saved backtest runs found.", no error, not stuck loading.
  - *History failure:* error shown (not as empty); a run can still be entered by UUID and viewed; a failure after a run is already selected leaves that run on screen; Refresh runs recovers.
  - *Repeated refresh, reverse order:* Refresh enabled while pending; three requests answered third, first, second leave only the third's data; an older failure arriving late is ignored; a newer failure is not overwritten by an older success.
  - *Unmount:* a success and a failure completing after unmount log nothing.
  - *Another run finishing:* with B manually selected, a new `lastBacktestRunId` leaves B selected, requests no outcomes/metadata for the new run, refreshes history exactly once (new run first), a sweep completion refreshes it once more, and no further requests follow (no polling).
  - *Preserved:* Follow latest returns to auto and the latest run; manual UUID Apply; outcomes Refresh refetches; sweep tab still makes its two requests and renders the sweep strip.
  - *CSV:* after selecting an older run the download is named `backtest-outcomes-run-<that run_id>.csv` and contains exactly that run's 3 loaded rows (header + 3), no other run's rows.
- **Regression proof (temporary edits, restored, not in the delivery).** Removing the request-ordering guards failed 5 checks; making View results also call `setLastBacktestRunId` failed 3; leaving View results in auto (follow-latest) mode failed 5; making the refresh key constant (no refresh on completion) failed 4.
- **Frontend build.** `npx tsc -b` clean and `npx vite build` clean (existing chunk-size advisory only). The build-modified tracked `tsconfig.tsbuildinfo` was restored and is not in the delivery.
- **Not verified.** No real browser, no real backend (every response was a controlled stub, so behavior against an actual `GET /intelligence/backtest-runs` response is as typed in `api-client.ts`, not exercised end to end), and layout/visual appearance was not inspected. React 18 ignores state updates on unmounted components without a warning, so the unmount check can only observe the hook's guard through its failure logging, not a suppressed state update directly. Cross-tab completion is not simulated.
- `git diff --check` clean; extracted-ZIP verification is reported in the handoff.
<!-- END DELIVERY SECTION: backtest-run-history -->

<!-- BEGIN DELIVERY SECTION: stored-candle-backtest (backend + frontend + tests + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `stored-candle-backtest`

Base `61d1d08e0126038d9c0f5cf5f403b69008853a33`, Python 3.12.3, PostgreSQL 16 (local, `trading_workspace`, `alembic upgrade head`), Node v22.22.2 with `npm ci` in `frontend/`. The full backend suite was **not** rerun (scoped delivery); the affected files below were. Re-verified on the base above after integrating Instance 1's delivery.

- **New file `backend/tests/test_stored_backtest_route.py`: 26 passed** against real PostgreSQL, the real route, the real `BacktestRunner` and engines. Doubles only for the connected-provider guard, the captured-arguments checks and the database-failure case. Coverage:
  - *Interval boundaries and ordering:* start inclusive / end exclusive; rows inserted newest-first come back ascending; a candle exactly at `end` is excluded and, alone, is "no data".
  - *Namespace isolation:* same-ticker decoy rows in the backtest namespace (1m and 1d, priced 999) are never read; decoys alone give `stored_candles_no_data`.
  - *Lookbacks and no look-ahead:* 1m warm-up window edges, 1d lookback edge, rows at/after `end`, the replay day's own and the next day's daily bars are all excluded; a provider call ending at a candle never returns that candle or later; absent daily history is served empty, never synthesized.
  - *Missing/invalid data:* warm-up-only is still no data (422); `NaN` close gives `stored_candles_malformed`; a failing session gives 503 with no driver text and the session closed.
  - *Worker-owned session:* the read runs in a non-event-loop thread and its own session is closed.
  - *Validation:* unknown strategy 400; naive/reversed/empty/over-24h/blank-symbol 422 `invalid_backtest_request` with acquisition never invoked and no run row; `-05:00` offsets normalized to UTC, symbol normalized, exactly 24 h not clamped, configured lookbacks passed through.
  - *Guards:* Finnhub, Polygon and IBKR connected each give 409 before any read or write; a provider connecting during the read gives 409 before the runner is constructed and no run row.
  - *Real runner replay:* the recorded `vwap_neutral_conquest` candles replayed via the stored route give 1 outcome (VWAP SELL, `is_backtest=TRUE`) with the same direction, prices, R, exit reason and timestamps as the fixture route on the same data; `backtests.data_version` and `symbol_universe` are as documented; source rows in both namespaces and live-namespace derived rows are byte-for-byte unchanged; backtest-namespace state was written; the historical-provider seam is restored.
  - *No look-ahead end to end:* adding 1m rows after `end` and daily bars for the replay day and the next day does not change a second replay's outcomes or discarded signals, with real recorded-style daily history present.
- **Regression proof (temporary edits, restored, not in the delivery).** Removing the `is_backtest` filter failed 2 tests; making the end bound inclusive failed 3; removing the daily trading-day filter failed 1.
- **Affected files on this base: 169 passed, 0 failed** — the new file plus Instance 1's `test_simulated_mvp_acceptance.py` and `test_backtest_routes.py`, `test_ibkr_backtest_route.py`, `test_backtest_runner.py`, `test_backtest_runner_fixtures.py`, `test_backtest_runner_regression.py`, `test_backtest_sweep_route.py`, `test_backtest_runs_route.py`, `test_ibkr_historical.py`, `test_symbol_namespace.py` (150 of the 169 on the previous base, before Instance 1's file landed).
- **Frontend.** `npx tsc -b` clean and `npx vite build` clean (existing chunk-size advisory only); the build-modified tracked `tsconfig.tsbuildinfo` was restored and is not in the delivery. The repo has no frontend test runner and none was added. A throwaway jsdom + React `act` harness outside the repository bundled the real `BacktestPanel`, `WorkspaceProvider`, hooks and `api-client.ts` (stubbing only `fetch`): **21 checks passed, 0 failed** — Stored tab and explanation; Run enabled for a valid form; exactly one request after a double click; `POST /backtest/run/stored` with the Eastern window converted to UTC; mode tabs disabled and loading text shown while pending; success shows `run_id`, publishes it to the workspace, and explains 0 outcomes; `422 stored_candles_no_data`, `409` and `503 stored_history_unavailable` show their headings and backend messages and publish nothing; Run usable again after an error; a 25-hour window disables Run and sends nothing; fixture and IBKR modes still post to their own endpoints with unchanged captions and the Sweep tab still renders. In that harness fixture/IBKR runs were made to fail on purpose, because a *successful* fixture/IBKR run triggers the pre-existing publish-effect loop described in `CHANGES.md`.
- **Not verified.** No real browser, no real recorded market data and no real-market profitability; behavior was verified on synthetic fixtures loaded into the database. The pre-existing effect loop was reproduced in a development React build only.
- `git diff --check` clean; extracted-ZIP verification is reported in the handoff.
<!-- END DELIVERY SECTION: stored-candle-backtest -->

<!-- BEGIN DELIVERY SECTION: simulated-mvp-acceptance (backend utility + tests + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `simulated-mvp-acceptance`

Base `0338678580164004b736329a3a27661fd9df02a1`, Python 3.13.16 with `backend/requirements.txt`, PostgreSQL 16 (a throwaway local cluster; databases `atos_acceptance` for the command and `atos_pytest` for pytest, both migrated to `0017`). No IBKR Gateway, account credentials, Finnhub/Polygon key or network feed was used. The full backend suite was not run (not requested).

**The acceptance command (how to run it).**

```bash
cd backend
createdb mvp_acceptance && POSTGRES_DB=mvp_acceptance alembic upgrade head     # fresh, disposable, migrated, empty
POSTGRES_DB=mvp_acceptance python scripts/simulated_mvp_acceptance.py --database mvp_acceptance
```

`POSTGRES_HOST/PORT/USER/PASSWORD` select the server as usual. A finished run leaves its rows as evidence and the next run is refused until a fresh database is created (nothing is ever cleaned). Exit `0` PASS, `1` milestone failed, `2` precondition failed, `3` watchdog.

- **Observed result.** Final code, three consecutive runs each on a freshly recreated and migrated database: `RESULT: PASS — 68 milestones passed` every time (4.2 s, 3.8 s, 4.1 s; 56–58 public API reads across 9 routes). Earlier runs on near-identical logic also passed. Milestones: preconditions P.1–P.4, S1 ×8, S2 ×15, S3 ×11, S4 ×15, S5 ×15. Output was searched for the database password: 0 occurrences.
- **Refusals and failure reporting (observed).** A populated database (the one a run had just used): exit 2 at P.4 naming the tables holding data, nothing started. No `--database`, a name that differs from `POSTGRES_DB`, and a non-disposable name (`trading_workspace`): exit 2 at P.1. A deliberately impossible wait bound (`--timeout 0.001`) on a fresh database: exit 1, `RESULT: FAIL — failed milestone S1.4 …` with what was last observed.
- **Unit tests for the module's own logic.** `backend/tests/test_simulated_mvp_acceptance.py`: 19 passed (about 1.5 s). Covered: database-selection rules, migrated/empty evaluation (unmigrated, behind head, missing tables, populated tables named, migration seed tolerated), scenario-settings validation, secret scrubbing, milestone numbering and failure naming, bounded waits (timeout reports the last observation; returns as soon as the state is observed), every exit code (2 for unselected/non-disposable/populated/unmigrated/unreachable, 1 for a named failed milestone and for an unexpected crash, 0 with the scope statement), and a read-only `inspect_database` check against the real migrated test database. These need no scenario run.
- **Relevant existing tests, run on `atos_pytest`.** `test_main_execution_pipeline.py`, `test_simulated_eod_integration.py`, `test_outcome_recorder_lifespan_recovery.py`, `test_outcome_recorder_restart_recovery.py`, `test_execution_startup_status_route.py`, `test_portfolio_state_route.py`, `test_execution_outcome_status_route.py`: 74 tests; 73 passed and 1 failed in the final run, and the six-file subset without the new test file and without `test_execution_outcome_status_route.py` passed 50 of 50 in each of 5 runs.
- **Known intermittent failure, pre-existing, not touched.** `test_main_execution_pipeline.py::test_position_monitor_places_durable_exit_and_closes_on_later_tick` (stop or target parameter) sometimes fails with a `protection_diagnostics.incident_counts.snapshot_unavailable` difference (2 vs 1) between two successive reads of `GET /intelligence/exit-intents`: a timing race in that test's own equality check. It fails 2 of 15 runs alone on a **pristine checkout of the base with none of this delivery's files**, so it is not caused by this delivery. It is reported, not fixed (out of scope; AGENTS.md §9 "related follow-up").
- **Observed, benign.** During entry fills the log can show `PositionMonitor: position snapshot unavailable; retained ticks await recovery` (ERROR): a tick arrives before Portfolio State has applied the fill and is replayed afterwards, as the existing tests already describe. The command waits for the position to be visible in Portfolio State before sending a trigger tick.
- **Not covered / limitations.** The command proves the downstream simulated lifecycle from seeded opportunities only: no strategy profitability, ranking, live-feed coverage or real broker execution; opportunities, prices and the clock are controlled. Restart restoration is shown with the venue's in-memory book retained (a fresh `SimulatedVenue` fails closed by design). One position at a time, one symbol per scenario. Wall-clock use outside the injected clocks (for example the recorder's own timers) is real. The output's timings and read counts vary slightly between runs. Not exercised: a Postgres outage during a run, concurrent runs against one database, or database-level fault injection.
- **Housekeeping.** `git diff --check` and the clean-checkout verification of the ZIP are reported in the final response (extract onto a fresh clone of the base: `git status --short` lists exactly the manifest).
<!-- END DELIVERY SECTION: simulated-mvp-acceptance -->

<!-- BEGIN DELIVERY SECTION: backtest-results-csv-export (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `backtest-results-csv-export`

Base `0734203eb7923f2a845ed1ec374c60738d6d23ef`, Node v22.22.2, `npm ci` in `frontend/`. Frontend and docs only: no backend, database or broker was touched, so backend pytest was not run.

- `npm run build` (`tsc -b && vite build`): clean on the final base (106 modules; existing >500 kB chunk advisory only). `git diff --check` clean.
- **Behavior harness (not committed).** The repo has no frontend test runner and none was added. A throwaway jsdom + React `act` harness kept outside the repository bundles the real `outcomesCsv.ts` and the real `BacktestResultsPanel` with its three real hooks (esbuild), stubbing only `WorkspaceContext` and `fetch` (every request stays pending until the test settles it with a success or HTTP error), the object-URL functions, the anchor click and `setTimeout`. **90 checks passed, 0 failed.**
  - *CSV utility:* stable header order; empty list gives header only; commas, quotes, CR/LF and CRLF inside a cell survive a round trip through an RFC 4180 parser; `null` gives an empty cell (text and number); negative values and `-0.25`/`-15` untouched; `1e-7` kept as written; zero stays `0`; ISO timestamps verbatim, other parseable timestamps via `toISOString()`, garbage verbatim; formula-like text protected for `=`, `+`, `-`, `@`, TAB and CR (including a quoted `=HYPERLINK("x","y")`), mid-string `=` untouched; input order preserved; non-finite numbers never written as text; filename for run, sweep, unfiltered, path-separator/reserved-character ids, a 500-character id and a whitespace id.
  - *Download resources:* one click per export, `download` attribute set, anchor attached while clicked and removed afterwards (also when the click throws), no synchronous revoke, a revoke scheduled at 10 s, Blob bytes start with the UTF-8 BOM, MIME type `text/csv;charset=utf-8`, and after all timers fire every created object URL was revoked exactly once.
  - *Panel states:* button label and loaded-subset note present; disabled while the initial load is pending and a click then exports nothing; enabled when rows load; exported file has the header, the rows in displayed order and a protected `=cmd|x` text cell next to an untouched `-2`; disabled during a Refresh of the same filter and while an older response is superseded by a newer pending one; disabled immediately after Apply of a new run_id and a click then exports nothing (previous filter's rows are not exportable); the exported file after B loads carries `run-B` in its name and B's rows; disabled on HTTP failure, on empty results, while reloading after failure/empty, and re-enabled after success; sweep mode disabled with no sweep applied, with both sweep requests pending, after only the runs request settles, on a sweep outcomes failure and for an all-zero sweep; sweep export named `backtest-outcomes-sweep-SW1.csv` with the sweep's rows; switching back to the run tab disables export until the run rows reload and then exports the run view's rows under the run filename.
- **Mutation checks** (each reverted; clean tree 90/90): ignoring `loading` in the export gate (2 failed), removing formula protection (4 failed), protecting numeric cells too (3 failed), disabling quote escaping (several failed), passing no identifier to the filename (3 failed), revoking the object URL synchronously (1 failed). Ignoring `error` in the gate is an equivalent mutant (the hooks clear rows on error), so that check is defense in depth only.
- **Not covered:** no real browser (actual file save, Excel/Sheets formula handling and BOM detection were not exercised), no visual/Tailwind review, no live backend. Manual check: load a run, press Download loaded rows, open the file in a spreadsheet, confirm headers, negative R values, a quoted strategy name and that Apply of another run_id disables the button until its rows load.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and was restored.
<!-- END DELIVERY SECTION: backtest-results-csv-export -->

<!-- BEGIN DELIVERY SECTION: live-portfolio-details (backend + frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `live-portfolio-details`

Base `75c1897dcf5c0c33ae6b50af4ecbb6f0d354252a`, Python 3.13 virtualenv from `backend/requirements.txt`, Node v22.22.0 with `npm ci` in `frontend/`. No PostgreSQL, application lifespan, provider or broker was used: the route tests restore real in-memory `PortfolioState` instances (`_install_state`, `update_mark`) and call the real route through `httpx.ASGITransport`.

- **New module.** `backend/tests/test_portfolio_state_route.py`: 20 passed (5 runs in a row, 20 passed each, about 1 s). Covered: absent reader, reader returning `None`, never-restored `PortfolioState` and a blocked ledger state all give `{"portfolio": null}`, distinct from a restored flat account (populated, empty lists, known-zero daily amounts, `buying_power` null) and from incomplete history (daily amounts and unknown-fee count null); marked position (exact `unrealized_pnl`, `open_risk`, mark timestamp, `snapshot_time` = newest mark); short-position sign; unmarked position making total unrealized P&L and open risk null; missing stop making open risk null; pending-entry exposure (`is_in_flight`, no mark); partially filled entry splitting into held part plus pending remainder only; exit order counted as in-flight with no exposure row; daily rows filtered to the snapshot trading day; unknown fees (`fees_today` null, `reported_fees_today` kept, count summed); exact decimals (nine-digit fractions, `1E-7` as `0.0000001`, `1E+3` as `1000`, no floats); one argument-free `get_snapshot()` per request with `symbol` ignored; no ledger access (a ledger whose every attribute raises), no `refresh`/mutator call (tripwire methods) and unchanged state/marks/queue; GET only; the compact World View projection keeps its four fields.
- **Related database-independent tests.** The new module with `test_world_view_portfolio.py`, `test_world_view_read_concurrency.py`, `test_performance_analytics_route_concurrency.py`, `test_intelligence_history_read_concurrency.py`: 34 passed, 0 failed.
- **Backend mutation checks** (each reverted; clean tree 20/20): serializing decimals with `str()` instead of fixed-point (1 failed, the exact-decimal test); adding a `reader.refresh()` call to the route (3 failed, including the tripwire test). An earlier version of the read-only test passed under the refresh mutation because the app's error middleware turns an exception into an HTTP 500 body and the test compared two identical error bodies; it now asserts status 200 and the expected portfolio.
- **Frontend build.** `npm run build` (`tsc -b && vite build`): clean (105 modules; the existing >500 kB chunk advisory only).
- **Behavior harness (not committed).** The repo has no frontend test runner and none was added. A throwaway jsdom 24 + React 18 `act` harness kept outside the repository bundles the real `PortfolioStateSummary`, `usePortfolioState` and `api-client` with esbuild and stubs only `fetch` (every request stays pending until the test settles it with success, an HTTP error with detail, or a network error). **47 checks passed, 0 failed.** Covered: collapsed sends nothing; expansion sends exactly one request; loading state with Refresh disabled; flat, unavailable and error states distinct (and error never shown as unavailable or flat); Refresh after an error clears it and recovers; network failure clears old data; populated view with held and pending groups in order, exact decimals verbatim, mark timestamps, "no mark yet", null amounts shown as "—" with reasons, unknown-fee wording, simulated label and "not a connected real-money account"; a response arriving after collapse and re-expand is ignored; an older failure after a newer success, and an older success after a newer failure, are ignored; an older settlement does not end the newest request's loading; a response arriving while collapsed is not shown on re-expansion; late success and late failure after unmount produce no state update, no `console.error` and no extra request; StrictMode sends one request per expansion.
- **Frontend mutation checks** (each reverted; clean tree 47/47): removing the stale-response guard on success (2 failed) and on failure (1 failed); not discarding a pending request on collapse (1 failed). Not detectable through the component: a failure keeping the earlier data in hook state (the error branch renders first and hides it), so that clearing is a defensive guard; stated rather than claimed as covered.
- **Not run.** The full backend suite (not requested); PostgreSQL-backed tests such as `test_main_execution_pipeline.py` (no database in this run; the route does no database read and `main.py` is untouched); no real browser, visual/Tailwind review or live backend with a running simulated session. Manual check: with the pipeline running, expand "Portfolio details" in the Info tab General view, compare it with the Execution panel's positions, press Refresh, stop the backend and press Refresh again, and confirm the error text replaces the numbers.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and was restored.
- **Delivery check.** See the final response for the base SHA and the clean-checkout verification of the ZIP (extract onto a fresh clone of the base, `git status --short` lists exactly the manifest, `git diff --check` clean, new module and build pass there).
<!-- END DELIVERY SECTION: live-portfolio-details -->

<!-- BEGIN DELIVERY SECTION: world-view-read-concurrency (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `world-view-read-concurrency`

Base `9ea37b442b7d6601c60ae76d621c3877feefd5e3`, Python 3.13 virtualenv from `backend/requirements.txt` plus pytest and pytest-asyncio. No PostgreSQL, application lifespan, external provider or broker was used or needed by the new module.

- **New module.** `tests/test_world_view_read_concurrency.py`: 6 passed (3 test functions; the contract test is parametrized over 4 portfolio modes). Repeated 8 times in a row: 6 passed each time (about 1.5 s per run).
- **Related database-independent tests.** `tests/test_world_view_portfolio.py`, `tests/test_performance_analytics_route_concurrency.py`, `tests/test_intelligence_history_read_concurrency.py` together with the new module: 14 passed, 0 failed.
- **Regression check (offload removed).** With `await asyncio.to_thread(_read_performance)` temporarily replaced by `_read_performance()` in `backend/app/world_view/composite.py`: 6 failed, 0 passed (about 35 s because each blocked read waits for its 3 s give-up). The responsiveness test fails with the blocked read on the event loop (`TimeoutError: test never released the hourly_win_rates read`). The production file was restored byte for byte (`git diff` clean for `backend/app`) and is not part of this delivery.
- **Not run.** `tests/test_world_view.py` (real-PostgreSQL integration; skipped without a reachable database) and the full backend suite were not run (not requested); frontend untouched.
- **Limitation.** These tests use doubles for the performance queries, so they prove event-loop responsiveness, concurrent isolation and the response contract only. They do **not** validate SQL correctness; the real aggregation queries are covered by the PostgreSQL-backed World View and outcome read-path tests.
- **Delivery check.** See the final response for the base SHA and the clean-checkout verification of the ZIP (extract onto a fresh clone of the base, `git status --short` lists exactly the manifest, `git diff --check` clean, new module passes there).
<!-- END DELIVERY SECTION: world-view-read-concurrency -->

<!-- BEGIN DELIVERY SECTION: broker-panel-request-safety (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `broker-panel-request-safety`

Base `c1333bdd95ffed6092c758bb309bdee5a3ce2abd`, Node v22, `npm ci` in `frontend/`. Frontend and docs only: no backend, database or real broker connection was touched, so backend pytest was not run (not requested).

- `npm run build` (`tsc -b && vite build`): clean (existing >500 kB chunk advisory only).
- **Behavior harness (not committed).** No frontend test runner exists and none was added. A throwaway jsdom 24 + React 18 `act` harness kept outside the repository (`/tmp/harness`) bundles the real `BrokerPanel`, `useBrokerStatus` and `api-client` with esbuild and replaces only `fetch` (every request stays pending until the test resolves it with a status/body or a network rejection) and `setInterval`/`clearInterval` (to fire polls by hand and observe cleanup). **110 checks (32 scenarios) passed, 0 failed.** Any `console.error` fails a scenario; none occurred. Covered: reverse-order status success; older error vs newer success; older success vs newer error; `statusLoading` ending only for the current read (success and error); a pre-disconnect read and a pre-connect read (after a failed and a successful connect) unable to overwrite the action result, as success or as error; duplicate connect/disconnect, connect↔disconnect exclusion, duplicate/different-symbol subscribe, unsubscribe during subscribe and vice versa, symbol actions during connect/disconnect (each sends exactly one request / none); refused calls resolve `false` without setting errors; failed unsubscribe (HTTP 400 and network error) restoring the row in its original order, success removing it; unsubscribe completion (failure and success) after a poll-detected drop not repopulating or showing an error; disconnect during a pending subscribe (success, failure, and failed disconnect letting the subscribe land); new connection during a pending subscribe, and `already_connected` keeping the list; failed status read keeping connection state and list vs poll-detected drop clearing it; StrictMode double mount; single 10 s interval and cleanup on unmount; no follow-up request after an unmounted connect/disconnect/subscribe/status completion; panel: repeated Enter / Enter+click send one POST, pending state disables Subscribe, ×, Connect/Disconnect as designed (input stays editable), input kept when changed during the subscribe and cleared/focused when unchanged (including retyped identical text), text kept on failure, backend 502 detail shown verbatim, "last known reading" message on a failed read.
- **Baseline fails.** The same harness against the untouched base: 54 passed, 52 failed (every one of the five reported problems reproduces: stale status overwrite, duplicate POSTs, permanent row loss on failed unsubscribe, stale subscribe re-adding after disconnect, input cleared despite newer text, refetch after unmount). Two later scenarios abort on the base because the new post-failed-connect read does not exist there.
- **Mutation checks** (each reverted; clean tree 110/110): removing the connect guard; the current-read check on success and on error (separately); disconnect or connect not invalidating earlier reads (separately); the stale-subscribe identity check; `resetSubscriptions` not detaching the pending symbol action; failed unsubscribe also deleting the row; the mounted check around the post-disconnect refetch; `clearInterval`; dropping the "no symbol action during connect/disconnect" guard; clearing the input unconditionally; × not disabled while mutating; Subscribe not disabled while pending. All 14 were detected (failure counts include scenarios that abort afterwards, so they are not 1:1 with the mutation).
- **Not detectable / not covered:** React 18 makes `setState` on an unmounted component a silent no-op, so the mounted guard on a late *status* completion can only be observed through side effects (no new request, interval gone) and through the StrictMode remount case, not through state. No real browser, visual/Tailwind review, real IBKR Gateway or genuinely hung connection (a pending promise stands in); requests are not cancelled. A status read slower than the 10 s poll is superseded and never displayed (documented limit). Manual check: throttle the backend, press Enter twice quickly in the symbol field, type another symbol while it is pending, click Disconnect mid-subscribe, make unsubscribe fail, collapse the panel mid-request.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and was restored.
- **Delivery check.** See the final response for the base SHA and the clean-checkout verification of the ZIP (`git diff --check`, `npm ci && npm run build`, harness rerun from the extracted ZIP).
<!-- END DELIVERY SECTION: broker-panel-request-safety -->

<!-- BEGIN DELIVERY SECTION: scanner-universe-mutation-recovery (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `scanner-universe-mutation-recovery`

Base `94b94753da24931a5c66c108fdbc573377fbbfa0`, Node v22, `npm ci` in `frontend/`. Frontend and docs only: no backend, database or broker was touched, so backend pytest and the full suite were not run (not requested).

- `npm run build` (`tsc -b && vite build`): clean on the change (existing >500 kB chunk advisory only).
- **Behavior harness (not committed).** The repo has no frontend test runner and none was added. A throwaway jsdom 24 + React 18 `act` harness kept outside the repository (`/tmp/harness`) bundles the real `ScannerPanel`, `useScannerUniverse` and `api-client` with esbuild and stubs only `WorkspaceContext` and `fetch`: every `/scanner/universe` GET, POST and DELETE stays pending until the test resolves it with success, an HTTP error (detail text) or a network error. **94 checks (28 scenarios) passed, 0 failed.** Any `console.error` fails a scenario; none occurred. Covered: initial loading is not "empty"; initial GET failure differs from an empty universe and offers Retry; Retry clears the failure; genuine empty and populated lists; an older GET unable to overwrite post-mutation reconciliation (settling before and while the write is pending, success and failure); superseded manual Retry reads (newer first, older success/failure later, loading kept while the newest is pending); repeated Enter, repeated click, click+Enter, remove during a pending add and two removes in the same tick each send exactly one request; pending labels/indicators ("Adding…", "Removing SYM…", "Reloading…") and disabled Add / × / Retry; failed DELETE + successful GET restores the row and keeps the deletion error; failed DELETE + failed GET restores the row, shows both errors and recovers through Retry (deletion error retained); successful DELETE and successful add + failed GET described as "Change saved, but reloading…" (not a failed write) with the last confirmed list kept and Retry recovering; rejected add keeps the text and its error survives the reconciliation GET, a new mutation clears it; read failure keeps the last list; text typed while an add is pending survives its completion; unmount and Results/Universe tab switch ignore late GET/POST/DELETE completions and start no reconciliation read; a fresh GET on returning to the tab.
- **Baseline fails.** The same harness against the untouched base: 50 passed, 33 failed. Failures include the "Universe is empty" message on an initial failure, no Retry, second POST/DELETE from repeated Enter/click/remove, no pending indicator, the deletion error cleared by the reconciliation GET, a failed write and a failed reload indistinguishable, an older GET overwriting state, a delayed add erasing newer input, and a reconciliation GET started after unmount (some later scenarios throw because the Retry button does not exist).
- **Mutation checks** (each reverted; clean tree 94/94; counts from the harness as it stood when each was run — 89 checks for all but the invalidation one): removing the synchronous mutation guard (6 failed); removing the read-counter advance on mutation start (1 failed, the mid-write stale read); removing the stale-id check on read success (2); removing the mounted check after the write (2); clearing `mutationError` on read success (3); reporting a successful write whose reload failed as a failed write (3); clearing the input unconditionally (1); showing the empty message on an initial failure (1).
- **Not detectable by the harness:** removing only the `if (mutating) return` in `handleAdd` left 89/89 — the hook's synchronous guard already blocks the second call, so that line is a defensive duplicate. Stated rather than claimed as covered.
- **Not covered:** no real browser, visual/Tailwind review, live backend or real hung connection (a pending promise stands in). Requests are not cancelled. Manual check: with the backend throttled, press Add twice quickly and Enter during the pending add, delete a symbol while the backend returns an error, and switch to Results mid-request.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and was restored.
- **Delivery check.** See the final response for the base SHA and the clean-checkout verification of the ZIP (`git diff --check`, `npm ci && npm run build`).
<!-- END DELIVERY SECTION: scanner-universe-mutation-recovery -->

<!-- BEGIN DELIVERY SECTION: outcome-read-limit-ordering (backend + tests + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-read-limit-ordering`

Base `54c884b4d0842ef39a04ff37c8e0dea652e18217`. Disposable PostgreSQL 16 database `trading_workspace` created for this run and migrated with `alembic upgrade head` (to 0017). Tests ran serially.

- **Baseline (untouched base).** `tests/test_backtest_runs_route.py` + `tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py`: 27 passed.
- **After the change.** The same two modules: 53 passed, 0 failed, 0 skipped (26 new test cases, including parametrized ones).
- **Related read coverage.** `tests/test_intelligence_history_read_concurrency.py`, `tests/test_outcome_read_path_integration.py`, `tests/test_performance_analytics_routes.py`: 13 passed.
- **Regression check.** With the new tests applied to the original `intelligence.py`: 9 failed, 44 passed. The failures are invalid `limit=0` / `limit=-1` accepted (both routes), and all five tie tests (outcomes: tied order, selected run, sweep; runs: tied order, sweep-filtered limit). The 501, non-integer and boundary cases already behaved correctly on the base and pass on both. Source restored afterwards.
- **Not run.** Full backend suite (not requested); frontend (untouched).
- **Delivery check.** The ZIP was extracted over a clean checkout of the base above; `git diff --check` is clean and the two route modules pass (53) there. `origin/main` was re-fetched immediately before packaging: unchanged.
<!-- END DELIVERY SECTION: outcome-read-limit-ordering -->

<!-- BEGIN DELIVERY SECTION: scanner-results-request-safety (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `scanner-results-request-safety`

Base `8e5d4f5474ceea389bf0ece685c461e4a6bb4fd9`, Node v22.22.0, `npm ci` in `frontend/`. Frontend and docs only: no backend, database or broker was touched, so backend pytest was not run.

- `npm run build` (`tsc -b && vite build`): clean on the untouched base and after the change (103 modules; existing >500 kB chunk advisory only).
- **Behavior harness (not committed).** The repo has no frontend test runner and none was added. A throwaway jsdom + React `act` harness kept outside the repository bundles the real `ScannerPanel`, `useScannerState` and `useScannerUniverse` with esbuild, stubs only `WorkspaceContext` and `fetch` (every `/scanner/state` request stays pending until the test settles it with success, HTTP error or network error) and uses `@sinonjs/fake-timers` for `setInterval`/`Date`. **61 checks passed, 0 failed.** Covered: initial loading; genuine empty versus request failure; hung request then manual Refresh; newer success then older success/HTTP failure/network failure; newer failure then older success; older success and failure settling while the newest of three stacked requests is pending (loading stays true); poll skipped while pending across several ticks, resumed after success and after failure, manual Refresh working while pending; same-query failure preserving rows, skipped, universe and the last-success timestamp, error persisting while a retry is pending and cleared by a later success; override change (request issued for the new query, no render under the new override shows previous rows/skipped/universe/error/timestamp, late old-query responses ignored, failure of a new query shows empty rows plus error, returning to an earlier query does not resurrect old rows); identical array contents causing no refetch and not restarting the interval (poll still fires at the original 15 s mark); explicit empty array distinct from omitted; unmount ignoring late responses with no further requests, no React errors and no timers left; one interval after an override change; panel: Refresh enabled while loading and superseding a pending request, newest rows only, empty/failure/initial states distinct, failure keeping rows and the "Updated" stamp, Results-tab unmount (the path collapse takes) ignoring late responses and stopping polling.
- **Baseline fails.** The same harness against the untouched base: 34 passed, 25 failed (two further panel scenarios aborted because the missing second request made the script throw). Failures include late older success/failure overwriting newer state, older settlement clearing `loading`, polls stacking behind a pending request, previous-override rows/error shown under a new override, Refresh disabled while pending.
- **Mutation checks** (each reverted; clean tree 61/61): removing the stale-response guard on success (5 failed) or failure (5); polling while pending (8); ignoring the snapshot key match (2); failure dropping rows/timestamp (4); not clearing the interval on cleanup (16); omitted treated as empty array (2); `symbols` array reference in the callback dependencies (6, identical contents then refetch/restart polling); refresh clearing rows under the same key (4); Refresh button disabled again while loading (4).
- **Not detectable by the harness:** invalidating the counter in the effect cleanup on *unmount* (removing it left 61/61) — React 18 silently ignores state updates on an unmounted component, and an override change is already invalidated by the next `load()`, so the explicit cleanup increment is a defensive guard that no observable behavior distinguishes. Stated rather than claimed as covered.
- **Not covered:** no real browser, visual/Tailwind review, live backend or real hung connection (a pending promise stands in); requests are not cancelled. Manual check: open the Scanner panel with the backend throttled, press Refresh while "loading…" shows, then confirm only the newest result and timestamp appear and that stopping the backend keeps the last rows with an error.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and was restored.
- **Delivery check.** `origin/main` was re-fetched immediately before packaging: still `8e5d4f54…` (Instance 1's `Outcome recorder ledger contention`, already on this base; its test file and its `CHANGES.md`, `TESTING.md` and `execution-engine-design.md` sections verified present and untouched). The build and both harness runs (61/61 on the change, 25 failures on the base) were re-run on that tree. The ZIP (complete files only: `CHANGES.md`, `TESTING.md`, `docs/architecture/scanner-design.md`, `frontend/src/hooks/useScannerState.ts`, `frontend/src/components/scanner/ScannerPanel.tsx`) has the project root as its top level and no other entries. It was extracted onto a fresh clone of the base and copied over it: `git status --short` listed exactly those five paths, `git diff --check` was clean, and `npm ci && npm run build` was clean there. No file is deleted or renamed, so the delivery is copy-only.
<!-- END DELIVERY SECTION: scanner-results-request-safety -->

<!-- BEGIN DELIVERY SECTION: outcome-recorder-ledger-contention (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-recorder-ledger-contention`

Base: GitHub `main` `17cda43e8c815330436248b4bf74f2685d51f74e`; Python 3.12.3, pytest 8.4.2, pytest-asyncio 0.24.0.

**Database target.** A disposable local PostgreSQL 16.15 cluster on `localhost:5432` (database `trading_workspace`, role `trading`, outside the repository), created for this run and migrated from empty with `alembic upgrade head` to revision `0017`. It held 0 `trades` and 0 `strategy_outcomes` rows before and after every run. This is not the project's configured PostgreSQL service.

- `python -m pytest tests/test_outcome_recorder_ledger_contention.py -v` — **2 passed, 0 failed, 0 skipped** (about 1 s). Twenty-five further consecutive runs: 2 passed each time.
- Related set, serial, one process — the new module with `tests/test_outcome_recorder.py`, `tests/test_outcome_recorder_restart_recovery.py`, `tests/test_outcome_recorder_event_path_integration.py`, `tests/test_outcome_recorder_lifespan_recovery.py`, `tests/test_execution_outcome_status_recorder_integration.py` — **36 passed, 0 failed, 0 skipped**.
- The roughly 1,600-test backend suite was **not** run.
- `git diff --check`: clean (the new file was marked intent-to-add so it is covered), in the working clone and again on the clean-checkout verification.

**Contention is observed, not assumed.** With `-s` each scenario prints the lock evidence taken from `pg_locks` / `pg_stat_activity` while A is still held: B's backend has an ungranted `ShareRowExclusiveLock` request on relation `trades`, `wait_event = Lock/relation`, and `pg_blocking_pids` names A's backend. A is shown holding granted `ShareRowExclusiveLock` on `trades`, `orders` and `trade_reservations`. At that moment B has built and written nothing and neither task has finished. The lock waited on is the table lock (the production order is table lock, then row lock); no row-lock wait is required or asserted.

**Negative controls** (temporary edits in a scratch copy of `backend/`, outside the working clone; not part of the delivery):
1. `LOCK TABLE` removed from `ledger_transaction` and `.with_for_update()` removed from `_record_locked` (no serialization): scenario 1 fails at its precondition that A holds `ShareRowExclusiveLock` on the ledger tables (`holds set()`), so unserialized transactions cannot pass.
2. Only `LOCK TABLE` removed (row lock kept): same failure, so the table-before-row order is pinned and a row-lock-only design is detected.
3. Only `.with_for_update()` removed (table lock kept): **both tests still pass.** The table lock alone serializes every writer that goes through `ledger_transaction`, so this module does not independently prove the row lock; it is not claimed to.
4. `trade.outcome_id is not None` dropped from the post-lock re-check (serialization intact, idempotence broken): scenario 1 fails — B returns `blocked` (the partial unique index rejects its second outcome) instead of `skipped`.
5. `_mark_retry` made to downgrade unconditionally: scenario 2 fails — final status is `pending_retry` instead of `recorded`.

**Limits.** Two independent database sessions (separate engines and backends) in one test process; not multi-process contention, not a process crash or `kill -9`. Scenario 2 relies on PostgreSQL granting released table locks to the already-queued waiter before A's follow-up `LOCK TABLE` queues; this held in every run and is what makes the "A's retry marker finds a recorded trade" assertion deterministic. The database is local and disposable.

**Delivery check.** `origin/main` was re-fetched immediately before packaging: still `17cda43e…`, no newer commits. The ZIP (complete files only: `CHANGES.md`, `TESTING.md`, `docs/architecture/execution-engine-design.md`, `backend/tests/test_outcome_recorder_ledger_contention.py`) was extracted outside a fresh clone of the base and copied over it; `git status --short` listed exactly those four paths, `git diff --check` was clean, and the new module (2 passed) and the related set (36 passed) were re-run there.
<!-- END DELIVERY SECTION: outcome-recorder-ledger-contention -->

<!-- BEGIN DELIVERY SECTION: outcome-recorder-lifespan-recovery (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-recorder-lifespan-recovery`

Base: GitHub `main` `445e9d43c906df806bfe3a7d541a3a763d81350f`; Python 3.13.16, pytest 8.4.2, pytest-asyncio 0.24.0.

**Database target.** A disposable local PostgreSQL 16 cluster on `localhost:5432` (database `olr_lifespan_recovery`, role `trading`, outside the repository), created for this run and migrated from empty with `alembic upgrade head` to revision `0017 (head)`. It held no trades, outcomes or non-zero Portfolio State cursor before or after the runs below. Finnhub/Polygon were blanked by `conftest.py`; no external service was contacted.

- `python -m pytest tests/test_outcome_recorder_lifespan_recovery.py -v` — **2 passed, 0 failed, 0 skipped** (about 2 s). Six further consecutive runs: 2 passed each time.
- Directly relevant set, serial, one process — the new module with `tests/test_main_execution_pipeline.py`, `tests/test_execution_startup_status_route.py`, `tests/test_outcome_recorder.py`, `tests/test_outcome_recorder_restart_recovery.py`, `tests/test_outcome_recorder_event_path_integration.py` and `tests/test_execution_outcome_status_recorder_integration.py`: **49 passed, 0 failed, 0 skipped.** Afterwards the database again held 0 trades, 0 outcomes and cursor 0.
- The roughly 1,600-test backend suite was **not** run.
- `git diff --check`: clean (the new file was marked intent-to-add so it is covered), in the working clone and again on the clean-checkout verification below.

**Negative controls** (temporary edits to `backend/app/main.py` in the working clone, restored from a copy; not part of the delivery):
1. The `await outcome_recorder.start()` call removed: the recovery test fails with the bounded 10 s timeout waiting for the worker verdict, reporting the trace (`reconcile:begin`, `reconcile:end`, no recorder events); no hang.
2. The recorder started inside the reconciliation-blocked branch: the blocked test fails on its "no recorder events" assertion.
3. The recorder started before `SimulatedVenue` is built, i.e. before reconciliation: both tests fail (the recovery test on its reconcile-before-recorder order assertion; the blocked test on the recorder events).

**Limits.** Two sequential in-process lifespans over a live database; not a process crash, `kill -9` or multi-process lock contention. The database is local and disposable, not the project's configured PostgreSQL service. The guard makes the module refuse to run against a database that already holds trades. Only the closed-trade startup-scan path is driven; entry-snapshot capture, the periodic sweep and the other blocked-outcome reasons are covered by their own suites.

**Delivery check.** `origin/main` was re-fetched immediately before packaging: still `445e9d43…`, no newer commits. The ZIP (complete files only: `CHANGES.md`, `TESTING.md`, `docs/architecture/execution-engine-design.md`, `backend/tests/test_outcome_recorder_lifespan_recovery.py`) was built outside the repository; its top level is the project root, with no enclosing folder and no other entries. It was extracted outside a fresh clone of GitHub `main` at `445e9d43…` and copied over the project root. `git status --short` then showed exactly the three modified documents and the one new test file, all byte-identical to the verified working tree, and `git diff --check` was clean. On that clean checkout, the new module with `tests/test_execution_startup_status_route.py` and `tests/test_outcome_recorder_restart_recovery.py` ran **9 passed, 0 failed, 0 skipped**. No file is deleted or renamed, so the delivery is copy-only.
<!-- END DELIVERY SECTION: outcome-recorder-lifespan-recovery -->

<!-- BEGIN DELIVERY SECTION: backtest-results-refresh-recovery (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `backtest-results-refresh-recovery`

Base `1989a43` (implemented and baseline-checked on `445e9d4`; re-validated after integration), Node v22.22.2, `npm ci` in `frontend/`. Frontend and docs only: no backend, database or broker was touched, so backend pytest was not run.

- `npm run build` (`tsc -b && vite build`): clean on the untouched base and after the change (103 modules; existing >500 kB chunk advisory only).
- **Behavior harness (not committed).** The repo has no frontend test runner and none was added. A throwaway jsdom + React `act` harness kept outside the repository bundles the real `BacktestResultsPanel` and the three real hooks with esbuild, stubs only `WorkspaceContext` and `fetch` (every request stays pending until the test settles it with success, HTTP error or network error). **95 checks passed, 0 failed.** Covered: hung request then Refresh; newer success then older success/HTTP failure/network failure; newer failure then older success; older success and failure while the newest is pending (three stacked Refreshes); Apply/Clear with requests pending; the DOM captured at the moment each new request starts contains no previous-filter rows, metadata, errors or empty messages; metadata association (no premature "no metadata", late older metadata ignored, newest settling before an older response); run/sweep tab switching with late responses in the other mode; independent filters preserved; sweep loads issue exactly two requests, Refresh two more, zero-outcome pairs remain listed (including all-zero sweeps); sweep newer failure/older success and the reverse; sweep Apply/Clear; collapse then late responses and re-expansion, unmount then late response without React errors; auto-follow and manual-freeze behavior; loading/error/empty/populated remain distinct.
- **Baseline fails.** The same harness against the untouched base: 27 failed (54 passed of 81 run; the harness then aborted tests that need the missing second request). Failures include Refresh disabled while pending, previous-filter rows/metadata/errors/empty messages present when the next request starts, and no supersession for sweeps.
- **Mutation checks** (each reverted; clean tree 95/95): removing the stale-response guard in the outcomes hook (8 failed), the sweep hook (5 failed) or the runs hook (1 failed); ignoring the key match in the outcomes (4), sweep (2) or runs (4) hook; re-disabling Refresh (15 failed of 80 before the harness aborted); retaining previous rows across keys (2); sweep loading false before the first effect (1).
- **Not covered:** no real browser, visual/Tailwind review, live backend or real hung connection (a pending promise stands in); requests are not cancelled. Manual check: expand Backtest Results with the backend throttled, press Refresh repeatedly while "Loading…" shows, apply a different run_id mid-request, then confirm only the current filter's result appears.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and was restored.
<!-- END DELIVERY SECTION: backtest-results-refresh-recovery -->

<!-- BEGIN DELIVERY SECTION: outcome-recorder-restart-recovery-tests (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-recorder-restart-recovery-tests`

Base: GitHub `main` `1ae3582b28fc45f69b3bfd6447f978b18edded7f`; Python 3.13.16, pytest 8.4.2, pytest-asyncio 0.24.0.

**Database target.** A disposable local PostgreSQL 16 cluster on `127.0.0.1:55432` (database `trading_test`, outside the repository), created for this run and migrated from empty with `alembic upgrade head` to revision `0017 (head)`. Before the tests it held 0 rows in `trades`, `strategy_outcomes` and `positions`; after every run all ledger tables were empty again. No development, external or production database, and no broker or market-data account, was touched. Environment used for every command: `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_DB=trading_test POSTGRES_USER=trading POSTGRES_PASSWORD=trading`, run from `backend/`.

- `python3 -m pytest tests/test_outcome_recorder_restart_recovery.py -v` — **2 passed, 0 failed, 0 skipped** (about 1 s). Eight consecutive repeat runs: 2 passed each time.
- Combined serial set — `python3 -m pytest tests/test_outcome_recorder.py tests/test_outcome_recorder_event_path_integration.py tests/test_execution_outcome_status_recorder_integration.py tests/test_outcome_read_path_integration.py tests/test_execution_outcome_status_route.py tests/test_outcome_recorder_restart_recovery.py -rs -q` — **57 passed, 0 failed, 0 skipped** (3.6 s). No skip summary was printed, so the real database was exercised.
- `git diff --check`: clean (the new file was marked intent-to-add so it is covered).
- The roughly 1,600-test backend suite was **not** run.
- **Delivery check.** The ZIP (complete files only: `AGENTS.md`, `CHANGES.md`, `TESTING.md`, `docs/architecture/execution-engine-design.md`, `backend/tests/test_outcome_recorder_restart_recovery.py`) was extracted outside a fresh clone of the base and copied over its root; `git status --short` listed exactly those five paths, `git diff --check` was clean, no existing line was removed, and the new module and the combined set above were re-run from that checkout with the results stated here.

**Negative controls** (temporary edits to `outcome_recorder.py` in a scratch clone, reverted with `git checkout`; not part of the delivery):
1. `_startup_scan` returns immediately: both tests fail.
2. The startup scan runs the real query but enqueues nothing: both tests fail with the bounded 10 s `TimeoutError` while waiting for the worker verdict (no hang), and cleanup still left the tables empty.
3. `_pending_rows` excludes `pending_retry`: the retry-survives-restart test fails at its "pending before start" assertion; the other test still passes.

**Limits.** This is a recorder-object restart over a live database, not a process crash or app-lifespan recovery. The database is local and disposable, not the project's configured PostgreSQL service. The guard makes the module fail (not skip) against any database that already holds trades, so it cannot be run against a populated development database. No production defect was found, so no blocked or failing case is reported.
<!-- END DELIVERY SECTION: outcome-recorder-restart-recovery-tests -->

<!-- BEGIN DELIVERY SECTION: execution-panel-protection-diagnostics (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `execution-panel-protection-diagnostics`

Base: GitHub `main` `3eb727fca82bf40678af32502a6d08c96c416666`; Node v22.22.2. Frontend-only delivery, so no backend pytest or full-suite run was made.

- `npx tsc -b` (in `frontend/`): no errors.
- `npm run build` (`tsc -b && vite build`): passed; only the existing chunk-size warning was printed.
- `git diff --check`: clean. `frontend/tsconfig.tsbuildinfo` churn from the builds was restored and `frontend/dist` is git-ignored, so neither is in the delivery.
- **Temporary harness, kept outside the repo** (`/tmp/harness`, not delivered): esbuild bundled the real `ExecutionLifecyclePanel` with the repo's own React 18.3 and ran it in jsdom 24 with a mocked `fetch` whose `/intelligence/exit-intents` calls were individually resolved or rejected by the test, a stub `WebSocket`, and every other endpoint left permanently loading. **23 scenarios passed (23/23)**: caption replaced; loading with Refresh enabled; healthy; degraded snapshot; pending fills; sticky `lost_window` after the snapshot recovered; `healthy` contradicted by `lost_window`; zero triggers with degraded diagnostics; populated triggers; monitor unavailable with the route's placeholder zeros; diagnostics absent; running with `{status: "unavailable"}`; minimal `{status: "healthy"}`; unrecognized or garbage `protection_diagnostics` (five inputs); `null` limits and `lost_window`; unknown cause code with missing symbol/timestamp and a markup-looking string (rendered as text, no element created, incident list collapsed by default); incident list capped at the newest 25; network failure and HTTP 503; a hung request followed by Refresh with a late success and a late failure ignored; a superseded request failing first and an older success unable to override the error; Refresh from the ready state; collapse while hung with late success/failure ignored and re-expand refetching; full unmount followed by late resolve/reject. Any `console.error` fails a scenario; none occurred.
- **Negative control.** The same harness against the unmodified baseline component: 17 of 23 scenarios failed (6 passed — loading with Refresh enabled, monitor unavailable, request failure, superseded request failing first, collapse/re-expand and unmount, none of which this delivery changes). So the harness does detect missing diagnostics.
- **Limits.** jsdom, not a real browser: no layout, Tailwind rendering or visual check of the panel width, and the `<details>` toggle was not clicked. The other sections were held in their loading state, so cross-section interaction was not exercised. Responses are fabricated from the backend shapes read in `engine.py` and `intelligence.py`, not captured from a running backend; the backend route test `test_exit_intents_route.py` was read, not run. No permanent frontend test framework was added.
<!-- END DELIVERY SECTION: execution-panel-protection-diagnostics -->

<!-- BEGIN DELIVERY SECTION: simulated-venue-invalid-tick-guard -->
# TESTING — `simulated-venue-invalid-tick-guard`

Base: GitHub `main` `98075f59a4d22cf59a27eccde74caac6dadb7884`. Focused test-first run before the venue fix: `./.venv/bin/pytest -q tests/test_simulated_venue.py -k invalid_tick` produced **8 failed**, 10 deselected. Each failure demonstrated an invalid tick consuming or advancing an order; the EventBus case also consumed a market order. No production code was changed before this red run.

After the fix, `./.venv/bin/pytest -q --disable-warnings tests/test_simulated_venue.py` passed **21/21**. The venue tests cover NaN, both infinities, zero, negative price, naive/missing/broken-timezone timestamps, all same-symbol pending market and crossing-limit orders, unchanged status/quantities/plan index/fill history/pending list/callback count, the next valid tranches and sequential `:f1`/`:f2` IDs, offset-aware timestamp preservation, EventBus continuation after bad values or an unparseable payload, and bounded warning output without traceback.

The configured PostgreSQL 18 cluster on port 5432 was down. Starting that service required root/sudo credentials unavailable to this session, so a disposable PostgreSQL 18 cluster was initialized under `backend/.simulated-venue-validation/` on localhost:55432. It contains only a `trading_workspace` development test database, migrated through existing Alembic revision `0017`; no external database or broker account was used. With `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432`, the selected venue, Execution Engine, entry lifecycle, reconciliation, simulated protective-session retry, simulated EOD integration and main execution pipeline files passed **96/96** in 9.51 seconds. The first combined attempt at the unavailable default database was interrupted after 29 passing tests; it is not counted as validation. The full backend suite was not run.

`git diff --check` passed. Pytest emitted existing dependency/runtime deprecation warnings; `--disable-warnings` suppressed their display in the successful runs. The temporary database is stopped and removed after validation. No migration was added by this delivery.
<!-- END DELIVERY SECTION: simulated-venue-invalid-tick-guard -->

<!-- BEGIN DELIVERY SECTION: execution-panel-refresh-recovery (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `execution-panel-refresh-recovery`

Integration on local `main` `8eb099a` (Node v22.22.3): `git apply --stat` found four files including the component; `git apply --check` for the combined patch failed only on the shared log headers. `code-only.patch` passed its check and applied. `npm run build` passed (`tsc -b` and Vite, 103 modules; existing >500 kB chunk advisory). `git diff --check` passed. A temporary jsdom/esbuild harness in `/tmp` ran against the patched panel and passed focused interactions for all four pending Refresh paths, older success/failure responses, newer errors, orders/fills filter changes and independence, and collapse/unmount. The harness mocked API functions and was not committed. The earlier 199-check and mutation results below are evidence supplied with the delivery, not checks rerun during this integration. The tracked `tsconfig.tsbuildinfo` generated by the build was restored to its pre-build content.

The extracted delivery reports verification on `main` `edc1543`, Node v22.22.2, `npm ci` in `frontend/`. Frontend and docs only: no backend, database or broker
was touched, so no pytest or PostgreSQL run applies (the backend suite was not run).

- `npm run build` (`tsc -b && vite build`): clean on the untouched base and clean after the change, 103 modules
  transformed; only the existing Vite chunk-size (>500 kB) advisory appeared.
- **Behavior check (not committed).** The repo has no frontend test runner and none was added; the four sections were
  exercised with a throwaway jsdom + React `act` script kept outside the repository (esbuild bundle of the real panel,
  controllable mocked `fetch` where every request stays pending until the test settles it, stub `WebSocket`):
  **199 checks passed, 0 failed.** Per section (orders, fills, startup status, exit triggers): Refresh enabled while the
  first request is pending and starts request B; B ok then A ok / A http error / A network error leaves B's result; B
  error then A ok / A error leaves B's error (never an empty or "unavailable" state); an older success or failure while B
  is still pending does not replace B's loading; three stacked Refreshes keep only the latest; collapse with two pending
  requests ignores both late responses, re-expansion starts a fresh fetch and shows loading, and unmount then a late
  response is harmless; no React `console.error`. Orders and fills additionally: input/Apply enabled while loading; Apply
  during a pending unfiltered request ignores the unfiltered response; the request URLs (`?symbol=MSFT`, none after
  Clear); Refresh keeps the applied symbol and ignores unapplied box text; a late response for a previous filter is never
  shown; a current-filter failure is an error, not an empty ledger; Clear while a filtered request hangs; filter-aware
  empty messages; no render exposes the previous filter's empty message before the new data arrives (DOM mutation
  recording); Apply with an unchanged filter issues no request; orders Apply refetches only the orders endpoint, and a
  filter in one section never reaches the other.
- **Mutation checks** (each reverted; tree restored and re-run 199/199): the untouched base fails (Refresh disabled; the
  harness then aborts on the missing second request); removing the `active` guards in all four sections (40 failed);
  removing the filter-match rule (2 failed, the render-glitch checks); re-disabling Refresh in only the startup-status
  section (2 failed before the harness aborted on the missing second request).
- **Not covered:** no real browser, no visual/Tailwind review, no run against a live backend, and no real hung connection
  (a pending promise stands in for it). Request cancellation is not implemented, so a superseded request still completes in
  the browser. Manual check for reviewers: expand the Execution panel with the backend stopped or throttled, press Refresh
  repeatedly while "Loading …" shows, then start the backend and confirm only the last response is displayed.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and was
  restored before packaging.
<!-- END DELIVERY SECTION: execution-panel-refresh-recovery -->

<!-- BEGIN DELIVERY SECTION: position-monitor-trigger-recovery -->
# TESTING — `position-monitor-trigger-recovery`

GitHub `main` was `edc1543` at decision assignment. The configured local PostgreSQL development database (`trading_workspace` on `localhost:5432`, project defaults) started at Alembic `0014`; `backend/.venv/bin/alembic upgrade head` advanced it through existing migrations `0015`–`0017` for the integration tests. Tests create and clean their scoped records; no external database or broker account was used. The outside-repository ZIP was not present in the workspace; its committed `APPLY.txt` and patch were inspected, and the already-present log sections were preserved.

| Check (from `backend/` unless noted) | Result |
|---|---|
| Combined focused monitor, Portfolio State, reader, exit ledger/handoff, route, Execution Engine, real-lifespan pipeline and EOD integration tests (`test_position_monitor_engine.py`, `test_position_monitor_eod.py`, `test_position_monitor_portfolio_reader.py`, `test_portfolio_worker.py`, `test_portfolio_state.py`, `test_governor_portfolio_state_reader.py`, `test_exit_intents_route.py`, `test_execution_engine.py`, `test_exit_ledger_postgres.py`, `test_exit_ledger_eod_postgres.py`, `test_main_execution_pipeline.py`, `test_simulated_eod_integration.py`, `test_execution_exit_requests_route.py`) | 234 passed |
| `test_main_execution_pipeline.py::test_transient_portfolio_sync_failure_replays_first_breach_without_second_tick` (repeated after the combined run) | Passed against real PostgreSQL with EOD pulses disabled: one injected sync failure, breach then reversal while snapshot unavailable, automatic retry, original trigger timestamp, one durable request and one close order; a later duplicate breach and repeated recovery still leave one of each |
| `git diff --check` | Passed |

The monitor cases cover ready-flat and unavailable snapshots, worker read failure, no-pulse recovery, entry timestamp equality/pre-entry rejection, target-before-stop, candle and EOD ordering, duplicate observation suppression, per-position isolation, capacity and monotonic expiry, sticky lost-window diagnostics, invisible-fill markers, and failure before safe cursor advancement. The Portfolio State cases cover automatic retry, idempotency, unresolved-order degradation, shutdown and restart accounting. The EOD integration test's only edit replaces an obsolete tick `_process_event` wait with the replay cursor; historical-candle semantics are unchanged. The approximately 1,589-test suite was not run. Python 3.14's `asyncio.to_thread` callbacks stalled under the workspace sandbox; database-backed tests ran with approved unsandboxed execution.
<!-- END DELIVERY SECTION: position-monitor-trigger-recovery -->

<!-- BEGIN DELIVERY SECTION: position-monitor-scratch-removal (docs + removal of one mistakenly tracked scratch file; integrate alongside other sections, do not merge them) -->
# TESTING — `position-monitor-scratch-removal`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user `trading`
(project defaults), created fresh in the sandbox and migrated with `alembic upgrade head` (revision `0017`). Python 3.12.3.
Verified on `main` `89f93ec` (unchanged on `origin/main`). Commands run from `backend/`.

| Command | Result |
|---|---|
| `git ls-files \| grep scratch` before the change | `backend/tests/scratch_dropped_trigger_repro.py` (tracked in `89f93ec`) |
| `python -m pytest tests --collect-only -q` before the change | 1589 tests collected; the only line containing "scratch" is the unrelated `test_rebuild_from_ledger_recovers_open_position_from_scratch` |
| same command after `git rm` | 1589 tests collected (unchanged: the file was never collected) |
| `git ls-files \| grep scratch` after the change | no output |
| preserved copy, run from outside-repo location (temporarily placed in `backend/tests/`, then removed): `python -m pytest tests/scratch_dropped_trigger_repro_v2.py -q -s -p no:cacheprovider` | 17 passed (46.7 s); every assertion characterizes today's behaviour |

The full backend suite was **not** run for this correction (no executable code changed).
<!-- END DELIVERY SECTION: position-monitor-scratch-removal -->

<!-- BEGIN DELIVERY SECTION: simulated-eod-outcome-recorded (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `simulated-eod-outcome-recorded`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (project defaults), created fresh in the sandbox and migrated with `alembic upgrade head` (revision `0017`).
No external or production database and no broker touched. Python 3.12.3. Verified on `main` `179fef3` (unchanged on
`origin/main` at packaging). All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_simulated_eod_integration.py -k recorded_once_by_running -q` | 1 passed (~2 s first run, 1.4-1.5 s after) |
| same command, 5 consecutive runs | 5/5 passed |
| `tests/test_simulated_eod_integration.py test_outcome_recorder.py test_outcome_recorder_event_path_integration.py test_execution_outcome_status_recorder_integration.py test_exit_ledger_eod_postgres.py test_position_monitor_eod.py test_outcome_read_path_integration.py` | 138 passed (9.6 s) |
| `python -m pytest tests -q` (full backend suite) | 1589 passed (134.7 s) |

After the runs, `trades`, `strategy_outcomes`, `trade_reservations`, `orders`, `fills`, `positions` and
`position_fill_receipts` all held 0 rows, including after a deliberately failing mutation run. The sandbox database was
empty, so this shows the cleanup works; that it removes *only* this test's rows holds by construction (every delete is keyed
on this test's trade id), not because the database was empty.

**Mutation checks** (throwaway plugin or temporary test copy outside the repo; production and the committed test untouched):

| Mutation | Result |
|---|---|
| recorder ignores `PositionClosed` (bus wake-up cut), recorder wait shortened to 3 s | **failed** at the bounded wait for the recorder verdict (nothing else could record the trade) |
| outcome `realized_r` shifted by +1.0 | **failed** on the R assertion (`0.9 == -0.1`) |
| price the recorder normalises for the close fill shifted by +0.5 | **failed** on the exit-price assertion (`99.5 == 99.0`) |

Not run: the frontend checks (no frontend change). Not covered: blocked/retry verdicts, snapshot contents, a fill after
the 20:00 close, an EOD request with a stop fallback, restart mid-recording, and the read routes (see `CHANGES.md`).
No production defect was found, so no reproduction is attached.
<!-- END DELIVERY SECTION: simulated-eod-outcome-recorded -->

<!-- BEGIN DELIVERY SECTION: outcome-unique-opportunity-guard (backend migration + model + tests + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-unique-opportunity-guard`

**Database target:** local PostgreSQL 16.15 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (project defaults), dropped, recreated and migrated with `alembic upgrade head` (now revision `0017`) before the
final runs. The migration tests also create and drop their own scratch databases. No external or production database and
no broker touched. Python 3.12. Verified on `main` `5f7ca87` (unchanged on `origin/main` at packaging). Run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_strategy_outcomes_unique_opportunity_migration.py -q` | 6 passed |
| same file + `test_exit_ledger_eod_migration.py` + `test_position_ledger_postgres.py` | 44 passed (before the 0016-test edit, `test_exit_ledger_eod_migration.py` failed `'0017' == '0016'`, as expected) |
| outcome/ledger set: `test_outcome_recorder.py`, `..._event_path_integration.py`, `test_outcome_read_path_integration.py`, `test_execution_outcome_status_route.py`, `..._recorder_integration.py`, `test_strategy_outcomes_and_opportunity_conflicts_routes.py`, `test_performance_queries.py`, `test_performance_intelligence.py`, `test_performance_analytics_routes.py`, `test_backtest_runner.py`, `..._regression.py`, `test_backtest_routes.py`, `test_execution_ledger.py` | 148 passed |
| `python -m pytest tests -q --ignore=tests/test_main_execution_pipeline.py` | 1581 passed (124 s) |

`test_main_execution_pipeline.py` was not edited and was excluded from the full run (owned by another Claude instance).
The one-line index check against `trading_workspace` after migration: `CREATE UNIQUE INDEX uq_strategy_outcomes_non_backtest_opportunity ... (opportunity_id) WHERE (is_backtest IS FALSE)`.

**What the new tests prove:** duplicate simulated and paper inserts raise `IntegrityError` naming the index and leave
rows unchanged; three backtest rows with one ID across two runs plus one simulated row coexist; upgrade over existing
rows leaves every row byte-identical; with two duplicated IDs (one also duplicated by a paper row) the upgrade exits
non-zero, names both IDs and not the clean one, leaves revision `0016`, no index and all 7 rows, and succeeds once the
operator resolves them; downgrade removes only this index (indexes on every table and constraints on `strategy_outcomes`
otherwise identical, rows kept, a duplicate insert possible again).

**Pre-existing duplicates:** none (sandbox DB had 0 `strategy_outcomes` rows; your own database was not accessible).
Not covered: concurrent writers racing the index build, and frontend (no frontend change).
<!-- END DELIVERY SECTION: outcome-unique-opportunity-guard -->

<!-- BEGIN DELIVERY SECTION: main-pipeline-outcome-recorded (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `main-pipeline-outcome-recorded`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (project defaults), created fresh in the sandbox and migrated with `alembic upgrade head` (revision `0016`).
No external or production database and no broker touched. Python 3.12.3. Verified on `main` `d473334` (unchanged on
`origin/main` at packaging). All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_main_execution_pipeline.py -k position_monitor_places_durable_exit -q` (before edit) | 2 passed (2.6 s) |
| same command, after edit (stop + target cases) | 2 passed (~2.2 s) |
| same command, 20 consecutive runs | 20/20 passed (2.1-2.5 s each) |
| `python -m pytest tests/test_main_execution_pipeline.py -q` (whole file) | 7 passed (2.7 s) |
| `tests/test_main_execution_pipeline.py test_outcome_recorder.py test_outcome_recorder_event_path_integration.py test_execution_outcome_status_recorder_integration.py test_execution_outcome_status_route.py test_outcome_read_path_integration.py test_strategy_outcomes_and_opportunity_conflicts_routes.py test_simulated_eod_integration.py` | 92 passed (10.9 s) |

After the final runs `strategy_outcomes` and `trades` hold 0 rows for `TEST_MAIN_EXECUTION_PIPELINE`.

**Mutation checks** (throwaway pytest plugin outside the repo; production untouched):

| Mutation | Result |
|---|---|
| `OutcomeRecorder.record_trade` returns `"skipped"` without recording | stop case **failed** after the 10 s bound: `timed out ... waiting for: OutcomeRecorder verdict for the closed trade (last observed: ((None, None, 0), []))` |
| outcome `realized_r` shifted by +1.0 before it is written | both cases **failed** on the R assertion (`-0.5` vs `-1.5`, `3.5` vs `2.5`) |

The second mutation first exposed a weakness in my own draft: ORM numeric columns are `Decimal`, and
`pytest.approx` against a float only passed because the values matched exactly. The assertions now convert to float.

Not run: the full backend suite and the frontend checks (no frontend change). Not covered: snapshot content,
partial fills, the blocked-reason paths (covered by `test_outcome_recorder.py`), and a restart mid-recording.
<!-- END DELIVERY SECTION: main-pipeline-outcome-recorded -->

<!-- BEGIN DELIVERY SECTION: simulated-eod-lifespan-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `simulated-eod-lifespan-test-waits`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (project defaults), created fresh in the sandbox and migrated with `alembic upgrade head` (revision `0016`).
No external or production database and no broker touched. Python 3.12.3, pytest 8.4.2. Verified on `main` `6e69993`
(unchanged on `origin/main` at packaging). All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_simulated_eod_integration.py -q` (before edit) | 12 passed (4.8 s) |
| `python -m pytest tests/test_simulated_eod_integration.py::test_real_lifespan_eod_and_fresh_venue_restart_block -q` x15 (before edit) | 15/15 passed (1.6-1.8 s each) |
| same single test, after edit, 30 consecutive runs | 30/30 passed (~2 s each) |
| `python -m pytest tests/test_simulated_eod_integration.py -q` (whole file, after edit) | 12 passed (4.7 s) |
| same whole-file command, 5 more consecutive runs | 5/5 green |
| `tests/test_simulated_eod_integration.py tests/test_position_monitor_eod.py tests/test_main_execution_pipeline.py tests/test_exit_*.py` | 122 passed (15.2 s) |

The baseline did not fail without injected delay (the flake is timing-dependent and was not reproduced naturally in
15 runs), so the evidence below is by injected delay and mutation.

**Injected-delay check** (throwaway plugin outside the repo, `PYTHONPATH=/tmp/plug INJ_DELAY=<D> python -m pytest -p
delayplug ...`; it delays, by `D` seconds, `PositionMonitor._process_event`/`_process_pulse`, `EventBus._safe_call`
and `ExecutionEngine._service_exits`; "old" is `6e69993`'s file run from a temporary copy, removed afterwards):

| Delay D | Old test | New test |
|---|---|---|
| 0 | passed | passed (1.8 s) |
| 0.1 s | **failed** | passed (3.3 s) |
| 0.3 s | **failed** | passed (7.9 s) |

**Mutation check** (same plugin; production untouched; `_evaluate` stubbed to never produce a protective intent):
the new test **failed** after the 10 s bound with a diagnostic naming the stop-fallback milestone and the observed
state (`fallback: None`, only the EOD observation acknowledged).

Not run: the full backend suite and the frontend checks (no frontend change).
<!-- END DELIVERY SECTION: simulated-eod-lifespan-test-waits -->

<!-- BEGIN DELIVERY SECTION: position-monitor-engine-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `position-monitor-engine-test-waits`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (the project defaults in `app/core/config.py`), created fresh in the sandbox and migrated with
`alembic upgrade head` (revision `0016`). No external or production database and no broker touched. Python 3.12.3.
`test_position_monitor_engine.py` itself uses only a fake reader and a real in-process `EventBus` (no database).
Verified on `main` `26ba01b` (unchanged on `origin/main` at packaging).

All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_position_monitor_engine.py -q` (before edit) | 10 passed (1.9 s) |
| `python -m pytest tests/test_position_monitor_engine.py -q` (whole file, after edit) | 10 passed (0.7 s) |
| same command, 20 consecutive runs, no injected delay | 20/20 green |
| `python -m pytest tests/test_position_monitor_engine.py tests/test_position_monitor_eod.py tests/test_position_monitor_portfolio_reader.py tests/test_event_bus.py -q` | 72 passed (1.6 s) |
| neighbours: the four files above plus `test_exit_*.py`, `test_simulated_protective_session_retry.py`, `test_simulated_venue.py`, `test_fill_*.py`, `test_portfolio_*.py`, `test_execution_exit_requests_route.py`, `test_execution_outcome_status_recorder_integration.py` | 288 passed (15.7 s) |

`tests/test_simulated_eod_integration.py` was not run or edited (owned by another session). Not run: the full backend
suite.

**Injected-delay check** (throwaway plugin outside the repo replaces the monitor's worker loop with a copy that awaits
`D` seconds before processing each queued item; `PYTHONPATH=/tmp/plug INJ_DELAY=<D> python -m pytest -p delayplug <file> -q`;
"old" is `26ba01b`'s file run from a copy):

| Worker delay per item | Old tests | New tests |
|---|---|---|
| 0 | 10 passed | 10 passed |
| 0.1 s | **6 failed** (every intent-expecting test) | 10 passed (2.1 s) |
| 0.3 s | **6 failed** (same six) | 10 passed (4.4 s) |

**Mutation checks** (same plugin; production code untouched; delay 0.3 s unless noted):

| Mutation | Old test | New test |
|---|---|---|
| Subscriber and worker stop filtering by symbol (MSFT tick trips AAPL's stop) | `test_unheld_symbol_events_are_ignored` **passed** (vacuous: the sleep ended before the worker ran; 0 s delay: failed) | **failed** |
| Idempotency latch removed, later intent replaces the first | `test_idempotent_no_second_intent...` failed (at 0.3 s only because its first `sleep(0.1)` was too short, at 0 s on the mutation itself) | **failed** (also at 0 s) |
| An event at/after session close produces an intent | `test_event_at_session_close_no_longer_labels_eod` **passed** (vacuous at 0.3 s; failed at 0 s) | **failed** (also at 0 s) |

An earlier injection that delayed the subscriber itself (not the worker) let the new absence tests return early; that
is the documented limitation in `CHANGES.md` item 1, not a worker-delay result, and is why the injection above is at the
worker.

Remaining failures: none. No pre-existing flakiness was attributed to anything other than the removed fixed sleeps.
<!-- END DELIVERY SECTION: position-monitor-engine-test-waits -->

<!-- BEGIN DELIVERY SECTION: execution-engine-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `execution-engine-test-waits`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (the project defaults in `app/core/config.py`), created fresh in the sandbox and migrated with
`alembic upgrade head` (revision `0016`). No external or production database and no broker touched. Python 3.12.3.
`test_execution_engine.py` itself uses only fakes and a real in-process `EventBus` (no database).
Verified on `main` `cbf3735` (unchanged on `origin/main` at packaging).

All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_execution_engine.py -q` (before edit) | 10 passed (2.8 s) |
| `python -m pytest tests/test_execution_engine.py -q` (whole file, after edit) | 10 passed (0.7 s) |
| same command, 20 consecutive runs, no injected delay | 20/20 green |
| 26 neighbouring files (`tests/test_execution_*.py` except this file and `test_main_execution_pipeline.py`, `test_event_bus.py`, `test_exit_*.py`, `test_fill_*.py`, `test_simulated_*.py`, `test_position_monitor_*.py`, `test_portfolio_*.py`) | 405 passed (17 s) |

`tests/test_main_execution_pipeline.py` was not run or edited (owned by another session).

**Injected-delay check** (throwaway plugin outside the repo adds `time.sleep(d)` to the fake ledger's
`insert_order` and `update_order_status`, which the engine runs via `asyncio.to_thread`;
`PYTHONPATH=/tmp/plug INJ_DELAY=<d> python -m pytest -p delayplug <file> -q`; "old" is `cbf3735`'s file run from a copy):

| Delay per ledger call | Old tests | New tests |
|---|---|---|
| 0 | 10 passed | 10 passed |
| 0.1 s | **4 failed** (happy path, no venue, mode not supported, venue rejection ack) | 10 passed (2.0 s) |
| 0.2 s | **4 failed** (same four) | 10 passed (3.3 s) |
| 0.5 s | **5 failed** (the four plus the duplicate test) | 10 passed (7.2 s) |

**Mutation checks** (throwaway plugins outside the repo; production code untouched):

| Mutation | Old test | New test |
|---|---|---|
| Engine stops dropping `close` orders (treats them as an entry), 0.3 s ledger delay | `test_close_position_effect_is_dropped_not_processed` **passed** (vacuous: the sleep ended before the insert) | **failed** (ledger row found) |
| `_on_order_approved` runs `_process_one` inline on the bus critical lane (violates I7) | failed (`PlanRejected should have been delivered promptly`) | **failed** in 5.4 s with `timed out after 5.0s waiting for PlanRejected delivery while the venue call is blocked; observed: []`; teardown did not hang because the venue is released in `finally` |

Not run in this delivery: the full backend suite (the neighbouring set above was run instead).

Remaining failures: none. No pre-existing flakiness was attributed to anything other than the removed fixed sleeps.
<!-- END DELIVERY SECTION: execution-engine-test-waits -->

<!-- BEGIN DELIVERY SECTION: main-pipeline-restart-rollback-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `main-pipeline-restart-rollback-test-waits`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (the project defaults in `app/core/config.py`), created fresh in the sandbox and migrated with
`alembic upgrade head` (revision `0016`). No external or production database and no broker touched. Python 3.12.3.
Verified on `main` `b0a09ad` (unchanged on `origin/main` at packaging).

All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_main_execution_pipeline.py -q` (before edit) | 7 passed |
| `python -m pytest tests/test_main_execution_pipeline.py -k "orphaned_submitted or partial_startup_rolls_back" -q` (after edit) | 2 passed |
| same command, 20 consecutive runs, no injected delay | 20/20 green |
| `python -m pytest tests/test_main_execution_pipeline.py -q` (whole file, after edit) | 7 passed (2.7 s) |
| same command, 8 consecutive runs | 8/8 green |

**Injected-delay check** (throwaway plugin outside the repo, adds `time.sleep(d)` to the thread-offloaded commits
`PostgresTradeLedger.commit_decision` and `PostgresOrderLedger.update_order_status`;
`PYTHONPATH=/tmp/plug INJ_DELAY=<d> python -m pytest -p delayplug tests/test_main_execution_pipeline.py -k "orphaned_submitted or partial_startup_rolls_back" -q`):

| Delay per commit | Old tests (`b0a09ad`) | New tests |
|---|---|---|
| 0 | 2 passed | 2 passed |
| 0.2 s | 2 passed | 2 passed |
| 0.4 s | **restart test failed**, rollback test passed | 2 passed (2.9 s) |
| 0.8 s | **restart test failed**, rollback test passed | 2 passed (4.7 s) |

The rollback test passes under every delay in both versions: correct code never commits anything for the injected
Opportunity, so delaying commits cannot expose the old 0.2 s sleep. Its new waits do not change the outcome; they make
the absence assertions non-vacuous by proving dispatch settled first. I did not mutate production code to demonstrate a
failing rollback case.

Not run in this delivery: the full backend suite and neighbouring pipeline files; the change is confined to two tests in
one file and the whole of that file was run.

Remaining failures: none. No pre-existing flakiness was attributed to this work.
<!-- END DELIVERY SECTION: main-pipeline-restart-rollback-test-waits -->

<!-- BEGIN DELIVERY SECTION: position-monitor-pipeline-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `position-monitor-pipeline-test-waits`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (the project defaults in `app/core/config.py`), created fresh in the sandbox and migrated with
`alembic upgrade head` (revision `0016`). No external or production database and no broker touched. Python 3.12.3.
Verified on `main` `867846d` (unchanged on `origin/main` at packaging).

All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_main_execution_pipeline.py -q` (before edit) | 7 passed |
| `python -m pytest tests/test_main_execution_pipeline.py -k position_monitor_places_durable -v` (before edit, x6) | 2 passed each (~5.4 s) |
| `python -m pytest tests/test_main_execution_pipeline.py -k position_monitor_places_durable -v` (after edit) | both cases PASSED (`[89.0-stop-90.0-85.0--150]`, `[121.0-target-120.0-125.0-250]`), 2.3 s |
| same command, 20 consecutive runs, no injected delay | 20/20 green (2 cases each) |
| `python -m pytest tests/test_main_execution_pipeline.py tests/test_simulated_eod_integration.py tests/test_simulated_protective_session_retry.py tests/test_outcome_recorder_event_path_integration.py tests/test_execution_outcome_status_recorder_integration.py tests/test_execution_engine.py tests/test_exit_intents_route.py tests/test_position_monitor_engine.py tests/test_position_monitor_eod.py tests/test_portfolio_worker.py tests/test_simulated_venue.py tests/test_world_view_portfolio.py -q` | 162 passed |
| `python -m pytest tests -q` (full backend suite) | 1582 passed |

**Injected-delay check** (throwaway plugin outside the repo, adds `time.sleep(d)` to the thread-offloaded commits
`PostgresOrderLedger.update_order_status`, `PostgresFillLedger.record_fill`, `PostgresExitLedger.observe_exit` /
`prepare_exit` / `claim_dispatch` and `PostgresPositionLedger.commit_fill`;
`PYTHONPATH=/tmp/plug INJ_DELAY=<d> python -m pytest -p delayplug tests/test_main_execution_pipeline.py -k position_monitor_places_durable -q`):

| Delay per commit | Old test | New test |
|---|---|---|
| 0.1 s | 2 passed | 2 passed |
| 0.2 s | **2 failed** (position never open in its window) | 2 passed |
| 0.4 s | **failed** (entry order still `approved` at the first assertion) | 2 passed (8.1 s) |
| 0.8 s | not run | 2 passed (15.3 s) |

**Snapshot-visibility check** (second throwaway plugin delaying `PostgresPositionLedger.load_state` /
`pending_fills` / `get_order`, plus a temporary copy of the test whose milestone 2 waits only on the DB row, deleted
afterwards): at 0.15 s and 0.3 s read delay both cases of the copy **failed** with
`timed out after 10.0s waiting for: durable exit request, exit intent listed and close order submitted (last observed: (False, [], 0))`
(the trigger tick was dropped by the monitor); the real test passed (2 passed) at 0.3 s.

Remaining failures: none. No pre-existing flakiness was attributed to this work.
<!-- END DELIVERY SECTION: position-monitor-pipeline-test-waits -->

<!-- BEGIN DELIVERY SECTION: feature-engine-cold-start-test-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `feature-engine-cold-start-test-waits`

**Database target:** local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (the project defaults in `app/core/config.py`), created fresh in the sandbox and migrated with
`alembic upgrade head` (revision `0016`). No external or production database and no broker touched. Python 3.12.3.
Verified on `main` `91a3323`.

All commands run from `backend/`.

| Command | Result |
|---|---|
| `python -m pytest tests/test_feature_engine.py -k cold_start -v` (before edit) | 4 passed |
| `python -m pytest tests/test_feature_engine.py tests/test_candle_recorder.py -k "cold_start or backfill" -v` | 4 passed |
| `python -m pytest tests/test_feature_engine.py -k cold_start -q` x10 | 10/10 passed (~2 s each) |
| `python -m pytest tests/test_feature_engine.py -q` (whole file) | 81 passed |

**Injected-delay check** (throwaway plugin outside the repo, adds `time.sleep(d)` to `CandleRecorder._write_one` and
`FeatureEngine._compute_one`; `PYTHONPATH=/tmp/plug INJ_DELAY=<d> python -m pytest -p delayplug ...`):

| Delay | Old tests | New tests |
|---|---|---|
| 60 ms | `test_aggregated_timeframe_backfills_prior_bars_on_cold_start` FAILED, vwap test passed | both passed |
| 150 ms | not run | all 4 cold-start tests passed |

Remaining failures: none.
<!-- END DELIVERY SECTION: feature-engine-cold-start-test-waits -->

<!-- BEGIN DELIVERY SECTION: daily-levels-test-cleanup (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `daily-levels-test-cleanup`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (`CREATE ROLE trading ... SUPERUSER`), created fresh and migrated with `alembic upgrade head` (revision `0016`).
No external or production database and no broker touched. Python 3.12.3, **1 vCPU** sandbox, so timings and contention
numbers are indicative, not a model of Saqib's machine. Verified on `main` `91791ed` (unchanged on `origin/main` at
packaging).

## What changed in the lifecycle

```
before:  pre-clean ticker -> test writes symbols + daily_levels_state -> (nothing)        rows leak on pass AND fail
after:   track(ticker): pre-clean + record
         test body ........ publish -> wait for exact FeaturesUpdated count -> assert all
         finally: DELETE daily_levels_state (child) -> DELETE symbols (parent)  [tracked tickers only]
                  -> assert 0 symbols / 0 state rows remain for the tracked tickers
```

## Checks run

- **Baseline, unmodified file, fresh migrated DB:** `15 passed` (1.92 s) and **5 leftover symbols**
  (`__TEST_DL__`, `__TEST_DL_LKBK__`, `__TEST_DL_RCN__`, `__TEST_DL_RST__`, `__TEST_DL_FLKY__`) plus **8**
  `daily_levels_state` rows (7 active, 1 archived) remained afterward.
- **New file, one run on the emptied database:** `17 passed` (1.40 s); 0 symbols and 0 state rows for `__TEST_DL%`.
- **Repeated runs, each a separate process, with an unrelated sentinel symbol + state row (`SENTINEL_UNREL`) present:**
  - idle: **25/25** `17 passed` (1.17-1.25 s each); after **every** run `__TEST_DL%` symbols = 0, their state rows = 0,
    sentinel still 1 symbol / 1 state row.
  - under one busy-loop process per vCPU: **15/15** `17 passed`, same post-run counts (0/0, sentinel 1/1).
- **Failure-path cleanup, run for real:** a throw-away copy of the file with three assertions deliberately broken (the
  populate test's `len(received)`, the restart test's `1d` call count, the flaky test's `flaky.calls`) gave
  `3 failed, 14 passed`; afterwards **0 `__TEST_DL%` symbols, 0 state rows, sentinel intact (1/1)** — rows were removed for
  the failing tests too. The copy was deleted and is not in the delivery. The in-repo test
  `test_teardown_deletes_tracked_rows_even_when_the_test_body_fails_and_spares_others` pins the same guarantee permanently.
- **Deterministic latency injection** (external pytest plugin, **not in the repo**: `time.sleep` inside
  `FeatureEngine._compute_one` and `_reconcile_and_persist_daily_levels`; original file is the control, DB cleaned between
  runs):

  | injected latency per call | original `test_daily_levels.py`              | new `test_daily_levels.py`  |
  |---------------------------|----------------------------------------------|-----------------------------|
  | none                      | 15 passed (5 test symbols left behind)       | 17 passed (0 left)          |
  | 0.15 s                    | **5 failed**, 10 passed (5 left)             | 17 passed (0 left) 4.15 s   |
  | 0.5 s                     | **5 failed**, 10 passed (5 left)             | 17 passed (0 left) 11.19 s  |

- **Neighbouring suites on the same DB** (`test_daily_levels`, `test_feature_engine`, `test_vwap_ext`,
  `test_level_interaction_engine`, `test_level_touch_tracking`): **153 passed** (14.57 s); 0 `__TEST_DL%` symbols left.
- **Timeout message** (helper called with nothing received, 0.2 s): `demo event was not met within 0.2s — expected >= 2
  FeaturesUpdated, received 0: []`.

## Limits

- Cleanup runs in teardown; a hard kill (SIGKILL, power loss) mid-test can still leave rows. The next run's `track()`
  pre-clean removes them, so they cannot corrupt a later run, but they remain until then.
- Two `FeatureEngine` instances in one process share the DB: the tests assume nothing else writes `__TEST_DL%` tickers.
- Waits poll every 20 ms with a 5 s ceiling; a genuinely stuck pipeline fails with a named timeout rather than passing.
<!-- END DELIVERY SECTION: daily-levels-test-cleanup -->

<!-- BEGIN DELIVERY SECTION: vwap-ext-test-bounded-waits (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `vwap-ext-test-bounded-waits`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (`CREATE ROLE trading ... SUPERUSER`), created fresh and migrated with `alembic upgrade head` (revision
`0016`). No external or production database and no broker touched. Python 3.12.3, **1 vCPU** sandbox, so the contention
numbers below are indicative, not a model of Saqib's machine. Verified on `main` `4137a16` (unchanged on `origin/main`
at packaging).

## The race being removed

```
test (event loop)                 EventBus            FeatureEngine worker (serial)           CandleRecorder writer
-----------------                 --------            -----------------------------           ---------------------
publish CandleClosed ----------> dispatch -----------> queue -> to_thread(_compute_one) ----> FEATURES_UPDATED
                                    \---------------------------------------------------->  queue -> to_thread(_write_one)
sleep(0.1-0.3)  <-- guess -->       (any hop can be late under load)
assert on `received` / on persisted history      # before:  asserts whenever the guess expires
                                                 # after:   waits for exact count / row, THEN asserts everything
```

Cold-start test, two independent guesses, both replaced: (1) `sleep(0.3)` that the bus had handed the pre-market candle
to the recorder (and it had been written) before `recorder.stop()` — `stop()` only drains what is *already queued*, so a
late bus hop loses the row; (2) `sleep(0.2)` that the fresh engine's cold-start backfill + compute had published.

## Checks run

Baseline before the change (`main` `4137a16`, file unmodified): `tests/test_vwap_ext.py` **6 passed** (1.81 s).

- **Whole file, idle, 30 separate processes after the change:** **30/30** `6 passed` (0.86-1.20 s each).
- **Whole file under 3 busy-loop processes on 1 vCPU:** new file **14/14** passed (stopped at 14 of a planned 20 by
  the tool time budget). **Control, original file under the same load:** **1 passed, 10 failed** in 11 runs (stopped
  by the time budget); failing tests: `..._present_during_premarket_unlike_vwap` x10,
  `..._resets_at_next_trading_day_not_at_930_boundary` x1.
- **Deterministic latency injection** (external pytest plugin, **not in the repo**: sleeps inside
  `FeatureEngine._compute_one` and/or `CandleRecorder._write_one`; the original file is the control):

  | injected latency                        | original `test_vwap_ext.py`                                   | new `test_vwap_ext.py` |
  |-----------------------------------------|---------------------------------------------------------------|------------------------|
  | none                                    | 6 passed                                                      | 6 passed               |
  | compute 50 ms / candle                  | **3 failed** (`continues_across_930`, `resets_at_next_trading_day`, `absent_after_hours`) | 6 passed |
  | compute 150 ms / candle                 | **4 failed** (+ `present_during_premarket`)                   | 6 passed               |
  | recorder write 400 ms / row             | 6 passed (`stop()` drains the writer queue)                   | 6 passed               |
  | compute 150 ms + recorder 400 ms        | **4 failed**                                                  | 6 passed               |

  Cold-start test only (`-k backfills_pre_market_history_on_cold_start`), adding a bus-to-recorder delivery lag
  (the recorder's `_on_candle_closed` deferred with `call_later`):

  | injected latency                        | original                                                      | new    |
  |-----------------------------------------|---------------------------------------------------------------|--------|
  | none                                    | passed                                                        | passed |
  | bus lag 400 ms                          | **failed**: `vwap_ext` obtained `150.0`, expected `100.0` (history never persisted, so the engine had nothing to backfill: exactly the wrong-result shape a guess-wait can produce) | passed |
  | engine compute 300 ms                   | **failed**: `assert 0 == 1` (no `FeaturesUpdated` yet)        | passed |
  | bus lag 400 ms + compute 300 ms         | **failed**: `assert 0 == 1`                                   | passed |

  Honest note: recorder *write* latency alone did not break the original cold-start test, because `stop()` drains the
  queue; the persisted-row wait protects the earlier bus-to-recorder hop, shown by the bus-lag rows.
- **Mutation checks** (temporary edits to `feature_engine/engine.py` `_update_vwap_ext`; each reverted, and
  `engine.py` confirmed byte-identical to `main` with `cmp` afterwards) — the waits do not mask wrong results:
  1. remove the per-day reset (`if state is None:`) -> `..._resets_at_next_trading_day_...` fails, `assert 150.0 == 300.0`
     (the other five pass, as expected);
  2. disable the cold-start history read (`rows = []`) -> the cold-start test fails on the `100.0` approx assertion;
  3. add `+ 1` to the published `vwap_ext` -> 5 of 6 fail (premarket, 9:30 continuation, day reset, 1m/5m parity,
     cold-start); only the after-hours test passes, because it asserts presence/absence, not the value.
  (A first attempt at mutation 1 used an anchor that matched four lines and was not applied; it was redone with a unique
  anchor and only the redone result is reported.)
- **Feature Engine neighbors, single files:** `test_feature_engine.py` **81 passed** (5.79 s);
  `test_vwap_strategy.py` + `test_premarket_volume_ratio.py` + `test_candle_recorder.py` + `test_event_bus.py` +
  `test_candle_aggregator.py` **51 passed** (9.69 s).
- **`test_feature_engine.py` + `test_vwap_ext.py` together, 8 runs:** **87 passed** each.
- **Neighbors in suite order** (`test_candle_aggregator.py`, `test_candle_recorder.py`, `test_daily_levels.py`,
  `test_event_bus.py`, `test_feature_engine.py`, `test_premarket_volume_ratio.py`, `test_vwap_ext.py`,
  `test_vwap_strategy.py`), 3 runs: **153 passed** each (about 15.7 s).
- `diff -rq` against a pristine `main` `4137a16` tree: the only code file that differs is
  `backend/tests/test_vwap_ext.py` (plus these two docs). `grep asyncio.sleep` on it finds only the helper's docstring.

Not run: the full backend suite (not requested; the changed file and its Feature Engine / recorder / bus / aggregator /
daily-levels neighbors were run instead), frontend checks (no frontend file changed).

## Wider issues found (not fixed — out of scope)

1. **Same pattern remains elsewhere:** `asyncio.sleep(0.x)` after publishing candles still appears in
   `test_feature_engine.py` and other files (earlier delivery counted 32 in `test_feature_engine.py` and 29 test files
   overall). Suggested follow-up: convert test by test with `_wait_until` on the exact event count, as done here.
2. **Leftover rows from `test_daily_levels.py`:** after those tests run, five `__TEST_DL_*__` rows remain in `symbols`
   (`FLKY`, `LKBK`, `RCN`, `RST`, plain `DL`). Unrelated to this change and not touched; worth a look at that file's
   teardown.
<!-- END DELIVERY SECTION: vwap-ext-test-bounded-waits -->

<!-- BEGIN DELIVERY SECTION: market-clock-next-session-boundary (backend + tests + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `market-clock-next-session-boundary`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (`CREATE USER trading ... SUPERUSER`), `alembic upgrade head` (revision `0016`). Dropped, recreated and
re-migrated before the final full run (a first full run was killed mid-way by the sandbox and a second, on that
leftover data, showed 91 ledger/execution failures from stale rows; the wiped-DB rerun below is the authoritative one).
No external or production database and no broker touched. Python 3.12.3. Base `main` `93f6d2a`.

- **Focused:** `cd backend && python -m pytest tests/test_market_clock.py -q` -> **25 passed** (17 existing + 8 new).
- **Relevant Context Engine / clock tests:** `python -m pytest tests/test_context_engine.py tests/test_calendar_provider.py
  tests/test_session_window.py tests/test_market_clock.py -q` -> **113 passed**.
- **Full suite, wiped DB:** `python -m pytest -q -rf` -> **1580 passed, 1 warning in 126.23 s**. Collection on the
  untouched base is 1572, so the delta is exactly the 8 new tests, with zero regressions.
- **Regression guard:** with the pre-change `market_clock.py` restored and the new tests kept, `test_market_clock.py`
  gives **7 failed, 18 passed**: the holiday, weekend, half-day, year-crossing, timezone, minute-oracle and 2029 tests
  fail on the old code; the normal-day sequence test passes on both, confirming normal sessions are unchanged.

New tests, all pure (no database), each asserting that the returned boundary is strictly after `ts`, aware, that the
session at `boundary - 1 microsecond` differs from the session at `boundary`, and that the session at `ts` equals the
session just before the boundary (no skipped transition):
`test_boundary_on_covered_holiday_skips_to_next_trading_day_pre_market` (Thanksgiving 2026 at eight instants, 2026-07-03
observed holiday into a weekend), `test_boundary_on_weekend_skips_to_monday_pre_market`,
`test_boundary_normal_session_day_sequence_is_unchanged` (04:00, 09:30, 11:30, 14:30, 16:00, 20:00, next day 04:00;
exact-boundary and sub-minute probes), `test_boundary_half_day_close_2026_2027_2028` (13:00 close for all five
configured half-days: LUNCH at 12:59:59.999999, CLOSED at 13:00, `is_market_open` flips, then next trading day 04:00),
`test_boundary_covered_year_crossing_2027_to_2028` (also 2026->2027), `test_boundary_results_are_timezone_aware_in_clock_zone_for_any_input_zone`
(UTC input, EST/EDT offsets across the 2027-03-14 shift, naive input still `ValueError`),
`test_boundary_matches_minute_by_minute_session_changes_across_covered_windows` (oracle over six windows, probes every
15 minutes), and `test_unverified_year_2029_keeps_existing_behavior_and_is_not_claimed_covered`.

Not run: frontend checks (no frontend file changed). No test exercises `ContextEngine._loop` sleep timing directly;
the existing Context Engine tests pass unchanged.
<!-- END DELIVERY SECTION: market-clock-next-session-boundary -->

<!-- BEGIN DELIVERY SECTION: eod-partial-fill-test-order (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `eod-partial-fill-test-order`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (`CREATE USER trading ... SUPERUSER`), created fresh and migrated with `alembic upgrade head` (revision
`0016`). Re-wiped and re-migrated before the post-fix full runs. No external or production database and no broker
touched. Python 3.12.3, 1 vCPU sandbox. Verified on `main` `66426eb` (unchanged on `origin/main` at packaging).

## Reproduction

- **Natural reproduction: achieved on the first full-suite run of an untouched baseline** (`cd backend && python -m
  pytest -q -rf`, fresh DB): `1 failed, 1571 passed` in 144.6 s; the one failure was
  `test_simulated_eod_integration.py::test_partial_venue_fills_keep_one_close_until_real_remaining_fill` at
  `assert [f.qty for f in fills] == [3, 2]` -> `assert [2, 3] == [3, 2]` (line 441). The earlier assertions in the same
  test (`Position.qty == 2`, then `status == "closed"`, one order, same `client_order_id`) had already passed.
- **Not reproduced in isolation:** the test alone, 20 separate processes -> 20/20 passed; alone under 3 busy-loop
  processes on 1 vCPU -> 32/32 passed (a 33rd run was killed by the tool time limit, not counted).

## Trace of the chain (what the joins actually cover)

```
test task                 SimulatedVenue          ExecutionEngine (worker)      EventBus critical lane     PortfolioState (worker)
---------                 --------------          ------------------------      ----------------------     -----------------------
ingest_tick(99) -------->  _apply_fill
                           _dispatch -----------> _on_venue_update
                                                   put_nowait(venue_update)       (sync: queue.unfinished=1
                                                                                   before ingest_tick returns)
await engine._queue.join()                         _process_venue_update
                                                   to_thread(record_fill) COMMIT
                                                   bus.publish(OrderFilled) ----> critical queue (unfinished=1)
                                                   task_done  <-- join returns
await bus._critical_queue.join()                                                  _on_event -------------------> put_nowait(OrderFilled)
                                                                                  task_done  <-- join returns
await portfolio._queue.join()                                                                                    _synchronize: load_state,
                                                                                                                 pending_fills, commit_fill
                                                                                                                 (position qty 5 -> 2)
                                                                                                                 task_done  <-- join returns
assert Position.qty == 2 / status == "closed"; rows(pid) -> fills read back  <-- UNORDERED SELECT (the defect)
```

Every hand-off enqueues onto the next queue **before** the previous item's `task_done`, so `engine -> bus critical ->
portfolio` joins cannot return early. To check this empirically rather than only by reading, each step was stalled by
0.7 s in turn (a scratch pytest file, not in the repo, that wraps the real target test and injects a `time.sleep` /
`asyncio.sleep` into): `PostgresExitLedger.{observe_exit, pending_exit_position_ids, prepare_exit, claim_dispatch, set_status}`,
`PostgresFillLedger.record_fill`, `PostgresPositionLedger.{load_state, pending_fills, commit_fill, get_order}` and
`SimulatedVenue.place_order` -> **11/11 passed**. A stall in any step, including ones long enough to fire the engine's
0.5 s idle `_service_exits()` pass, did not change the result.

## Root-cause proof (deterministic)

1. **Heap order is not ledger order.** Scratch script (not in the repo) on the real `fills` table: insert a dummy fill
   and fill #1 (qty 3), delete the dummy, `VACUUM fills`, insert fill #2 (qty 2). Unordered query returned
   `[(2, ledger_seq 653, ctid (0,2)), (3, ledger_seq 652, ctid (0,3))]`; the `rows()`-style ORM select gave `[2, 3]`;
   the same select with `ORDER BY ledger_seq` gave `[3, 2]`.
2. **Same failure from the real test, forced.** Scratch wrapper around the unmodified target test that, after the second
   `record_fill`, performs a no-op `UPDATE` of fill #1 (new tuple version at a later heap slot): **pre-fix -> FAILED with
   the identical `assert [2, 3] == [3, 2]`; post-fix -> PASSED.** (A first attempt of this injection used `:f1` inside
   `text()` and failed for an unrelated bind-parameter reason in the scratch code; it was corrected and rerun, and only
   the corrected result is reported.)

## Checks run after the fix

- Target test alone, 15 separate processes: **15/15 passed**.
- `tests/test_simulated_eod_integration.py` (whole file, file order): **12 passed**.
- Neighboring EOD / exit-ledger files (`test_exit_ledger_eod_migration.py`, `test_exit_ledger_eod_postgres.py`,
  `test_exit_ledger_postgres.py`, `test_position_monitor_eod.py`, `test_simulated_protective_session_retry.py`,
  `test_execution_exit_requests_route.py`): **171 passed**.
- **Full suite** (`cd backend && python -m pytest -q -rf`), database dropped, recreated and migrated first: run 1:
  **1572 passed** (142.9 s); run 2 (`-v`, same database): **1572 passed** (144.9 s), the target test `PASSED`.
  Baseline for comparison: `1 failed, 1571 passed` (above). Test count unchanged: 1572.
- `git diff` shows exactly one edited code file, `backend/tests/test_simulated_eod_integration.py` (the `rows()` fills
  query); `test_feature_engine.py` and `test_vwap_ext.py` are untouched.

## Limits of this evidence

- The failure rate in the untouched baseline was 1 of 1 full runs here; the user reported it as intermittent, and the
  full-suite pass in both post-fix runs is consistent with the fix but two passes alone do not prove a rare flake gone.
  The proof rests on the deterministic reproduction (identical assertion, fails before / passes after) and the
  heap-order demonstration, not on pass counts.
- Other multi-row reads without `ORDER BY` in other test files were not audited (out of scope).
<!-- END DELIVERY SECTION: eod-partial-fill-test-order -->

<!-- BEGIN DELIVERY SECTION: feature-engine-test-featureset-wait (backend tests + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `feature-engine-test-featureset-wait`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (`CREATE USER trading ... SUPERUSER`), created fresh and migrated with `alembic upgrade head` (revision
`0016`). No external or production database and no broker touched. Python 3.12.3, 1 vCPU sandbox. Verified on `main`
`41aafc0` (unchanged on `origin/main` at packaging).

## What was reproduced

```
test (event loop)                     FeatureEngine worker (serial)                EventBus normal lane
-----------------                     -----------------------------                --------------------
publish candle 1..5  --------------->  queue.get -> to_thread(_compute_one) ---->  FEATURES_UPDATED (1m) x5
asyncio.sleep(0.1)  <-- guess -->      ...5 serial computes, then the 5m set ---->  FEATURES_UPDATED (5m) x1
assert on `received`                   (6 events total; finishes in 10-26 ms idle)  handler -> received.append
```

- **Idle latency** (diagnostic script outside the repo, same engine config, last publish -> last FeatureSet): 10-26 ms,
  always exactly six events (`1m` x5, `5m` x1) — comfortably inside 100 ms.
- **Under CPU contention** (3 busy-loop processes on 1 vCPU): the cold first run took **129 ms** (KAMA config) and
  **108 ms** (default config) — past the 100 ms sleep — while still producing the correct six events; warm runs 21-46 ms.
  So the engine finishes correctly, just later than the fixed wait.
- **Deterministic reproduction** (external pytest plugin, not in the repo, adding a per-candle sleep inside
  `_compute_one`): 0 ms and 10 ms per candle -> both tests pass; **20 ms and 30 ms per candle -> both fail** with the
  reported symptoms: `assert 'kama_2' in {... 'sma_1': 102.0 ...}` (latest 1m set seen, warm-up not reached yet) and
  `assert {'1m'} == {'1m', '5m'}`. Same assertions, same code, only latency changed -> an assertion-before-completion
  race, not a computation defect.
- **Natural reproduction was not achieved in this sandbox:** 6 runs of `test_feature_engine.py` + `test_vwap_ext.py` under
  3 busy loops all passed (87 passed), and the pre-change full run 1 passed (1572). The previous delivery's recorded
  full runs (two of three) did hit these tests; this session's evidence for the mechanism is the contention timing plus
  the deterministic delay reproduction above.

## Checks run after the fix

- Both target tests alone, 30 separate processes: **30/30 passed**.
- Both target tests with 20 ms/candle injected delay (fails before the fix), 10 runs: **10/10 passed**; also passed at
  0, 30 and 100 ms per candle.
- Both target tests under 3 busy loops, 15 runs: **15/15 passed**.
- `tests/test_feature_engine.py tests/test_vwap_ext.py` in file order, 5 runs: **87 passed** each.
- Neighbors in suite order (`test_candle_aggregator.py`, `test_candle_recorder.py`, `test_daily_levels.py`,
  `test_feature_engine.py`, `test_premarket_volume_ratio.py`, `test_vwap_ext.py`, `test_vwap_strategy.py`): **149 passed**
  x3, and **149 passed** once more under 3 busy loops.
- **Mutation checks (reverted; `git diff` showed only the intended edits afterwards):** suppressing 5m aggregation
  (`_AGGREGATED_WIDTHS = ()`) -> both tests fail after the 5 s bound with `five 1m FeaturesUpdated events plus the 5m
  bucket-close event was not met within 5.0s`; changing the expected `vwap_ext` value -> the equality assertion fails.
- **Full suite** (`cd backend && python3 -m pytest -q`), database freshly migrated, three runs: run 1 (pre-change):
  1572 passed; run 2 (post-change): **1 failed, 1571 passed**, the failure being
  `test_simulated_eod_integration.py::test_partial_venue_fills_keep_one_close_until_real_remaining_fill` (not one of the
  two target tests); run 3 (post-change, `-v`): **1572 passed**, both target tests `PASSED`. About 126 s per run.

## Wider issues found (not fixed — out of scope)

1. **Same fixed-sleep pattern elsewhere:** `asyncio.sleep(0.x)` after publishing candles appears 32 times in
   `test_feature_engine.py`, 6 in `test_vwap_ext.py` (the remaining ones, incl. 0.1 s waits), 8 in `test_daily_levels.py`,
   and in 29 test files overall. Any of them is exposed to the same race at enough latency; they were not individually
   audited here. Suggested follow-up: convert them to `_wait_until` on the expected event count, test by test.
2. **`test_simulated_eod_integration.py` is flaky for a different reason** (file not edited): `rows(pid)` loads fills with
   `select(Fill).where(...)` and **no `ORDER BY`**, then the test asserts `[f.qty for f in fills] == [3, 2]`; the failing
   run saw `[2, 3]`. That is an unspecified row-order assumption, not a wait, and not related to this change. Suggested
   follow-up: order the fills query by the fill's own sequence/timestamp column.
3. **Sandbox limit:** one vCPU, so contention numbers above are indicative, not a model of Saqib's machine.
<!-- END DELIVERY SECTION: feature-engine-test-featureset-wait -->

<!-- BEGIN DELIVERY SECTION: outcome-status-repeatable-read-test (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-status-repeatable-read-test`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading` (`CREATE USER trading ... SUPERUSER`), created fresh and migrated with `alembic upgrade head` (revision
`0016`). No external or production database and no broker touched. Python 3.12.3. Verified on `main` `b271733`
(unchanged on `origin/main` at packaging).

- **New tests:** `cd backend && python3 -m pytest tests/test_execution_outcome_status_route.py -q -k repeatable` ->
  **2 passed** (about 1.2 s); 6 runs in total (1 + 5 consecutive repeats), 2 passed each time.
- **Focused route suite:** `python3 -m pytest tests/test_execution_outcome_status_route.py -q` -> **24 passed** (22
  existing + 2 new), about 1.6 s.
- **Related integration suites** (`test_execution_outcome_status_recorder_integration.py`,
  `test_outcome_read_path_integration.py`, `test_outcome_recorder.py`, `test_outcome_recorder_event_path_integration.py`,
  `test_strategy_outcomes_and_opportunity_conflicts_routes.py`): **49 passed, 1 warning** (existing starlette/anyio
  deprecation notice).
- **Mutation check (reverted; `git status` showed only the test file afterwards):** changing the route's
  `"isolation_level": "REPEATABLE READ"` to `"READ COMMITTED"` -> **both new cases failed** — `insert`: the newly
  committed trade appeared in the list (`assert '<id>' not in {...}`); `transition`: the list showed `'recorded'` where
  the pre-change snapshot has NULL (`assert 'recorded' is None`). With the real route both pass.
- **Cleanup check:** `trades` and `strategy_outcomes` held 0 rows with the test `strategy_name` after the new tests.
- **Full suite** (`cd backend && python3 -m pytest -q`), database dropped, recreated and migrated first: **not green,
  and not attributable to this change.** Three full runs each produced one failure in a different unrelated file:
  1. this tree, with `-x`: `tests/test_simulated_eod_integration.py::test_partial_venue_fills_keep_one_close_until_real_remaining_fill`
     (1386 passed before stopping);
  2. **untouched fresh clone of `main` `b271733`**: `tests/test_feature_engine.py::test_kama_only_computed_for_its_configured_timeframe`
     (1 failed, 1569 passed, 122 s);
  3. this tree, no `-x`: `tests/test_vwap_ext.py::test_vwap_ext_is_identical_across_1m_and_5m_featuresets_on_the_same_close`
     (1 failed, 1571 passed, 119 s) — 1571 + 1 = 1572 tests = 1570 baseline + 2 new.

  All three failing tests **passed on rerun** (individually, and `test_simulated_eod_integration.py` as a whole: 12
  passed, on both this tree and the clean clone). Because the untouched baseline fails too, this is pre-existing
  order/timing-dependent flakiness; its root cause was not investigated here (out of scope — reported as a follow-up).
- **Not covered / limits:** one concurrent change per run (insert or transition); no concurrent change that removes a
  trade from the population; verified on a local database that was empty of unrelated qualifying trades at baseline.
<!-- END DELIVERY SECTION: outcome-status-repeatable-read-test -->

<!-- BEGIN DELIVERY SECTION: outcome-recorder-event-path-integration (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-recorder-event-path-integration`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading`, created fresh and migrated with `alembic upgrade head` (revision `0016`). No external or production database
and no broker touched. Python 3.12.3. Verified on `main` `55e8678`.

- New file: `cd backend && python3 -m pytest tests/test_outcome_recorder_event_path_integration.py -q` -> **1 passed**
  (about 1.2 s); 4 runs in total (3 consecutive plus one with an unrelated pending trade present), 1 passed each time.
- **Full suite** (`cd backend && python3 -m pytest tests -q`): **1570 passed, 1 warning in 124 s** (1569 baseline + 1
  new); the warning is the existing starlette/anyio deprecation notice.
- **Mutation checks** (each reverted; `git status` showed only the new test file afterwards): removing the recorder's
  `POSITION_CLOSED` subscription -> 1 failed (bounded wait timed out); making `_on_close` enqueue nothing -> 1 failed
  (same timeout). The failures take about 11 s, i.e. the 10 s bound, not a hang.
- **Shared-database check:** with an unrelated qualifying closed trade seeded beforehand, the test passed and that trade
  still had `outcome_status` NULL and no outcome (the startup scan did not record it); it was then removed by id.
- **Cleanup check:** `trades` and `strategy_outcomes` held 0 rows after the full suite on this database.
- **Not covered / limits:** no execution-pipeline fixture; startup-scan and sweep recovery not exercised; one duplicate
  timing (after completion); one trade shape (the `_seed` default).
<!-- END DELIVERY SECTION: outcome-recorder-event-path-integration -->

<!-- BEGIN DELIVERY SECTION: execution-outcome-status-recorder-integration (backend test + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `execution-outcome-status-recorder-integration`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading`, created fresh and migrated with `alembic upgrade head` (revision `0016`). No external or production
database and no broker touched. Python 3.12.3. Verified on `main` `fc8e2c9` (`main` advanced from `7ab2522` during the
task; the new test and the full suite were re-run on `fc8e2c9`).

- New file: `cd backend && python3 -m pytest tests/test_execution_outcome_status_recorder_integration.py -q` →
  **3 passed** (about 1.2 s); 5 consecutive repeat runs, 3 passed each time.
- The three source suites plus the new file together
  (`test_outcome_recorder.py`, `test_outcome_read_path_integration.py`, `test_execution_outcome_status_route.py`):
  **52 passed** (49 existing + 3 new).
- **Full suite** (`cd backend && python3 -m pytest tests -q`): **1569 passed, 1 warning in 130 s** (1566 baseline + 3
  new); the one warning is the existing starlette/anyio deprecation notice.
- **Mutation checks** (each reverted; `git status` showed only the new test file afterwards): making `_mark_retry` a
  no-op (1 failed: the `pending_retry` test); making the permanent-block path leave the status NULL (1 failed: the
  blocked test); the other two tests passed in each case, so the cases are independent.
- **Cleanup check:** `trades` and `strategy_outcomes` hold 0 rows after the new tests and after the full suite on this
  database; the test's cleanup is keyed on seeded `trade_id`s only.
- **Not covered / limits:** not run against a shared database that already contains unrelated qualifying trades
  (the deltas are designed for it, but this local database was empty at baseline); no entry-to-exit execution fixture
  and no live recorder worker or sweep (`record_trade()` is called directly); only one permanent-block reason is
  exercised.
<!-- END DELIVERY SECTION: execution-outcome-status-recorder-integration -->

<!-- BEGIN DELIVERY SECTION: outcome-status-doc-sync (docs + comments only; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-status-doc-sync`

Verified on `main` `7ab2522`. Documentation and comments only, so no pytest, PostgreSQL, `tsc` or browser run applies and
none was made.

- `git diff --check`: clean.
- **Code unchanged:** an AST comparison of `backend/app/schemas/performance.py` before and after, with every string
  constant masked, is identical (so only the docstring and `Field` description text moved). The module imports under
  Python with pydantic installed.
- **Corrected claims checked against source:** `OutcomeRecorder` exists and is the sole non-backtest writer
  (`trading_intelligence/outcome_recorder.py`; wired in `main.py` lifespan); it calls
  `capture_strategy_outcome_snapshots` for entry (`capture_entry`) and exit (`record_trade`) and writes
  `record_strategy_outcome_in_session`; it constructs `StrategyOutcome` with `is_backtest=False` and explicit
  `execution_mode`/`execution_venue` taken from the trade; `BacktestRunner` still calls `record_strategy_outcome()`.
  The route and panel exist (`execution-outcome-status` in `intelligence.py`; "Simulated outcome recording" in
  `ExecutionLifecyclePanel.tsx`), and the Live empty-state text cited in the correction matches `InfoTab.tsx`.
- **Cross-references checked:** §6.7.1 K and L headings, the `frontend-performance-panel-refresh` as-built note, and
  the named source files all exist. No new Markdown links were added.
- **Not covered:** no rendered-Markdown view.

**Further drift found, not fixed (report only):**
1. `docs/architecture/strategy-engine-design.md` line ~298 still says the live-path caller "still doesn't exist" and D17
   "remains open for that path" in the present tense (the paragraph is otherwise a decision-era record).
2. `strategy-engine-design.md` line ~717 checklist item says "No Execution Engine/Position Monitor exists yet" (dated #120
   entry; historical, but unmarked).
3. `backend/app/schemas/performance.py` line ~133: the `origin` comment says the `trades` table is "not yet built"; it
   exists (`models/execution_ledger.py`, migration 0012).
4. Same file: `backtest_run_id` and `is_backtest` descriptions say "live" (for example "None for every live trade") where
   "non-backtest (simulated)" is meant.
<!-- END DELIVERY SECTION: outcome-status-doc-sync -->

<!-- BEGIN DELIVERY SECTION: execution-panel-outcome-status (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `execution-panel-outcome-status`

Verified on `main` `4e40a79`, Node v22.22.2, `npm ci` in `frontend/`. Frontend and docs only: no backend, database or
broker was touched, so no pytest or PostgreSQL run applies.

- `npm run build` (`tsc -b && vite build`): clean on the untouched base and clean after the change, 103 modules
  transformed; only the existing Vite chunk-size (>500 kB) advisory appeared.
- **Behavior check (not committed).** The repo has no frontend test runner and none was added; the section was
  exercised with a throwaway jsdom + React `act` script kept outside the repository (esbuild bundle of the real panel,
  controllable mocked `fetch`, stub `WebSocket`): **29 checks passed**. It covered: no request while collapsed; the
  request URL `/intelligence/execution-outcome-status?limit=50` on expansion; loading state; the explanatory wording
  (closed simulated auto trades, not a live portfolio or real-money result, recovery, logs-not-API, not a live feed);
  server row order preserved; `null` → "Pending" with "no outcome link"; "Pending retry", "Blocked"; "Recorded" with
  "outcome linked" and the id not printed; the literal `"pending"` and `""` shown as `Unexpected status ...` and not
  as recorded; the exact counts line; the "Showing the N most recently changed of M" note only when the list is
  shorter than the population; Refresh refetching and showing loading; empty state and no counts block; HTTP-500 and
  rejected-fetch errors, neither shown as empty; an older success after a newer Refresh ignored; an older failure
  after a newer success ignored; an older success not masking a newer failure; a response after collapse harmless;
  and no React `console.error`.
- **Mutation checks** (each reverted; tree restored and re-run 29/29): removing the `active` guards in the new
  section (3 failed: the three stale-response checks); folding unexpected statuses into "Recorded" (2 failed);
  labelling `null` something other than "Pending" (1 failed).
- **Not covered:** no real browser, no visual/Tailwind review, and no run against a live backend with real
  `OutcomeRecorder` rows (fixtures are hand-built from §K's documented response). The wire shape was checked by
  reading `_fetch_execution_outcome_status` and §K, not by a call. A malformed response (missing `counts` or
  `trades`) is not guarded and would throw in render, like the sibling sections. Manual check for reviewers: with the
  backend up, expand the Execution panel, confirm the counts add up to the list size when under 50, press Refresh,
  then stop the backend and press Refresh to see the error line.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of this delivery and
  was restored before packaging.
<!-- END DELIVERY SECTION: execution-panel-outcome-status -->

<!-- BEGIN DELIVERY SECTION: execution-outcome-status-route (backend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `execution-outcome-status-route`

**Database target:** real local PostgreSQL 16 (Ubuntu package) on `localhost:5432`, database `trading_workspace`, user
`trading`, created fresh and migrated with `alembic upgrade head` (revision `0016`) before the runs. No external or
production database and no broker touched. Python 3.12.3. Base `main` `2d11108`.

- New file: `cd backend && python3 -m pytest tests/test_execution_outcome_status_route.py -q` → **22 passed**.
  Covers: population isolation (rejected, open, closing, NULL status, backtest/paper/live, manual all excluded;
  undeclared mode/origin/decision/status query params ignored); all five buckets with exact counts, incl. unexpected
  values (`"pending"`, `""`, an unknown label, upper-case `BLOCKED`) under `other`; SQL NULL returned as `null` and
  counted `pending`; counts independent of `limit`; buckets sum to the population; stored status returned verbatim;
  `updated_at` descending ordering, `trade_id` descending tie-break, a `limit` cutting through a tie, default 50 with
  51 rows, limit 0/-1/101/abc → 422 and 1/100 accepted; a truly empty population (scratch schema with an empty
  `trades` table) → all-zero counts and `[]`; exact key sets and types, UUID strings, a real linked `outcome_id`,
  microsecond-exact UTC `updated_at` (also with a non-UTC `Asia/Dhaka` session); GET leaves the row untouched and
  POST/PUT/PATCH/DELETE → 405; a blocked helper does not block `/health` (event-loop offload).
- **Mutation checks** (each reverted; tree restored and re-run green): dropping the `origin` filter (3 failed),
  dropping the `status` filter (2 failed), dropping the `trade_id` tie-break (2 failed), making `other` ignore
  unexpected values (2 failed), removing UTC normalisation (1 failed), calling the helper synchronously instead of via
  `asyncio.to_thread` (1 failed). Not cleanly mutated: "counts computed from the limited list" (my mutation did not
  compile); `test_route_counts_are_independent_of_limit` compares `limit=2` with `limit=100` on 5 rows and is the guard.
- **Full suite** (`cd backend && python3 -m pytest tests -q`): **1566 passed in 175 s** (1544 baseline + 22 new), 1
  existing deprecation warning. The new tests leave no `trades`/`strategy_outcomes` rows and drop their scratch schema.
- **Not covered / limits:** only the route was tested; the recorder is not run (rows are hand-inserted), so the
  end-to-end "recorder writes status → route reports it" path is not exercised. The `REPEATABLE READ` snapshot is not
  proven by a concurrent-writer test (it is set and the read succeeds; it makes no behavioral difference in the
  single-connection tests). Only one test DB exists here, so counts in a DB with other qualifying trades are checked as
  deltas, not absolutes. No frontend, `tsc` or build was run (none touched).
<!-- END DELIVERY SECTION: execution-outcome-status-route -->

<!-- BEGIN DELIVERY SECTION: frontend-world-view-simulated-column (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `frontend-world-view-simulated-column`

Verified on `main` `c91ad01` (branch `frontend-world-view-simulated-column`), Node v22.22.2, `npm ci` in `frontend/`.
Frontend and docs only: no backend, database or broker was touched.

- `npx tsc -b`: exit 0, no diagnostics.
- `npm run build` (`tsc -b && vite build`): built (`✓ built`); only the existing Vite chunk-size (>500 kB) advisory appeared.
  (`frontend/tsconfig.tsbuildinfo`, which `tsc -b` rewrites, was restored and is not part of the change.)
- **Behavior check (temporary; not committed, no dependency added to the repo).** The real `WorldViewSummary` and
  `useWorldView` were bundled with the installed esbuild (via a temporary exported copy of `InfoTab.tsx`, deleted
  afterwards) and driven with jsdom + React `act` against a mocked `fetch`: **16 checks passed, 0 failed.** Covered:
  loading state and one request to `/intelligence/world-view`; both columns empty ("Simulated execution" /
  "No simulated-execution trades recorded yet." and "Backtest" / "No backtest trades yet."); simulated populated +
  backtest empty; backtest populated + simulated empty; both populated independently; no "Live"/"No live trades yet"
  wording in the columns; subtitle says not real-money; manual Refresh refetches and shows Loading; a failed fetch
  renders the error (not an empty message) and Refresh recovers.
- **Mutation check:** restoring the old label and old empty message failed exactly 3 checks; discarded afterwards.
- **Not covered:** no committed frontend test runner exists, so the check is not part of the repo; the Portfolio block
  and the backend envelope were not re-tested (unchanged); no visual/browser inspection.
<!-- END DELIVERY SECTION: frontend-world-view-simulated-column -->

<!-- BEGIN DELIVERY SECTION: outcome-read-path-integration (backend tests; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-read-path-integration`

**Database target:** real local PostgreSQL 16.15 (Ubuntu package) on `localhost:5432`, database `trading_workspace`,
user `trading` (superuser), no external or production database. For the final run the database was dropped, recreated
and migrated with `alembic upgrade head` (revision `0016`, `strategy_outcomes` empty) before the suite. Ledger rows and
outcomes are real; only `capture_strategy_outcome_snapshots` is stubbed. No broker touched.

- Baseline on `fb9462a` before changes: focused recorder/performance/World View files 48 passed.
- New: `python -m pytest tests/test_outcome_read_path_integration.py -q` → 1 passed.
- Mutation checks (each reverted afterwards, tree clean): removing the aggregate `is_backtest` filter, UTC hour
  bucketing, removing the route's `is_backtest` filter, swapping World View's populations, and changing the session
  JSONB key each failed the test with a specific assertion.
- Focused set: `tests/test_outcome_read_path_integration.py tests/test_outcome_recorder.py tests/test_world_view.py
  tests/test_performance_queries.py tests/test_performance_analytics_routes.py
  tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py` → 67 passed.
- **Full suite** (`python -m pytest tests -q` from `backend/`): 1544 passed in ~140 s on the freshly wiped DB
  (1543 baseline + 1 new); also 1544 passed on the pre-wipe DB. The test leaves no `TEST_OUTCOME_RECORDER` rows behind.
- Frontend (comment-only edits): `npx tsc -b` exit 0; `npm run build` built.
- **Shared-DB behavior:** World View's envelope is system-wide with no filter, so its assertions are before/after
  deltas; nothing deletes rows the test did not create.
- **Not covered:** the recorder's own writes are not re-tested here (see `test_outcome_recorder.py`); the simulated
  rows are all wins because the reused ledger seed always produces the same prices, so win-rate variety comes from
  the backtest rows; no full app-lifespan run and no real-market data.
<!-- END DELIVERY SECTION: outcome-read-path-integration -->

<!-- BEGIN DELIVERY SECTION: frontend-performance-panel-refresh (frontend + docs; integrate alongside other sections, do not merge them) -->
# TESTING — `frontend-performance-panel-refresh`

Verified on `main` `5079a08` (branch `frontend-performance-panel-refresh`), Node v22.22.2, `npm ci` in
`frontend/`. Frontend and docs only: no backend, database or broker was touched.

- `npx tsc -b`: exit 0, no diagnostics (before and after changes).
- `npm run build` (`tsc -b && vite build`): exit 0, 103 modules transformed; only the existing Vite
  chunk-size (>500 kB) advisory appeared.
- **Behavior check (not committed; no dependency added to the repo).** The frontend has no test runner,
  so the real `StrategyPerformanceSummary` and `usePerformanceAnalytics` were bundled with the
  already-installed esbuild and driven with jsdom + React `act` against a controllable mocked `fetch`
  (kept outside the repo, shipped beside the zip as `performance-panel-behavior-check/`): **50 checks
  passed, 0 failed.** Covered: default Backtest and request URLs (`is_backtest`, `strategy_name`);
  loading → genuine empty; the four Live/Backtest × All/strategy empty messages, none claiming that no
  Execution Engine exists; fetch failure distinct from empty, and Refresh recovering from it; rows
  never shown under another population's or strategy's label (while pending, and on the first render
  after a switch); rapid filter changes resolving out of order (older success, older failure, and an
  older success after a newer failure are all ignored); repeated `refetch()` ordering; same-population
  Refresh keeping rows while in flight; failed refresh clearing rows; late response after unmount
  harmless; no unexpected React `console.error`.
- **Mutation checks:** removing the request-id guard failed exactly 6 checks (the older-response
  cases); removing the render-time population match failed exactly 6 checks (cross-population and
  first-render cases); both mutations were discarded afterwards.
- **Not covered:** no real browser, no Tailwind/visual review, no run against a live backend with real
  `OutcomeRecorder` rows (fixtures are hand-built). Manual check: with the backend up, open the Info tab,
  switch Live/Backtest and strategy quickly, confirm the label always matches the rows, press Refresh,
  then stop the backend and Refresh — an error (not "no data") must show.
- **Housekeeping:** `tsc -b` rewrites tracked `frontend/tsconfig.tsbuildinfo`; it was restored and is
  not part of this delivery.
<!-- END DELIVERY SECTION: frontend-performance-panel-refresh -->

<!-- BEGIN DELIVERY SECTION: outcome-recorder-zero-position-recovery (backend-only; integrate alongside other sections, do not merge them) -->
# TESTING — `outcome-recorder-zero-position-recovery`

**Database:** real local PostgreSQL 16 (Ubuntu package), database `trading_workspace`, user `trading` (superuser),
created fresh for this run and migrated with `alembic upgrade head` (through `0016`). No mocks for the ledger;
only `capture_strategy_outcome_snapshots` is stubbed, as in the existing recorder tests. No broker or external DB.

- **Reproduced first, on unmodified `main` `c7c8550`:** new tests, 8 failed / 18 passed in
  `tests/test_outcome_recorder.py` (zero-position trade stayed unblocked after startup scan and after sweep; a
  3-position trade was returned 3 times; page walks lost or repeated candidates for batch sizes 1, 2, 3; sweep
  rotation never queued the zero-position trades).
- **After the fix:** `python -m pytest tests/test_outcome_recorder.py -q` → 26 passed.
- **Full suite:** `python -m pytest tests -q` (from `backend/`) → 1543 passed in ~120 s.
- **New coverage:** no event + zero positions blocked by startup scan; same via sweep for a trade that appears
  after start, and a blocked trade is not rediscovered; multiple position rows give one bounded candidate and
  `blocked`; NULL `closed_at` discovered and blocked; each candidate appears exactly once, in key order, across page
  boundaries for batch sizes 1, 2, 3; `batch_size=1` startup scan records the ordinary trade once (a later sweep
  adds no second outcome) and blocks the others; sweep rotation wraps. Every blocked case asserts no
  `strategy_outcomes` row and no position created.
- **Not covered:** the NULL-`closed_at` ordering when other unrelated rows already exist in a shared dev database
  (tests assert only on their own trade ids); no run against the app's full startup wiring beyond the recorder's `start()`.
<!-- END DELIVERY SECTION: outcome-recorder-zero-position-recovery -->

<!-- BEGIN DELIVERY SECTION: frontend-outcomes-simulated-reader (frontend-only; integrate alongside the backend task's section, do not merge the two) -->
# TESTING — `frontend-outcomes-simulated-reader`

Verified on `main` `9c69d91` (branch `frontend-outcomes-simulated-reader`), Node v22.22.2, `npm ci`
in `frontend/`. Frontend only: no backend, database or broker was touched, so no backend or
PostgreSQL run applies to this delivery.

- `npx tsc -b`: baseline before changes clean, and clean after (exit 0, no diagnostics).
- `npm run build` (`tsc -b && vite build`): succeeded, 103 modules transformed. Only the existing
  Vite chunk-size (>500 kB) advisory appeared.
- **Behavior check (not committed).** The frontend has no test runner, and adding one is outside this
  task's scope, so the hook and section were exercised with a throwaway jsdom + React `act` script kept
  outside the repository, against a controllable mocked `fetch`: 23 checks passed. It covered:
  the request URL (`is_backtest=false&limit=10`, never `is_backtest=true`); loading, empty,
  populated and first-load-error states (an error is never shown as the empty state); Refresh re-request;
  a failed refresh keeping the loaded rows and showing the banner; a later success clearing it; mode/venue
  and "N snapshots unavailable" rendering; an older success not replacing a newer refresh's rows;
  an older failure after a newer success being ignored; an older success not masking a newer
  failure; a response after unmount being harmless; and no unexpected React `console.error`.
  As a mutation check, disabling the request-id guard made exactly the three race checks fail
  (20 passed, 3 failed); the guard was restored afterward.
- **Not covered:** no real browser, no visual/Tailwind review, and no run against a live backend
  with real `OutcomeRecorder` rows (the fixtures are hand-built and mirror the Pydantic contract). Manual
  check for reviewers: with the backend up, open the Info tab, confirm the Simulated label,
  press Refresh, then stop the backend and press Refresh again — the rows must stay with a
  "Refresh failed" banner.
- **Housekeeping:** `tsc -b` rewrites the tracked `frontend/tsconfig.tsbuildinfo`; it is not part of
  this delivery and was restored before packaging.
<!-- END DELIVERY SECTION: frontend-outcomes-simulated-reader -->

# TESTING — decision #186, `outcome-recorder-contract` (EX-12 option a)

Database target: isolated local PostgreSQL 18.6 in `/tmp/atos_pg2`, port 5545,
database `trading_workspace`, migrated through Alembic `0016`. Only test
fixtures and migration records were written there. The project worktree's
configured production or broker accounts were not used.

- Focused writer/recorder/startup tests: 33 passed against real PostgreSQL.
  They exercise the NULL-snapshot CHECK and outer rollback; entry capture and
  failure; partial fills/reductions, VWAPs, known and unknown commissions,
  closing-order exit reason, missing evidence/R/exit reason, duplicate and
  concurrent recorders, injected insert-before-link failure and retry, missed
  event recovery by startup scan and sweep, late snapshots, and execution
  startup isolation. The full suite also covers Backtest Runner persistence.
- Full backend suite with the database session timezone inherited from the
  local Asia/Dhaka server: 1,525 passed, 2 failed. Both existing failures
  compare UTC test timestamps to PostgreSQL-returned `+06:00` timestamps.
  Rerunning those two with `PGTZ=UTC` passed without changing assertions.
- Full backend suite with `PGTZ=UTC` after the final recorder validation and
  test additions: 1,534 passed, 0 failed, 186,731 deprecation warnings
  (106.31 s). PostgreSQL schema and contract assertions were not weakened.

<!-- Previous delivery record retained below. -->

# TESTING — `execution-status-doc-sync`

Documentation delivery plus a test-only fix. The first pass changed no code or test and was
verified at `eca3573`; the follow-up (below) was verified at `main` `79650ad`, which contains
that first pass (`c2ea927`) and the later `79650ad` governor-evidence commit. Local PostgreSQL 16
(`trading_workspace`, timezone UTC) created for this run and migrated with `alembic upgrade
head` through `0016`; no external, broker or production database was contacted.

**Claim verification.** A script asserted, by regex against the source at `eca3573`, every
positive claim the updated docs make: calendar years `{2026, 2027, 2028}`; EOD fail-closed
`UnsupportedEodCalendarError`; window `[close − lead, close)`; lead default 60 and range
1..900; config rejects a non-`simulated` `execution_mode`; authorizer rule 0 and its
`OpportunityCreated` subscription; `WAIT_OUTSIDE_REGULAR_SESSION` and the ledger's
`is_regular_session(now)` check; first-fallback-wins (`FALLBACK_ALREADY_STORED`); the venue's
session guard; `IBKRAdapter.place_order()` raising `NotImplementedError`; the monitor emitting
`eod_flatten`; `main.py` restoring observations and publishing the World View portfolio reader;
startup status tracking; the `execution-exit-requests` route; migration `0016`. All passed.
Negative claims checked by search: `record_strategy_outcome` has one real caller
(`backtest_runner/runner.py:414`); no `class OutcomeRecorder`; eight `system-design.md` §8
file names (`order_manager.py`, `execution_router.py`, `mode.py`, `approval_queue.py`,
`governor.py`, `position_sizing.py`, `risk_rules.py`, `monitor.py`) do not exist.

**Documentation checks.** `git diff --check` clean; every relative link in the three edited
docs resolves; grep found no remaining "EOD order path unbuilt", "no live Execution/Position
Monitor writer", "Not started as application modules" or "Portfolio State slot honestly"
text (the one remaining "monitor half only" is the deliberate historical label);
decision-log state re-checked (INDEX and the tail of `confirmed-decisions.md` both end at #185;
archive `161-184.md` plus #185 in the main log) and untouched.

**Test runs for the first pass (evidence for the claims, at `eca3573`; superseded for the
real-clock result by the follow-up below).**

- Focused (market clock, session window, monitor EOD, exit-ledger EOD Postgres and migration,
  EOD integration, protective session retry, entry lifecycle, main execution pipeline): **242
  passed**.
- Full backend suite at a fixed market-hours start (`faketime '2026-09-30 15:00:00'`, 11:00 ET):
  **1453 passed**, 1 pre-existing warning — the same count `simulated-protective-session-retry`
  recorded.
- Full backend suite on the real clock at 04:13 UTC (outside US regular hours): **1450 passed,
  3 failed**. The three failures are `tests/test_exit_ledger_postgres.py`
  `test_cancel_entry_then_reserve_one_close_and_retry_after_rejection`,
  `test_pre_submit_guard_rejects_new_entry_activity_and_unapplied_fill` and
  `test_database_rejects_second_active_close_for_same_position`. Cause: they use
  `datetime.now()` and the real `MarketClock`, and the session guard added at `eca3573` makes
  `prepare()` return `None` outside regular hours. All four tests in that file pass at the
  market-hours instant and when `is_regular_session` is forced true. Fixed in the follow-up
  below.
- An additional run forcing `is_regular_session` true for the whole suite was discarded: it
  breaks tests that deliberately assert out-of-session behavior (42 failures) and says nothing
  about the documentation.

**Follow-up: time-independent `test_exit_ledger_postgres.py` (base `79650ad`).**

Change: `NOW` is fixed at `2026-09-16 15:00Z` (11:00 ET) and injected as the clock into every
`PostgresExitLedger` in the file; one guard test added. See `CHANGES.md` for the consistency
check of `opened_at`, `retry_after`, `trigger_ts` and `venue_ts` against that instant.

- `tests/test_exit_ledger_postgres.py`, real clock at 06:23 UTC (outside US hours):
  **5 passed** (4 existing + `test_fixed_now_is_a_regular_session`).
- Same file under `faketime` at other instants, all **5 passed** each: Wed 2026-09-30 15:00Z
  (11:00 ET, in session); Wed 2026-09-30 13:30Z (09:30 ET, at the open); Wed 2026-09-30 22:30Z
  (after hours); Sat 2026-10-03 15:00Z (weekend); Fri 2026-11-27 19:00Z (after the 13:00 ET
  half-day close); Wed 2026-09-16 15:00Z (the fixed instant itself).
- Control: the pre-fix file at 2026-09-30 22:30Z: **3 failed, 1 passed** — the same three tests,
  confirming the fix addresses the cause.
- Focused set (market clock, session window, monitor EOD, exit-ledger Postgres, EOD Postgres and
  migration, EOD integration, protective session retry, entry lifecycle, main execution
  pipeline): **247 passed**, 1 warning. (The first pass's 242-test set plus this file's 5
  tests.)
- **Full backend suite on the real clock (outside US hours), at `79650ad`, two runs:**
  - 06:33 UTC: **1512 passed, 1 failed** —
    `tests/test_feature_engine.py::test_get_snapshot_reflects_latest_computed_values`
    (`KeyError: '__TEST_FE_SNAP_B__'`). That test publishes two candles and then waits a fixed
    `asyncio.sleep(0.1)` before reading the snapshot, so it is timing-sensitive under full-suite
    load. It passed alone, in its whole file (81 passed), and on pristine `origin/main` with
    these changes stashed, and at a market-hours instant. It does not touch the ledger, clock
    or config; not changed here.
  - 06:36 UTC, immediately re-run, no code change: **1513 passed, 0 failed**, 1 pre-existing
    warning.
- Full backend suite at a fixed market-hours start (`faketime '2026-09-30 15:00:00'`):
  **1513 passed, 0 failed**.
- `git diff --check`: clean.

Remaining known issue: the `test_feature_engine.py` snapshot test above is intermittently
flaky (one failure in three full runs at `79650ad`; the fix would be to await the event
instead of sleeping, in a separate change). Nothing else fails. The exit-ledger fix is test-only
and the `config.py` edit is a comment, so no production code path changed.

Not covered: no real IBKR/paper path (none exists); doc rows outside the execution path
(Phases 1–4 roadmap bullets, most of `system-design.md`, `strategy-engine-design.md`) were not
re-audited; §6.7.1 rows A2–A4, A6–A9 and A11 and the §9 acceptance list were not re-verified.

---

# TESTING — `simulated-protective-session-retry`

Baseline: GitHub `main` `b6d1e57`, unchanged at the final fetch. Local PostgreSQL 16
(`trading_workspace`, timezone UTC) created for this run and migrated with
`alembic upgrade head` through `0016`; no external, broker or production database was
contacted. Reproduction rows were deleted afterwards.

New `backend/tests/test_simulated_protective_session_retry.py` — **32 tests**, real
PostgreSQL and injected clocks; the last three use the real `ExecutionEngine`,
`PostgresExitLedger`, Portfolio State and `SimulatedVenue`, counting `place_order`/
`cancel_order` calls:
ordinary-close boundaries for stop and target (16:00 ET minus 1 µs submit; 16:00 wait; overnight
wait; 09:30 ET minus 1 µs wait; 09:30 submit), the 13:00 ET half-day (2026-11-27) and its next
open, weekend and holiday; rejected at the bell then overnight passes with no new order, ID or
`exit_attempt` growth, exactly one permitted attempt at the open, reuse of the same reserved ID,
and the normal 5 s delay applying again; original stop/target observed after hours; submitted
close and dispatch-marked EOD close staying exclusive; EOD fallback held then placed once with
its dispatch marker; EOD-inside-window control; claim after the bell (no marker, reservation
reused) including the fallback at the next close; resume with changed committed quantity, an
unreceipted fill, a working entry and a flat position; engine tests for repeated overnight
passes (one venue call in total before the open, no warnings), an after-hours original, and the
bell ringing between prepare and claim.

Regression guard: with the guard neutralised, 22 of the 32 fail; restored, 32 pass.

Existing tests: four tests in `test_exit_ledger_eod_postgres.py` needed their fallback placement
time moved to the next open (see CHANGES.md); all other existing tests unchanged.

Focused run (exit ledger, EOD ledger, new file, venue, EOD integration, engine): 109 passed.
Full backend suite: **1453 passed**, 1 pre-existing warning, run after the final code change
(1421 on the previous baseline + 32 new). Only docs were edited afterwards.

Not covered: no real-IBKR/paper path; no timer-driven resume (the worker's own poll is the only
trigger); ledger vs venue clock disagreement is reasoned, not tested; calendar years outside
2026-2028 follow MarketClock's documented unverified behavior.

---

# TESTING — `market-clock-2027-2028-coverage`

Baseline: clean GitHub `main` `0390ef1` (unchanged at packaging). Tests ran on a scratch
PostgreSQL 16 (`trading_workspace`, timezone UTC) migrated with `alembic upgrade head`
through `0016`; no external database was contacted.

New/extended pure tests (no database): `test_market_clock.py` carries an independent copy of
the NYSE schedule and asserts `is_holiday`/`is_half_day` for **every day** of 2026–2028; checks
internal consistency (weekday-only, holidays and early closes disjoint);
`has_calendar_for_year` true for exactly 2026–2028 over 2000–2099; session membership across
2027 closures, the ordinary 2027-07-02/12-23 days, the 2027-11-26 and 2028-07-03/11-24 13:00
closes (12:59 open, 13:00 closed, bounds/`is_regular_session`), no 2028 New Year's holiday,
DST from UTC instants (2027-03-14, 2028-11-05), and `next_session_boundary` over the observed
holidays and the 2027→2028 year end. `test_session_window.py` adds hand-written UTC
close/flatten instants for 18 days (EST/EDT either side of every 2027/2028 DST shift, early
closes, Dec 31 2027, Jan 3 2028, Dec 29 2028), `None` for all 19 covered holidays plus
weekends, exact inclusive/exclusive microsecond boundaries on the three early-close days,
ET-entry-day year-boundary cases (both directions), fail-closed for 2025, 2029 and 2030 (the
2025-12-31 22:00 ET / 2026-01-01 03:00Z case included), and Backtest Runner
`regular_session_close_utc` parity plus 13:00/16:00 ET check on every trading day of 2026,
2027 and 2028 (251 each).

Regression guard: with the pre-change `market_clock.py` restored, these two files fail
(53 failed, 38 passed); with this change, 91 passed.

Full backend suite: 1361 collected on clean `main`, 1421 with this change (+60 net), **1421
passed**, zero regressions. Directly affected pre-existing tests
(`test_position_monitor_eod.py`, `test_exit_ledger_eod_postgres.py`) pass with 2029 as the
unsupported year. No frontend, migration, Execution Engine or exit-ledger code changed.

---

# TESTING — `simulated-eod-flatten-integration`

Baseline: clean GitHub `main` `f9b6d77`; decision #185 and migration `0016` were
already committed. Tests used an isolated PostgreSQL 18.6 cluster and database
`agentic_eod_test` on localhost port 5433, migrated with `alembic upgrade head` through
`0016`. The test database session timezone was set to UTC. The first full-suite attempt
used the cluster's Asia/Dhaka default and produced two unrelated timestamp-format
assertion failures in backtest/outcome route tests; rerunning after setting this isolated
database to UTC passed. No production or external database was contacted.

New `test_simulated_eod_integration.py` exercises the real EventBus, Position Monitor,
Execution worker, PostgreSQL ledgers, Portfolio State receipt worker and SimulatedVenue:
EOD observation/order inside the window, one accepted order with no synthetic fill,
real after-hours fill and closure, no tick/missed window, active EOD followed by a stored
stop fallback, protective stop before EOD, actual venue rejection and one delayed retry, partial fills with the
remaining quantity held by the same close, unfinished-entry cancellation with deduped fill
evidence, ordered replay after an injected observation
failure, and three real FastAPI lifespan starts (retained venue recovery with hydrated
expired EOD/fallback slots, then fresh venue discrepancy block). Two further lifespan
starts verify that a proven-unsent approved EOD reservation is sent only inside its
stored window and cancelled at the deadline. Two fault cases prove that a venue exception
or lost status commit after an EOD dispatch claim cannot resend or replace that order.
Shared injected wall
time drives monitor, ledger and venue session boundaries in these tests.
`test_reconciliation.py` adds a missing dispatch-marked approved-close report that blocks
startup and a retained report that advances the same ID after a lost status commit.

§6.6 acceptance-case map (existing focused tests remain part of this delivery's
verification; `test_simulated_eod_integration.py` is abbreviated as `integration`):

| Case | Test evidence |
|---|---|
| A1 | `test_position_monitor_eod.py::test_pulse_boundaries`, `test_repeated_pulses_create_one_eod_with_stable_label`; `integration::test_eod_attempt_then_real_late_fill_closes_once` |
| A2 | `test_position_monitor_eod.py::test_missing_tick_creates_nothing_and_logs_bounded`, protective stop/target tests; `integration::test_no_tick_no_order_and_missed_window` |
| A3 | `test_exit_ledger_eod_postgres.py::test_expiry_persists_even_when_entry_or_receipt_delays_the_reservation`; `integration::test_working_entry_is_cancelled_and_its_existing_fill_is_deduped_before_eod_close` |
| A4 | `test_exit_ledger_eod_postgres.py::test_unsent_approved_reservation_is_cancelled_atomically_at_the_deadline_and_never_reused`, `test_claim_after_deadline_never_sends_and_cancels_the_unsent_reservation` |
| A5 | `test_exit_ledger_eod_postgres.py::test_terminal_eod_inside_window_retries_eod_on_a_new_id_and_duplicates_add_nothing`; `integration::test_venue_rejection_keeps_actual_reason_and_retries_once_before_close` |
| A6 | `test_exit_ledger_eod_postgres.py::test_terminal_eod_at_or_after_close_expires_and_only_a_fallback_can_place` (rejection/cancellation, stop/target parametrization) |
| A7 | `test_exit_ledger_eod_postgres.py::test_submitted_order_stays_working_past_close_and_fallback_cannot_place`; `integration::test_eod_attempt_then_real_late_fill_closes_once` |
| A8 | `integration::test_eod_attempt_then_real_late_fill_closes_once`; `test_simulated_venue.py` delayed-tick acceptance behavior |
| A9 | `test_exit_ledger_eod_postgres.py::test_partial_fill_keeps_one_active_order_then_fallback_sizes_the_settled_remainder`, `test_partial_cancellation_without_fallback_is_dormant_and_full_closure_makes_fallback_inert`; `integration::test_partial_venue_fills_keep_one_close_until_real_remaining_fill` |
| A10 | `test_exit_ledger_eod_postgres.py::test_partial_cancellation_without_fallback_is_dormant_and_full_closure_makes_fallback_inert`; fill dedupe in `test_fill_ledger_postgres.py` |
| A11 | `test_position_monitor_eod.py` pulse/tie/queued-event cases; `integration::test_protective_stop_before_pulse_prevents_eod_order`, `test_eod_attempt_then_real_late_fill_closes_once` |
| A12 | `test_position_monitor_eod.py` pending/release cases; `integration::test_failed_eod_commit_retains_both_ordered_slots_until_replay` |
| A13 | `test_position_monitor_eod.py` pre-entry/reopened/future/invalid tick cases; `test_exit_ledger_eod_postgres.py::test_invalid_eod_observations_are_rejected_and_never_stored` |
| A14 | `test_position_monitor_eod.py` monotonic/equal-time cache, delayed candle, queue-order cases |
| A15 | `test_exit_ledger_eod_postgres.py::test_dispatch_marked_approved_close_at_close_is_uncertain_not_unsent`; `integration::test_claimed_eod_call_is_never_blindly_resent_after_uncertainty`; `test_reconciliation.py::test_dispatch_marked_approved_exit_without_venue_report_blocks_recovery`, `test_dispatch_marked_approved_exit_with_retained_venue_ack_recovers_same_id` |
| A16 | `test_exit_ledger_eod_postgres.py` expiry, dormant, fallback, unsent, submitted and partial cases; `integration::test_real_lifespan_eod_and_fresh_venue_restart_block` and `test_lifespan_revalidates_proven_unsent_eod_reservation` verify retained venue, persisted expiry, inside/outside window revalidation and hydrated slots before subscription |
| A17 | `integration::test_real_lifespan_eod_and_fresh_venue_restart_block`; `test_reconciliation.py::test_approved_exit_is_not_sent_into_missing_venue_position` |
| A18 | `test_session_window.py`, `test_position_monitor_eod.py::test_half_day_window_uses_13_00_et`, `test_exit_ledger_eod_postgres.py::test_advance_expiry_recovers_from_stored_bounds_and_config_or_clock_cannot_reopen_it` |
| A19 | `test_exit_ledger_eod_postgres.py` concurrent observers/prepares/claims, guard and receipt tests; `integration::test_failed_eod_commit_retains_both_ordered_slots_until_replay` |
| A20 | `test_exit_ledger_eod_migration.py`, `test_execution_exit_requests_route.py`, `test_execution_startup_status_route.py`, `integration::test_real_lifespan_eod_and_fresh_venue_restart_block` |

Deterministic tests reproduce A15's two persisted crash states rather than killing the
Python process at the exact instruction boundary. Retained venue evidence is exercised by
reusing a `SimulatedVenue` book across lifespan starts; this is not a durable broker and
cannot prove recovery from an external production venue. The fresh-venue restart uses a
new actual `SimulatedVenue` instance. No browser click-through or real market feed was used.

Validation: focused reconciliation/integration tests **21 passed**; full backend suite
**1361 passed, 0 failed**; `npx tsc -b` passed; `npm run build` passed (Vite's existing
large-chunk advisory). `git diff --check` passed. These counts include the final test
additions and are recorded after the final rerun below.

Package: `simulated-eod-flatten-integration.zip`, root-relative files listed in the zip.

---

# TESTING — `simulated-eod-exit-request-visibility`

**Verified against Task 2:** GitHub `main` `c1d09e4` ("Simulated eod ledger handoff",
`simulated-eod-ledger-handoff`, migration `0016`, parent `0015`). Its model, migration and
exit ledger are byte-identical to the Task 2 zip that was delivered. `main` had no newer
commit at packaging. Separate worktree; untouched `c1d09e4` collects 1335 backend tests.
Local PostgreSQL 16, wiped and recreated, `alembic upgrade head` (`0016`) before the run.

- `backend/tests/test_execution_exit_requests_route.py` (real PostgreSQL, ORM-inserted rows
  through Task 2's model so a wrong column name or CHECK fails here): **25 → 37 tests**.
  The two exact-key-set assertions now include the six new fields; the legacy target-row
  test also asserts all six are JSON `null`. New tests cover: legacy stop row unchanged with
  all-null EOD fields; original EOD request (no expiry, no fallback); expired EOD without
  fallback; EOD with a stored `stop` and `target` fallback before expiry; expired EOD with
  fallback (every field set); fallback price exact decimal strings (`12345678901.123456`,
  `0.100000`); an ended window with no recorded expiry stays `eod_expired_at: null` (no
  clock); ordering follows the original `trigger_ts` then `position_id`, not the fallback;
  mode, exact-symbol and `limit` apply to EOD rows; mixed legacy and EOD rows share one
  shape; a closed position keeps its stored EOD state. The existing worker-thread and
  event-loop test still passes unchanged.
- Mutation checks (temporary, reverted): forcing `eod_expired_at` to `None` failed 3 tests;
  converting the fallback price to `float` failed 4.
- Frontend: repo has no test runner, so behaviour was checked with a scratch jsdom harness
  (not shipped) rendering the real panel with a mocked `fetch`: **28/28** — legacy rows and
  order preserved, every EOD/fallback/null combination, exact prices, older-backend rows
  with missing keys, loading/empty/error, one request on expand and none while idle, only
  the Refresh button, stale Refresh response discarded, caveat text present. Mutations
  (EOD block on every row; unrecorded expiry shown as expired) failed 2 and 3 checks.
- `npx tsc -b`: clean. `npx vite build`: succeeds, 103 modules (same as baseline).
  `frontend/tsconfig.tsbuildinfo` is rewritten by `tsc -b`; it was reverted and is not in
  the package.
- Full backend suite on the wiped database: **1347 passed, 0 failed, 0 skipped** (1335 on `c1d09e4` + 12 new).

Not covered: no running system writes EOD rows yet (ledger unwired), so nothing here shows
the panel against live EOD data; no browser/visual check; no frontend test runner exists.

Zip `simulated-eod-exit-request-visibility.zip` entries: `CHANGES.md`, `TESTING.md`,
`backend/app/api/routes/intelligence.py`,
`backend/tests/test_execution_exit_requests_route.py`, `frontend/src/services/api-client.ts`,
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`,
`docs/architecture/execution-engine-design.md`.

---

# TESTING — `simulated-eod-ledger-handoff`

Baseline: built at `1c927db`, then rebased onto GitHub `main` `5fc4dfb` (the Position
Monitor sibling, `simulated-eod-monitor-handoff`) after Saqib reported git updated. Serial
check: no new migration (head still `0015`, so `0016` is free), decision logs unchanged.
File collision check: the sibling touched only `CHANGES.md`, `TESTING.md` and
`execution-engine-design.md` among my files; the design doc merged cleanly in separate
hunks and both entries were kept in `CHANGES.md`/`TESTING.md`. Untouched `5fc4dfb` collects
1291 tests. Local
PostgreSQL 16 (`trading` / `trading_workspace`), wiped and recreated, `alembic upgrade head`
(now `0016`) before the final run.

- New `backend/tests/test_exit_ledger_eod_postgres.py` — **41 tests**, real PostgreSQL,
  injected clock. Cover: derived bounds and ignored quantity; exact `flatten_at`
  inclusive / `close_at` exclusive; invalid label/price/timestamp/bounds/reason; holiday,
  half-day, DST and 2025/2027 dates; identity mismatch raises; flat and unknown positions;
  concurrent observers (one row, one first fallback); DB field-group and immutability
  enforcement; reservation on committed quantity and unsent reuse; a single dispatch
  claimant under 8 threads; unclaimed transitions refused; claim guards (entry activity,
  fill without receipt, quantity/identity/flat); approved-unsent cancellation and claim
  after the deadline; expiry despite entry/receipt delay; restart recovery, config change
  and backward clock; reject/cancel inside and at/after close; submitted-through-close;
  dispatch-marked uncertainty; partial fill `[3,7]` on 10 and late full closure;
  legacy-surface compatibility; and the merged monitor's real `ExitIntent` accepted unchanged.
- New `backend/tests/test_exit_ledger_eod_migration.py` — **3 tests** on a scratch database
  in an alembic subprocess: legacy rows survive `0015→0016`; evidence-free downgrade
  round-trips; downgrade is refused for an EOD row, a fallback/expiry, and a dispatch
  marker, leaving the revision at `0016`.
- Mutation checks (temporary, reverted): removing the dispatch marker write failed 11 tests;
  cancelling marked orders at expiry and ignoring stored expiry each failed tests.
- Existing: `test_exit_ledger_postgres.py`, `test_execution_exit_requests_route.py`,
  `test_execution_engine.py`, `test_main_execution_pipeline.py`, `test_reconciliation.py`,
  `test_session_window.py` — 75 passed unchanged before and after.
- Full backend suite on the wiped database, after the rebase onto `5fc4dfb`: **1335 passed,
  0 failed** (1291 on `main` + 44 new). Before the rebase, a first full run had 2 failures,
  both the hard-coded-`"0015"` head assertions described in `CHANGES.md`; fixed, then green
  at 1281 on the earlier base and again after the rebase.

Not covered: monitor timer, real lifespan, bus and `SimulatedVenue` delivery (A8, A11, A12,
A14, A17 and the venue halves of A7/A15/A16/A20), reconciliation against a marker-set
order, frontend. The full EOD path is not claimed to work.

Zip `simulated-eod-ledger-handoff.zip` entries: `CHANGES.md`, `TESTING.md`,
`backend/alembic/versions/0016_simulated_eod_exit_state.py`,
`backend/app/execution_engine/exit_ledger.py`, `backend/app/models/execution_ledger.py`,
`backend/tests/test_exit_ledger_eod_postgres.py`,
`backend/tests/test_exit_ledger_eod_migration.py`,
`backend/tests/test_authorization_ledger_postgres.py`,
`backend/tests/test_position_ledger_postgres.py`,
`docs/architecture/execution-engine-design.md`.

---

# TESTING — `simulated-eod-monitor-handoff`

Baseline: GitHub `main` `1c927db`, unchanged at the final fetch. Local PostgreSQL 16 (throwaway, migrated to head) was used; no live/broker/external database was touched.

- Focused: `pytest tests/test_position_monitor_eod.py tests/test_position_monitor_engine.py` — **63 passed**. Covers boundary pulses (flatten−1 s, flatten, close−1 µs, close, after; configured lead; half-day 13:00 ET), holiday/weekend/unsupported year, missing/pre-entry/exactly-at-open/five-minute-old/previous-day/future/naive/non-positive/NaN/inf ticks, reopened symbol, older and equal-time ticks (held and unheld paths), tick arriving after a queued pulse, candles never labelling or overwriting, delayed candle after EOD, stop/target precedence and same-tick tie, event-before-pulse ordering, repeated and coalesced pulses, pending/ack idempotence, callback failure, DB-failure (no release), release WINDOW_CLOSED/POSITION_CLOSED/INVALID, real timer, real EventBus, shutdown.
- Adjacent (real PostgreSQL): `test_main_execution_pipeline`, `test_execution_engine`, `test_exit_ledger_postgres`, `test_entry_lifecycle_wiring`, `test_exit_intents_route`, `test_position_monitor_portfolio_reader`, `test_session_window` — passed together with the focused files (115 passed).
- Full backend suite: `pytest -q` — **1291 passed**, 1 pre-existing warning (run before the docstring-only `__init__.py` edit).
- Two test-authoring mistakes fixed during the run (a bounded-log test stepping outside its window; a cache-validity test that also tripped the unchanged event-path stop) — no assertion weakened.
- Not covered: the frontend, the exit ledger accepting EOD, Execution consuming/acknowledging observations, restart hydration, and any real-clock timing at the actual close. Acceptance rows A1–A2, A11–A14 are covered at the monitor level only; the rest of A1–A20 remain for later tasks.

Zip: `simulated-eod-monitor-handoff/` — `backend/app/position_monitor/{__init__,engine,handoff}.py`, `backend/tests/{test_position_monitor_eod,test_position_monitor_engine}.py`, `docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.

---

# TESTING — `simulated-eod-flatten-contract` shared foundation (#185)

Baseline: clean local `main` and fetched GitHub `main` at `3ac975d`; fetched again
immediately before assigning decision #185. Decision index and open-log tails both ended
at #184; archive filenames ended at `134-160.md`. No database state was changed.
The open log exceeded 200 KB; following `docs/decisions/README.md`, decisions #161–#184
were moved byte-for-byte to `archive/161-184.md`, 24 index locations updated, and #185
left as the first entry in the new open log. Existing decision bodies were not edited.

Command: `backend/.venv/bin/pytest -q backend/tests/test_session_window.py
backend/tests/test_market_clock.py backend/tests/test_fill_simulator_and_gate.py
backend/tests/test_governor_config.py backend/tests/test_position_monitor_engine.py
backend/tests/test_simulated_venue.py` — **78 passed**. This includes every supported
2026 trading day's close parity, both half-days, DST-season dates, exact boundary
microseconds, covered closed days, unsupported years, invalid leads and existing
monitor/venue behavior. An initial mistaken expected trading-day count in the new test
(250 versus the calendar's 251) was corrected; the final run passed.

`git diff --check` and a scoped changed-file/decision/zip review passed. No PostgreSQL,
migration, full backend suite or frontend checks were needed for this pure foundation.
A1–A20 in the architecture document remain acceptance cases for subsequent executable
EOD work, not passing integration tests for this delivery.

Zip: `simulated-eod-flatten-foundation.zip` has root-relative entries:
`backend/app/core/config.py`, `backend/app/core/market_clock.py`,
`backend/app/core/session_window.py`, `backend/tests/test_session_window.py`,
`docs/architecture/execution-engine-design.md`, `docs/decisions/INDEX.md`,
`docs/decisions/confirmed-decisions.md`, `docs/decisions/archive/161-184.md`,
`CHANGES.md`, `TESTING.md`.

---

# TESTING — `simulated-eod-flatten-contract` revision (design only)

Baseline: GitHub `main` fetched before design work at `67ef81d`, matching local `main`;
initial tree clean. Final remote/state check performed before handoff.

Validation for this revision:

- Read AGENTS.md, decision process/index and relevant canonical decisions (#170, #175,
  #184), archive boundaries and execution architecture.
- Inspected Position Monitor/ports, exit ledger/model, Execution worker, SimulatedVenue,
  MarketClock, reconciliation, and relevant monitor/ledger/venue/clock/Execution/recovery
  tests, including the protective-exit real-lifespan test.
- Checked request/latch/dispatch/recovery transitions against actual methods. Corrected
  the old proposal's immutable-row dead end, timestamp guarantee and restart error claim.
- Checked diff whitespace, balanced proposal code fences, A1–A20 coverage/uniqueness,
  unchanged text outside the proposal, unchanged canonical decision records and docs-only
  changed-file scope. Results: passed.

No pytest, PostgreSQL, migration, runtime probe or frontend build was run: no implementation
changed. A1–A20 are future acceptance requirements, not executed/passing tests. Earlier
records below describe earlier deliveries and are not validation claims for this revision.

---

# TESTING — `outcome-recorder-contract`

## Environment

- Fresh `git clone` of GitHub `main`; code inspected at `f517834`. `origin/main` was re-fetched
  before packaging and had advanced to `3c23701` (documentation only). The tree was fast-forwarded
  locally and this delivery reapplied on top: no conflict, backend code unchanged. Local only;
  nothing merged or pushed.
- Docs-only delivery. **No repository test was run** (no code changed); the repository suite was not
  executed.
- Scratch environment (sandbox only, not shipped): PostgreSQL 16 installed with `apt`, a throwaway
  database, and `alembic upgrade head` run to `0015` against the real migrations. Backend
  requirements installed with pip. Nothing in the repo was modified by this.

## Results

- **Claim-to-source check (machine).** All 15 `backend/...` paths and all 14 `path:symbol` citations in
  §6.7.1 exist in the tree; code fences are balanced.
- **Scratch script against real PostgreSQL 16**, calling the real `record_strategy_outcome()`,
  `StrategyOutcomeRecord`, `Trade`, `Position` and `apply_fill` (six probes, all behaved as §6.7.1 states):
  - E1: a row with a NULL exit snapshot **and** a reasons dict is rejected by
    `ck_strategy_outcomes_null_snapshot_has_reason`, because the current writer drops
    `snapshot_missing_reasons` (A6b).
  - E2: a full simulated outcome stores `execution_mode = simulated`, `execution_venue = simulated`,
    `is_backtest = False`, `schema_version = 2`.
  - E3: the same `opportunity_id` written twice gives **two** rows (A6c).
  - E4: the proposed link transaction (scratch SQL, not application code): a fault between insert and
    link rolls back to zero outcome rows; the first real attempt records one and sets the link; a
    duplicate returns `already_recorded` and inserts nothing.
  - E5: two `positions` rows for one `trade_id` are accepted (A9).
  - E6: replay of 2 opens and 2 reductions, one with `commission = NULL`, through `apply_fill` gave
    status `closed`, avg price `100.4`, entry qty 100, exit qty 100, exit VWAP `99.9`, gross `-50.0`,
    `fees = None`, `unknown_fee_count = 1`. This confirms section B's derivations and C2's
    NULL-commission rule.

## Not covered

- The proposal itself is untested by construction: no `OutcomeRecorder`, no same-session
  `record_strategy_outcome()` variant, no entry-snapshot hook and no persisted `evidence` exist. E4
  proves the locking and rollback mechanics with plain SQL, not the recorder.
- Concurrent workers (two processes racing the row lock) were reasoned about, not run.
- `SimulatedVenue` commission behavior (A10) and the exit-reason plumbing were read, not executed.
- Nothing was checked against a running app or a live event bus.

## Package

`outcome-recorder-contract.zip` contains exactly three files, root-relative:
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.

<!-- Previous delivery record retained below. -->

# TESTING — `simulated-eod-flatten-contract` (PROPOSAL — design only)

## Environment

- Fresh `git clone` of GitHub `main` at `f517834` (`Execution panel exit requests`), local
  branch `simulated-eod-flatten-contract`. `origin/main` was re-fetched before packaging:
  unchanged. Nothing merged or pushed.
- Python 3.12 virtualenv from `backend/requirements.txt`. **No PostgreSQL was available**,
  so nothing database-backed was run and no real-lifespan case was executed.

## Results

Two behaviours the proposal depends on were **executed** against the real classes (scratch
script, not committed):

- `SimulatedVenue.place_order` with an injected clock: `15:59:59` → `submitted`;
  `16:00:00` and `16:00:01` → `rejected`, reason `outside_regular_session`. An order accepted
  at 15:59:59 then **filled** on a tick stamped 16:20 ET (`filled`, `venue_ts`
  `2026-09-29T20:20:00+00:00`).
- Real `PositionMonitor` on a buy position (stop 90, target 120): a 16:00 ET tick at 100
  produced one `eod_flatten` intent with trigger price 100.0; a 16:05 tick at 80 produced no
  second intent, and **0** intents were handed to the Execution callback.

Baseline on the untouched tree: `tests/test_position_monitor_engine.py` and
`tests/test_fill_simulator_and_gate.py` — **26 passed**.

Documentation checks: the design-doc diff is insertion-only (no line removed or altered);
every backend/frontend path named in the new section exists, except the six files it labels
as new; every symbol it cites (`uq_orders_active_exit_per_position`,
`ck_exit_requests_reason`, `_check_positions_discrepancy`, `_reconcile_unknown_to_venue`,
`regular_session_close_utc`, `InsufficientReplayDataError`, `confirm_recovery_exit`,
`on_exit_intent`, `pending_position_ids`, `_service_exits`) is present in the tree.

## Not covered

- **Read, not executed:** the exit-ledger transaction behaviour, the unbounded
  stop/target retry finding, the `_service_exits()` abort finding, the reconciliation blocks
  after a restart with a lost venue position (the code was read; the existing tests for
  orphaned orders were not re-run without PostgreSQL), and that `orders.exit_reason` has no
  CHECK (read from migration `0012`).
- The acceptance cases in the design section are a specification. None has been written or run.
- The full backend suite and the frontend build were not run; no code changed.

## Manual check (recommended)

Review the "The one policy to confirm — EOD-A" block and either confirm it or change the
lead time / cancel-at-bell choice before an implementation task starts.

## Package

`simulated-eod-flatten-contract.zip`: `docs/architecture/execution-engine-design.md`,
`CHANGES.md`, `TESTING.md`.

<!-- Previous delivery record retained below. -->

# TESTING — `execution-panel-exit-requests`

## Environment

- Fresh `git clone` of GitHub `main` at `c2f66e5` (`execution-panel-position-history`
  on top of `a8c4b84`). `origin/main` was re-fetched immediately before packaging:
  unchanged. Local branch only; nothing merged or pushed.
- Frontend only. No backend, database or Python test was run: no backend file
  changed, and the route's own behavior is covered by
  `backend/tests/test_execution_exit_requests_route.py`, which this delivery does
  not touch and did not re-run.
- `npm ci` from the committed lockfile.

## Results

- `npm run build` (`tsc -b && vite build`): **clean, zero type errors, 103 modules
  transformed** — on the base before the change and on the final delivery tree. The
  >500 kB chunk advisory is pre-existing.
- Scratch behavioral harness: **18 tests, all passing**, run against the real
  `ExecutionLifecyclePanel` and the real `fetchExecutionExitRequests` in jsdom, with
  a stubbed global `fetch` (proper empty shapes for every other endpoint) and a
  stubbed `useOrderLifecycle` hook that emits nothing:
  - Client (2): the request is the bare `/intelligence/execution-exit-requests`
    path (no query string); an exact string (`0.1000000000000000055511151231257827`)
    comes back untouched; a non-OK response throws `ApiError`.
  - Fetch timing (3): nothing is requested while the panel is collapsed and
    expanding requests once; no request repeats over 10 minutes of fake time (no
    polling) while Refresh adds exactly one; collapse then re-expand fetches again.
  - Rendering (6): loading text, then rows in server order (`ZZZ, AAA, MMM` is not
    re-sorted) showing symbol, Stop/Target, exact trigger price (`10.123400`,
    `1E+3`), trigger time attribute, and current status plus remaining quantity
    (`closing`/3, `open`/7, `closed`/0); `Retry after` appears only for the row with
    a non-null `retry_after`; the "does not prove an order was placed / position is
    protected" text is present and the section is distinct from "Observed exit
    triggers"; empty text versus error text are distinct and each recovers on a
    later Refresh; a rejected fetch shows the error line; a failing exit-requests
    request leaves the orders and positions sections rendering normally.
  - Actions (1): the only button in the section is Refresh.
  - Late responses (4): with two requests in flight, an older response arriving
    after the newer one is discarded; an older *failure* arriving after newer rows
    does not overwrite them; a response arriving after collapse is dropped with no
    React error logged; a response from a previous expansion does not leak into a
    re-expansion (it shows loading, then only the new rows).
  - Separation (1): with distinct symbols in the exit-intents, exit-requests,
    orders and fills responses, each appears only in its own section, and each of
    the five endpoints (`exit-intents`, `execution-exit-requests`,
    `execution-orders`, `execution-fills`, `execution-positions`) is hit exactly
    once on expansion.
  - Refresh isolation (1): Refreshing this section issues exactly one request, to
    its own endpoint.
- Regression guard (run): with the effect cleanup in `RecordedExitRequests`
  disabled (`return () => {}` instead of clearing `active`), **2 of 18 fail** — the
  two Refresh-race tests (stale response, stale failure). With the file restored,
  18/18 pass. The collapse and re-expansion tests pass either way, because React
  drops the unmounted component's state; they check observable behavior, not the
  flag. Reverting the whole client and panel was not separately run.
- `git diff --check`: clean.

## How the harness ran (and its limits)

The repo has no frontend test runner, so `vitest@2`, `jsdom@25`,
`@testing-library/react@16` and `@testing-library/dom@10` were installed with
`npm install --no-save`, and the harness lived in an untracked `frontend/.scratch/`
directory. All of it was deleted and `npm ci` re-run before the final build and
packaging; `package.json` and `package-lock.json` are unchanged, and
`tsconfig.tsbuildinfo` (rewritten by the build) was restored with `git checkout`.
The harness is not shipped.

## Not covered

- Browser rendering, styling and narrow-panel fit were not looked at; jsdom has no
  layout.
- No test against a running backend or real `exit_requests` rows. Server ordering,
  the simulated scope and `null` semantics are covered by the backend route tests.
- Time strings are asserted through the `dateTime` attribute, not the
  locale-formatted text, which depends on the machine's locale and time zone.
- No test with 50+ rows or very long decimal strings for layout; the list is a
  `max-h-48` scroll box like its siblings.

## Manual check (recommended)

With the backend running and at least one recorded stop/target request (ideally one
with a `retry_after` and one whose position is now closed): expand the Execution
panel and confirm the section lists the same rows in the same order as
`curl /intelligence/execution-exit-requests`; the trigger price matches the JSON
string exactly; the position status and remaining quantity match the current
position; `Retry after` appears only where the JSON has a value. Compare it against
"Observed exit triggers" after a backend restart: the observed section should empty
while this one keeps its rows. Stop the backend and press Refresh to see the error
line; collapse and re-expand to see it reload.

## Package

`execution-panel-exit-requests.zip` contains exactly five files, root-relative:
`frontend/src/services/api-client.ts`,
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`,
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.

<!-- Previous delivery record retained below. -->

# TESTING — `execution-panel-position-history`

## Environment

- Fresh `git clone` of GitHub `main`. Code and harness were authored and run on
  base `e1814fd`; `origin/main` then advanced to `a8c4b84`
  (`execution-exit-requests-route`), which changed only backend and
  documentation files (no `frontend/` file). The work was fast-forwarded onto
  `a8c4b84` and the build re-run there; the scratch harness was **not** re-run
  after the fast-forward (it had already been deleted), which is safe only
  because no frontend file changed between the two bases. Local branch only;
  nothing merged or pushed.
- Frontend only. No backend, database or Python test was run: no backend file
  changed.
- `npm ci` from the committed lockfile.

## Results

- `npm run build` (`tsc -b && vite build`): **clean, zero type errors, 103
  modules transformed** — on the base before the change, after it, and again after
  the fast-forward. The >500 kB chunk advisory is pre-existing.
- Scratch behavioral harness: **18 tests, all passing**, run against the real
  `ExecutionLifecyclePanel` and the real `fetchExecutionPositions` in jsdom, with a
  stubbed global `fetch` and a stubbed `useOrderLifecycle` hook that emits one
  fixed WebSocket-side event:
  - Client (2): the request is the bare `/intelligence/execution-positions` path
    (no query string); exact strings (`0.1000000000000000055511151231257827`,
    `1E+3`) come back untouched; a non-OK response throws `ApiError`.
  - Fetch timing (1): nothing is requested while the panel is collapsed; expanding
    requests once.
  - Rendering (4): loading text, then rows in server order (`ZZZ, AAA, MMM` is not
    re-sorted); symbol, side, status, qty, exact average entry, stop/target and
    gross realized P&L all render, with the loss tone; null stop, target and P&L
    are omitted (checked with each null in turn) rather than shown as zero; known
    `0.000000` and `-0.000000` P&L are shown and neutral while a gain is green.
  - Closed positions (1): `closed` with qty 0 reads "Qty 0 — closed, nothing
    held" and is dimmed; a `closed` row with qty 5 shows `Qty 5` and no "nothing
    held" wording.
  - States (2): empty text versus error text are distinct and each recovers on a
    later Refresh; a network failure (rejected fetch) shows the error line.
  - Refresh and staleness (4): Refresh re-requests the identical URL; with two
    requests in flight, a stale response is discarded whether it resolves after
    or before the newer one; a stale *failure* does not overwrite newer rows.
  - Lifecycle (2): a response arriving after unmount is discarded with no React
    error logged; collapse discards the in-flight response and re-expanding
    fetches afresh, showing loading and not the discarded row.
  - Separation (2): the WebSocket-only event appears outside the positions section
    and the snapshot row appears in no other section; each of the positions,
    orders and fills endpoints is hit exactly once with no polling; the section
    says it is "not the live portfolio"; Refreshing positions issues exactly one
    request and does not refetch the sibling sections.
- Regression guards: with the original `api-client.ts` restored, **all 18 fail**
  (the panel then imports a function that does not exist, so the file cannot run at
  all); with the original panel restored, **16 of 18 fail** (the two client tests
  still pass); with both new files back, 18/18 pass.
- `git diff --check`: clean.

## How the harness ran (and its limits)

The repo has no frontend test runner, so `vitest@2`, `jsdom@24`,
`@testing-library/react@16` and `@testing-library/dom@10` were installed with
`npm install --no-save`, and the harness lived in an untracked `frontend/.scratch/`
directory. All of it was deleted, and `npm ci` re-run, before the final build and
packaging; `package.json`, `package-lock.json` and `tsconfig.tsbuildinfo` are
unchanged (the build rewrites the last one, so it was restored with
`git checkout`).

## Not covered

- Browser rendering, styling, colour and narrow-panel fit were not looked at;
  jsdom has no layout. The dimmed style for closed rows is asserted by class name,
  not by looking at it.
- Tone (gain/loss/neutral) is asserted by the `text-bull` / `text-bear` class
  names, not by rendered colour.
- No test against a running backend or real `positions` rows. Server ordering,
  the simulated scope and the `null` semantics are covered by
  `backend/tests/test_execution_positions_route.py`, which this delivery does not
  touch and did not re-run.
- No test with 50+ rows or very long decimal strings for layout; the list is a
  `max-h-48` scroll box like its siblings.

## Manual check (recommended)

With the backend running and a mix of open, partially reduced and closed simulated
positions: expand the Execution panel and confirm the section shows them in the
same order as `curl /intelligence/execution-positions`; a closed position reads
"Qty 0 — closed, nothing held"; a position with no stop shows no Stop text; press
Refresh after changing a position and confirm the new values appear; stop the
backend and press Refresh to see the error line; collapse and re-expand to see it
reload. Confirm the WebSocket activity list and the World View portfolio behave as
before.

## Package

`execution-panel-position-history.zip` contains exactly five files, root-relative:
`frontend/src/services/api-client.ts`,
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`,
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.

<!-- Previous delivery record retained below. -->

# TESTING — `execution-exit-requests-route`

## Environment

- Fresh `git clone` of GitHub `main`, base `e1814fd`; `origin/main` re-fetched
  before packaging and unchanged. Local branch only; nothing merged or pushed.
- PostgreSQL 16, database `trading_workspace` (user `trading`), migrated to
  head (`0015`). No migration added.
- Baseline before the change: **1191 passed**.

## Results

- New file `backend/tests/test_execution_exit_requests_route.py`: **25 passed**.
- Full backend suite on a wiped and recreated database (drop, create, `alembic
  upgrade head`, then pytest): **1216 passed** (1191 + 25), zero regressions.
- Regression guard: with `intelligence.py` restored to `main`, the new file
  gives **24 failed, 1 error**; with the route back, 25/25 pass.

Coverage (real PostgreSQL, hand-inserted rows, `httpx.ASGITransport`):

- Mode isolation (2): backtest, paper and live positions' requests are excluded;
  an `execution_mode` query value cannot widen the scope.
- Symbol filter (3): exact match; lower-case and partial values return nothing;
  no symbol returns rows across symbols.
- Ordering (3): newest `trigger_ts` first regardless of insertion order; ties
  break by `position_id` descending and are stable across reads; a `limit`
  inside a tie keeps the same rows.
- Bounds (7 tests, 4 of them parametrized rejects): limit caps to the newest; default is 50 (51 inserted,
  oldest cut); 0, -1, 101 and "abc" return 422; 1 and 100 accepted.
- Empty (3): unknown symbol; only other-mode rows; a position with no request.
- Serialization (4): exactly the nine curated fields; `retry_after` null when
  unset and the stored timestamp when set; `trigger_price` exact strings
  (`12345678901.123456`, `0.100000`); position status and remaining quantity for
  `open`, `closing` (4 left) and `closed` (0).
- Distinct from `/exit-intents` (1): with no monitor, `/exit-intents` reports
  `unavailable` and no rows while the new route returns the durable row and none
  of the monitor envelope fields.
- Read-only (1): GET leaves request and position untouched; four write verbs
  return 405.
- Event loop (1): a helper blocked on a `threading.Event` runs in a worker
  thread while `/health` still answers within 2 s.

## Notes on the harness

- No app lifespan is booted (same choice as the positions route tests): the
  lifespan would run Portfolio State's restore against fill-less positions and
  log a `PositionLedgerError`. Cleanup is scoped to a strategy-name tag and
  deletes `exit_requests`, then `positions`, then `trades`; symbols are
  synthetic (`ZZXR*`).
- PostgreSQL was installed in the sandbox for this run (`apt`), not available
  before.

## Not covered

- No test against rows written by a live Execution Engine run; requests are
  hand-inserted. Creation and retry behavior belong to decision #184's tests.
- `retry_after` semantics beyond "stored value is returned" are not asserted.
- Query plan and behavior at large table sizes were not measured.

## Package

`execution-exit-requests-route.zip` contains exactly five files, root-relative:
`backend/app/api/routes/intelligence.py`,
`backend/tests/test_execution_exit_requests_route.py`,
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.

<!-- Previous delivery record retained below. -->

# TESTING — `execution-fills-symbol-filter-implementation`

## Environment

- Fresh `git clone` of GitHub `main`. Code was authored on base `9dcb879`, then
  rebased onto `a725d5d` (`restore-protective-exits-record`), which changed only
  documentation, no `frontend/` file. Code and harness were re-run after the
  rebase. Local branch only; nothing merged or pushed.
- Frontend only. No backend, database or Python test run was needed or done:
  no backend file changed.
- `npm ci` from the committed lockfile.

## Results

- `npm run build` (`tsc -b && vite build`): **clean, zero type errors, 103
  modules transformed** — before the change, after it, and again after the
  rebase. The >500 kB chunk advisory is pre-existing.
- Scratch behavioral harness: **15 tests, all passing**, run against the real
  `ExecutionLifecyclePanel` in jsdom with a stubbed global `fetch` and a stubbed
  `useOrderLifecycle` hook:
  - `fetchExecutionFills` URL (2): omitted or empty symbol gives the bare path;
    `BRK.B` stays as is; `A&B C` becomes `A%26B%20C`.
  - Default (1): first load requests no `symbol`; the orders section is also
    unfiltered.
  - Input handling (3): uppercased as typed; `"  brk.b "` sent as `BRK.B` on
    Enter; Apply button works; other keys do not apply; whitespace-only apply
    makes no request when nothing is applied, and clears an applied filter to
    the bare URL (never `symbol=`).
  - Refresh and Clear (3): Refresh re-sends the applied symbol, not
    typed-but-unapplied text; Clear resets the input, refetches unfiltered and
    is disabled when there is nothing to clear; Clear on typed-but-unapplied
    text empties the box and makes no request.
  - Loading (1): while a request is pending the loading text shows and input,
    Apply, Clear and Refresh are all disabled.
  - Results (3): "No simulated fills recorded yet." versus "No simulated fills
    for XYZ." and back after Clear; exact decimal strings render verbatim
    (`185.1000`, `0.1000000000000000055511151231257827`, `1E+3`) with a null
    commission omitted; an error state recovers on a later Apply.
  - Isolation (1): applying a fills filter leaves the orders section's input
    and requests untouched, and the reverse.
  - Lifecycle (1): a response resolving after unmount is discarded without
    error.
- Regression guards: with the original `api-client.ts` restored, 4 of 15 fail;
  with the original panel restored, 10 of 15 fail; with both new files back,
  15/15 pass.

## How the harness ran (and its limits)

The repo has no frontend test runner, so `vitest@2`, `jsdom@24` and
`@testing-library/react@16` were installed with `npm install --no-save` and the
harness lived in an untracked `frontend/.scratch/` directory. All of it was
deleted, and `npm ci` was re-run, before the final build and packaging;
`package.json`, `package-lock.json` and `tsconfig.tsbuildinfo` are unchanged.
Unlike the orders-filter delivery's extracted-helper harness, this one renders
the actual component and calls the real `fetchExecutionFills`.

## Not covered

- The stale-response guard cannot be exercised through the UI: every control is
  disabled during a request, so the filter cannot change while one is in
  flight. Only discard-after-unmount was tested. The guard is the same
  `active`-flag pattern as the orders section.
- Browser rendering, styling and narrow-panel fit were not looked at; jsdom has
  no layout.
- No test against a running backend or real `fills` rows. Server-side symbol
  matching is covered by decision #183's backend tests, which this delivery does
  not touch.

## Manual check (recommended)

With the backend running and fills for at least two symbols: expand the
Execution panel; type `aapl` -> field shows `AAPL`; Enter -> only AAPL fills;
Refresh -> still AAPL; apply a symbol with no fills -> "No simulated fills for
XYZ."; Clear -> all symbols return; confirm the orders section's filter was
unaffected throughout; stop the backend and Apply -> error line appears.

## Package

`execution-fills-symbol-filter-implementation.zip` contains exactly five files,
root-relative: `frontend/src/services/api-client.ts`,
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`,
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.

<!-- Previous delivery record retained below. -->

# TESTING — `restore-protective-exits-record`

## Environment

- Fresh `git clone` of GitHub `main`, base `9dcb879`; `origin/main` re-fetched
  before packaging — unchanged. Work on a local branch; nothing merged or
  pushed.
- Documentation-only delivery. No database, backend suite, or frontend build was
  run: no application file changed, and no test reads documentation content
  (the only `docs/` hits under `backend/tests/` are docstrings and comments).

## Results

- **Application code untouched.** `git diff --stat` against `origin/main` lists
  only the five documentation files. `4aea47f..a00dbd0` touches no file under
  `backend/` or `frontend/`, and `4aea47f..main` differs in application code
  only by the positions route and its tests.
- **Design doc.** Diffed against `4aea47f`, the file's changed lines are
  identical (111 of 111) to the lines `9dcb879` itself added — restored text
  plus the positions-route section, nothing else. The restoration applied
  `a00dbd0`'s inverse for that file; it applied cleanly with no conflict.
- **Decision log.** `INDEX.md` and `confirmed-decisions.md` are byte-identical
  to `4aea47f` (`cmp`). Index and log agree: 184 rows, contiguous `1..184`, no
  duplicates; the log's tail is `182, 183, 184` in order; row 184 points to
  `confirmed-decisions.md`; every index pointer names an existing file and
  numbers fall inside archive filename ranges; no number is in the log or
  archive but missing from the index. Archive files unchanged.
- **`CHANGES.md` / `TESTING.md`.** The removed blocks are exactly the first 116
  and 175 lines of `4aea47f`'s files, which are the lines `a00dbd0` deleted
  (compared line by line). They were spliced back as pure insertions; the only
  edits to pre-existing text are the two "Resolved afterwards" annotations in the
  positions record.
- `git diff --check`: clean.

## Restored architecture vs current code

Read against `main`, each restored claim held:

- `PostgresExitLedger`: the stop/target-only observation, first-observation
  storage, entry cancel before close, waiting on fills without a position
  receipt, reuse or wait for an active close, `<trade_id>:exit:<attempt>` with
  `positions.exit_attempt` advanced in the same transaction, the 5 s retry
  delay after a rejected/cancelled close, and the pre-submit recheck.
- Migration `0015`: `exit_requests`, `orders.position_id`, and the partial
  unique index `uq_orders_active_exit_per_position` (one active close per
  position).
- `ExecutionEngine.on_exit_intent` queues the hand-off; the worker persists,
  cancels, reserves, rechecks and places. The Position Monitor hands over
  stop/target only; EOD stays observed. `/intelligence/exit-intents` still
  reports `intent_status: observed_only`.
- Startup: Portfolio State applies pending fills before reconciliation;
  reconciliation validates approved exits without submitting them and reports a
  discrepancy for an unsafe/unreserved one; a position mismatch against the
  fresh in-memory `SimulatedVenue` makes reconciliation block execution.
- The fills-panel text matches `RecentSimulatedFills` and
  `fetchExecutionFills`: mounts on expand, unfiltered default fetch, Refresh,
  `ledger_seq` keys, commission omitted when `null`, three distinct states,
  stale responses ignored.

## Issues met

1. My first design-doc comparison used the wrong reference range
   (`4aea47f..9dcb879`, which includes `a00dbd0`'s reverts) and reported a false
   mismatch. Re-run against `a00dbd0..9dcb879` — the delivery's own change —
   it is identical, as stated above.
2. My first decision-log parser saw archive headings only from #134 because
   older archives use another heading style; it was replaced by the
   pointer/range check above.

## Limits

Verification is by static comparison and code reading; no runtime test of the
exit path was re-run for this delivery (#184's own record above holds its
1168-pass suite, the same baseline the positions record reports).

<!-- Previous delivery record retained below. -->

# TESTING — `execution-positions-route`

## Environment

- Fresh `git clone`, base commit `a00dbd0337774978ad2ace59243dc696ed2f5af2`;
  `origin/main` re-fetched before the final run and at packaging — unchanged.
- Postgres 16 installed via apt and started with `pg_ctlcluster`;
  `trading`/`trading_workspace` per `core/config.py`; `alembic upgrade head` to
  `0015`; `backend/requirements.txt` installed.

## Results

- Baseline on an untouched clean worktree of `a00dbd0`: **1168 passed**.
- New file alone: **23 passed**, repeated runs, no flakes.
- **Final verification on a wiped and recreated database**
  (`DROP/CREATE DATABASE`, `alembic upgrade head`): full backend suite
  **1191 passed** (1168 + 23), zero regressions. Afterward the database held no
  tagged `trades` rows and empty `positions`/`orders`/`fills`.
- Regression guard: with the route reverted to `HEAD` (new test file kept),
  22 tests fail and 1 errors; restored afterward.
- Mutation checks, each restored afterward (all caught by the intended tests):
  no mode filter, no symbol filter, ascending `opened_at`, float money, null
  rendered as `"0"`, limit ignored, no tie-break, ascending tie-break, and the
  read run on the event loop instead of `asyncio.to_thread`.

## What the tests cover

Real Postgres throughout; rows are hand-inserted, so Portfolio State's write
path is not re-tested here (see the position-ledger tests). Backtest/paper/live
positions excluded and an `execution_mode` query parameter ignored; exact
symbol filter (lowercase and substring rejected); no-symbol read across
symbols; newest-first by `opened_at` with rows inserted out of time order;
`opened_at` ties broken by `position_id` descending, stable across reads, and a
limit landing inside a tie keeping the same rows; limit cap, default 50 keeping
the newest 50, 422 for 0/-1/101/non-integer, both edges accepted; honest empty
list for an unknown symbol and for a symbol with only other-mode rows; open
position with `stop`/`target`/`closed_at`/`realized_pnl` all `null` and only the
curated keys present; closed position with exact strings and close time; a
17-significant-digit price and a `"0.000000"` P&L kept as strings; `closing`
position with remaining quantity; GET leaves the row unchanged and
POST/PUT/PATCH/DELETE return 405; and a blocked `_fetch_execution_positions` in
a worker thread must not block `/health`.

## Issues met while testing

1. My first tie tests could pass by chance with the tie-break removed, because
   random `uuid4` ids sometimes land in the expected order. Mutation checking
   exposed it. Tied rows are now inserted with explicitly sorted ascending ids
   while the route must return them descending; both tie tests then fail 6/6
   with the tie-break removed or reversed.
2. The no-symbol test could have been displaced from the 100-row window by
   unrelated newer simulated rows; its timestamps are pinned far in the future.
3. The lifespan is deliberately not booted (see `CHANGES.md` findings).
4. A mutation-check script of mine crashed once mid-run and left the route
   file mutated. It was restored from a saved copy and byte-compared before any
   further run; the delivered file is the verified one.
5. My first background baseline run died silently (empty log). The baseline
   above is from a detached rerun.

<!-- Previous delivery record retained below. -->

# TESTING — decision #184: simulated protective exits

## Environment and results

- Baseline branch `main` at `3fdaacf98876b476373b5b08d93da2960e3096ef`;
  GitHub `main` matched before decision #184 was assigned. Existing
  uncommitted simulated-exit files were inspected and completed in place.
- Isolated local PostgreSQL 18 database `agentic_exit_tests` on port 55432,
  under the repository's `.exit-validation/` directory. No live broker or
  external database was contacted. `alembic current` reports `0015 (head)`.
  The migration was already applied to this isolated database at the start
  of this resumed delivery; tests verified its active-close unique index
  with an actual duplicate insert rejected by PostgreSQL.
- Full backend suite with `PGTZ=UTC`: **1168 passed**, zero failures
  (`pytest -q --disable-warnings`, 101.63 s). Focused exit, lifespan,
  reconciliation, and monitor suite after the final test addition:
  **28 passed**. `git diff --check`: clean.
- The first full run without `PGTZ=UTC` had 1165 passes and two existing
  timestamp assertion failures: PostgreSQL returned `+06:00` to tests
  expecting UTC. Both passed with `PGTZ=UTC`; the final full run used that
  session setting. No timestamp assertion or unrelated route was changed.
- `alembic check` is not clean on this baseline: it reports many existing
  model/migration differences, including partition tables, historical indexes,
  and timezone types. The new `exit_requests` timestamp fields were aligned
  with migration `0015` after this check. The unrelated drift was left intact.

## Behavior verified

- Real FastAPI lifespan: both stop and target observations create one durable
  close order; a later simulated tick fills it, closes the position, marks the
  trade closed, and produces a `PositionClosed` event.
- PostgreSQL ledger: duplicate observations collapse to one request; an
  unfinished entry must be cancelled first; a rejected close waits before a
  new attempt ID is reserved; the partial unique index rejects a second active
  close; a closed position gets no new order.
- The pre-submit guard blocks placement if a new entry is active or a fill
  lacks a position receipt. Restart reconciliation blocks a close when a
  fresh simulated venue has lost its open position; it does not submit during
  startup.

## Limits and follow-up

EOD flatten remains observed only. A restarted simulated venue has no durable
position book, so an open-position discrepancy blocks execution and needs
manual resolution; the durable exit request does not by itself protect that
position across restart. Live/paper protective orders and live outcome writing
remain separate work. The pre-existing decision log exceeds its documented
rollover size; an archive rollover should be handled separately while
preserving all historical decision bodies and index mappings.

<!-- Previous delivery record retained below. -->

# TESTING — `execution-panel-fill-history`

## Environment and results

- Current `main` at `f0a624914488b769176582bcf04ef70aae093f6b`;
  GitHub `main` matched immediately before packaging. Working tree was clean
  before this delivery. Frontend only; no database or backend test run.
- `npm run build` in `frontend/`: passed (`tsc -b && vite build`, 103 modules).
  Vite's >500 kB chunk advisory remains.
- Focused direct execution of the shipped `fetchExecutionFills` function via
  TypeScript transpilation and a stubbed `fetch`: bare route URL, exact price
  and commission strings, preserved response order, and HTTP error detail/
  status all passed. Source-level panel guards confirmed expansion mounting,
  refresh dependency, stale-response cleanup, distinct loading/error/empty
  branches, ordered mapping keyed by `ledger_seq`, and required field renders.

The repo has no frontend DOM test runner. The source guards do not simulate
clicks or prove visual fit at narrow widths; a browser check with populated
fills remains useful. The first scratch check failed because its VM context
omitted CommonJS `exports`; the corrected harness passed. No scratch files
were retained.

## Package

`execution-panel-fill-history.zip` contains exactly:
`frontend/src/services/api-client.ts`,
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`,
`docs/architecture/execution-engine-design.md`, `CHANGES.md`, `TESTING.md`.

# TESTING — `execution-orders-symbol-filter`

## Environment

- Fresh `git clone --depth 1`; work authored on base commit
  `b65d6ed0a185d2f73ad377591a714cab45e73312`. `origin/main` then advanced to
  `cbca16cc685af3e0b1441e808cc40d1ae1cbe511` (decision #183,
  `execution-fills-route`); the work was carried onto it. That commit touches
  no `frontend/` file and only adds to `intelligence.py` (zero removed lines),
  so the `GET /intelligence/execution-orders` contract this uses is unchanged.
  Only `CHANGES.md`/`TESTING.md` overlap, and both entries here are prepended
  above #183's, which is preserved intact.
- Frontend only. No backend, database or test-suite run was needed or done.

## Results

- `npm ci` from the committed lockfile (TypeScript 5.9.3), then
  `npm run build` (`tsc -b && vite build`): **clean, zero type errors, 103
  modules transformed** — on the original base and again on `cbca16c`. The
  >500 kB chunk-size advisory is the pre-existing one.
- Direct execution of the shipped logic, **15 assertions, all passing**
  (scratch harness, deleted before packaging — see below):
  - `normalizeSymbolFilter` (7): lowercase, already-upper, surrounding
    whitespace, mixed case, `""` and whitespace-only -> `undefined`,
    `brk.b` -> `BRK.B`.
  - `emptyOrdersMessage` (3): no filter -> "No simulated orders recorded
    yet."; applied -> "No simulated orders for SYMBOL."
  - Query construction (5): no symbol / explicit `undefined` -> bare path
    with no query string; `AAPL` -> `?symbol=AAPL`; `BRK.B` unchanged;
    `A&B C` -> `?symbol=A%26B%20C`.
- Regression guards: with the helpers stubbed to no-ops, 8 of the 10 helper
  assertions fail; with the URL builder stubbed to always append the raw
  symbol, 3 of the 5 URL assertions fail (including `symbol=undefined`).
  Real code restored afterward, 15/15 again.

## How the harness ran (and its limits)

The repo has no frontend test runner (no `test` script, no `.test.`/`.spec.`
files), so this follows the `scanner-panel-session-restore` precedent of
direct execution with `tsx`. That precedent's whole-file scratch copy could
not be used: `ExecutionLifecyclePanel.tsx` and `api-client.ts` transitively
import `config.ts`, which reads `import.meta.env` (Vite-only, undefined under
plain Node). Instead:

- The two helpers were `sed`-extracted verbatim from the real file, with only
  `function` -> `export function` changed.
- For query construction, the three-line `url` expression was `sed`-extracted
  from the real `fetchExecutionOrders` with `API_BASE_URL` parameterized as
  `base`. **This tests the URL expression, not `fetchExecutionOrders` itself
  end to end** (no mocked `fetch`, no `ApiError` path).
- Scratch files placed inside `src/` break `tsc -b` (Node globals are not
  typed in the browser tsconfig), so all three were deleted before packaging;
  the final build above ran with them gone.

## Not covered by any automated check

No DOM or component-render test exists in this repo, so these were verified by
reading the code only, not by running the UI:

- Enter key and the Apply / Clear buttons; Clear disabled when nothing to clear.
- Loading and error states still rendering, and controls disabled while loading.
- Refresh keeping the applied filter (it only bumps `refreshKey`, which does
  not touch `appliedSymbol`).
- Stale-response discard when the filter changes quickly (the existing
  `active`-flag cleanup, now keyed on `[refreshKey, appliedSymbol]`).
- Visual fit at narrow panel widths.

## Manual check (recommended)

With the backend running and at least two symbols in `orders`: open the
Execution panel; type `aapl` -> field shows `AAPL`; Enter -> only AAPL rows;
Refresh -> still AAPL; type a symbol with no orders -> "No simulated orders
for XYZ."; Clear -> all symbols return and the "recorded yet" text is used
only when the table is truly empty; stop the backend and Apply -> error line
appears; click Apply twice quickly with different symbols and confirm the
final list matches the last one applied.

## Environment note

The first `npm install` left a truncated `node_modules/csstype/index.d.ts`
(a corrupted download), which made `tsc -b` fail inside `node_modules`. This
was a sandbox cache problem, not a code or lockfile problem: `npm cache
verify` + `npm ci` fixed it. One intermediate `npm install typescript@5.5.3`
rewrote `package-lock.json`; that was reverted with `git checkout`, and the
lockfile is not part of this delivery. `tsconfig.tsbuildinfo`, regenerated by
each `tsc -b`, was likewise reverted.

## Package

`execution-orders-symbol-filter.zip` contains exactly four files, root-relative:
`frontend/src/services/api-client.ts`,
`frontend/src/components/execution/ExecutionLifecyclePanel.tsx`,
`CHANGES.md`, `TESTING.md`.

# TESTING — decision #183: `execution-fills-route`

## Environment

- Fresh `git clone --depth 1`, base commit
  `b65d6ed0a185d2f73ad377591a714cab45e73312`; `origin/main` re-fetched at the
  mid-task check and again at packaging — unchanged, no rebase needed.
- Postgres 16 installed via apt, started manually with `pg_ctl`
  (`/etc/postgresql/16/main/postgresql.conf`); `trading`/`trading_workspace`
  per `core/config.py`; `alembic upgrade head` to `0014`; venv with
  `backend/requirements.txt`. The server was restarted once mid-session after
  the sandbox paused; nothing else changed.

## Results

- Baseline on the untouched clone: **1148 passed**.
- New file alone: **15 passed**, three consecutive runs, no flakes.
- **Final verification on a wiped and recreated database**
  (`DROP/CREATE DATABASE`, `alembic upgrade head`): full backend suite
  **1163 passed** (1148 + 15), zero regressions. After the run the database
  held no leftover tagged `trades` rows, and `fills`/`positions`/
  `position_fill_receipts` were empty.
- Regression guard: with the route reverted (`git stash`, new test file kept),
  all 15 new tests fail (14 failed, 1 error); restored afterward.
- Execution-related files together (`fills`/`orders` route, ledger, engine,
  main pipeline): 51 passed.

## What the tests cover

Newest-first ordering by `ledger_seq`; exact symbol filter (lowercase and
substring rejected); a `backtest`-mode order's fill excluded by the join;
limit cap, default, 422 at 0 and 101, both edges accepted; honest empty
list; curated fields with UUID/timestamps serialized, `price` an exact
string, no `execution_mode` field; `commission` null when absent and exact
string when set; `anomaly` passthrough; and a concurrency regression (blocked
`_fetch_execution_fills` in a worker thread must not block `/health`).
Real Postgres throughout; rows are hand-inserted, so the write path is not
re-tested here (see `test_execution_ledger.py`/`test_execution_engine.py`).

## Issues met while testing

1. Cleanup first failed on a `positions` FK: booting `TestClient(app)`
   reconciles inserted simulated fills into `positions` and
   `position_fill_receipts`. Cleanup now deletes receipts, positions, fills,
   orders, trades in that order.
2. A test with fill `qty` 17 against order `qty` 10 returned `anomaly:
   "overfill"` — real behavior (`portfolio_state/legacy.py` flags it during
   reconciliation), not a route bug. Test data was corrected.
3. A commission test that opened `TestClient(app)` twice failed at the second
   shutdown with "Queue is bound to a different event loop" in
   `bus.stop()`. A scan of the suite found no other test doing this. Split
   into two single-boot tests; `main.py`/`event_bus` left untouched.
4. Two of my own docstring/doc sentences claimed this was the first reader of
   `fills` anywhere; a grep showed internal readers exist. Corrected to "first
   HTTP route" in the route, test file, design doc and decision entry.

Startup logs during these tests include a CRITICAL "reconciliation found 1
discrepancy" line: expected, because hand-inserted fills were never placed
through the simulated venue. It is not a failure.

<!-- Previous delivery record retained below. -->

# TESTING — `backtest-isolation-flake-fix`

## Task

Investigate and fix the intermittent failure in
`backend/tests/test_backtest_routes.py::test_two_separate_runs_isolate_level_interaction_state_and_events`.
Approved, scoped task — no other test or production file in scope unless
evidence required it (it did not).

## Environment setup

- Fresh `git clone --depth 1 https://github.com/rotate-zero/agentic-trading-os.git`,
  base commit `b16ac8b9d880ae95b255c2e21155a9590a9bc9e0`. Re-checked
  `origin/main` immediately before packaging — identical, nothing else
  landed in between.
- No PostgreSQL preinstalled in this sandbox — installed Postgres 16
  (`apt-get install postgresql postgresql-contrib`), started manually via
  `pg_ctl` against the Debian-layout config
  (`/etc/postgresql/16/main/postgresql.conf`; the packaged `service`/
  `systemd` units are policy-blocked here), matching this project's own
  documented workaround from prior sessions.
- Created the `trading`/`trading_workspace` role and database exactly per
  `core/config.py`'s documented convention, plus a second, separate scratch
  database, `trading_workspace_flaketest`, used only for the
  accumulating-database reproduction below — kept apart from
  `trading_workspace` so the fresh-DB and accumulating-DB evidence can't
  cross-contaminate. **Neither is Saqib's own development database — both
  are dedicated to this task and disposable.** Python venv,
  `pip install -r backend/requirements.txt`, unchanged from `main`.
- All reproduction and verification runs were strictly serial — one
  `pytest` process at a time against a given database, never overlapping,
  per this task's own instruction.
- **Packaging-time collision check**: `origin/main` advanced during this
  session, to `f08363cd7534f316d5035addbf9e84e1932085e0` — a docs-only
  sibling delivery (`execution-doc-drift-r1-r5-r9`) touching
  `docs/architecture/execution-engine-design.md` and `system-design.md`
  only. `diff -rq` against a fresh clone of that newer `main` confirms zero
  file overlap with this delivery's own three changed files
  (`backend/tests/test_backtest_routes.py`, `TESTING.md`, `CHANGES.md`).
  No rebase needed; this delivery's own base remains
  `b16ac8b9d880ae95b255c2e21155a9590a9bc9e0`.

## Diagnosis

Read `AGENTS.md`, this file, `docs/architecture/` (backtest runner and
level-interaction-engine design), the decision-archive entries for #133
(D18), #159/#160 (D20, `EngineBackedReplayStateProducer`), and the full
source of `level_interaction_engine.py`, `replay_state_producer.py`,
`runner.py`, `engine_singleton_guard.py`, `fixture_provider.py`,
`scenarios.py`, `candle_store.py`, and the `FeatureEngine` VWAP accumulator
before changing anything.

Ruled out, with evidence, not assumption:

- **Wall-clock-dependent replay settlement.** Every persisted
  `zone_entered_ts`/`touch_entered_ts`/`entered_ts`/`exited_ts` in
  `level_interaction_engine.py` is derived from the replayed `candle_ts`
  (`_process_level`/`_process_one`), never `datetime.now()`. The file's one
  real `datetime.now(timezone.utc)` call (`get_snapshot()`, a live
  metadata field) is never written to any column this test inspects.
  Fixture candle timestamps come straight from the static
  `first_pullback_vwap_dip.csv` (`fixture_provider.py`) and are never
  rebased to "today."
- **Test data left across runs.** `EngineBackedReplayStateProducer`
  constructs a brand-new `LevelInteractionEngine`/`FeatureEngine` instance
  per run (fresh in-memory state), and all DB reads/writes are scoped by
  that run's own fresh `backtest_run_id` (decision #160/D20).
  `FeatureEngine`'s VWAP cold-start backfill
  (`candle_store.get_recorded_candles`) filters `Symbol.is_backtest.is_(False)`
  — it only ever reads *live* candle history, so it cannot be polluted by a
  prior backtest run of this test's synthetic ticker. Confirmed empirically
  with a standalone diagnostic script (bypassing the test's own lookup,
  dumping every column including `timeframe`): the two runs'
  `level_interaction_state` rows are byte-identical per timeframe, every
  time, fixed or not.

**Actual root cause — a test-query defect, not a production defect.**
`level_interaction_state` legitimately tracks each `level_key` once **per
timeframe** (this scenario's 129-candle replay produces independent
`1m`/`5m`/`15m`/`1h` rows for `level_key='vwap'`, each with its own
`zone_entered_ts` — confirmed by direct query). The test's own SQL selected
`level_key` but never `timeframe`, and ordered only by
`lis.backtest_run_id, lis.level_key`; it then picked "the" vwap row with
`next(row for row in states_by_run[run_id] if row["level_key"] == "vwap")`.
Four rows match that predicate per run, tied on the only sort key the query
provides. SQL does not guarantee relative order among tied rows without an
explicit tiebreaker, so `next()` returned whichever physical row Postgres's
scan surfaced first for that execution — which can differ run to run
depending on heap/page layout, independent of any real state divergence.
Confirmed directly: dumping `timeframe` alongside the other columns showed
the `14:30` value belongs to the `5m`/`15m`/`1h` rows and `16:02` to the
`1m` row, for both runs, every time — the two runs' actual state was
identical; only which tied row the ambiguous query happened to return
first differed.

Checked for the same pick-one-of-several-ties pattern elsewhere
(`test_backtest_sweep_route.py`, `test_level_interaction_engine.py`,
`test_replay_state_producer.py`, `test_symbol_namespace.py`) and in
production's own `get_snapshot()` (which correctly nests by timeframe):
found nowhere else. **No production behavior defect; no scope expansion
needed or done.**

### Correcting prior entries in this file

This exact failure (same `14:30`/`16:02` signature) was already observed
and logged at least three times before this task — see the corrections
inserted in place at the `count-lower-bound-validation`,
`execution-authorizer-and-engine`, and `execution-ledger-and-venue`
sections below. Each time it was re-run once or twice, seen to "pass in
isolation," and filed away as either "non-reproducible against a pristine
database" or this project's own long-documented **#119** intermittent
cluster (`test_vwap_publishes_even_while_sma_is_still_warming_up` and three
sibling `FeatureEngine`-warmup tests — see decision #149,
`flaky-test-cluster-rootcause`). Neither characterization holds: this task
reproduced the failure on a freshly migrated database, first attempt, 4
times out of 5 consecutive fresh-database attempts (below) — it is not
tied to database accumulation, and it has nothing to do with the `#119`
cluster (different test file, different mechanism — a query tiebreak, not
`FeatureEngine` warmup timing).

## Reproduction

Dedicated database `trading_workspace`, `DROP DATABASE`/recreate/`alembic
upgrade head` before each attempt, one `pytest` process at a time:

```
python -m pytest -q tests/test_backtest_routes.py::test_two_separate_runs_isolate_level_interaction_state_and_events
```

**Before the fix, 5 consecutive fresh-database attempts: 4 failed, 1
passed** — all 4 failures with the identical signature
`datetime(2026, 2, 2, 14, 30, ...) != datetime(2026, 2, 2, 16, 2, ...)` at
the `vwap_state(run_ids[0]) == vwap_state(run_ids[1])` assertion.

**After the fix, 10 consecutive fresh-database attempts: 10 passed, 0
failed.** A further 15 consecutive attempts against one never-wiped,
never-recreated database (`trading_workspace_flaketest`, so row count and
heap layout keep growing exactly as they did historically) — the condition
under which this failure was previously observed — also **15 passed, 0
failed**. 25/25 total with the fix.

## Fix

`backend/tests/test_backtest_routes.py` only — no production file touched:

- Added `lis.timeframe` to the `level_interaction_state` `SELECT` and to
  the `ORDER BY` (now `ORDER BY lis.backtest_run_id, lis.level_key,
  lis.timeframe`) — fully deterministic ordering, no remaining ties.
- `vwap_rows` now filters `row["timeframe"] == "1m"` in addition to
  `row["level_key"] == "vwap"` — pins the lookup to the timeframe every v1
  strategy actually reads (decision #99), which is what the test's own
  comment already intended before this ambiguity existed.
- Added an inline comment explaining the timeframe-tie mechanism so a
  future reader doesn't have to re-derive this diagnosis.

No change to the test's meaningful contract: it still asserts that two
identical replays produce independent, equivalent persisted state
(row-count disjointness, `is_backtest` flags, the full VWAP checkpoint
tuple, and the full normalized event sequence) — it now asserts this
against one well-defined row instead of an arbitrarily-selected one among
four legitimate rows. No assertion weakened, no sleep added.

`level_interaction_events`/`normalized_events()` needed no change — that
query already orders by `lie.id` (a true, monotonic tiebreaker), and the
diagnostic script's raw dump showed the two runs' event sequences matching
exactly, content for content.

## Checks and results

- **Targeted, fixed, repeated**: see Reproduction above — 25/25 passed
  across fresh and accumulating databases.
- **Full suite, post-fix, fresh database, single run**:
  `POSTGRES_DB=trading_workspace python -m pytest -q tests/` — **1148
  passed, 0 failed, 99.22s**. Identical count to this file's own
  `count-lower-bound-validation` baseline, so this fix adds no test and
  removes none — it only changes two query lines and a lookup filter
  inside one existing test.
- **No decision number assigned** — a test-query correction, not an
  architecture change, matching this task's own instruction.

## Not covered / limitations

- The `count-lower-bound-validation` entry's *other* transient artifact —
  "a larger burst of constraint-violation errors ... where two `pytest`
  invocations briefly overlapped against the same database" — is a
  different failure mode (genuine process overlap against one database)
  that this task did not investigate and makes no claim about; that part
  of its entry is left as originally written.
- This task did not attempt to characterize or fix the actual `#119`
  cluster (`test_vwap_publishes_even_while_sma_is_still_warming_up` and its
  three siblings) — out of scope; per decision #149 it already has its own
  root-cause task.

<!-- Previous delivery record retained below. -->

# TESTING — `count-lower-bound-validation`

Repository access was via the project's tarball convention
(`curl -sL https://codeload.github.com/rotate-zero/agentic-trading-os/tar.gz/refs/heads/main | tar -xzf - --strip-components=1`).
`main` was pulled at the start of this task; re-pulled again immediately
before packaging — identical, nothing else landed on `main` in between.

No PostgreSQL preinstalled in this sandbox — installed Postgres 16
(`apt-get install postgresql postgresql-contrib`, `archive.ubuntu.com`/
`security.ubuntu.com` allowed by the network egress list), started it
manually (`pg_ctl` against the Debian-layout config under
`/etc/postgresql/16/main/postgresql.conf`, since the packaged `service`/
`systemd` units are policy-blocked in this environment), created the
`trading`/`trading_workspace` role and database matching `core/config.py`'s
conventions, ran `alembic upgrade head` — 14 migrations, all applied
cleanly. Python venv, `pip install -r backend/requirements.txt` (unchanged
from `main` — no new dependency).

- **Baseline, pre-change, full suite** (`python3 -m pytest -q` from
  `backend/`, untouched pull, fresh `trading_workspace` database, single
  run): **1140 passed**, 0 failed, 99.74s (reconfirmed later on a
  re-recreated pristine database at 98.15s — identical count, see the
  database-state note below).
- **Targeted, post-change**: `python3 -m pytest -q tests/test_market_routes.py
  tests/test_intelligence_routes.py` — **33 passed** (25 pre-existing + 8
  new), 0 failed.
- **Full suite, post-change, fresh database, single run**: **1148 passed** —
  exactly +8 (the new tests below), 0 failed, 98.79s.
- **Database-state note**: this project's suite runs entirely against real
  PostgreSQL, and repeatedly re-running the full suite against the *same*
  long-lived database (done here purely from re-verifying several times
  during this task) accumulates rows across runs and produced two
  transient, non-reproducible artifacts along the way — a single
  `test_backtest_routes.py` failure (unrelated to this change: a
  timestamp mismatch between two backtest runs' level-interaction state)
  on one repeated run, and a larger burst of constraint-violation errors
  on another where two `pytest` invocations briefly overlapped against the
  same database. Both vanished on a `DROP DATABASE`/recreate/`alembic
  upgrade head` and a single subsequent run — neither reproduced against a
  pristine database, before or after this change, so neither is
  attributed to it. The baseline and post-change counts above are each
  from one clean run against a database recreated immediately beforehand.
  **Correction (`backtest-isolation-flake-fix`, see below):** the
  `test_backtest_routes.py` half of this claim was wrong. That failure
  *does* reproduce on a freshly migrated, pristine database — 4 times out
  of 5 consecutive attempts, in a later task that root-caused it to a
  timeframe-ambiguous test query, not to database accumulation. It was
  "not reproduced" here only because this session happened to re-run it
  too few times on the un-migrated-fresh path to catch it; it was never
  actually non-reproducible. The constraint-violation half of this note
  (genuinely overlapping `pytest` processes) is unrelated and uninvestigated
  by that later task — left as originally written.
- **New tests, `test_market_routes.py`** (4): `test_market_candles_rejects_zero_count`
  and `test_market_candles_rejects_negative_count` assert a `422` for
  `count=0`/`count=-1` with no adapter connected — pure request-validation
  rejection, before the route body ever runs. `test_market_candles_accepts_count_of_one`
  and `test_market_candles_accepts_count_of_1000` assert the two boundary
  values still reach the route's own logic unchanged — reusing the existing
  `_FakeConnectedAdapter`/`SymbolNotFoundError` pattern
  (`test_market_candles_returns_400_for_unresolvable_symbol`'s own shape):
  a `400` naming the unresolvable symbol proves validation passed the
  request through, not that it was rejected.
- **New tests, `test_intelligence_routes.py`** (4): the same four cases
  against `GET /intelligence/series`, via this file's existing
  `app.router.lifespan_context` + `httpx.ASGITransport` pattern (needed for
  the route's real singleton engines, same as every other test in this
  file). `count=0`/`count=-1` → `422`. `count=1`/`count=1000` → `200`, same
  empty-series shape as the existing
  `test_intelligence_series_empty_for_never_recorded_symbol` for a symbol
  with no recorded history — confirms both boundaries reach `compute_series`
  unchanged.
- **Negative control**: reverted both routes' `count` declaration to the
  pre-fix `Query(240, le=1000)` (no `ge=1`) and reran just the four new
  zero/negative tests in each file — all 4 **failed**: both `GET
  /market/candles` cases (`count=0`, `count=-1`) fell through validation and
  reached the route body, which then returned its ordinary `400` ("no
  historical provider connected") since the test symbol has no
  self-recorded data in this database — `assert 400 == 422`; both `GET
  /intelligence/series` cases similarly reached `compute_series` on a
  zero-width/inverted range and returned an ordinary `200` empty-series
  response — `assert 200 == 422`. Neither case happened to hit the
  `[-0:]`-whole-list slice bug directly (that needs the test symbol to
  actually have self-recorded/aggregated candles for it to return
  everything instead of nothing — described analytically in `CHANGES.md`,
  not separately reproduced here), but the core claim — invalid `count`
  values reached application logic instead of being rejected at validation
  — is exactly what this failure demonstrates. Restored `ge=1` on both
  routes and reran — all 8 passed again.
- Final `main` re-check immediately before packaging: identical to the
  task-start pull aside from this task's own files (this delivery's zip
  manifest, below) — no concurrent-session collision.

**Manual spot-check**, matching this file's own existing "Manually
exercising `/market/candles`" convention below: ran a real `uvicorn`
process (no provider connected, no data recorded for the test symbol).
`curl "http://localhost:8000/market/candles?symbol=NVDA&count=0"` and
`count=-1` both now return a `422` —
`{"detail":[{"type":"greater_than_equal","loc":["query","count"],"msg":
"Input should be greater than or equal to 1", ...}]}` — instead of reaching
the route body at all. The equivalent `GET
/intelligence/series?symbol=NVDA&count=0` returns the identical `422`
shape. `count=1` and `count=1000` on `GET /market/candles` both still
return the pre-existing, unchanged `400` ("no historical provider
connected") rather than a validation error — confirming both boundaries
still reach the route's own logic exactly as before.

**What this delivery deliberately didn't touch:** candle retrieval order
(self-recorded → aggregated → external provider on `GET /market/candles`;
self-recorded/aggregated only, no provider fallback, on `GET
/intelligence/series`), `compute_series`'s own computation, either route's
response shape, the `timeframe` parameter or its own unsupported-value
`400` handling, or the pre-existing `le=1000` upper bound and `240` default.
`test_intelligence_routes.py` has no corresponding row in `backend/README.md`'s
test table — a pre-existing documentation gap, present before this task and
unrelated to this change (the file already existed, undocumented, prior to
this delivery); noted here rather than fixed, per this project's own scope-
discipline convention, since fixing it would mean documenting roughly a
dozen pre-existing tests this delivery didn't write or touch.

Delivered as `count-lower-bound-validation.zip`: `backend/app/api/routes/
market.py` (edited — `count`'s `Query(...)` declaration plus an explanatory
comment; no other line changed), `backend/app/api/routes/intelligence.py`
(edited — the same, on `GET /intelligence/series`), `backend/tests/
test_market_routes.py` (4 new tests), `backend/tests/test_intelligence_routes.py`
(4 new tests), `backend/README.md` (edited — the `GET /market/candles`
bullet and the `test_market_routes.py` table row), `CHANGES.md` and this
file (both edited, this delivery's entry prepended, every prior entry
preserved intact below it).

<!-- Previous delivery record retained below. -->

# TESTING — `layout-import-fault-isolation`

Repository access was via `git clone --depth 1`, per this project's
documented convention. `main` was pulled at the start of this task; baseline
`358db5d` ("Daily levels lookback offload"), clean working tree. Re-pulled
`main` into a second, independent fresh clone immediately before packaging:
identical `358db5d` — nothing else landed on `main` in between. `diff -rq`
between that fresh clone and this task's working tree (ignoring `.git`,
`node_modules`, `dist`, and the regenerated `tsconfig.tsbuildinfo` build-cache
artifact, none of which are part of the delivery) shows exactly three files
differ: `frontend/src/state/WorkspaceContext.tsx`, `docs/architecture/
system-design.md`, and `CHANGES.md` — matching this delivery's stated
boundary exactly (this file, `TESTING.md`, is the fourth, edited after that
check).

This is a frontend-only change and, as recorded in the retained
`saved-layouts-restore-isolation` entry below, `frontend/package.json`
defines no test script and no `.test.`/`.spec.` file exists anywhere under
`frontend/` in this repository — unchanged as of this task. Verification
follows that same delivery's own posture: static/build checks plus direct
execution of the changed function, rather than a project test-runner run.

- **Direct execution of `normalizeImportedLayouts()`** — the actual function,
  not a reimplementation. `importLayouts()`'s parsing logic was pulled out
  into this new module-level function specifically so it, like
  `loadSavedLayouts()`, can be imported and called directly without going
  through React state. A temporary same-directory scratch copy of
  `WorkspaceContext.tsx` (`WorkspaceContext.verify.tsx`) additionally
  exported `normalizeImportedLayouts` and `normalizeSubWindow` (both
  otherwise module-private); `tsx` (Node 22, no browser/DOM) imported and
  called them directly via a scratch harness
  (`verify-import-layouts.mts`, run with `npx tsx`), relative imports
  resolving unchanged since the copy sits next to the real file.
  `frontend`'s dependencies were installed (`npm install`, 137 packages,
  none were present) so `crossTabSync.ts`'s `BroadcastChannel` import
  resolved. `normalizeImportedLayouts()` takes a JSON string directly and
  touches no `localStorage`, so no fake storage was needed for this harness
  (unlike `loadSavedLayouts()`'s own verification). Eight fixtures, 17
  assertions, all passed:
  - Wholly valid import, two well-formed layouts → both imported, names and
    order preserved, each given a fresh id distinct from the file's own ids,
    ids unique from each other, `subWindows` preserved (5/5).
  - Mixed: one entry has no `subWindows` key at all, between two valid
    entries → both valid entries imported in order, the malformed one
    skipped (2/2).
  - Mixed: one entry's `subWindows` present but not an array (a string) →
    same result, both valid entries imported (1/1).
  - Mixed: one entry is `null` outright → the two valid entries around it
    still import, in order (2/2).
  - Wholly invalid import — every entry malformed (`{subWindows: null}`, a
    bare string, a number, `null`) → degrades to an empty array, no thrown
    error (1/1).
  - Unparsable JSON entirely (`"{not json"`) → empty array, confirming the
    pre-existing outer try/catch (JSON.parse failure) still works after
    moving the per-entry logic inside it (1/1).
  - Non-array top level (a JSON object instead of an array) → empty array,
    pre-existing behavior unchanged (1/1).
  - A valid layout with a legacy sub-window (predating `chartStyle`,
    `timer`, `volumeAvg`, and the rest) still normalizes and backfills
    correctly through the per-entry path — confirms the fix only changed
    fault isolation and id assignment, not `normalizeSubWindow()`'s own
    backfill behavior, reused unchanged from the import path (4/4).
- **Genuine-regression-guard check.** Reverted the scratch copy's
  `normalizeImportedLayouts()` to the pre-fix single-`.map()`-with-outer-catch
  form (the same shape `importLayouts()` had before this delivery) and
  re-ran the identical harness: exactly the 5 assertions covering the three
  mixed valid/invalid fixtures failed (each collapsed to 0 imported entries
  instead of preserving the 2 valid ones — the reported bug, reproduced),
  while the other 12 (wholly-valid, wholly-invalid, unparsable-JSON,
  non-array-top-level, and legacy-backfill cases) still passed, since none
  of those exercise the fixed code path. Restored the fix and re-confirmed
  17/17 before proceeding. The scratch copy and the harness script were both
  deleted afterward — neither is part of this delivery.
- **Harness note:** same `BroadcastChannel`-keeps-the-process-alive
  consideration already recorded in the retained `saved-layouts-restore-
  isolation` entry below — the harness calls `process.exit()` explicitly.
  Harness-only; no production code path needed to change.
- **`npm run build`** (`tsc -b && vite build`) from `frontend/`, run twice —
  once with the scratch harness files present, once after they were deleted
  (the delivered state): both clean, 103 modules transformed, identical
  output hashes both times. Vite emitted its existing advisory for a bundle
  over 500 kB (`dist/assets/index-*.js` ~505 kB minified), unrelated to this
  change and consistent with prior deliveries' own notes on this baseline.
  The regenerated `tsconfig.tsbuildinfo` build-cache artifact was reverted
  (`git checkout --`) rather than included, matching this repository's own
  git-access convention for this kind of artifact.
- No live browser check was possible in this environment — same standing
  caveat every frontend delivery in this doc already carries.
- Final `diff -rq` against the independently fresh `main` pull immediately
  before packaging (see above): exactly three application/doc files differ,
  plus this pair (`CHANGES.md`/`TESTING.md`, both edited, this delivery's
  entry prepended, every prior entry preserved intact below it).

Package contains only the changed repository-relative files:
`frontend/src/state/WorkspaceContext.tsx` (edited — `importLayouts()`
reduced to a thin wrapper, new `normalizeImportedLayouts()` added;
`loadSavedLayouts()`, `normalizeSubWindow()`, `normalizeMainWindow()`,
`loadSession()`, and every other function untouched), `docs/architecture/
system-design.md` (edited — one new §4.11 note plus two diagrams,
immediately below the existing "Saved layout restoration resilience" note),
`CHANGES.md` and `TESTING.md` (both edited, this delivery's entry prepended,
prior entries preserved intact below it). Untouched, exactly as scoped:
`loadSavedLayouts()`, `normalizeSubWindow()`, session restoration, and every
other saved-layout interaction (save, load, delete, export).

<!-- Previous delivery record retained below. -->

# TESTING — `daily-levels-lookback-offload`

Pulled `origin/main` on branch `main` before editing; baseline `a3cdb17`,
clean working tree. A local PostgreSQL 16 instance was created for this
session (`trading`/`trading` role, `trading_workspace` database, `alembic
upgrade head` — 14 migrations, all applied cleanly) since none pre-existed
in this environment; no live broker or external production database was
used.

- Baseline, pre-change, full suite from `backend/` (untouched `main`,
  changes stashed): `python3 -m pytest -q` — **1139 passed**, 0 failed,
  94.76s.
- Same command with this delivery's changes restored: **1140 passed** —
  exactly +1 (the new concurrency test), 0 regressions, 91.35s.
- Negative control: temporarily reverted the route to its pre-fix
  synchronous call (keeping the new test as-is) and reran the new test
  alone — it **failed** with a `TimeoutError` waiting for
  `_compute_daily_levels_lookback` to start, confirming the test actually
  exercises the offload rather than passing unconditionally. Restored the
  fix and reran — **passed** again.
- `python3 -m pytest tests/test_intelligence_history_read_concurrency.py
  -q`, repeated 5x: **3 passed** every time, 0.91–1.00s each — deterministic,
  no flakiness.
- `python3 -m pytest tests/test_intelligence_routes.py
  tests/test_intelligence_helpers.py
  tests/test_intelligence_history_read_concurrency.py -q`: **23 passed**,
  including both existing Daily Levels tests
  (`test_daily_levels_appear_in_intelligence_state`,
  `test_daily_levels_carry_level_interaction_once_touched`) against the
  real database — confirms the default (no-lookback) path and existing
  Daily Levels behavior are unaffected.
- `diff -rq` against a fresh `git clone --depth 1` of the same baseline
  commit (ignoring `.env`, `.pytest_cache`, and `__pycache__`, none of
  which are tracked): exactly two files differ —
  `backend/app/api/routes/intelligence.py` and
  `backend/tests/test_intelligence_history_read_concurrency.py` — matching
  this delivery's stated boundary exactly.
- Benchmarked `cluster_daily_levels()` directly (standalone module import,
  no package/DB dependency) to quantify the blocking risk cited in
  `CHANGES.md` and the architecture doc: realistic 360-candle random-walk
  cache 2.617ms; pathological all-near-identical-price 360-candle cache
  21.233ms; realistic 1000-candle cache 6.193ms; pathological 1000-candle
  cache 156.905ms. Investigation only, not part of the shipped test suite.

<!-- Previous delivery record retained below. -->

# TESTING — `saved-layouts-restore-isolation`

Repository access was via the tarball endpoint
(`codeload.github.com/.../tar.gz/refs/heads/main`), not `git clone`, per this
project's own documented access convention — so there is no local git history
to point at, only content diffs against fresh pulls. `main` was pulled at the
start of this task; `docs/decisions/INDEX.md` and `confirmed-decisions.md`
both ended at #182, agreeing with the latest archive file (`archive/134-160.md`
plus decisions #161–182 living directly in `confirmed-decisions.md`) — no
archive rollover pending. Re-pulled `main` again immediately before packaging:
identical aside from this task's own four files
(`WorkspaceContext.tsx`, `docs/architecture/system-design.md`, `CHANGES.md`,
`TESTING.md`) — nothing else landed on `main` in between.

This is a frontend-only change and `frontend/package.json` defines no test
script; no `.test.`/`.spec.` file exists anywhere under `frontend/` in this
repository. Verification follows the same posture as every other
frontend-only delivery already recorded in this file (e.g.
`scanner-panel-session-restore`) — static/build checks plus direct execution
of the changed function, rather than a project test-runner run:

- **Direct execution of `loadSavedLayouts()`** — the actual function, not a
  reimplementation. A temporary same-directory scratch copy of
  `WorkspaceContext.tsx` exported `loadSavedLayouts` and `normalizeSubWindow`
  (both otherwise module-private) so `tsx` (Node 22, no browser/DOM) could
  import and call them directly; relative imports (`../types/workspace`,
  `./crossTabSync`) resolve unchanged since the copy sits next to the real
  file. `frontend`'s dependencies were installed (`npm install`, none were
  present) so `react`/`crossTabSync`'s `BroadcastChannel` import resolved. A
  fake in-memory `localStorage` backed the `trading-workspace:saved-layouts`
  key. Eight fixtures, 14 assertions, all passed:
  - Wholly valid storage, two well-formed saved layouts → both restored,
    names and order preserved (2/2).
  - Mixed: entry 2 has no `subWindows` key at all (the exact bug report —
    "missing subWindows") between two valid entries → both valid entries
    restored, the malformed one skipped (2/2).
  - Mixed: entry 2's `subWindows` present but not an array (a string) →
    same result, both valid entries restored (2/2).
  - Mixed: entry 1 is `null` outright (hand-edited/corrupted storage) →
    the two valid entries after it still restore (2/2).
  - Wholly invalid storage — every entry malformed
    (`{subWindows: null}`, a bare string, a number) → degrades to an empty
    array, no thrown error (1/1).
  - Unparsable JSON entirely (`"{not json"`) → empty array, confirming the
    pre-existing outer try/catch (JSON.parse failure) still works after
    moving the per-entry logic inside it (1/1).
  - Storage key entirely absent → empty array, pre-existing behavior (1/1).
  - A valid layout with a legacy sub-window (predating `chartStyle`,
    opacities, `timer`, `volumeAvg`, `volumeBars`, `dailyLevelsConfig`,
    `hud`) still normalizes and backfills correctly through the per-entry
    path — confirms the fix only changed fault isolation, not
    `normalizeSubWindow()`'s own backfill behavior (3/3).
- **Genuine-regression-guard check.** Reverted the scratch copy's
  `loadSavedLayouts()` to the pre-fix single-`.map()`-with-outer-catch form
  and re-ran the identical harness: exactly the 6 assertions covering the
  three mixed valid/invalid fixtures failed (each collapsed to 0 restored
  layouts instead of preserving the 2 valid ones — the reported bug,
  reproduced), while the other 8 (all-valid, wholly-invalid, unparsable-JSON,
  missing-key, and legacy-backfill cases) still passed, since none of those
  exercise the fixed code path. Restored the fix and re-confirmed 14/14
  before proceeding. The scratch copy (`WorkspaceContext.verify.tsx`) and the
  harness script (`verify-saved-layouts.mts`) were both deleted afterward —
  neither is part of this delivery.
- **Harness note:** `crossTabSync.ts` opens a module-scoped `BroadcastChannel`
  (a real Node 22 global) on import, which keeps a bare `tsx` process alive
  indefinitely — the harness calls `process.exit()` explicitly after its
  assertions rather than relying on natural process exit. This is a harness-
  only concern; no production code path needed to change.
- **`npm install`** in `frontend/` — no `node_modules` were present in this
  fresh tarball pull; installed cleanly (137 packages) before any build or
  harness run.
- **`npm run build`** (`tsc -b && vite build`) from `frontend/`: clean, 103
  modules transformed. Vite emitted its existing advisory for a bundle over
  500 kB (`dist/assets/index-*.js` ~505 kB minified), unrelated to this
  change and consistent with prior deliveries' own notes on this baseline.
  The regenerated `tsconfig.tsbuildinfo` build-cache artifact was left out of
  the packaged delivery (this session has no `.git` to `checkout --` it back
  with, since access is by tarball; it is simply excluded from the zip
  rather than tracked-content).
- No live browser check was possible in this environment — same standing
  caveat every frontend delivery in this doc already carries.
- `diff -rq` against the final, independently fresh `main` pull immediately
  before packaging: exactly four files differ —
  `frontend/src/state/WorkspaceContext.tsx` (edited, `loadSavedLayouts()`
  only), `docs/architecture/system-design.md` (edited, new §4.11 note plus
  two diagrams), and this pair (`CHANGES.md`/`TESTING.md`, both edited, this
  delivery's entry prepended, every prior entry preserved intact below it).

Package: `saved-layouts-restore-isolation.zip` contains only the changed
repository-relative files — `frontend/src/state/WorkspaceContext.tsx`
(edited, `loadSavedLayouts()` only), `docs/architecture/system-design.md`
(edited, one new note plus two diagrams in §4.11), `CHANGES.md` and
`TESTING.md` (both edited, this delivery's entry prepended, prior entries
preserved intact below it). Untouched, exactly as scoped: `normalizeSubWindow`,
`normalizeMainWindow`, `loadSession`, every workspace interaction
(`saveCurrentLayout`, `loadLayout`, `deleteLayout`, `exportLayouts`,
`importLayouts`), the `trading-workspace:session` format, every panel, every
API call, and every backend file. No decision number was assigned, following
the `scanner-panel-session-restore`/`feature-engine-panel-session-restore`
precedent — this repairs an existing normalization/fallback gap using the
codebase's own established try/catch convention, nothing new decided.

<!-- Previous delivery record retained below. -->

# TESTING — `info-panel-session-restore`

Pulled `origin/main` on branch `main` before editing; baseline `8109426`.
The working tree already contained an unrelated, uncommitted analytics
delivery, including earlier sections in this file and `CHANGES.md`; those
changes were preserved. No database was used or changed for this task.

- Directly executed the actual `normalizeMainWindow()` function extracted
  from `WorkspaceContext.tsx` and transpiled with the installed TypeScript
  compiler. Six fixtures passed: old session missing both Info fields;
  partial sessions missing either collapse or width; current sessions
  explicitly collapsed or expanded with custom widths; and current
  session with explicit defaults. Every fixture also checked preservation
  of Scanner, Feature Engine, backtest IDs, and sub-window normalization.
  The harness checked that `makeMainWindow()` declares `false` and `300`.
- `npm run build` from `frontend/`: passed (`tsc -b` and Vite; 103 modules).
  Vite emitted its existing advisory for a bundle over 500 kB.
- `git diff --check`: passed. The tracked TypeScript build cache was
  restored after the successful build; it was clean before this task.

There is no frontend test script in `frontend/package.json`; the direct
function fixtures cover the changed restoration path. No Info panel
interaction code changed.

<!-- Previous delivery record retained below. -->

# TESTING — `performance-analytics-route-read-offload`

Pulled `origin/main` on branch `main` before editing; baseline `8109426`,
clean working tree. The configured local PostgreSQL development database
was used by the existing analytics route/query tests; their fixtures clean
their own `strategy_outcomes` rows. No live broker or external production
database was used.

- Focused analytics run from `backend/`: `.venv/bin/pytest -q --tb=short
  --disable-warnings tests/test_performance_analytics_route_concurrency.py
  tests/test_performance_analytics_routes.py tests/test_performance_queries.py`
  — **20 passed**. Each new test blocks its query with `threading.Event`,
  proves `/health` responds before releasing it, and checks forwarding of
  all three filters and the unchanged empty response envelope.
- Adjacent backend run: `.venv/bin/pytest -q --tb=short --disable-warnings
  tests/test_intelligence_routes.py
  tests/test_intelligence_history_read_concurrency.py
  tests/test_performance_intelligence.py
  tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py` —
  **40 passed**.
- The initial sandboxed analytics run had two worker-thread timeouts and
  skipped 18 database tests, matching the previously documented sandbox
  thread/database restriction. Both runs above passed outside the sandbox.

<!-- Previous delivery record retained below. -->

# TESTING — `feature-engine-panel-session-restore`

Pulled `origin/main` on branch `main` before editing; baseline `533f854`,
clean working tree. No database was used or changed.

- Directly executed the actual `normalizeMainWindow()` function extracted
  from `WorkspaceContext.tsx` and transpiled with the installed TypeScript
  compiler. Six fixtures passed: legacy session missing all three Feature
  Engine fields; three partial sessions each retaining one explicit field;
  current session with `false`, custom width, and selected symbol; and
  current session with explicit defaults. Each case also checked that
  Scanner state, backtest run ID, and a sub-window were preserved.
- `npm run build` from `frontend/`: passed (`tsc -b`, then Vite; 103 modules).
  Vite reported its existing advisory for a bundle over 500 kB.
- `git diff --check`: passed.

There is no frontend test script or test runner in `frontend/package.json`.
The direct function fixtures cover restoration values; no browser interaction
check was performed because panel behavior was outside this change.

<!-- Previous delivery record retained below. -->

# TESTING — `intelligence-history-read-offload`

Current branch: `main`, baseline commit `1def0b3`. At inspection, the two
route edits and the new concurrency test were already uncommitted work in the
tree. This delivery verified and completed that work; it did not recreate it.

- Focused backend run, from `backend/`:
  `.venv/bin/pytest -q --tb=short --disable-warnings
  tests/test_intelligence_history_read_concurrency.py
  tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py
  tests/test_backtest_runs_route.py tests/test_execution_orders_route.py` —
  **42 passed**. The concurrency test covers both changed routes with a
  blocked worker read and a concurrent `/health` request. Existing database
  route tests cover filtering, ordering, limits, empty results, and response
  serialization.
- The configured local development PostgreSQL database (`localhost:5432`,
  `trading_workspace`) was at Alembic revision `0011`; it was upgraded to
  repository head `0014` before the passing run. The first focused run failed
  because the older schema lacked the current `orders` table and outcome
  columns. Test fixtures cleaned their own rows; no live broker or external
  production database was used.
- Tests ran outside the command sandbox because a standalone
  `asyncio.to_thread(lambda: 1)` hung inside it but returned `1` immediately
  outside it. The sandbox symptom also affected the existing scanner
  concurrency test. This is an execution-environment limitation, not an
  application failure.

<!-- Previous delivery record retained below. -->

# TESTING — `execution-panel-order-history` (decision #182)

Pulled `main` before editing: `33721e4a73ab1836f932370003c4f2e3c6bc0e91`,
clean working tree. Rechecked `origin/main` immediately before numbering:
the same commit; `INDEX.md` and `confirmed-decisions.md` ended at #181,
and the latest archive ends at #160. Assigned #182 at the true log tail.

- `npm run build` from `frontend/`: passed (`tsc -b` and `vite build`). Vite
  emitted its existing advisory about a bundle over 500 kB; build succeeded.
- Direct React server rendering of the actual `RecentSimulatedOrders` JSX:
  passed loading, empty, populated, and request-error visible-state checks.
  The temporary `/tmp` harness exported the existing component from a copy,
  injected each load state, and compiled with the installed esbuild/React;
  it did not alter the repository. The populated fixture checked both rows,
  newest-first order, symbol, side, effect, quantity, status, venue, and exit
  and rejection reasons. Empty and error messages were checked as distinct.
- `git diff --check`: passed.

There is no frontend test runner or browser automation dependency in this
repository. The direct rendering check validates displayed states; it does
not exercise browser clicks or a live backend request. The route's simulated
filter, bounded default, and newest-first result are existing decision #181
behavior covered by `backend/tests/test_execution_orders_route.py`; this
frontend delivery changed no backend code or database state.

<!-- Previous delivery record retained below. -->

# TESTING — `scanner-panel-session-restore`

Repository access was via the tarball endpoint
(`codeload.github.com/.../tar.gz/refs/heads/main`), not `git clone`, per
this project's own documented access convention — so there is no local
git history to point at, only content diffs against fresh pulls. `main`
was first pulled before `execution-orders-route` (decision #181) had
landed; a re-pull surfaced it, and `diff -rq` against that fresh pull
confirmed zero overlap with this task's own file
(`frontend/src/state/WorkspaceContext.tsx` was byte-identical to the
pre-#181 baseline), so the work was carried over onto the newer base
rather than merged by hand. The GitHub API independently confirmed the
tip of `main` at that point as commit `7f8a0c90981fc6e8a99a943695031417dcc27411`.
Re-checked immediately before packaging via a third independent pull:
still identical aside from this task's own three files
(`WorkspaceContext.tsx`, `docs/architecture/scanner-design.md`,
`CHANGES.md`) — nothing further landed on `main` in between.

This is a frontend-only change and `frontend/package.json` defines no test
script; no `.test.`/`.spec.` file exists anywhere under `frontend/` in this
repository. Verification therefore follows the same posture as every other
frontend-only delivery already recorded in `scanner-design.md` (§11's own
standing caveat) — static/build checks plus direct execution of the changed
function, rather than a project test-runner run:

- **Direct execution of `normalizeMainWindow()`** — the actual function,
  not a reimplementation. A temporary same-directory scratch copy of
  `WorkspaceContext.tsx` exported `normalizeMainWindow` and `makeMainWindow`
  (both otherwise module-private) so `tsx` (Node 22, no browser/DOM) could
  import and call them directly; relative imports (`../types/workspace`,
  `./crossTabSync`) resolve unchanged since the copy sits next to the real
  file. Five fixtures, ten assertions, all passed:
  - Fully old session (both `scannerCollapsed`/`scannerWidthPx` keys
    absent, as a real pre-Scanner-panel `JSON.parse` would produce) →
    backfills to `scannerCollapsed: true`, `scannerWidthPx: 300` (2/2).
  - Current session with explicit non-default values
    (`scannerCollapsed: false`, `scannerWidthPx: 420`) → both preserved
    exactly, confirming `??` does not treat an explicit `false` as missing
    (2/2).
  - Current session with explicit default values (`true`/`300`) → passes
    through unchanged (2/2).
  - Partial/hand-edited session — `scannerCollapsed` absent,
    `scannerWidthPx: 500` present — each field backfills or is preserved
    independently of the other (2/2).
  - Unrelated fields on the same object (`lastBacktestRunId`,
    `subWindows`) — confirmed still correct, guarding against the change
    accidentally affecting anything else `normalizeMainWindow` does (2/2).
- **Genuine-regression-guard check.** Re-ran the identical harness against
  the function with the two new backfill lines removed: exactly the 3
  assertions covering the two missing-field cases failed (falling back to
  `undefined`, as the pre-fix code actually does), the other 7 (explicit-
  value and unrelated-field cases) still passed as expected since those
  don't exercise the missing lines. Restored the fix and re-confirmed
  10/10 before proceeding. The scratch copy (`WorkspaceContext.verify.tsx`)
  and the harness script were both deleted afterward — neither is part of
  this delivery.
- **`npx tsc -b`** — clean, 0 errors (this repository's checked-in
  `tsconfig.tsbuildinfo` build cache was regenerated by this run and then
  reverted with `git checkout --`, since it's a generated artifact
  unrelated to this task's scope, not a tracked-content change).
- **`npx vite build`** — clean production build, 103 modules transformed,
  no new warnings beyond the pre-existing chunk-size advisory
  (`dist/assets/index-*.js` at ~502 kB minified, unrelated to this change).
- No live browser check was possible in this environment — same standing
  caveat every frontend delivery in this doc already carries (§11).
- `diff -rq` against a final, independently fresh clone of `main`
  immediately before packaging (confirmed still at the same content as
  `a2317ac`): exactly three files differ —
  `frontend/src/state/WorkspaceContext.tsx` (edited),
  `docs/architecture/scanner-design.md` (edited, new §15 plus one clause
  in §12), and this pair (`CHANGES.md`/`TESTING.md`, both edited, this
  delivery's entry prepended, the `execution-orders-route` /
  `scanner-override-ticker-validation` entries preserved intact below it).

Package: `scanner-panel-session-restore.zip` contains only the changed
repository-relative files — `frontend/src/state/WorkspaceContext.tsx`
(edited, two lines plus a comment inside `normalizeMainWindow`),
`docs/architecture/scanner-design.md` (edited, new §15 plus one clause
appended to §12), `CHANGES.md` and `TESTING.md` (both edited, this
delivery's entry prepended, prior entries preserved intact below it).
Untouched, exactly as scoped: `ScannerPanel.tsx`, `normalizeSubWindow`,
`loadSession`/`loadSavedLayouts`, every API call and polling hook, universe
editing, every other panel (`featureEngineCollapsed`/`featureEngineWidthPx`
keep the identical, still-undocumented-as-fixed gap — out of this task's
scope), and every backend file.

<!-- Previous delivery record retained below. -->

# TESTING — decision #181: `execution-orders-route`

GitHub `main` was `0546492f30c66d8434acf36f7806523655d14ee8`
(`scanner-route-db-offload`) when this task was assigned and first pulled.
Mid-task, a fresh baseline pull surfaced a new commit,
`bbac618786595faf6978458d559a4c69b351bc43` (`scanner-override-ticker-validation`)
— `diff -rq` against a freshly re-pulled `main` confirmed it touched only
`backend/app/api/routes/scanner.py`, `backend/tests/test_scanner_runner.py`,
`backend/tests/test_scanner_state_route.py`, and
`docs/architecture/scanner-design.md` — zero overlap with this task's own
exclusive file, `backend/app/api/routes/intelligence.py`. This delivery's
two already-written files (`intelligence.py`, the new test file) were copied
onto that fresh `bbac618` clone rather than merged by hand, migrations
re-run (already at head — no new migration), and the full suite re-run clean
before continuing. Re-checked immediately before packaging: `git ls-remote`
against the same URL still returned `bbac618786595faf6978458d559a4c69b351bc43`
— nothing further landed. The canonical index and log both ended at #180;
next number #181 assigned and written to both `confirmed-decisions.md` and
`INDEX.md` in this same change, per protocol.

No PostgreSQL was preinstalled in this environment — installed PostgreSQL
16 locally, started it, created the `trading`/`trading_workspace` role and
database per this project's own documented convention, and ran
`alembic upgrade head` (through `0014` — no new migration exists for this
delivery, which adds no schema). No pre-existing development database,
broker account, or external service was touched.

- Full backend suite baseline, before any code change (at `0546492`):
  `pytest -q` — **1111 passed, 0 failed**.
- Baseline re-confirmed after rebasing onto `bbac618`
  (`scanner-override-ticker-validation` applied, this delivery's files not
  yet copied over): **1122 passed, 0 failed** — matches that delivery's own
  `CHANGES.md` entry exactly.
- New focused file: `pytest -q tests/test_execution_orders_route.py -v` —
  **13 passed**. Covers descending-`orders.id` ordering (three hand-inserted
  rows, asserted in reverse insertion order); exact-`symbol` filtering,
  including that a lowercase (`zzxo4`) and a substring (`ZZX`) query each
  return `[]` rather than matching; hard exclusion of an `execution_mode ==
  "backtest"` row (same `execution_venue == "simulated"`, passing the DB's
  own mode/venue CHECK) from the `simulated`-only route with no parameter
  able to request it; `limit` capping to the most recent N rows; rejection of
  `limit=0` and `limit=101` with 422; acceptance at both bound edges (1 and
  100); the default-limit path requiring no `limit` param; an honest
  `{"orders": []}` for a symbol with zero matching rows; response-shape
  verification — `id`/`client_order_id`/`symbol`/`side`/`position_effect`/
  `qty`/`status`/`execution_venue`/`exit_reason`/`reject_reason` field values
  asserted directly, `trade_id` asserted equal to `str(uuid)` (not a raw
  UUID object), `created_at`/`updated_at` round-tripped through
  `datetime.fromisoformat()` confirming real ISO-8601 with timezone info,
  and `order_type`/`limit_price`/`venue_order_id`/`execution_mode` asserted
  absent from the response (curated fields only, not a full-row dump); a
  nullable `reject_reason` populated on a `status="rejected"` row; and a
  concurrency regression (`test_blocked_execution_orders_read_does_not_block_
  an_unrelated_route`) using the same deterministic `threading.Event`
  start/release technique as `test_scanner_route_concurrency.py` — monkeypatches
  the module-level `_fetch_execution_orders` to block until released, confirms
  it is actually running in a worker thread (`started.wait`), then confirms a
  concurrent `GET /health` still completes in under 2 seconds before
  releasing the block and confirming the original request completes too.
- Verified the new tests are a genuine regression guard, not false
  positives: temporarily replaced `app/api/routes/intelligence.py` with its
  pre-delivery form (copied from a fresh, untouched clone) and re-ran the
  same file — **12 of 13 failed, 1 errored** (every route-hitting assertion
  got a 404 instead of 200/422, since the route did not exist; the
  concurrency test errored outright since `_fetch_execution_orders` did not
  exist to monkeypatch). Restored the real implementation and re-ran —
  **13 passed** again before proceeding.
- Full backend suite with the delivery applied: `pytest -q` —
  **1135 passed, 0 failed** (exactly +13 versus the 1122 post-rebase
  baseline — the new route tests; zero regressions elsewhere, no flaky-
  intermittent failure observed on this run).
- `diff -rq` against a final, independently fresh clone of `main`
  (confirmed still at `bbac618`) immediately before packaging: exactly six
  files differ — `backend/app/api/routes/intelligence.py` (edited),
  `backend/tests/test_execution_orders_route.py` (new),
  `docs/architecture/execution-engine-design.md` (edited),
  `docs/decisions/confirmed-decisions.md` (edited),
  `docs/decisions/INDEX.md` (edited), and this pair (`CHANGES.md`/
  `TESTING.md`, both edited, this delivery's entry prepended, the sibling
  `scanner-override-ticker-validation` entry preserved intact below it).
- Frontend: not touched by this delivery (backend-only observation
  endpoint, exclusive scope per the approved task); no frontend check run.
- `git diff --check` — passed (no whitespace errors).

Package: `execution-orders-route.zip` contains only the changed
repository-relative files — `backend/app/api/routes/intelligence.py`
(edited), `backend/tests/test_execution_orders_route.py` (new),
`docs/architecture/execution-engine-design.md` (edited, §6.3 as-built note
plus two new diagrams, §6.8 table annotation), `docs/decisions/
confirmed-decisions.md` (edited, new #181 entry appended), `docs/decisions/
INDEX.md` (edited, new #181 row appended), `CHANGES.md` and `TESTING.md`
(both edited, this delivery's entry prepended, the
`scanner-override-ticker-validation` entry preserved intact below it).
Untouched, exactly as scoped: `models/execution_ledger.py` (imported, not
edited), every `governor/`, `execution_engine/`, `portfolio_state/`, and
scanner file, `main.py`, every frontend file, any Alembic migration, and
EX-5/EX-12.

<!-- Previous delivery record retained below. -->

# TESTING — `scanner-override-ticker-validation`

GitHub `main` was pulled fresh at task start; re-pulled and `diff -rq`'d
against the working tree immediately before packaging — identical except
for this delivery's own three changed/new files (`backend/app/api/routes/scanner.py`,
`backend/tests/test_scanner_runner.py`, `backend/tests/test_scanner_state_route.py`).
No concurrent changes to reconcile; the sibling `scanner-route-db-offload`
delivery (immediately below) had already landed on `main` before this task
pulled it, confirmed by its `asyncio.to_thread` wrapping and
`test_scanner_route_concurrency.py` both already present in the fresh pull.
The canonical index and log both end at #180; per the same standing
instruction that delivery's own entry states, reusing an existing,
already-decided validation rule at a second call site needs no new
decision number.

No PostgreSQL was preinstalled in this environment — installed PostgreSQL
16.15 locally, started it, created the `trading`/`trading_workspace` role
and database per this project's own documented convention, and ran
`alembic upgrade head` (through `0014`). No pre-existing development
database, broker account, or external service was touched.

- Full backend suite baseline, before any code change: `pytest -q` —
  **1111 passed, 0 failed**.
- New focused file: `pytest -q tests/test_scanner_state_route.py -v` —
  **11 passed**. Covers valid multi-symbol normalization (trim/uppercase,
  cross-checked against `run_scan`'s own honest `skipped` list, not just
  the echoed `universe` field), duplicate-entry dedup preserving
  first-seen order, a `BRK.B`-style share-class suffix accepted, a
  lowercase-only input NOT rejected for case (the other direction of the
  rule), an invalid-format ticker (400, message names the bad entry), a
  too-long ticker (400), an empty entry from a stray internal comma (400),
  a trailing comma (400), an explicitly empty `?symbols=` (400), a
  whitespace-only override (400), and the omitted-parameter path (no
  `symbols` key in the query string at all) asserting a non-empty
  `universe` list and never a 400.
- Verified the new tests are a genuine regression guard, not false
  positives: temporarily reverted `app/api/routes/scanner.py` to its
  pre-fix form (`git show HEAD:...` from the pre-edit baseline commit) and
  re-ran the same file — **7 of 11 failed** (every 400-expecting case
  returned 200 instead, since the old code only stripped/uppercased with
  no validation at all). Restored the fix and re-ran — **11 passed**
  again before proceeding.
- All four Scanner test files together: `pytest -q tests/test_scanner.py
  tests/test_scanner_runner.py tests/test_scanner_universe.py
  tests/test_scanner_route_concurrency.py tests/test_scanner_state_route.py`
  — **30 passed** (the pre-existing 19 unchanged, plus the 11 new).
- Full backend suite with the delivery applied: `pytest -q` —
  **1122 passed, 0 failed** (exactly +11 versus the 1111 baseline — the
  new route tests; zero regressions elsewhere, no flaky-intermittent
  failure observed on this run).
- Frontend: not touched by this delivery (backend-only, exclusive scope
  per the approved task); no frontend check run.
- `git diff --check` — passed.

Package: `scanner-override-ticker-validation.zip` contains only the
changed repository-relative files — `backend/app/api/routes/scanner.py`
(edited), `backend/tests/test_scanner_runner.py` (docstring correction
only), `backend/tests/test_scanner_state_route.py` (new),
`docs/architecture/scanner-design.md` (edited, new §14), `CHANGES.md` and
`TESTING.md` (both edited, this delivery's entry prepended, the sibling
`scanner-route-db-offload` entry preserved intact below it). Untouched,
exactly as scoped: `app/scanner/universe.py` (called, not edited),
`app/scanner/runner.py`, `app/scanner/scorer.py`, `main.py`, every
execution/frontend file, universe CRUD behavior, scoring, ranking,
`top_n`, and the continuous `MarketActivityScanner`/`ScanCadenceSchedule`/
promotion path.

<!-- Previous delivery record retained below. -->

# TESTING — `scanner-route-db-offload`

GitHub `main` was `fc1b674427b4a0062ebb1b55f11a4369099ea22b` (`execution-startup-fail-closed`)
when this task was assigned and first pulled. Re-pulled after being told "git
is updated": `c1e347cab8e03ac73513142fd67eee9c648ea157` (`position-monitor-lite`)
— `diff -rq` against the prior pull confirmed zero overlap with Scanner scope
(only `backend/app/main.py`, `backend/app/api/routes/health.py`,
`backend/tests/test_execution_startup_status_route.py`,
`docs/architecture/execution-engine-design.md`, and the decision log/
`CHANGES.md`/`TESTING.md` changed — none touched by this delivery). Re-checked
again immediately before packaging: GitHub `main` still `c1e347c...`,
unchanged. The canonical index and log both end at #180; per standing
instruction, a pure event-loop offload with no behavior change needs no new
decision number.

No PostgreSQL was preinstalled in this environment — installed PostgreSQL
16.15 locally, started it, created the `trading`/`trading_workspace` role and
database per this project's own documented convention, and ran `alembic
upgrade head` (through `0014`). No pre-existing development database, broker
account, or external service was touched.

- Full backend suite baseline, before any code change: `pytest -q` —
  **1109 passed, 0 failed**.
- Scanner-focused baseline: `pytest -q tests/test_scanner.py
  tests/test_scanner_runner.py tests/test_scanner_universe.py` —
  **17 passed**.
- After the `asyncio.to_thread` change: same three files, unchanged —
  **17 passed**. Confirms response shapes, scoring, and validation are
  untouched, as expected from a boundary-only change.
- New focused file: `pytest -q tests/test_scanner_route_concurrency.py` —
  **2 passed**. One test proves `GET /health` still responds while a
  `GET /scanner/universe` request is deliberately blocked (a
  `threading.Event`-controlled fake `list_universe_symbols`) in its worker
  thread; the other proves two concurrently blocked `GET /scanner/universe`
  requests both complete rather than one starving the other. Both use
  explicit `threading.Event`s to synchronize — no sleeps.
- Verified the concurrency test is a genuine regression guard, not a false
  positive: temporarily reverted `GET /scanner/universe` to call
  `list_universe_symbols` directly (no `asyncio.to_thread`) and re-ran the
  same file — **both tests failed** (timed out waiting for the blocked call
  to release, since it was now running on the same event loop the test
  itself needed to send its second request). Re-applied the fix and
  confirmed **2 passed** again before proceeding.
- All four Scanner test files together: `pytest -q tests/test_scanner.py
  tests/test_scanner_runner.py tests/test_scanner_universe.py
  tests/test_scanner_route_concurrency.py` — **19 passed**.
- Full backend suite with the delivery applied: `pytest -q` —
  **1111 passed, 0 failed** (exactly +2 versus the 1109 baseline — the two
  new concurrency tests; zero regressions elsewhere, no flaky-intermittent
  failure observed on this run).
- Frontend: not touched by this delivery (backend-only, exclusive scope); no
  frontend check run.
- `git diff --check` — passed.

Package: `scanner-route-db-offload.zip` contains only the changed
repository-relative files — `backend/app/api/routes/scanner.py` (edited),
`backend/tests/test_scanner_route_concurrency.py` (new),
`docs/architecture/scanner-design.md` (edited, new §13), `CHANGES.md` and
`TESTING.md` (both edited, this delivery's entry prepended). Untouched,
exactly as scoped: `app/scanner/universe.py`, `app/scanner/runner.py`,
`app/scanner/scorer.py`, `main.py`, every execution and frontend file, and
the continuous `MarketActivityScanner`/promotion/cadence path.

<!-- Previous delivery record retained below. -->

# TESTING — decision #180: `execution-startup-status`

Initial local `main` and GitHub `main` both `fc1b674427b4a0062ebb1b55f11a4369099ea22b`
(`execution-startup-fail-closed`, decision #179); working tree clean. No
PostgreSQL cluster was preinstalled in this environment — installed PostgreSQL
16.15 locally, started it, and created `trading`/`trading_workspace` per this
project's own documented convention plus an isolated `execution_startup_status_test`
database, migrated through `0014`. No pre-existing development database, broker
account, or external service was touched.

- New focused file: `pytest -q tests/test_execution_startup_status_route.py
  --tb=short --disable-warnings` — **4 passed**. Covers a route call with no
  active lifespan (direct ASGI call against the unstarted app, no `with
  TestClient`) reporting `unavailable`; a real-lifespan clean startup reporting
  `ready`, then `unavailable` again after that lifespan's own shutdown; an
  injected reconciliation discrepancy (3 synthetic mismatches) reporting
  `reconciliation_blocked` with `discrepancy_count: 3` and asserting the raw
  discrepancy text never reaches the response body; and an injected
  `PositionMonitor.start()` failure after the authorizer/execution engine have
  already started, reporting `startup_failed` after the existing #179 rollback
  completes, asserting the caught exception's own text never reaches the
  response body either.
- Adjacent regression command: `pytest -q tests/test_execution_startup_status_route.py
  tests/test_main_execution_pipeline.py tests/test_exit_intents_route.py
  tests/test_world_view_portfolio.py tests/test_world_view.py
  tests/test_entry_lifecycle_wiring.py tests/test_governor_engine.py
  tests/test_execution_engine.py tests/test_event_bus.py
  tests/test_position_monitor_engine.py tests/test_portfolio_worker.py
  tests/test_portfolio_state.py tests/test_reconciliation.py
  tests/test_position_monitor_portfolio_reader.py tests/test_simulated_venue.py
  tests/test_intelligence_routes.py --tb=short --disable-warnings` —
  **123 passed**.
- Full backend suite baseline, changes stashed (`git stash -u`) on the
  untouched `fc1b674` tree: **1105 passed, 0 failed**.
- Full backend suite with the delivery restored: **1108 passed, 1 failed**
  first run (`test_backtest_routes.py::test_two_separate_runs_isolate_level_interaction_state_and_events`,
  a VWAP-state-equality assertion unrelated to any file this delivery
  touches), then **1109 passed, 0 failed** on an immediate re-run with no
  code change. Confirmed pre-existing and unrelated, not attributed to this
  work without evidence: isolated the single test and ran it 2x against the
  changed tree (1 fail / 1 pass) and 4x against the untouched `fc1b674`
  baseline (4/4 passed there), then 6x more against the changed tree (6/6
  passed) — consistent with genuine intermittency in that unrelated test, not
  a regression this delivery introduced. Net delta from the 1105 baseline is
  exactly +4 (this delivery's own new tests); zero other regressions.
- Frontend: `npx tsc -b` — clean (only the pre-existing #166-excluded
  `GridPresetPicker.tsx` sketch stays out of the active program, as before).
  `npm run build` — clean (`tsc -b && vite build`, same bundle-size-only
  warning this project already has). The build-generated
  `frontend/tsconfig.tsbuildinfo` was restored (`git checkout --`) after the
  build and is not packaged.
- `git diff --check` — passed.

Immediately before numbering, GitHub `main` was re-checked and still matched
local `fc1b674`; the canonical index and log both ended at #179, and the
newest archive was `134-160.md`. Decision #180 was appended at the true end
of `confirmed-decisions.md` and indexed in `INDEX.md` only after that recheck.

Package: `execution-startup-status.zip` contains only the changed
repository-relative files.

<!-- Previous delivery record retained below. -->

# TESTING — decision #179: `execution-startup-fail-closed`

Initial local `main` and GitHub `main`: `fd3657157c2e840963f4916fe7d9dc53d2fef387`;
working tree clean. The isolated PostgreSQL 18.6 cluster was initialized in
`/tmp/execution-startup-fail-closed-pg`, listening only on local port 55438.
The new `execution_startup_fail_closed_test` database was migrated through
`0014`. No existing development database, broker account, or external service
was changed.

- Real-lifespan injection: `PositionMonitor.start()` completes after the
  authorizer and execution engine have started, queues an `OpportunityCreated`,
  then raises. `/health` returns 200 while the execution venue registry role,
  World View reader, and monitor read reference are unavailable; every started
  pipeline worker has stopped. A subsequent price and opportunity are valid for
  approval if the gate were live, but write no approved trade or order. Stale
  copied callbacks leave worker queues empty. The normal-start regression still
  exposes a venue, restored Portfolio State, and running entry workers.
- Initial focused command: `pytest -q tests/test_main_execution_pipeline.py
  tests/test_entry_lifecycle_wiring.py tests/test_governor_engine.py
  tests/test_execution_engine.py tests/test_event_bus.py
  tests/test_position_monitor_engine.py tests/test_portfolio_worker.py
  --tb=short --disable-warnings` — **62 passed**.
- Expanded focused/regression command adds `test_simulated_venue.py`,
  `test_exit_intents_route.py`, `test_world_view_portfolio.py`, and
  `test_intelligence_routes.py` — **88 passed**.
- Strengthened real-lifespan test rerun: `pytest -q
  tests/test_main_execution_pipeline.py --tb=short --disable-warnings` —
  **6 passed**.
- Final focused and adjacent regression command added `test_portfolio_state.py`,
  `test_reconciliation.py`, and `test_position_monitor_portfolio_reader.py`
  after the strengthened fault assertion — **115 passed**.

Before numbering, GitHub `main` still matched local `fd36571`; the canonical
index and log both ended at #178, and the newest archive was `134-160.md`.
Decision #179 was appended at the true end of the log and indexed. `git diff
--check` passed. Package: `execution-startup-fail-closed.zip` contains only
the changed repository-relative files.

<!-- Previous delivery record retained below. -->

# TESTING — `observed-exit-intents-ui`

GitHub `main` and local `HEAD` both resolved to `222f36507cdec15245a33c292196a9785f47ed6d` before editing and at the pre-packaging recheck. The initial working tree was clean. The canonical decision log and index end at #178; this consumer of the existing read contract needs no new decision.

- `cd frontend && npx tsc -b` — passed.
- `cd frontend && npm run build` — passed (TypeScript build and Vite production bundle).
- `git diff --check` — passed.

No backend or database behavior changed. Package: `observed-exit-intents-ui.zip` contains the frontend consumer and required documentation with repository-relative paths. The build-generated `frontend/tsconfig.tsbuildinfo` is excluded.

<!-- Previous delivery record retained below. -->

# TESTING — decision #178: `position-monitor-observer-wiring`

Initial and pre-numbering GitHub `origin/main` checks both resolved to `5b43d55038c0b5af3aefd1477e66dbc5267510e0`, matching local `HEAD`. The initial working tree was clean. `docs/decisions/INDEX.md` and the canonical log ended at #177; the latest archive remained `134-160.md`. Decision #178 was appended in numerical order after that recheck.

Used the existing local PostgreSQL 18 test cluster on port 55437 and created a separate `position_monitor_observer_test` database. Alembic `upgrade head` applied migrations through `0014`. No production database or broker account was touched.

- Focused: `pytest -q tests/test_exit_intents_route.py tests/test_main_execution_pipeline.py tests/test_position_monitor_engine.py tests/test_position_monitor_portfolio_reader.py --tb=short --disable-warnings` — **21 passed**. The new real-FastAPI-lifespan test opens a simulated position through `OpportunityCreated`, fills it through `SimulatedVenue`, sends a stop-crossing `PriceUpdated`, checks one observed-only intent and its fields, sends a second crossing, and checks there is still one intent, one entry order/fill, and an open position. It also checks unavailable startup and shutdown cleanup. A separate route test checks multi-intent sorting and symbol-filter delegation.
- Adjacent regressions: `pytest -q tests/test_entry_lifecycle_wiring.py tests/test_portfolio_state.py tests/test_intelligence_routes.py tests/test_world_view_portfolio.py tests/test_world_view.py --tb=short --disable-warnings` — **37 passed**.
- `git diff --check` — passed after code, test, decision, and architecture edits.

The first focused run found only a test expectation mismatch: FastAPI emits a UTC `Z` suffix while Python's `datetime.isoformat()` returns `+00:00`. The expected timestamp was corrected; the repeated focused run passed without changing response behavior.

Package: `position-monitor-observer-wiring.zip` contains the 12 changed files with repository-relative paths, including the new route test and the decision/doc updates.

<!-- Previous delivery record retained below. -->

# TESTING — decision #177: `world-view-portfolio-read`

At task start and at the final pre-numbering recheck, local `main` and freshly fetched GitHub `origin/main` were both `05d0cbc07c4903e0dda0232a84536ce6248a6e5f`; the initial working tree was clean. `docs/decisions/INDEX.md` and the canonical log both ended at #176, with archive filenames ending at `134-160.md`. The new cross-component lifecycle read decision was assigned #177 only after that recheck, appended at the true end of the log, and indexed to `confirmed-decisions.md`.

The default local development database had an older schema (`strategy_outcomes.execution_mode` and `position_fill_receipts` absent), so the initial backend attempt was not a valid regression run. Created an isolated PostgreSQL 18 cluster in `/tmp/world-view-portfolio-read.N8y53w`, database `world_view_portfolio_read_test` on port 55437, and applied Alembic migrations through `0014`. No existing development database was migrated or cleaned. The first new serialization test also exposed an assertion expecting `Z` where FastAPI emits `+00:00`; the expected string was corrected without changing response behavior.

- Backend: `pytest tests/test_world_view_portfolio.py tests/test_world_view.py tests/test_main_execution_pipeline.py tests/test_entry_lifecycle_wiring.py tests/test_portfolio_state.py -q --tb=short --disable-warnings` against the isolated database: **28 passed**. Coverage includes unavailable reader/snapshot, restored empty portfolio, an open `PositionState` with exact Decimal price strings and an in-flight order, system-wide portfolio under symbol-scoped reads, unchanged World View fields, clean startup exposure, reconciliation failure, and shutdown cleanup.
- Frontend: `npm run build` (includes `tsc -b`): **passed**.
- `git diff --check`: **passed**.

Package: `world-view-portfolio-read.zip` contains the 14 changed files with repository-relative paths (three backend application files, two backend test files, two frontend application files plus the API client, four documentation files, `CHANGES.md`, and `TESTING.md`). The generated `frontend/tsconfig.tsbuildinfo` was restored after the build and is not packaged. No production database or external account was touched.

<!-- Previous delivery record retained below. -->

# TESTING — `position-monitor-portfolio-reader`

GitHub `main` and local `main` both resolved to `51ba48a9eebe1ad36d7eb77bd9060fc4d4edc460` before editing and at the final packaging recheck; the initial working tree was clean. The decision index, canonical log, and archive inventory ended at #176. No new decision was required for this adapter.

Focused checks: `backend/.venv/bin/pytest -q backend/tests/test_position_monitor_portfolio_reader.py backend/tests/test_position_monitor_engine.py backend/tests/test_portfolio_accounting.py` → **40 passed**. The five new tests cover restored empty, open position, `closing` position after a partial reduction, in-flight entry without a position, and unavailable (unrestored or blocked) snapshots. The tests use real `PortfolioState.get_snapshot()` and accounting `apply_fill()`; they install a ledger state directly without database I/O.

`git diff --check` passed. A broader command that also included `test_portfolio_worker.py` passed its first 40 tests and then stalled in that worker test module; it was interrupted after no further output. A separate 20-second run confirmed the stall at its first test, `test_unknown_then_known_flat_snapshot_is_detached_and_io_free`. The focused adapter and adjacent monitor/accounting checks were rerun separately and passed. No production startup wiring or database mutation was part of this delivery.

<!-- Previous delivery record retained below. -->

# TESTING — decision #176: Entry-order lifecycle wired to real Postgres (`entry-lifecycle-wiring`)

## Baseline and evidence

Fresh pull via `curl -sL https://codeload.github.com/rotate-zero/agentic-trading-os/tar.gz/refs/heads/main | tar -xzf - --strip-components=1` (tarball, no `git status`). PostgreSQL 16 installed and started locally (`apt-get install postgresql`), role/database created matching `core/config.py`'s conventions, `alembic upgrade head` applied cleanly including the two migrations this task found already on `main` (`0013_position_ledger_receipts.py`, `0014_authorization_reservations.py`).

**Collision, resolved twice.** First re-check (task start): `INDEX.md`/`confirmed-decisions.md` tail both at #174 — reserved #175 as temp expectation, used the slug `entry-lifecycle-wiring` throughout instead. Final re-check (immediately before packaging): a file-disjoint sibling, `position-monitor-lite` (`backend/app/position_monitor/**` + one test file, confirmed zero overlap with this task's own edited/created files by `diff -rq` in both directions), had landed and correctly taken #175 — its own decision entry explicitly anticipated this exact collision by name and pre-committed to deferring to whichever session landed first. This delivery renumbers to **#176**.

**Found already on `main`, undocumented, at task start** (both re-checks): `backend/app/execution_engine/postgres.py`, `backend/app/governor/postgres.py`, `backend/app/portfolio_state/postgres.py`, `backend/app/db/ledger_transaction.py`, migrations `0013`/`0014` — real, tested, zero decision-log entry. Verified before writing any new code: `alembic upgrade head` clean; `python3 -m pytest tests/test_authorization_ledger_postgres.py tests/test_position_ledger_postgres.py tests/test_execution_engine.py tests/test_governor_engine.py tests/test_governor_rules.py tests/test_governor_config.py -q` → **153 passed**. Full baseline suite on the untouched pull: **1066 passed**, zero failures (a live local Postgres was available this session, unlike `position-monitor-lite`'s own sandbox — its 48 failed/93 errors on an untouched pull were exactly this gap, confirmed by that task's own TESTING.md).

## Environment setup

```
apt-get install -y postgresql postgresql-contrib
service postgresql start
su postgres -c "psql -c \"CREATE USER trading WITH PASSWORD 'trading' CREATEDB SUPERUSER;\""
su postgres -c "psql -c \"CREATE DATABASE trading_workspace OWNER trading;\""
cd backend && pip install -r requirements.txt --break-system-packages
export POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading
python3 -m alembic upgrade head
python3 -m pytest -q
```

## What changed

See `CHANGES.md`'s matching entry for the full description. In test terms: two new adapters (`FillLedgerPort`/`PostgresFillLedger`, `PortfolioStateAdapter`), fill processing added to `ExecutionEngine` (additive, `None`-guarded — every pre-existing test path unaffected), a new `main.py` startup/shutdown block, two corrected docstrings, and the design doc's premature-citation cleanup (no test surface — doc-only).

## Checks and results

- **Unit, `PostgresFillLedger`** (`test_fill_ledger_postgres.py`, 7 tests): fill persists and advances order status to `partially_filled`; full-quantity fill advances to `filled`; duplicate delivery is a no-op, not an error (exactly one row survives — a second insert would have hit the `UNIQUE` constraint); overfill is persisted and flagged, not rejected; a fill for an unknown `client_order_id` raises `FillLedgerError` rather than attempting an FK-violating insert; a stale/out-of-order status update does not regress the order but the fill is still persisted; `execution_venue` is read from the order row, never the caller.
- **Unit, `PortfolioStateAdapter`** (`test_governor_portfolio_state_reader.py`, 4 tests): a mode mismatch is refused, not silently answered from the wrong instance; a never-started `PortfolioState` raises "not ready" rather than a default; an unresolved anomalous fill raises (I14's halt, proven directly — not merely that the mechanism exists); a real open position translates correctly into governor's own `PortfolioSnapshot`/`OpenExposure` shape, Decimal→float included.
- **Integration, direct component wiring** (`test_entry_lifecycle_wiring.py`, 3 tests): `OpportunityCreated` → real `trades`/`trade_reservations`/`orders` rows → a real `SimulatedVenue` tick fill → real `fills` row + `orders.status` advance → `OrderFilled` published → the real `PortfolioState` event worker → a real, open `positions` row, read back correctly through the same `PortfolioStateAdapter` the next authorization would consult; a rejected opportunity never reaches the ledger at all; a second opportunity for a now-busy symbol is rejected by rule 4, proving the read side actually feeds real rejections, not just that it returns *some* snapshot.
- **Restart recovery, through `main.py`'s real lifespan** (`test_main_execution_pipeline.py`, 1 test): boot the real FastAPI app (`TestClient`), submit an order via a real `PriceUpdated`+`OpportunityCreated` pair (via `client.portal.call`, the established pattern `test_websocket_channels.py` already uses for this exact cross-loop problem), exit the process before any fill, re-enter with a fresh (non-durable-by-design) `SimulatedVenue` — confirms the orphaned order is marked `expired`/`venue_lost_state_on_restart` and that a fresh opportunity for the same, now-free symbol is approved normally afterward. Module-level singletons (`EventBus`, `AuthorizerStub`, `ExecutionEngine`, `broker_registry`, ...) reset mid-test, mirroring `conftest.py`'s own autouse fixture exactly — needed here specifically because this one test spans two separate `TestClient` enters/exits, each with its own torn-down-and-rebuilt anyio portal/event loop.
- **Full regression**, this task's own branch: **1066 → 1081** (+15), zero regressions.
- **Combined with `position-monitor-lite`** (fully reconciled tree, both deliveries applied): **1091 passed**, zero regressions, zero failures, zero errors.
- **Flakiness check:** the four new test files re-run 3x in immediate succession (async queue-hop chain across four cooperating engines is the main timing risk) — stable every time, no `asyncio.sleep` tuning needed beyond the values already used elsewhere in this codebase's own async tests (0.15–0.3s).

## Not covered by this delivery's own tests, stated precisely

The restart-recovery branch where a **durable** venue (a real broker, not `SimulatedVenue`) reports a fill the dead process never got to persist (`reconcile_with_venue`'s own `_reconcile_known_to_venue` missing-fills-pulled-in path) is covered at the function level by #172's own `test_reconciliation.py`; it is not, and structurally cannot be, reproduced at the process level against `SimulatedVenue`, which has no memory across a restart by design (see the decision entry's J5). No cancel/expire path beyond what restart reconciliation already provides — EX-5/EX-12 remain untouched. No load/concurrency testing of the fill-processing path beyond what the existing single-worker-queue design already guarantees by construction.

<!-- Previous delivery record retained below. -->

# TESTING — decision #175: Position Monitor-lite built (`position-monitor-lite`)

## Baseline, pulled fresh

`curl -sL https://codeload.github.com/rotate-zero/agentic-trading-os/tar.gz/refs/heads/main | tar -xzf - --strip-components=1` (a tarball, not a clone — `git status` recorded as absent, per this task's own §1.1). `pip install -r requirements.txt --break-system-packages` (Python 3.12.3, pytest 8.4.2, pytest-asyncio 0.24.0).

Full suite run on the untouched pull, before any edit, per `ways-of-working.md`'s "confirm pre-existing flakiness before attributing any failure to new work":

```
48 failed, 606 passed, 319 skipped, 15 warnings, 93 errors in 58.86s
```

Every failure/error inspected is the same root cause: `sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (127.0.0.1), port 5432 failed: Connection refused` — this sandbox has no live Postgres. Files affected: `test_daily_levels.py`, `test_feature_engine.py`, `test_market_routes.py`, `test_scanner_universe.py`, `test_strategy_outcomes_and_opportunity_conflicts_routes.py`, `test_vwap_ext.py`, `test_websocket_channels.py` (the 48 failures), plus `test_authorization_ledger_postgres.py`/`test_position_ledger_postgres.py` (all 93 errors — the `entry-lifecycle-wiring` sibling's own still-undocumented Postgres-backed ledger adapters and their tests, confirmed present on this pull by direct `ls`: `backend/app/{portfolio_state,execution_engine,governor}/postgres.py`, `backend/app/db/ledger_transaction.py`, migrations `0013_position_ledger_receipts.py`/`0014_authorization_reservations.py`). None of this relates to `position-monitor-lite`, which touches no database at all — confirmed true in practice, not just asserted: `position_monitor/` imports nothing from `app.db`, `sqlalchemy`, or `psycopg2`.

## Command

From `backend/`: `python3 -m pytest -q` (whole suite) and `python3 -m pytest -q tests/test_position_monitor_engine.py -v` (this task's own file, in isolation).

## Result

This task's own 10 tests, isolated: **10 passed in 1.82s.**

Whole suite after adding `backend/app/position_monitor/**` and `backend/tests/test_position_monitor_engine.py`:

```
48 failed, 616 passed, 319 skipped, 15 warnings, 93 errors in 59.29s
```

Exactly **+10** over the untouched baseline (606 → 616) — the new tests, nothing else. Same 48 failed / 93 errored, same root cause, same files — zero regressions, zero incidental fixes, zero incidental breakage.

## What's tested and how

Real `pytest`/`pytest-asyncio` (`asyncio_mode = auto`), no live Postgres needed (this task's own §1.2, confirmed correct) — real `EventBus`, a fake `PositionReader` (dataclass wrapping a plain list, no concrete adapter exists to test against yet — same fork-1 precedent `test_execution_engine.py`/`test_governor_engine.py` both already set for their own narrow ports), and a real `MarketClock` (not mocked — `is_half_day()`/`trading_day()` run their genuine logic against a fixed, injected instant, never wall-clock):

- Long-position stop touched by a single tick (`_on_market_event` → queue → `_process_event` → `_evaluate` → `ExitIntent(exit_reason="stop")`).
- Short-position target touched by a single tick.
- One candle whose high crosses target AND whose low crosses stop in the same bar — asserts `exit_reason == "stop"` (EX-8's stop-wins-tie, checked-first ordering).
- EOD-flatten: a tick one minute before the real `regular_session_close_utc` instant produces nothing; a tick exactly at that instant produces `exit_reason="eod_flatten"` with `trigger_price` equal to the bar's own price (not a fabricated value) and `trigger_ts` equal to the triggering tick's own timestamp.
- Idempotency: after the first `ExitIntent`, two further ticks — one that would independently re-trigger the stop, one that would independently re-trigger the target — both produce nothing; `get_exit_intents()` is byte-identical before and after.
- An unheld symbol's tick (a position exists for `AAPL`; a tick arrives for `MSFT`) produces nothing.
- `get_exit_intents(symbol=...)` filters correctly against two symbols with independently-triggered intents.
- Two positions on the same symbol with different stops — only the one actually crossed produces an intent; the other stays untouched.
- Two pure `_evaluate()`-level tests, no asyncio/EventBus at all: the stop case directly, and the "no stop/target configured" honest-absence case (`stop=None, target=None` — price alone can never produce an intent; only `eod_flatten` remains reachable).

## Verification against an untouched clone

A second, independent fresh tarball pull (`/home/claude/atos_clean`), `diff -rq` against the working tree: the only non-cache differences are `backend/app/position_monitor/` (new directory, 3 files) and `backend/tests/test_position_monitor_engine.py` (new file). Nothing else in the tree was touched — no existing file's content changed, confirmed by the same `diff -rq` convention every prior packaging in this project's history has used.

## Decision-number reconciliation

Immediately before packaging, re-pulled fresh a second time (`/home/claude/atos_verify`) and diffed it against a fresh `/home/claude/atos_clean` pull taken minutes apart — byte-identical, confirming nothing landed on `main` mid-session. `INDEX.md`'s last row: #174. `confirmed-decisions.md`'s last heading: `### 174.`. Archive file list: unchanged, ends `134-160.md`. No `PENDING` marker, no `### 175.` heading, anywhere in either canonical log. Assigned **#175**. See the decision entry itself for the citation-drift heads-up (`execution-engine-design.md` §6.8 already informally references "#174"/"#175" for the `entry-lifecycle-wiring` sibling's own, still-undocumented migrations) — a likely future collision, not treated as a reservation.

## Not verified / not applicable to this task

No live Postgres involved (this task's own module touches no database). No frontend change (`frontend/` untouched — confirmed by the `diff -rq` above). No end-to-end run against a live EventBus/`main.py` process — `main.py` wiring is explicitly out of this task's scope, so there is no running system yet for this module to be exercised inside of; every test here drives `PositionMonitor` directly against a real, standalone `EventBus`.

<!-- Previous delivery record retained below. -->

# TESTING — decision #174: First frontend consumer of the order-lifecycle events wired (`execution-lifecycle-frontend`)

## Baseline and evidence — two collisions, both resolved

Repository: `rotate-zero/agentic-trading-os`, `main`, pulled via tarball four times across this session: at the start; a second time before what was believed to be the final #172 packaging (byte-identical, nothing had landed); a third time in response to a direct instruction ("git has been just updated. check and zip"), which surfaced `execution-ledger-and-venue` merging first and taking #172 (this delivery renumbered to #173, restoring dropped `TESTING.md` history along the way — full detail in that packaging's own record below); and a fourth time in response to a second direct instruction ("one git update happen in between. check if documentation or code modification is required and rezip"), which surfaced `portfolio-state-engine` merging next and taking #173 (this delivery renumbered again, to **#174**).

Three-source check redone a second time, immediately before assigning `#174`: `INDEX.md`'s last row **#173**, `confirmed-decisions.md`'s tail **#173**, archive file list unchanged (`001-060` … `134-160`).

**Zero file overlap, confirmed both times, not assumed.** `execution-ledger-and-venue`'s own footprint (checked at the #173 renumber) never touches this task's two editable files. `portfolio-state-engine`'s own stated boundary is explicit: "No model/migration, broker-adapter/registry, governor/execution-engine, websocket channel, main startup, or frontend edits." Directly verified by hash a second time: `backend/app/api/websocket/channels.py` and `frontend/src/App.tsx` are byte-identical, by SHA-256, to this task's own original start-of-session baseline (`f158d706...` / `037eb9c3...`) even after both intervening merges.

**This round required a real code change, not just a renumber.** `portfolio-state-engine` added a real `PositionClosed` Pydantic model to `backend/app/schemas/events/execution.py` and added `EventType.POSITION_CLOSED` to `backend/app/schemas/events/envelope.py`'s `CRITICAL_EVENT_TYPES` — both confirmed absent on this session's third pull, confirmed present by direct `diff` on the fourth. Full detail in the "What changed" and "Checks and results" sections below.

## Environment setup

Unchanged from prior packagings: no Postgres needed (no database/migration/model touched). `npm ci` in `frontend/`. `pip install fastapi pydantic --break-system-packages` for a real `channels.py` import check, re-run a third time against the tree that now also includes `portfolio_state/accounting.py`, `legacy.py`, `ports.py`, `snapshot.py`.

## What changed

New: `frontend/src/hooks/useOrderLifecycle.ts`, `frontend/src/components/execution/ExecutionLifecyclePanel.tsx` (both unchanged in design from the #173 packaging; `PositionClosedWire` and its normalization/rendering updated per above).

Edited, additive only: `backend/app/api/websocket/channels.py` (routing lines unchanged; the `POSITION_CLOSED` explanatory comment updated, since its prior claim — "no `CRITICAL_EVENT_TYPES` entry or payload model does yet" — is now false), `frontend/src/App.tsx` (unchanged this round).

Footprint confirmed by `diff -rq` of a freshly re-pulled, untouched `main` (post-#173) against the working tree: the same 4 paths as every prior packaging. Nothing else touched.

## Checks and results

**Backend (`channels.py`).** Real Python import, re-run a third time, with a new assertion that wasn't meaningful to check before decision #173 added the entry:

```
from app.api.websocket.channels import EVENT_TO_CHANNEL
from app.schemas.events.envelope import EventType, CRITICAL_EVENT_TYPES
assert EVENT_TO_CHANNEL[EventType.TRADE_PLANNED] == "orders.status"        # PASS
assert EVENT_TO_CHANNEL[EventType.ORDER_STATUS_CHANGED] == "orders.status" # PASS
assert EVENT_TO_CHANNEL[EventType.POSITION_CLOSED] == "orders.status"      # PASS
assert EventType.POSITION_CLOSED in CRITICAL_EVENT_TYPES                   # PASS — new; false before decision #173
```

**Frontend.** `npx tsc -b` and `npm run build` — clean, exit 0, re-run a third time after this round's `PositionClosedWire`/display-type/`describeEvent()` changes (103 modules transformed).

**`PositionClosed` normalization re-exercised against two fabricated messages** (the hook has no way to receive a real one — see below):

| Fabricated payload | Result |
|---|---|
| Full shape matching decision #173's real model exactly — every optional field populated (`r_multiple_missing_reason: "immutable_risk_basis_unavailable"`, `trade_id`, `execution_mode`, `execution_venue`, `realized_profit`, `realized_loss`, `fees: 1.5`, `reported_fees`, `unknown_fee_count`) | Normalized correctly; `describeEvent()` → `"exit 108.00, pnl +260.00, fees 1.50"` |
| Minimal shape — only the original five fields this hook guessed before decision #173 existed, none of the newer optional ones present | Normalized correctly (new fields default to `null` via `??`); `describeEvent()` → `"exit 50.00, pnl -10.00 (-0.50R)"` |

Confirms the wider wire type handles both a message with every new field present and one with none of them, without throwing either way.

**Per-event-type publisher status, re-confirmed against the post-#173 tree (updates the table from the #173 packaging):**

`OrderFilled` — unchanged, **No**: re-confirmed via `grep -rn "OrderFilled(" backend/app`, still only the class definition, no `.publish()` call anywhere.

`PositionClosed` — reason updated, still **No** in a running system: `grep -rn "PositionClosed(" backend/app` now finds the class definition (`execution.py`) and its use inside `portfolio_state`'s own worker/tests — a real publish path exists and is unit-tested (decision #173: 161 focused checks) — but that same decision states plainly, twice, that "no production implementation of this Protocol ships here" and the "PositionLedgerPort adapter / startup / outcome recovery" remain "not wired." Nothing in a running system instantiates the worker, so this event still cannot arrive on `main` today — a more precise reason than the #173 packaging's "no publisher and no payload model," which decision #173 superseded.

**Not verified.** Unchanged from before: no live backend was run against a browser for this delivery.

## Found and fixed, not part of this task's own scope — restored a second time

As of this session's third pull (documented in the #173 packaging's own record below), `#172`'s own `TESTING.md` section had dropped #171-and-earlier's history entirely — no retain-history marker, no history beneath it. That was restored once already, in the #173 packaging. On this session's fourth pull, the gap was still present on `main`: the #173 fix was only ever handed to Saqib as a zip, never applied, so it never reached the real repository. `portfolio-state-engine`'s own `TESTING.md` section (decision #173) correctly preserved #172's section above it — so the gap has not grown on this pull, but it also has not shrunk. Restored again here: #171-and-earlier's history, byte-for-byte from this session's own original pre-#172 pull, preserved beneath both #173's and #172's own sections, neither of which was altered.

<!-- Previous delivery record retained below. -->

# TESTING — decision #173: Portfolio State Engine (`portfolio-state-engine`)

## Baseline and scope

Fresh GitHub `main`, fetched with `git fetch origin main`: **`c341a2c2f3e2e38901fe2ce10a430b826201be11`**, branch `main`; initial `git status --short` output was empty. This is a Git checkout, not a tarball. Read `AGENTS.md`, all of the execution design, decisions #170/#171 and the already-merged #172, event/bus/clock contracts, governor/execution ports and worker, existing Portfolio State/reconciliation, ledger schema, and representative tests before editing. #172's ownership overlap was reported; Saqib explicitly approved revising that package, nullable R, and separate profit/loss/fees. He clarified support for medium- and long-term holdings as well as day trading.

Immediately before assigning #173, GitHub main remained at the baseline SHA; its decision index and log both ended at #172 and archives ended at `134-160`. Existing decision bodies were preserved verbatim. The canonical design's older inventory is now explicitly historical, and its Portfolio State section describes this delivery.

## Environment and command

Python 3.14; repository `backend/.venv`. A newly initialized, isolated **PostgreSQL 18.6** instance on port **55436**, database **`portfolio_state_test`**, was migrated from empty through existing migration **0012** using the repository's Alembic environment. PostgreSQL 16 was not available here and is not claimed. No existing application database was used. Socket access required sandbox escalation. The existing migrations and production configuration were not changed.

From `backend/`:

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55436 POSTGRES_DB=portfolio_state_test \
  .venv/bin/pytest \
  tests/test_portfolio_accounting.py tests/test_portfolio_worker.py \
  tests/test_portfolio_state.py tests/test_reconciliation.py \
  tests/test_execution_event_schemas.py tests/test_execution_engine.py \
  tests/test_governor_engine.py tests/test_governor_rules.py \
  tests/test_execution_ledger.py tests/test_simulated_venue.py \
  tests/test_event_bus.py tests/test_market_clock.py \
  -q --tb=short --disable-warnings
```

**Result: 161 passed, no skips, in 5.20s.** Dependency deprecation warnings (pytest-asyncio/FastAPI on Python 3.14) remain; no test failures. No full-suite claim. Also ran `git diff --check` and Python compilation checks for the changed package/new test modules.

**Final handoff revalidation, 2026-09-23:** restarted the same isolated PostgreSQL instance and reran the exact command above: **161 passed, no skips, in 4.85s** (6,547 dependency deprecation warnings). A separate accounting/worker check also passed all 45 cases. Fresh `git fetch origin main` confirmed both `HEAD` and `origin/main` remain `c341a2c2f3e2e38901fe2ce10a430b826201be11`. No application implementation changes were needed during handoff.

## Meaningful coverage

- **Pure arithmetic and fake-ledger worker: 45 cases.** Long/short gains and losses, weighted adds including adds after a reduction, partial/full closes, gross profit/loss and separate known/unknown fees, invalid/nonfinite inputs, incompatible position identity/mode, invalid side/effect, over-closes and reversal rejection. Synthetic events drive the real EventBus and queue worker; the fake stores the worker's proposed committed values and enforces its cursor/key contract.
- **Holding period and dates.** January entries, April partial reductions, September closures retain one position ID and preserve each day's realized amount/fee. ET midnight attribution and all four capital modes are exercised separately. Changing the snapshot day never resets/closes a held position. A later reopening receives a new durable ID and no stale mark.
- **Worker lifecycle/read honesty.** Unknown before startup, known flat after complete restore, unknown symbol, unknown realized history, detached snapshots, unknown marks after restart, stale/pre-opening/unheld tick filtering (including rejection before queueing), partial-entry remaining exposure, rejection, terminal cancellation via explicit refresh, stale approval, unresolved approval metadata, and graceful worker drain/critical-bus isolation during blocked persistence.
- **Commit/publication boundary.** Fake commit failure publishes nothing; retry does not double-apply. Closure handlers observe a previously committed cursor. Duplicate notifications/applications publish no extra closure. Injected lost commit acknowledgement and post-commit publication failure recover accounting but deliberately demonstrate the missing-notification window; they do not prove durable delivery.
- **Retained Session/reconciliation API: 22 real-PostgreSQL cases.** 15 portfolio tests plus 7 existing reconciliation tests. Persistence and restart retain IDs, all already-committed partial fills apply even when the order is already `filled`, daily partial realizations/fees reconstruct with cursor already advanced, full rebuild does not duplicate positions, and a visible earlier fill cannot be skipped. Pre-commit fault injection verifies no snapshot installation and real database rollback of position/cursor while the independently committed source fill survives. Known unapplied backlog keeps snapshots unavailable. Overfill rows persist with an anomaly while the stored quantity/P&L remain unchanged.
- **Related regressions.** Governor rules and worker, Execution Engine and event schemas, ledger uniqueness/population constraints, SimulatedVenue, EventBus, and MarketClock.

The initial PostgreSQL run caught five expectations tied to old behavior: close fixtures incorrectly used BUY for a long close; cache corruption used mutable returned state; overfill expectations accepted clamping and misclassified excess fills as merely terminal. Fixtures now use opposite-side closes, the corruption test explicitly corrupts internal state, and overfill checks assert preserved fill facts, unchanged stored position arithmetic, and unavailable snapshots. Assertions were strengthened rather than weakened. The code also marks a partially processed committed backlog unavailable rather than exposing incomplete state as current.

## What is not verified or delivered

**No real `PositionLedgerPort` adapter exists.** Fake-ledger tests do not verify PostgreSQL atomic application through that Protocol, concurrent writers, durable deduplication under races, safe committed-prefix discovery, process-kill recovery, or schema adequacy for all future risk/fee metadata. PostgreSQL Identity allocation is not commit order; the adapter must serialize ingestion or implement a safe watermark so later commits cannot appear below an advanced cursor. The retained Session path assumes serialized ingestion; tests cover one writer, not that future concurrency guarantee. Existing tables are present from #172; no new tables or migration are claimed necessary or sufficient without the adapter design.

No live startup wiring, real fill publisher, cancellation/expiration publisher, venue placement, exit policy, governor/World View adapter, Position Monitor, OutcomeRecorder, or StrategyOutcome writing was added. Rejection is the only current order-status event. Persisted cancellation/expiration is discovered at startup or explicit `refresh()`, not guaranteed promptly in a running pipeline. Unknown approved-order metadata blocks usable reads until the persistence side resolves it. A future adapter must include approved reservations and handle the approval-before-order-insert race.

`PositionClosed` is best-effort after commit: the in-memory critical lane is neither an outbox nor durable delivery. Already-applied closures are not re-emitted on restart. Consumers must recover committed closures from persistence independently. R is always `None` with a reason in this delivery: no numeric R or scale-in/changed-stop risk-basis policy is invented. Fees missing from any included fill remain unknown, and gross P&L does not subtract them. Splits, dividends, financing/borrow charges, and late fee corrections have no current input contracts. Mark timestamps are exposed; this task does not invent a freshness threshold. Legacy identical `(trade, symbol, opened_at)` reopening identities fail explicitly because the old schema cannot disambiguate them.

## Packaging verification

`portfolio-state-engine.zip` contains only the changed/new task files, with paths relative to the project root and no enclosing directory. It excludes virtualenvs, database files, caches, validation logs, and the archive itself. Shared documentation and the footprint are compared to a final fresh GitHub main; prohibited paths and existing decision bodies are checked unchanged. Packaging also verifies archive contents against the working files and overlays the archive on a clean export of the recorded main commit to compare its exact footprint. The archive's base is the SHA recorded above; later overlapping changes must be reconciled before applying it.

The final archive contains **16 files: 10 modified tracked files and 6 new Python files**. Verification checks ZIP integrity, byte-for-byte equality with the working files, the exact change set after overlay onto a clean baseline export, and preservation of all pre-existing decision-log and index content. The temporary PostgreSQL validation server is stopped after verification; its data remains outside the archive.

<!-- Previous delivery evidence retained below. -->

# TESTING — decision #172: Execution ledger + `OrderVenue`/`SimulatedVenue` + Portfolio State built (`execution-ledger-and-venue`)

## Baseline and evidence

Repository: `rotate-zero/agentic-trading-os`, `main`, pulled via tarball (`curl -sL https://codeload.github.com/... | tar -xzf -`) at the start of this session. Required reading done in the order this task's prompt specified: `AGENTS.md`, the full `execution-engine-design.md` (§1, §5, §6.4-§6.10, §7, §9), decisions #168/#170, `broker_adapters/base.py`, `services/broker_registry.py`, `models/trading_intelligence.py`, `schemas/performance.py`, `core/config.py`, and the latest Alembic migration (`0011`, confirmed still head at that point).

**A sibling merge landed mid-session.** A later `diff -rq` against a freshly re-pulled `main` (taken while drafting the ledger tests) showed files this task never touched had changed — `backend/app/execution_engine/`, `backend/app/governor/`, `schemas/events/{envelope,execution}.py`, `conftest.py` all now existed/differed. Checked directly against the GitHub API (`/repos/.../commits?sha=main`): commit `0501f9f...`, "Execution authorizer and engine", 2026-09-22T11:55:23Z — decision #171, the sibling `execution-authorizer-and-engine` task, exactly as this task's own prompt anticipated ("a sibling instance is working `execution-authorizer-and-engine` in parallel, file-disjoint from you"). Per this task's own standing rule ("if a re-pull shows any of your editable files changed since your start-of-task hashes, stop and report before continuing"), checked precisely which of the four files on this task's own "may edit" list #171 touched: **none** — `broker_registry.py`, `models/trading_intelligence.py`, `schemas/performance.py` were all untouched by #171 (grepped directly for `execution_mode`/`execution`-role additions — none found). `core/config.py` was touched, at the exact same append point this task also uses — the "trivial merge, not a conflict" this task's own prompt explicitly predicted for that one file. Resolved by taking #171's already-merged `main` as the new base and re-applying this task's own file set on top of it (see "What changed" below); nothing from #171 was reverted or overwritten. `execution_engine/ports.py` was read directly (not touched) to confirm its local `OrderVenue` `Protocol`/`VenueOrderInstruction`/`VenueAck` dataclasses and `default_execution_venue_provider()`'s duck-typed `getattr(broker_registry, "get_execution_venue", None)` lookup are structurally compatible with this delivery's real classes — Python duck typing means #171's already-merged code needs no further change to work against this delivery's concrete `SimulatedVenue`/`broker_registry`.

Three-source re-check for the decision number, done immediately before writing the entry: `INDEX.md`'s last row **#171**, `confirmed-decisions.md`'s tail **#171**, archive file list unchanged (`001-060` … `134-160`) — so **#172** was assigned only after that check, and every reference to the temporary slug `execution-ledger-and-venue` in code/docs was checked and left only where it names this delivery for traceability (file/dir names, decision-entry title parenthetical — matching #171's and #163's own precedent of keeping the slug in the title even once numbered).

## Environment setup

Postgres 16 installed and started in the sandbox (`apt-get install postgresql-16 postgresql-client-16`, `archive.ubuntu.com`/`security.ubuntu.com` allowed by the network egress list); `CREATE USER trading WITH PASSWORD 'trading' SUPERUSER`, `CREATE DATABASE trading_workspace OWNER trading`. Python venv, `pip install -r backend/requirements.txt` (unchanged from `main` — no new dependency).

## What changed

New: `backend/app/broker_adapters/{order_venue,simulated_venue}.py`, `backend/app/models/execution_ledger.py`, `backend/alembic/versions/0012_execution_ledger_and_strategy_outcomes_mode.py`, `backend/app/portfolio_state/{__init__,engine,reconciliation}.py`, `backend/tests/{test_simulated_venue,test_execution_registry,test_execution_ledger,test_portfolio_state,test_reconciliation}.py`.

Edited: `backend/app/services/broker_registry.py` (new `execution` role — `set_execution_venue`/`get_execution_venue`/`clear_execution_venue`, `clear_all()` extended), `backend/app/models/trading_intelligence.py` (additive — `execution_mode`/`execution_venue`/`snapshot_missing_reasons` columns, four snapshot columns relaxed to nullable, one context-sensitive default function), `backend/app/schemas/performance.py` (additive — same fields mirrored into the Pydantic contract, four new `model_validator`s), `backend/app/core/config.py` (appended `execution_mode` setting + validator, merged alongside #171's own already-appended block at the same anchor point), `backend/app/db/base.py` (one import line registering the new model module — see the decision entry's J1 for why this one file outside the "may edit" list was touched).

Footprint confirmed by `diff -rq` of a freshly re-pulled `main` (post-#171) against the working tree, excluding `__pycache__`/`.pytest_cache`/`.env`: exactly the files listed above, plus this file, `CHANGES.md`, and the decision-log entry/row. Nothing under `backend/app/execution_engine/**`, `backend/app/governor/**`, `backend/app/broker_adapters/{base,ibkr_adapter}.py`, `backend/app/schemas/events/**`, or any `docs/architecture/*.md` was touched.

## Checks and results

- **Regression baseline, before any change:** fresh `main` pull (pre-#171-awareness), migrated through `0011`, full suite: **810 passed, 0 failed** (~80s).
- **After migration `0012` alone** (before mirroring the Pydantic/ORM fields): **39 failures** — every `record_strategy_outcome()`/`StrategyOutcomeRecord` caller broke on the new `NOT NULL` `execution_mode`/`execution_venue` columns, exactly as expected from making them required at the DB level with no writer updated yet.
- **After mirroring, first pass:** 7 failures remained — a bare `execution_mode="backtest"` default doesn't account for the pre-existing population-isolation tests' synthetic `is_backtest=False` rows. Fixed with a `model_validator(mode="before")` on the Pydantic side (default derived from `is_backtest`, not a fixed literal) — this reduced it to 4 failures, all one root cause: `record_strategy_outcome()` (outside this task's file boundary) constructs the ORM row by explicit kwargs and never forwards the Pydantic-computed value, so a STATIC ORM-level default still ignored `is_backtest`. Fixed with a context-sensitive SQLAlchemy default (`get_current_parameters()`) reading the row's own `is_backtest` at insert time (J4 in the decision entry) — **0 failures** after this fix, confirmed by re-running the exact previously-failing suites.
- **Two real bugs found via testing against real Postgres** (both described in the decision entry, both fixed and re-verified with the migration downgraded and re-upgraded from scratch): a Postgres CHECK-constraint-vs-NULL gap, and a SQLAlchemy JSONB `None`-vs-`null` gap that would have silently defeated EX-7's entire nullable-snapshot mechanism. Neither was caught by writing the code carefully — both were caught by a failing assertion against a real database, which is exactly why this project's testing baseline insists on real Postgres over mocks.
- **This delivery's own 37 new tests, by acceptance criterion:**
  - AC #5 — `test_execution_registry.py` (4 tests): fail-closed venue/mode registration, registry-slot independence.
  - AC #7 ledger half / #8 — `test_execution_ledger.py` (7 tests): DB-level `IntegrityError` on a duplicate `client_order_id` and a duplicate `(execution_venue, venue_fill_id)`; `strategy_outcomes`' four CHECK constraints, DB-level and Pydantic-level.
  - AC #9, #11, #12, #13 — `test_portfolio_state.py` (9 tests): open/close/idempotent `apply_fill`; `rebuild_from_ledger()` recovering an open AND a closed position from committed-but-never-"published" fills (this delivery builds no publish path at all — J2 — so every closure in this suite genuinely is "critical event never handled," AC #12's own scenario, by construction rather than by simulation); ledger-wins-over-corrupted-memory with a logged warning; overfill and unmatched-order fills persisted and flagged, never dropped.
  - AC #10 — `test_reconciliation.py` (7 tests): all four `§6.9` step-3 branches (cancelled-stale-entry, resubmitted-exit, expired-lost-state, advanced-with-missing-fills-applied) against a real (fresh-instance) `SimulatedVenue`; reconciliation-pass idempotency; open-order and position-quantity discrepancy reporting.
  - `test_simulated_venue.py` (10 tests): market/limit fills, idempotent placement, session guard, honest-`None` for an unknown order, no memory across instances (the restart precondition §6.9 depends on), injectable partial-fill planner, cancel semantics, derived `get_positions()`.
- **Combined regression, final:** merged onto post-#171 `main`, migrated through `0012`, full suite: **922 passed, 0 failed** (one run of two surfaced the same intermittent `test_backtest_routes.py::test_two_separate_runs_isolate_level_interaction_state_and_events` failure #171 itself documented — reproduced in isolation 3× on unmodified code (pass, pass, fail), confirming it is this project's own long-documented #119 wall-clock-timing cluster, unrelated to this delivery; 810 (original baseline) + 37 (this delivery) + 75 (#171) = 922, exact). **Correction (`backtest-isolation-flake-fix`, see top of file):** the #119 attribution was wrong — #119 is a different, four-test `FeatureEngine`-warmup cluster in unrelated files; this failure is `test_backtest_routes.py`'s own, root-caused to a timeframe-ambiguous test query, unrelated to wall-clock timing.
- **Migration round-trip:** `alembic downgrade 0011` → `alembic upgrade head` run twice during development (once to fix the CHECK-vs-NULL bug, once to verify the final state) — both directions clean against real Postgres 16.

## Not covered by this delivery's own tests, stated precisely

The `PositionClosed` critical-lane publish itself (J2 — no payload model or `CRITICAL_EVENT_TYPES` entry exists yet; this task's file boundary excludes both files that would need to change). `main.py` startup sequencing of `rebuild_from_ledger()` → `reconcile_with_venue()` → resume (§6.9 steps 1-6 as an ordered whole) — the individual callable pieces (steps 2-3) are tested directly and thoroughly; the orchestration around them is outside this task's file boundary (`main.py` not in "may edit"). A real abort of migration `0012` on a pre-existing `is_backtest = false` row — none existed in this repository, so that specific path is exercised only by code inspection and the migration's own explicit row-count-and-raise logic, not by a live failing run against real data.

<!-- Previous delivery record retained below. -->

# TESTING — decision #171: Authorizer stub + entry-order Execution Engine built (`execution-authorizer-and-engine`)

## Baseline and evidence

Repository: `rotate-zero/agentic-trading-os`, `main`, pulled via tarball (`curl ... codeload.github.com ... | tar -xzf -`), three separate times across this session (start, immediately before editing `envelope.py` per fork 2's own instruction, and immediately before assigning the real decision number) — each re-pull `diff -rq`'d against the previous one, identical every time, so nothing landed on `main` mid-session (in particular, the sibling `execution-ledger-and-venue` task had not merged as of this delivery). Three-source check for the decision number, done immediately before writing the entry above: `INDEX.md`'s last row **#170**, `confirmed-decisions.md`'s tail **#170**, archive file list unchanged (`001-060` … `134-160`) — so **#171** was assigned only after that check, and every in-code reference to the temporary slug `execution-authorizer-and-engine` was replaced with `#171` before packaging (`grep -rn "execution-authorizer-and-engine" backend/` returns nothing outside this file/`CHANGES.md`/the decision log after that pass).

Baseline hashes of every file this task was allowed to edit, recorded before any change: `backend/app/schemas/events/execution.py` = `f64d6c90...`, `backend/app/core/config.py` = `5b5fe1ac...` (SHA-256, first 8 hex chars shown). Neither `backend/app/governor/` nor `backend/app/execution_engine/` existed before this delivery.

## Environment setup

Postgres 16 installed and started in the sandbox (`apt-get install postgresql postgresql-contrib`, allowed by the network egress list's `archive.ubuntu.com`/`security.ubuntu.com`); `CREATE USER trading WITH PASSWORD 'trading' SUPERUSER`, `CREATE DATABASE trading_workspace OWNER trading`, `alembic upgrade head` (migrated cleanly through `0011`, unchanged by this delivery — no new migration). Python venv, `pip install -r backend/requirements.txt`.

## What changed

New: `backend/app/governor/{__init__,ports,reference_price,rules,engine}.py`, `backend/app/execution_engine/{__init__,ports,engine}.py`, `backend/tests/{test_governor_rules,test_governor_config,test_governor_engine,test_execution_engine,test_execution_event_schemas}.py`.

Edited, additive only: `backend/app/schemas/events/envelope.py` (+1 `EventType` member, +1 `CRITICAL_EVENT_TYPES` entry — the one approved exception to the "may edit" list, fork 2), `backend/app/schemas/events/execution.py` (`OrderApproved.position_effect`; new `TradePlanned`/`OrderStatusChanged` models), `backend/app/core/config.py` (the three-setting block + one `field_validator`), `backend/tests/conftest.py` (+2 singleton-reset lines — the second approved exception, necessary for this delivery's own tests to be isolated from each other, same convention every prior engine there already needed).

Footprint confirmed by `diff -rq` of a freshly re-pulled, untouched `main` against the working tree (excluding `__pycache__`/`.pytest_cache`): exactly the 12 files above. Nothing under `backend/app/broker_adapters/**`, `backend/app/services/broker_registry.py`, `backend/app/models/**`, `backend/app/schemas/performance.py`, `backend/app/portfolio_state/**`, any Alembic migration, `main.py`, or any `docs/architecture/*.md` was touched — confirmed directly, not assumed.

## Test results

Targeted: `pytest tests/test_governor_rules.py tests/test_governor_config.py tests/test_governor_engine.py tests/test_execution_engine.py tests/test_execution_event_schemas.py -q` → **75 passed**.

Full suite: `pytest -q` → **885 collected**. First run: 885 passed, 0 failed. A second full run surfaced one intermittent failure, `test_backtest_routes.py::test_two_separate_runs_isolate_level_interaction_state_and_events` (described at the time as a wall-clock-time-sensitive assertion, `datetime(...,14:30,...)` vs `datetime(...,16:02,...)` — matches this project's own long-documented #119 intermittent cluster, e.g. decisions #128/#129/#131's own notes on the same class of test — **correction, `backtest-isolation-flake-fix`, see top of file: neither characterization holds; it is not wall-clock-driven and not the #119 cluster, it is `test_backtest_routes.py`'s own timeframe-ambiguous test query**). Confirmed pre-existing, not caused by this delivery, per this project's own testing philosophy ("pre-existing flakiness must be confirmed pre-existing... before attributing any test failure to new work"): (1) the same test passes in isolation every time; (2) the full suite run against a **freshly re-pulled, completely untouched `main`** (zero files from this delivery present) reproduces the exact same failure with the exact same values, 809 passed / 1 failed — 809 + this delivery's 75 = 884, matching this delivery's own second-run count of 884 passed / 1 failed exactly. Zero regressions attributable to this delivery either way.

**Why fakes, not real Postgres, for `test_governor_engine.py`/`test_execution_engine.py`:** fork 1 (Saqib, 2026-09-22) — this task's file boundary forbids the real ledger tables/migration, so `TradeLedgerPort`/`PortfolioStateReader`/`OrderLedgerPort`/`DecisionAuthorizationPort` have no concrete real-Postgres implementation to test against yet. `test_governor_rules.py` (the pure rule pipeline this task DOES own outright) is real, DB-free, mock-free pure-function testing per the usual convention — only the persistence SEAM uses fakes, not this task's own logic.

## Manual reconciliation notes for merge (for whoever merges this alongside `execution-ledger-and-venue`)

1. **`core/config.py` append point.** This delivery's block is appended after `scanner_weight_premarket_volume_ratio` and before `get_settings()`, importing `field_validator`/`ValidationInfo` from `pydantic` (new imports to this file). The sibling task appends `execution_mode` in its own block — expected to be a trivial sequential append (both blocks land one after another, no line overlap), not a real conflict. `AuthorizerStub`/`ExecutionEngine` both already read `execution_mode` defensively via `getattr(get_settings(), "execution_mode", None)` — no code change needed here once that field lands; it starts being read for real automatically.
2. **`OrderVenue` interface reconciliation.** `execution_engine/ports.py`'s `OrderVenue` `Protocol` is this task's own reading of design-doc §6.4 — async `connect`/`disconnect`/`place_order`/`cancel_order`/`get_order`/`list_open_orders`/`get_fills`/`get_positions`, sync `is_connected`/`on_order_update`, matching `broker_adapters/base.py`'s existing async convention. `SimulatedVenue` (sibling) needs no inheritance to satisfy it (`Protocol` is structural) — only matching method names/signatures. Reconcile by running `test_execution_engine.py`'s fakes' shape against `SimulatedVenue`'s real one once it lands; if a signature drifts, this Protocol is the one to fix (nothing in `governor/` depends on it).
3. **Ledger `Protocol` reconciliation.** `governor/ports.py`'s `TradeLedgerPort`/`PortfolioStateReader` and `execution_engine/ports.py`'s `OrderLedgerPort`/`DecisionAuthorizationPort` are this task's own narrow reading of what the real ledger/Portfolio State need to expose. A single concrete adapter class over the sibling's real ORM models can satisfy all four simultaneously (`Protocol`s are structural, no shared base class needed) — `DecisionAuthorizationPort.has_committed_decision()` and `TradeLedgerPort.commit_decision()` both read/write the same underlying `trades` row, by design (see the decision entry's own "Fork 1" note for why they're two Protocols, not one).
4. **`broker_registry`'s `execution` role.** `execution_engine/ports.py::default_execution_venue_provider()` duck-types onto `broker_registry.get_execution_venue` via `getattr(..., None)` — no code change needed in this task's files once that role is added; it starts resolving automatically. Confirmed by direct read of `broker_registry.py` at the start of this session: no `execution` role, no `get_execution_venue`, exists yet.
5. **`main.py` wiring.** Neither `AuthorizerStub` nor `ExecutionEngine` is started by `main.py`'s `lifespan()` — outside this task's file boundary (`main.py` isn't in "may edit"). `get_authorizer_stub(bus, trade_ledger, portfolio_state)` / `get_execution_engine(bus, order_ledger, decision_authorization)` both raise `RuntimeError` with a clear message if called without their required concrete ports — by design, so a real wiring attempt fails loudly rather than silently no-op'ing, until the sibling's concrete implementations exist to pass in.

## Not verified / explicitly out of scope for this delivery

Real-Postgres verification of AC #7's "creates one `orders` row" (only the client-order-id-mint half, the deterministic-ID/duplicate-detection CONTRACT, is verified here, against a fake ledger); AC #8 (replayed `venue_fill_id`), #9 (persist-before-publish fault injection for fills), #13 (overfill/unknown-order anomalies), #14 (missing snapshot at fill time) — all require the real ledger and/or fill processing, neither built here. AC #5's `set_execution_venue()` registry-refusal half (only the Execution Engine's own venue-refusal half is built/tested here). The rest of AC #17 beyond the gate's own arithmetic (this task tests the GATE, i.e. `rules.py`, directly with constructed `PortfolioSnapshot` fixtures — the real Portfolio State computation of those raw exposures against a live ledger is the sibling's/a later task's own scope). AC #19's reduce-only/exit-refusal half (needs EX-5). AC #22's full regression (this delivery's own footprint is verified regression-clean; the AGGREGATE regression once the sibling's changes land is a merge-time check, not something this delivery alone can run). No real venue exists in this sandbox — `SimulatedVenue` is entirely faked in this delivery's own tests.

<!-- Previous delivery record retained below. -->

# TESTING — decision #170: Execution Engine design amended — Slice A approved in principle (`execution-engine-design-amendment`)

## Baseline and evidence

Repository: `rotate-zero/agentic-trading-os`, `main`, pulled as a tarball (no `.git` metadata in this sandbox). At the start of this revision `main` carried decisions **#168** (the original design delivery) and **#169** (the Phase 4 measurement). Verified, not assumed: the design doc on `main` is byte-identical to the delivery it came from (`cmp`), and the #168 entry in `confirmed-decisions.md` matches it. A second fresh pull taken immediately before packaging was compared with the first by `diff -rq` — **identical**, so nothing landed in between. Three-source check for the number: `INDEX.md` last row **#169**, `confirmed-decisions.md` tail **#169**, archive files `001-060` … `134-160` unchanged — so **#170** was assigned only after that re-check.

**Why a new decision instead of editing #168.** #168 is merged, and `AGENTS.md` §6 says existing decision content is immutable and is corrected by a new entry that references the original (check C68). So #170 *amends* #168; #168's text is untouched, and the living design doc is revised in place.

This delivery is **documentation only**. No file under `backend/` or `frontend/` changed; no application code, migration, schema, configuration key, or event model was written. No application tests were run for that reason.

## What changed (exact six-file footprint)

| File | Change |
|---|---|
| `docs/architecture/execution-engine-design.md` | revised in place: status (approved in principle), §0 summary, F6/F10b/F11 notes, invariants (I4, I6, I7, I8 amended; **I10–I15 added**), §4 rows, §5 decision, **§6 rewritten** (revised data-flow diagram; authorizer stub with four fail-closed layers, rules and the daily-loss gate; Execution Engine with client-order IDs, dedupe, and persist-before-publish; the `OrderVenue` port and `execution` registry role; Portfolio State as a ledger-backed cache; `OutcomeRecorder` with nullable snapshots + reasons; persistence and migration sketch; **new §6.9 restart recovery**, **new §6.10 configuration**, §6.11 mechanisms), §7 fork statuses and §7.1, §8–§9, and findings R7–R9 in §10 |
| `docs/architecture/system-design.md` | the companion-doc entry and the two pointer paragraphs under §4.6 and §4.9 only; no other text touched |
| `docs/decisions/confirmed-decisions.md` | decision #170 appended (`### 170. …`); #168 and every earlier entry untouched |
| `docs/decisions/INDEX.md` | row #170 appended |
| `CHANGES.md`, `TESTING.md` | this delivery's records |

## Checks and results

- **Footprint:** `diff -rq` of a fresh untouched `main` pull against the working tree lists exactly the six files above.
- **Claim-to-source check: 72 of 72 pass** against latest `main`. The original 54 checks (code and doc facts behind the design) still hold on `main` with #169 merged; **18 were added for this revision** (C55–C72), covering exactly the facts the amendments rest on: the bus's handler-failure isolation and fire-and-forget `publish` (C55, C56, plus the existing C37/C38), the registry's two `MarketDataProvider`-typed role slots (C57, C58), `Settings` as the single configuration source and the paper-default IBKR port (C59, C60), the four snapshot columns being `NOT NULL` today and the `schema_version` rule (C61, C62), the `ExecutionMode` naming collision (C63), the Backtest Runner's `to_thread` call and `DiscardedSignal` (C64, C65), `MarketClock.is_regular_session` (C66), the absence of any order-status event (C67), the immutability rule (C68), #168/#169 on `main` (C69), and that none of the proposed new names (`execution_mode`, `execution_venue`, `OrderVenue`) exist in `backend/` yet (C70, C71, C72) — so the doc's "new work" claims are true. Code-level claims use Python `ast`.
- **Internal consistency of the doc (scripted):** 7 relative links, 0 unresolved; every `EX-n`, `I1`–`I15`, and `§6.x`/`§7.1` reference resolves to a defined target; 14 fork headings — 6 RESOLVED (EX-1, 2, 3, 4, 6, 7), 1 SETTLED (EX-10), 7 OPEN (EX-5, 8, 9, 11, 12, 13, 14); 9 fenced blocks (the revised data-flow diagram, the authorizer stub's gate flow and daily-loss formula, the Execution Engine flow and order state machine, Portfolio State, `OutcomeRecorder`, restart recovery, and the slice sketch); no placeholders or TODOs.
- **Decision-log format:** heading is `### 170. …` (the repo's grep `^### [0-9]+\.|^[0-9]+\. \*\*` finds it), appended after #169; no earlier text changed.
- **Fidelity to Saqib's instructions:** each of the six resolutions, the six added requirements, and the three limits appears in the doc (§3, §6.2, §6.3, §6.4, §6.5, §6.7–§6.10, §7), the decision entry, and the acceptance criteria (§9, items 3–18).

## Claim-to-source table (machine-checked)

| ID | Claim (design doc or decision #170) | Result | Where checked |
|---|---|---|---|
| C1 | All 8 execution-side EventType names exist | PASS | envelope.py:EventType |
| C2 | Payload models exist for GovernorDecision, OrderApproved, PlanRejected, OrderFilled | PASS | models in execution.py = GovernorDecision,OrderApproved,PlanRejected,… |
| C3 | No Pydantic class TradePlanned/OpportunitySelected/PositionAdjusted/PositionClosed anywhere in backend/app/schemas | PASS | grep class defs in schemas/ |
| C4 | Critical set = exactly OrderFilled, PlanRejected, GovernorDecision, OrderApproved | PASS | CRITICAL_EVENT_TYPES = { EventType.ORDER_FILLED, EventType.PLAN_REJEC… |
| C5 | channels.py routes ORDER_APPROVED/PLAN_REJECTED/ORDER_FILLED/GOVERNOR_DECISION/OPPORTUNITY_SELECTED | PASS | channels.py |
| C6 | channels.py has no route for TRADE_PLANNED / POSITION_ADJUSTED / POSITION_CLOSED | PASS | channels.py |
| C7 | No application code (AST: names/attrs/imports, docstrings and comments ignored) other than execution.py/envelope.py/channels.py/dev.py references any of the 8 execution events | PASS | code references elsewhere = 0 |
| C8 | dev.py publishes a GovernorDecision | PASS | api/routes/dev.py |
| C9 | OrderApproved fields = order_id,symbol,side,qty,order_type,limit_price | PASS | execution.py (no fill_id anywhere) |
| C10 | OrderFilled has no symbol field and no fill_id/cumulative_qty/venue | PASS | OrderFilled body |
| C11 | base.py declares BrokerAdapter, OrderRequest, OrderAck, Position + place_order/cancel_order/get_positions | PASS | base.py |
| C12 | OrderAck.status is submitted\|rejected only | PASS | OrderAck body |
| C13 | No order-update/fill callback, client order id, TIF, or bracket method/field in base.py (AST: defs and annotated fields) | PASS | offending identifiers = [] |
| C14 | BrokerAdapter extends MarketDataProvider | PASS | base.py |
| C15 | IBKRAdapter connects readonly=True | PASS | ibkr_adapter.py |
| C16 | IBKRAdapter place_order and cancel_order raise NotImplementedError | PASS | NotImplementedError x3 |
| C17 | get_positions() has zero callers (app/tests/frontend) | PASS | callers=0 |
| C18 | No call to place_order()/cancel_order() anywhere in backend/app or backend/tests (AST calls) | PASS | calls=[] |
| C19 | broker_registry has streaming+historical roles and no execution role | PASS | broker_registry.py |
| C20 | config.py has no dry_run/execution setting | PASS | config.py |
| C21 | No trades/orders/positions/ai_decisions/feature_snapshots/market_events tables | PASS | tables=daily_levels_state,symbols,candles,market_state_history,scanne… |
| C22 | strategy_outcomes and backtests tables exist | PASS | models |
| C23 | Migration head is 0011 | PASS | 0011_level_interaction_backtest_run_isolation.py |
| C24 | StrategyOutcomeRecord: strategy_name/strategy_version/opportunity_id/structural_*/final_*/confidence_at_signal/evidence NOT NULL | PASS | StrategyOutcomeRecord |
| C25 | StrategyOutcomeRecord has is_backtest Boolean and no CheckConstraint | PASS | StrategyOutcomeRecord |
| C26 | StrategyOutcomeRecord has no venue column | PASS | StrategyOutcomeRecord |
| C27 | record_strategy_outcome is sync def, uses SessionLocal, and docstring forbids live wiring without Execution Engine | PASS | performance.py |
| C28 | record_strategy_outcome has exactly one application user (AST name reference; runner.py passes it to asyncio.to_thread): backtest_runner/runner.py | PASS | ['backend/app/backtest_runner/runner.py'] |
| C29 | state_snapshot: capture_strategy_outcome_snapshots + capture_market_state_snapshot + capture_context_snapshot | PASS | state_snapshot.py |
| C30 | MarketStateEngine.get_snapshot returns candle_ts per symbol | PASS | market_state_engine/engine.py |
| C31 | performance_queries._common_filters filters on is_backtest | PASS | performance_queries.py |
| C32 | World View portfolio slot is None | PASS | composite.py |
| C33 | Opportunity has no id, symbol, or entry-price field | PASS | Opportunity body |
| C34 | Backtest Runner mints opportunity_id with uuid4 | PASS | runner.py |
| C35 | OpportunityCache overwrites latest per (symbol, strategy) | PASS | opportunity_cache.py |
| C36 | fill_simulator: simulate_entry/simulate_exit/compute_realized_r/compute_realized_pnl/regular_session_close_utc + InsufficientReplayDataError | PASS | fill_simulator.py |
| C37 | EventBus._consume awaits asyncio.gather over handlers; _safe_call exists | PASS | bus.py |
| C38 | Event Bus is in-memory (asyncio.Queue) with no persistence import | PASS | bus.py |
| C39 | No frontend consumer of orders.status | PASS | matches=0 |
| C40 | No frontend Positions/ApprovalQueue/TradeManagement code | PASS | matches=0 |
| C41 | No TradeRequest/ExecutionMode/ManualConfirm*/PlanAwaiting* code | PASS | matches=0 |
| C42 | MarketClock.trading_day exists | PASS | market_clock.py |
| C43 | PriceUpdated has exchange_ts | PASS | schemas/events/market_data.py |
| C44 | A strategy declares gate_conditions {'session': 'regular'} | PASS | strategy_engine/*.py |
| C45 | FeatureEngine._on_candle_closed exists (subscribe -> queue pattern) | PASS | feature_engine/engine.py |
| C46 | system-design §10.3 TradePlanned row has max_hold_minutes; TIA §18.3 TradePlan has max_hold_seconds | PASS | system-design.md / TIA |
| C47 | system-design §4.8 table: Position Monitor -> PositionAdjusted/PositionClosed -> positions; Performance Intelligence consumes PositionClosed | PASS | system-design.md |
| C48 | system-design still names AlpacaAdapter in §4.1, and folder tree says 'Alpaca deferred, not stubbed' | PASS | system-design.md |
| C49 | TIA §18.5: ExecutionMode owned by Portfolio State; ManualConfirmOrder calls place_order | PASS | TIA §18.5 |
| C50 | TIA §18.8 says filled manual plan recorded in 'the existing `trades` table' | PASS | TIA §18.8 |
| C51 | TradeRequest has no stop field; TradePlan.stop required | PASS | TIA §18.2-18.3 |
| C52 | future-ideas has entries #14, #16, #27 | PASS | future-ideas.md |
| C53 | system-design folder tree names execution_engine/, portfolio_state/, position_monitor/, governor/ | PASS | system-design.md §8 |
| C54 | decision #95 documents Finnhub free-tier IEX-only trade feed | PASS | archive/091-106.md |
| C55 | EventBus._safe_call catches Exception, logs it, and never re-raises (handler failures are isolated, not propagated) | PASS | bus.py:_safe_call |
| C56 | EventBus.publish only enqueues onto an in-memory queue (never awaits a handler) | PASS | bus.py:publish = async def publish(self, envelope: EventEnvelope) -> … |
| C57 | broker_registry holds exactly two role slots, both MarketDataProvider-typed (_streaming_provider, _historical_provider); no OrderVenue | PASS | broker_registry.py |
| C58 | broker_registry docstring names the roles as decision #33's pattern | PASS | broker_registry.py docstring |
| C59 | config.py: Settings(BaseSettings) is the documented single source of configuration | PASS | config.py |
| C60 | config.py: ibkr_port default 4002 documented as the PAPER Gateway | PASS | config.py |
| C61 | StrategyOutcomeRecord: the four snapshot columns are currently NOT NULL | PASS | StrategyOutcomeRecord |
| C62 | StrategyOutcome.schema_version description: a new OPTIONAL field doesn't bump; changing a field's meaning does | PASS | schemas/performance.py |
| C63 | TIA §18.5 defines ExecutionMode as auto\|manual (the naming collision with execution_mode) | PASS | TIA §18.5 |
| C64 | Backtest Runner passes record_strategy_outcome to asyncio.to_thread | PASS | runner.py |
| C65 | Backtest Runner turns a None snapshot into a DiscardedSignal (decision #128) | PASS | runner.py |
| C66 | MarketClock.is_regular_session exists | PASS | market_clock.py |
| C67 | No order-status/venue-rejection EventType exists (ORDER_REJECTED / ORDER_STATUS_CHANGED) | PASS | envelope.py |
| C68 | AGENTS.md: existing decision content is immutable; correct by adding a new entry that references the original | PASS | AGENTS.md |
| C69 | Main already carries #168 (this design) and #169 (Phase 4 measurement) in both decision-log files | PASS | INDEX.md / confirmed-decisions.md |
| C70 | Registry role for an OrderVenue does not exist yet (proposal is new work): no set_execution_venue anywhere in backend/app | PASS | grep backend/app |
| C71 | No execution_mode / execution_venue column or field exists yet (proposal is new work) | PASS | grep backend/app, alembic |
| C72 | Only IBKRAdapter overrides place_order among BrokerAdapter subclasses (dormant stubs, unwired) | PASS | def place_order count (base + ibkr) |

## Not covered / limitations

- **Nothing was built or run.** The amended design is a specification; no prototype, migration, or test of any proposed constraint, recovery path, or gate exists. The acceptance criteria (§9) are what a build task must turn into tests.
- **Two proposals rest on judgment, not on Saqib's instruction** and are listed for confirmation in §7.1 (J1–J5) — chiefly that the daily-loss gate also counts the candidate trade's own stop-out loss (J2), the `schema_version` bump and backtest-row labels (J3), and cancelling unsent entry orders at recovery (J4).
- **EX-5 and EX-12 remain open** and need confirmation before a build task.
- The claim check proves cited symbols exist and behave as stated at this `main`; it cannot prove the proposals correct.
- Left alone per the boundary: `docs/roadmap/phase-roadmap.md`, `docs/architecture/trading-intelligence-architecture.md`, `docs/architecture/strategy-engine-open-decisions.md`, `docs/architecture/scanner-design.md`, `backend/`, `frontend/`.

## Manual merge notes (parallel session)

If another session lands a decision first, renumber this one and re-run the three-source check. **`#170` appears in exactly these places:** the design doc (status line, §3 table, §6, §7, §7.1, §8–§10 — every `#170`/`decision #170` token), the `### 170.` heading in `confirmed-decisions.md`, the `| 170 |` row in `INDEX.md`, the three pointers in `system-design.md`, and the title lines of `CHANGES.md` and `TESTING.md`. A single find-and-replace of the token `#170` (and `| 170 |`, `### 170.`) across those six files covers it; no other `#170` exists in the repository (verified). `CHANGES.md`: keep both records. `TESTING.md`: keep this record on top and the other directly below.

## Baseline SHA-256

The five pre-existing files this delivery edits were clean at baseline with these hashes (the design doc's baseline is the #168 delivery, byte-identical to `main`):

```text
docs/architecture/execution-engine-design.md 4b3127ea6b287ab949e6510bea7f45b14c488dac17240d195203f5dddc434a27
docs/architecture/system-design.md e35e42fde8b4a4a350797a74c00198cb00f5197241cbf8efbc2d854ace09ccbc
docs/decisions/INDEX.md 1a7b276a24c7bf69f97927890a5fa2a77d83513fd503aac1d995ab3438c87264
docs/decisions/confirmed-decisions.md 07e4ff1dd43ccb4dfe5631605ada67a5fbec7d3728108428bea2be3cc61fb83c
CHANGES.md 06280c9dcbf93b65907af488f89d4f39f6af35a6b45b60709393c791c323eee0
TESTING.md 276a21d77f65cb0676c61a993d0b6cbb29ba393e42a2e908b06f90ebbef9b9bb
```

Design doc after this delivery: `docs/architecture/execution-engine-design.md 4827c1101fee4c5d08cb442c4599641569c90f98505ae8329d28b696a10199fa` (recompute after any renumbering).

<!-- Previous delivery record retained below. -->

# TESTING — decision #169: Phase 4 scale/load investigation (`phase4-scale-load-measurement`)

## Baseline and evidence

Repository: `rotate-zero/agentic-trading-os`, `main`, pulled as a tarball
(`codeload.github.com/.../tar.gz/refs/heads/main`; no `.git` metadata in this
sandbox). Highest decision at task start: **#167** (three-source check
agreed: `INDEX.md` last row #167, `confirmed-decisions.md` tail #167, archive
files `001-060`…`134-160` — open log held #161–#167). A second, fresh pull
taken immediately before assigning a real decision number found the parallel
`execution-engine-design` sibling had already landed **#168** — confirmed by
the same three-source check against the fresh pull (`INDEX.md` last row
#168, `confirmed-decisions.md` tail #168, archive files unchanged). That
sibling's own `TESTING.md` record (retained below) named this task by its
exact slug in a "Manual merge notes" section and anticipated exactly this
ordering, including exactly which four files are shared and how to merge
them. Diffed the fresh pull against this session's working tree for every
file outside the four shared ones this task might touch
(`docs/roadmap/phase-roadmap.md`, `docs/architecture/scanner-design.md`,
`backend/scripts/`): identical — zero collision on this task's own file
boundary. **#169 assigned only after that re-check.**

## What changed (exact five-file footprint)

| File | Change |
|---|---|
| `backend/scripts/measure_live_pipeline_scale.py` | **new** — opt-in harness, not pytest-collected (matches no glob in `pytest.ini`) |
| `docs/roadmap/phase-roadmap.md` | one sentence — Phase 4 exit-criterion status, now measured-on-synthetic-input |
| `docs/decisions/confirmed-decisions.md` | decision #169 appended at the true end (`### 169. …`) |
| `docs/decisions/INDEX.md` | row #169 appended after #168 |
| `CHANGES.md`, `TESTING.md` | this delivery's records, prepended above the #168 record (kept intact below) |

**Not touched:** `backend/app/**`, `frontend/**`, `backend/tests/**`,
`docs/architecture/system-design.md`, `docs/architecture/scanner-design.md`
(see the decision entry for why — a different, still-open question), any
existing decision text.

## Environment

1 vCPU / 3.9 GB sandbox (`nproc`=1, `os.cpu_count()`=1), Ubuntu 24.04, Python
3.12.3, PostgreSQL 16.15 (apt, freshly installed this session — not present
at container start). Scratch database only:

```bash
# one-time setup
service postgresql start
su postgres -c "psql -c \"CREATE USER trading WITH PASSWORD 'trading' SUPERUSER;\""
su postgres -c "psql -c \"CREATE DATABASE trading_scale_scratch OWNER trading;\""
su postgres -c "psql -c \"ALTER SYSTEM SET fsync = off;\""   # matches decision #155's own precedent
su postgres -c "psql -c \"SELECT pg_reload_conf();\""

cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=trading_scale_scratch \
  POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/python -m alembic upgrade head   # -> head 0011, clean
```

SQLAlchemy `create_engine()` (`app/db/session.py`) takes no explicit
`pool_size`/`max_overflow` — confirmed by reading the call site — so both are
library defaults (`pool_size=5`, `max_overflow=10`). Default
`asyncio.to_thread` executor size on this box: `min(32, cpu+4) == 5` threads
(process-wide, shared by every stage).

## Reproduction

```bash
cd backend
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=trading_scale_scratch \
  POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/python scripts/measure_live_pipeline_scale.py \
  --ramp 1,10,25,50,100 --bursts 16 --tick-minutes 3 --ticks-per-minute 3 \
  --output full_ramp_results.json

# supplementary stress point (N=100, 60-candle burst — beyond the requested ramp)
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=trading_scale_scratch \
  POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/python scripts/measure_live_pipeline_scale.py \
  --ramp 100 --bursts 60 --tick-minutes 3 --ticks-per-minute 3 \
  --output stress_n100_k60.json
```

The script refuses to run unless `POSTGRES_DB` contains `"scratch"`
(`_require_scratch_db()`) — a hard guard, not a convention, since it
`TRUNCATE`s application tables between ramp steps.

## Results

Full numeric results are in the decision #169 entry (`confirmed-decisions.md`)
— tables reproduced from this run's own JSON output, not hand-transcribed.
Summary:

| N | FeaturesUpdated | drain | LevelInteraction (queue-verified) | drain | MarketStateChanged | 1m coverage |
|---|---|---|---|---|---|---|
| 1 | 21 | 0.031s | queue fully drained (3 events) | 0.058s | 1 | 1/1 |
| 10 | 210 | 0.236s | queue fully drained (17 events) | 0.427s | 20 | 10/10 |
| 25 | 525 | 0.673s | queue fully drained (36 events) | 1.203s | 50 | 25/25 |
| 50 | 1050 | 1.157s | queue fully drained (57 events) | 2.205s | 124 | 50/50 |
| 100 | 2100 | 2.744s | queue fully drained (125 events) | 4.194s | 400 | **100/100** |

Stress point N=100/K=60: 7,700 FeaturesUpdated in 9.11s; LevelInteraction
queue fully drained (`queue.join()`) in 14.03s (1,038 events); 1m coverage
100/100. `MarketStateChanged` settle timed out at its fixed 2.0s window
(`"timed out after 2.0s at count=1051"`) — a documented lower bound for that
one number at K=60 only (the settle window is sized for the K=16 primary
ramp); does not affect the FeatureEngine/LevelInteractionEngine coverage or
drain-time numbers.

Stage A (tick ingestion), every N: `PriceUpdated` observed == expected and
`CandleClosed` observed == expected (10/10 … 1000/1000 ticks; 3/3 … 300/300
candles); zero `LiveTickRelay` active-symbol gating violations at any N.

Full per-burst queue-depth telemetry, per-symbol coverage arrays, and raw
timing are in `full_ramp_results.json` / `stress_n100_k60.json` (not
committed — regenerate via the commands above; each run TRUNCATEs and
reseeds the scratch database itself, so results are exactly reproducible
modulo this sandbox's own timing noise).

## Checks and results

- **Footprint:** `diff -rq` of a fresh untouched `main` pull (post-#168)
  against the working tree lists exactly the five files above and nothing
  else.
- **No production code changed:** confirmed no `backend/app/**` file's
  content differs from the fresh pull (`diff -rq`); this harness only reads
  private `_queue` attributes and calls existing public methods
  (`bus.subscribe_all`, `queue.join()`, `evaluate_for_symbol` indirectly via
  `ContextEngine.start()`'s own bootstrap) — nothing under
  `backend/app/**` was edited to make any of this observable.
- **No suite-time impact:** `backend/scripts/measure_live_pipeline_scale.py`
  matches no glob `pytest.ini` collects (`grep -n "python_files\|testpaths"
  backend/pytest.ini` — scripts/ is not a testpath; the file also has no
  `test_` prefix). Not run as part of the backend test suite; full suite was
  not re-run for this delivery since no application code changed (same
  posture as decision #155/#158/#168's own precedent for docs/investigation-
  only deliveries).
- **Harness self-verification (smoke test, N=1):** cross-checked
  `LevelInteractionChanged`'s low count directly against
  `level_interaction_state` — 6 rows (vwap/vwap_ext/regular_open × 1m/5m),
  `updated_at` spanning the whole run, confirming the engine really
  processed every item even though it published only 1 transition event
  (see decision entry Finding 1).
- **Coverage is per-symbol, not just aggregate:** every ramp step asserts
  `min == max == burst_count` across all N symbols' individual
  `FeaturesUpdated(1m)` counts, not just that the total matches.

## Not covered / limitations

- No real broker/feed exercised — everything upstream of `CandleClosed`
  publication in Stage B, and the tick source in Stage A, is synthetic.
- Real tick burstiness, reconnects, and partial/duplicate provider delivery
  are not represented.
- Production hardware was not used; this sandbox's `fsync=off` is a real
  advantage a production database likely won't have, and its 1 vCPU is a
  real disadvantage a production host likely won't have — both stated, not
  netted against each other.
- `ContextEngine`, `StrategyScheduler`, and `OpportunityCache` ran (so
  `StrategyScheduler` genuinely evaluated all 7 strategies per
  `MarketStateChanged`, via seeded `scanner_universe_symbols` rows) but
  their own throughput was not separately measured or reported.
- N was not pushed past 100 (K was, at fixed N=100, to 60). The
  `MarketStateChanged` settle-wait limitation at K=60 is noted above and in
  the decision entry.

## Manual merge notes (parallel session)

This delivery landed **second**, after the sibling `execution-engine-design`
task's #168. Per that sibling's own anticipated merge notes (retained
below): `confirmed-decisions.md`/`INDEX.md` keep both entries in numerical
order (#168 then #169 — already true, appended at the true end); `CHANGES.md`
keeps both records (this one prepended on top); `TESTING.md` keeps this
record on top and the sibling's retained directly below, unedited.

## Baseline SHA-256

The five pre-existing files this delivery edits were clean at baseline
(matching the fresh post-#168 pull) with these hashes (the new file has
none until after this delivery):

```text
docs/decisions/confirmed-decisions.md 79a37d3b51d782c627ff6988e8ea2781709128e59f0857eef39ce70726abd152
docs/decisions/INDEX.md               15b84c8293501d5558ab012d2e1cae0877e525e3ab2ebae116cc06cfd7839d88
CHANGES.md                            61faa01cf81ca90eac4b29fcf82e69312edbb5a7879f437214de6b609437284d
TESTING.md                            41723b3f0ffd301495e45f806968b3c1cb61037241e6d601ecade799473ce83e
docs/roadmap/phase-roadmap.md         473239117a5c5f08ffa2a0930a981a27963c3e252445d9d5a842933bb8380651
```
