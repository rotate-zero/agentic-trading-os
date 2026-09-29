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
