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
