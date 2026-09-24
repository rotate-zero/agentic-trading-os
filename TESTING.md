# TESTING — decision #175: authorizer/order persistence

## Scope and environment

2026-09-24: resumed on `main` at `898cde95f0e3e9291ba5e531adad780961a11007`, preserving the existing #174 adapter and unfinished authorizer/order changes. Fetched GitHub `main` immediately before assigning #175: same HEAD; remote index/log through #173, local index/log through pending #174, archives through `134-160`. Existing decision bodies were not rewritten.

Python 3.14 in `backend/.venv`; PostgreSQL **18.6**. Started the existing isolated validation cluster on port 55436, created the new database `authorization_resume_test`, and migrated it from empty through `0014`. No application database was used. The sandbox blocked the initial Alembic connection; reran with approved local database access. These tests delete their fixture records and reset accounting cursors, so use only an isolated database.

## Reproducible command

From `backend/`, with the isolated PostgreSQL server running:

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55436 POSTGRES_DB=authorization_resume_test \
  .venv/bin/alembic upgrade head

env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55436 POSTGRES_DB=authorization_resume_test \
  .venv/bin/pytest \
  tests/test_authorization_ledger_postgres.py tests/test_position_ledger_postgres.py \
  tests/test_portfolio_accounting.py tests/test_portfolio_worker.py \
  tests/test_portfolio_state.py tests/test_reconciliation.py \
  tests/test_execution_event_schemas.py tests/test_execution_engine.py \
  tests/test_governor_engine.py tests/test_governor_rules.py \
  tests/test_execution_ledger.py tests/test_simulated_venue.py \
  tests/test_event_bus.py tests/test_market_clock.py \
  -q --tb=short --disable-warnings
```

**Result: 254 passed, no skips, 7.95s** (9,010 dependency deprecation warnings): 58 authorizer/order integration cases plus 196 focused adapter/accounting/execution regressions. The initial resumed suite passed 250 cases; four additional failure-path cases brought the final total to 254. No test assertions were weakened.

Also passed `git diff --check` and Python AST parsing of all 11 changed/new Python files. Byte comparisons confirm committed decision-log/index content is unchanged; appended numbers are ordered and index/log agree through #175. Final repository inspection found the same intended file set and unchanged local/remote HEAD. The isolated validation server was stopped after testing.

## Verified behavior

- Decision and reservation commit atomically; rollback leaves neither. Lost commit acknowledgement leaves one recoverable reservation, and a retry does not duplicate it. Concurrent identical approvals converge; changed terms fail.
- Missing/unknown/paper/live/backtest requested modes remain rejection audit facts, with no reservation or invented venue. Approved database rows still require complete, valid mode/venue labels. Actual authorizer event handling sees already durable approval/rejection records.
- Portfolio lookup/restart works before order insertion and preserves exact reference price. The order handoff counts exposure once; applied partial fills reduce the remainder; terminal order reads release it.
- Order insert rejects changed identity, symbol, direction, quantity, effect, type, mode, venue, and initial status. Concurrent duplicates return one inserted result. Actual Execution sees a committed order before its venue call and never resubmits a duplicate.
- Injected failure before order commit rolls back the order; lost acknowledgement after commit retains it. Both paths make no venue call or event publication and preserve one reservation on restart. Subsequent insertion distinguishes missing versus already committed orders.
- A venue claiming simulated support but using a different ID receives no order. Durable rejection retains the authorized venue. Failed rejection commit emits nothing and retains approved exposure. Stale acknowledgement/status writes cannot regress partial, terminal, or unknown states.
- Fresh upgrade through `0014`; combined `0014 -> 0012 -> head` round trip preserves legacy source rows. Downgrade refuses to discard reservations, approved decision records, rejection-only audit records, or applied fill receipts. Existing PositionLedger tests now clean reservation fixtures and expect migration head `0014`.

## Limits

No full-backend-suite, PostgreSQL 16, load, server-crash, or power-loss claim. Commit acknowledgement loss uses SQLAlchemy hooks; persistence is real PostgreSQL. Engine composition uses the real EventBus/adapters and controlled venue/market/portfolio inputs, not a fully wired application.

Startup, governor Portfolio-State snapshot adaptation/cache synchronization, venue-fill persistence/publication, exits, outcomes, and recovery orchestration remain out of scope. Persisted pre-order approvals can survive lost publication; committed orders can survive lost acknowledgements without submission. Future recovery must reconcile those facts rather than blindly retry venue calls. Table locks serialize writes but do not make separate authorizers' risk decisions atomic. Legacy approvals without orders/reservations and legacy applied checkpoints without receipts still require explicitly reviewed recovery. Migration `0014` was applied only to the isolated test database; no commit or push is included.

<!-- Previous delivery evidence retained below. -->

# TESTING — decision #174: PostgreSQL PositionLedgerPort adapter

## Baseline and environment

2026-09-23: clean `main`, HEAD and freshly fetched GitHub main both `898cde95f0e3e9291ba5e531adad780961a11007`. Rechecked main before assigning #174: index/log end at #173, archives through `134-160`. Existing decision bodies preserved; no changes from another session were present.

Used Python 3.14, `backend/.venv`, and PostgreSQL **18.6**. Started the existing isolated validation cluster on port 55436 and created a **new database `position_ledger_adapter_test`**, migrated from empty through `0013`. No application database was used. PostgreSQL socket access required sandbox escalation; an accidental sandbox-only suite attempt produced connection errors and was stopped, then rerun with the required access. PostgreSQL 16 and a full backend-suite run are not claimed.

## Reproducible focused command

Run against an isolated database; these database tests create/delete their fixtures and reset accounting cursors. From `backend/`:

```bash
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55436 POSTGRES_DB=position_ledger_adapter_test \
  .venv/bin/alembic upgrade head

env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55436 POSTGRES_DB=position_ledger_adapter_test \
  .venv/bin/pytest \
  tests/test_position_ledger_postgres.py \
  tests/test_portfolio_accounting.py tests/test_portfolio_worker.py \
  tests/test_portfolio_state.py tests/test_reconciliation.py \
  tests/test_execution_event_schemas.py tests/test_execution_engine.py \
  tests/test_governor_engine.py tests/test_governor_rules.py \
  tests/test_execution_ledger.py tests/test_simulated_venue.py \
  tests/test_event_bus.py tests/test_market_clock.py \
  -q --tb=short --disable-warnings
```

**Result: 196 passed, no skips, 6.95s** (7,759 dependency deprecation warnings). This comprises **35 new real-PostgreSQL adapter tests** plus the previous 161 focused regression cases. The first 29-case adapter run also passed. An intermediate combined run had one existing log-capture assertion failure: in-process Alembic tests used fileConfig and disabled existing loggers. Switching those tests to programmatic Alembic Config without global logging configuration resolved the failure; production logging and the existing assertion were not changed.

Also passed Python compilation and `git diff --check`; byte comparisons confirm all pre-existing decision-log and index content is unchanged. Final repository inspection found only the ten intended task files. The isolated validation server was stopped after testing.

## New coverage

- **Committed-prefix safety:** two actual database transactions allocate fills in sequence order and commit the higher one first. A concurrent adapter read is observed waiting in `pg_locks`, not merely assumed blocked by a timing sleep. Committing, rolling back, or disconnecting the lower writer yields exactly the safe prefix. Other-mode sequence gaps are preserved.
- **Atomicity and concurrency:** simultaneous consumers return one applied result and one durable duplicate; stale cursor, skipped source fill, changed fill facts, incorrect resulting position, and incorrect attribution fail without partial accounting writes. A before-commit exception rolls back position/receipt/cursor while retaining independently committed fills. An after-commit acknowledgement exception leaves recoverable accounting and an idempotent retry.
- **Restoration and arithmetic:** all four execution modes; exact weighted Decimal averages (including a six-place rounding tie); multi-month long/short reductions; per-day profit/loss/entry and exit fees; unknown commission and rebates; ET midnight; explicit reopening IDs even with repeated timestamps; historical stop inputs survive thesis edits.
- **Orders and incomplete state:** approved orders, partial remaining quantities, persisted cancellation/terminal reads, absent order lookup, and unchanged execution-owned statuses. Missing approved reservation quantities, anomalous fills, overfills, mode/symbol inconsistency, changed source facts, damaged position projections, missing receipts/cursors, and legacy applied state fail closed.
- **Migration:** fresh upgrade; 0013 → 0012 → 0013 with existing source fills; legacy explicitly supplied IDs survive and the next generated ID is greater; ordinary explicit-ID inserts rejected; unsafe sequence-cache policy detected; downgrade with receipts refused without deleting history or changing revision.
- **Actual worker composition:** real PostgreSQL adapter + EventBus + PortfolioState; a closure callback sees an already committed final cursor/closed state, and a fresh worker restores without re-publishing the closure.

## Limits and follow-ups

The adapter serializes all modes through table locks and replays full receipt history on each checkpoint; no load/throughput claim. Administrative identity override/reseeding and integrity-constraint disabling are outside the supported ingestion contract. Connection close covers rollback/released locks, but no operating-system process kill, server crash, replica/failover, or power-loss test was performed. Commit acknowledgement loss is injected through SQLAlchemy's after-commit hook.

Existing unapplied fills and empty ledgers work. **Already-applied legacy checkpoints have no receipt history and require a separately reviewed import/recovery task**; the adapter reports that condition, and the unchanged Session path remains available. Do not use both paths as writers for the same mode. The current trades schema cannot restore the quantity of an approved reservation before its order exists; the adapter raises until that information is persisted. Authorizer/order persistence integration must resolve that gap.

Startup, execution fill ingestion/publication, full status notifications, governor/World View integration, exits, OutcomeRecorder, outbox delivery, and numeric R remain outside this delivery. The migration was applied only to the isolated test database. No archive or commit is produced by this task.

<!-- Previous delivery evidence retained below. -->

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
