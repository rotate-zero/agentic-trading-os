# CHANGES — decision #160: Level Interaction persistence isolated per backtest run

## Current delivery

- D20 resolved: Level Interaction persistence is now isolated and attributable per backtest run. Two identical `/backtest/run` calls and duplicate same-symbol sweep pairs no longer inherit touch/zone/resolution state and both produce the same deterministic outcome count.
- `level_interaction_state` and `level_interaction_events` now carry `is_backtest` plus nullable `backtest_run_id`, with composite symbol-namespace FKs, database CHECKs enforcing exactly live/NULL or backtest/non-NULL, and cascading run FKs for replay-derived lifecycle ownership.
- State uses separate partial unique indexes for the live singleton and backtest run scopes, avoiding PostgreSQL nullable-unique semantics. Events remain append-only and gain only run attribution plus a run-read/cleanup index.
- Migration `0011` preserves legacy live rows and removes legacy backtest Level Interaction rows whose run provenance cannot be recovered. Downgrade removes replay-derived rows before restoring the legacy state uniqueness constraint. A real PostgreSQL `0010 → 0011 → 0010 → 0011` round-trip passed with legacy and two-run fixtures.
- `LevelInteractionEngine` now requires `backtest_run_id` exactly when `is_backtest=True`; cold-start load, state lookup/upsert, and event append all use the exact origin/run scope. `EngineBackedReplayStateProducer` passes through its existing run UUID. `BacktestRunner` remains unchanged.
- The confirmed route regression failed `[1, 0]` before the fix and passed `[1, 1]` afterward while directly validating distinct state rows, semantic state values, and event ownership. Constructor, database invariant, cascade, live, replay-producer, and sweep coverage were added or strengthened.
- `backtest-runner-design.md` documents the as-built runner-to-persistence flow and internal load/upsert/append flow with the required diagrams. D20 is resolved as decision #160.

## Prior delivery record — decision #159: POST /backtest/sweep (batch/sweep endpoint v1)

- New, additive `POST /backtest/sweep` route: one strategy across an explicit `symbols` × `scenarios` cross-product, run sequentially through the exact existing `BacktestRunner`/`_RUN_LOCK` path (no new locking mechanism), sharing one real `sweep_id` across every resulting `backtests` row. Existing `/backtest/run`, `/backtest/run/ibkr` unmodified.
- `BacktestRunner.__init__` gains one new, optional, additive parameter: `sweep_id: UUID | None = None`. Every existing caller is unaffected — omitting it still mints a fresh `uuid4()` per run, exactly as before.
- Confirmed with Saqib before implementation: both `symbols`/`scenarios` lists explicit and required (no implicit "all known symbols"/"all scenarios" expansion); batch bound `len(symbols) * len(scenarios) <= 20`, sized off a measured ~2.01s/run in this environment. Fixture scenarios only — `/backtest/run/ibkr`'s real-data path is explicitly out of scope.
- A fresh `Strategy` instance is built per `(symbol, scenario)` pair rather than reused across the loop — confirmed necessary by reading all 7 strategy implementations directly (each holds per-symbol-keyed internal state).
- Partial-failure handling: pre-execution validation rejects the whole request before any run starts; a genuine per-pair error afterward is caught, logged, and reported per-pair without aborting the remaining pairs or losing already-completed results — matching an existing codebase convention for independent-item batches (`FeatureEngine`'s worker loop, `websocket/manager.py`'s broadcast).
- The live-trading guard (decision #132) is checked once before the sweep starts, matching `/backtest/run`'s own existing precedent.
- Response ordering (`symbols` outer, `scenarios` inner) is deterministic by construction, never database order.
- Real finding surfaced during verification, documented but not fixed in this delivery: repeat real runs of the same symbol (within a sweep, or via two separate `/backtest/run` calls) can legitimately produce different `outcomes_recorded`, because `LevelInteractionState`/`LevelInteractionEvent` never received the per-run `backtest_run_id` isolation `daily_levels_state` got at decision #141/D19. Flagged to Saqib directly; `strategy-engine-open-decisions.md` was not touched (this delivery's own explicit boundary — owned by a parallel session).
- New `backend/tests/test_backtest_sweep_route.py` (13 real-Postgres tests, including a non-mocked proof of strict sequential execution via a spy on the real lock context manager) and two new unit tests in `test_backtest_runner.py` for `sweep_id` threading. Full backend suite: 798 passed (783 baseline + 15 new), 77.68s.
- `docs/architecture/backtest-runner-design.md` gained a new as-built note (decision #159) with a cross-component data-flow diagram and an internal-loop diagram.
- Route-only — no frontend change, matching this project's established backend-first sequencing.

## Prior delivery record — decision #157: deterministic fast backtest replay

### Current delivery (as landed)

- `MarketStateEngine` now derives Acceleration elapsed seconds from consecutive source 1m `candle_ts` values. First and non-positive deltas remain honestly `None`; the existing points/second cap is unchanged.
- `DebounceScheduler.flush()` atomically consumes a pending callback and neutralizes its delayed task. `stop()` now awaits canceled scheduler tasks.
- Backtest-only `MarketStateEngine.settle_replay()` flushes the latest payload and drains the authoritative worker queue. Replay does not start a periodic ceiling; live/default cadence remains 1s floor and 10s/4s ceiling.
- `EngineBackedReplayStateProducer.advance_to()` now uses exact bus/worker queue completion; polling, `ReplaySettleTimeout`, and its constructor knobs were removed.
- Tests cover pace-independent Acceleration, source-time gaps, first/non-positive deltas, live cadence, no delayed duplicate event/row, exact Feature/Market State/Context settlement, D18/D19 isolation, route behavior, and Momentum's intentional zero-outcome change.
- Existing 119-candle timing check: 1.97s process wall (pytest 1.47s) in the documented local environment, versus decision #155's ~118s baseline in a different sandbox.
- Backend/frontend route copy was corrected to remove obsolete one-second-per-candle and fixed-duration claims; no frontend controls or behavior were added.
- Batch/sweep orchestration remains out of scope. Serialized runs and synchronous HTTP routes remain.
- Confirmed without changing it: passing the last fixture timestamp as `end` to the `[start,end)` provider excludes that candle.

## Prior delivery record — decisions #155 and #156

## Part A — Backtest replay timing investigation (decision #155)

Base: `main`, re-pulled immediately before packaging; the decision number was assigned by the three-source re-check (`INDEX.md` last row, `confirmed-decisions.md` tail, archive file list) run immediately before writing the entry. Slug during parallel work: `backtest-replay-timing-investigation`. **Docs only — no `backend/` or `frontend/` file is touched, no test is touched.**

## What changed

- `docs/decisions/confirmed-decisions.md` — new entry #155 appended: measured findings, two diagrams, options table (a)–(e), a recommendation flagged for Saqib's decision, two design forks, and what was not measured. Status in the entry: **awaiting Saqib's direction — nothing decided or changed.**
- `docs/decisions/INDEX.md` — one new row (#155), appended.
- `CHANGES.md` / `TESTING.md` — replaced (this file and its sibling).

## What the investigation found (details in the entry and `TESTING.md`)

- The 787 s full suite is dominated by four replay tests (719 s, 91.7%); every replayed candle after the first waits on `MarketStateEngine`'s real 1.0 s debounce floor — confirmed by a per-candle instrumented run, not just by reading the docstring.
- The floor also feeds `acceleration_score` (it divides by real elapsed time), so making replay faster would change persisted replay values — this is why the options table separates "faster tests" from "faster replay for users".
- Nothing was implemented, patched or renumbered. Existing decisions (including #153) are untouched.

## Parallel-work note

`test-wall-clock-audit` appends to the same four files. If it lands first, renumber this entry to the next free number, update the entry header, the `INDEX.md` row and the "decision #N" mentions in these two root files, and note the collision inline. See `TESTING.md` for the manual-merge notes.

## Part B — Wall-clock-dependent test audit (decision #156)

Base: `main`, re-pulled immediately before packaging (already includes decision #155, merged). Decision number assigned by the three-source re-check (`INDEX.md` last row #155, `confirmed-decisions.md` tail #155, archive file list unchanged through `122-133`) run immediately before writing the entry. Slug during parallel work: `test-wall-clock-audit`. **Docs only — no `backend/` or `frontend/` file is touched, no test is touched.**

## What changed

- `docs/decisions/confirmed-decisions.md` — new entry #156 appended after #155's entry (kept intact, not overwritten): full inventory methodology, per-test findings, the controlled-clock proof for the two genuine candidates, and what was not covered.
- `docs/decisions/INDEX.md` — one new row (#156), appended after #155's row.
- `CHANGES.md` / `TESTING.md` — combined as sections (Part A = #155's original content, unedited; Part B = this delivery), per #155's own manual-merge notes, rather than replaced outright.

## What the audit found (details in the entry and `TESTING.md`)

- **Zero proven wall-clock-dependent-test defects.** All 17 in-scope test files inventoried (36 real call sites across 16 files); every site traced to a safe code path except two tests in `test_intelligence_routes.py`, which were proven safe under `libfaketime` across 10 controlled-clock instants (weekday sessions, weekend, holiday, UTC/ET midnight, month rollover, both 2026 DST edges), 3 reps each — 30 runs, 60 individual passes, 0 failures.
- The production engine (`feature_engine/engine.py`) never reads the wall clock at all — confirmed by reading `_compute_one`/`_compute_aggregated` — so this defect class is structurally confined to test code.
- One real-looking lead (the `candles` table's monthly partitions, seeded only through August 2026 by migration `0001`, with today's real date — 2026-09-19/20 — outside that range) was chased and ruled out: `app/db/partitions.py` auto-creates each write's own month, keyed off that row's `candle_ts`, not wall-clock `now`.
- `test_backtest_runner_*.py` reviewed read-only, per the file boundary — no suspected defects to report to #155. Its own flagged overlap (`test_backtest_routes.py`'s `elapsed > 60`) confirmed to be unrelated real-duration timing (`time.monotonic()`), not this defect class.
- Full backend suite re-run at the real clock: 776 passed, 0 failed (786.25s) — matches the pre-audit baseline, no regression.
- Nothing was fixed, because nothing proven-broken was found — a valid "none found, here is the evidence" outcome per this task's own explicit allowance. Existing decisions (including #153, #155) are untouched.

## Collision resolution

Landed second against `backtest-replay-timing-investigation` (already merged as #155). Followed #155's own manual-merge notes exactly: `confirmed-decisions.md`/`INDEX.md` appended (no real conflict, append-only); `CHANGES.md`/`TESTING.md` combined as clearly labeled Part A / Part B sections in each file rather than one delivery overwriting the other. #155's content is reproduced here unedited; only this section and the matching `TESTING.md` Part B are new.
