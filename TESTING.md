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
