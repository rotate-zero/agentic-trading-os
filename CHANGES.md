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
