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
- **Combined regression, final:** merged onto post-#171 `main`, migrated through `0012`, full suite: **922 passed, 0 failed** (one run of two surfaced the same intermittent `test_backtest_routes.py::test_two_separate_runs_isolate_level_interaction_state_and_events` failure #171 itself documented — reproduced in isolation 3× on unmodified code (pass, pass, fail), confirming it is this project's own long-documented #119 wall-clock-timing cluster, unrelated to this delivery; 810 (original baseline) + 37 (this delivery) + 75 (#171) = 922, exact).
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

Full suite: `pytest -q` → **885 collected**. First run: 885 passed, 0 failed. A second full run surfaced one intermittent failure, `test_backtest_routes.py::test_two_separate_runs_isolate_level_interaction_state_and_events` (a wall-clock-time-sensitive assertion, `datetime(...,14:30,...)` vs `datetime(...,16:02,...)` — matches this project's own long-documented #119 intermittent cluster, e.g. decisions #128/#129/#131's own notes on the same class of test). Confirmed pre-existing, not caused by this delivery, per this project's own testing philosophy ("pre-existing flakiness must be confirmed pre-existing... before attributing any test failure to new work"): (1) the same test passes in isolation every time; (2) the full suite run against a **freshly re-pulled, completely untouched `main`** (zero files from this delivery present) reproduces the exact same failure with the exact same values, 809 passed / 1 failed — 809 + this delivery's 75 = 884, matching this delivery's own second-run count of 884 passed / 1 failed exactly. Zero regressions attributable to this delivery either way.

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
