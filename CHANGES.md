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
