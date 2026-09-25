# Execution Engine & Portfolio State — Design (approved in principle; amended by decision #170)
**Owner:** Saqib
**Status:** **Approved in principle** by Saqib (2026-09-22) — the simulated-venue automatic path (Slice A) with the corrections recorded in decision #170; **this revised text is the implementation specification for that slice.** That approval revision built no application code. **As-built update:** decisions #171–#174, plus this task's own delivery (temp id `entry-lifecycle-wiring`, real number assigned at packaging — see the decision entry), now implement parts of this design; §§6.2–6.5 describe the current persistence and Portfolio State slices. **Correction:** an earlier edit of this banner and §§6.2/6.3 below cited "#175" as already built — no such decision exists in `confirmed-decisions.md`/`INDEX.md` (confirmed by the three-source re-check at the start of this task); the code those sections describe (`PostgresOrderLedger`, `PostgresTradeLedger`) was real and already on `main`, but undocumented — first canonically logged by entry-lifecycle-wiring's own decision entry. The original inventory in §§1–2 remains historical, not a current implementation inventory. Baseline: `main` through decision #169. Originally recorded by decision #168, which stays exactly as merged (decision content is immutable — `AGENTS.md` §6); decision #170 amends it. **Fork status (§7):** EX-1, EX-2, EX-3, EX-4, EX-6, EX-7 are **RESOLVED** by #170, and EX-10 is settled by its added ledger requirement; the remaining forks stay open with their recommendations — **EX-5 and EX-12 still need Saqib's confirmation before a build task** (§7.1). Fork labels `EX-n` are provisional and are not D-numbers.
**Companion documents:** [`system-design.md`](./system-design.md) §4.4 (Event Bus), §4.6 (Portfolio State Engine), §4.9 (Execution Engine), §4.13 (Database), §10 (event contracts) — the prose this doc turns into a design; [`trading-intelligence-architecture.md`](./trading-intelligence-architecture.md) §6, §10–§13, §18 (Portfolio State, Decision Engine, Trade Planning, Governor, Position Monitor, Manual Trading & Execution Modes) — the reasoning behind each module; [`strategy-engine-design.md`](./strategy-engine-design.md) §5 (`StrategyOutcome`), §6 (Decision Engine vs Governor), §9 (the full feedback loop); [`strategy-engine-open-decisions.md`](./strategy-engine-open-decisions.md) (D1, D4, D17 — the three rows this design touches); [`backtest-runner-design.md`](./backtest-runner-design.md) §7 (the only existing writer of `StrategyOutcome`, and the precedent for decision #128's option (a)); [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (#6, #9, #89, #120, #128, #158); [`../decisions/future-ideas.md`](../decisions/future-ideas.md) (#14, #16, #21, #27).

**Why this doc exists.** Everything downstream of the Strategy Engine — Decision Engine, Trade Planning, Governor, Portfolio State, Execution Engine, Position Monitor — exists only as prose (`system-design.md` §4.6/§4.9, `trading-intelligence-architecture.md` §6/§10–§13/§18). Performance Intelligence is built and tested, but its live half is empty: `record_strategy_outcome()` has no live caller (D17's live half, decision #158), and Decision Engine's arbitration (D4) is explicitly waiting for real outcome data. The Execution Engine is the missing writer, so it is the module whose design most gates the rest. The prose was written before the surrounding code existed; several of its premises no longer match the as-built repository (§2). This project's pattern is *design → forks resolved by Saqib → build*; this is the design pass, and it stops at the forks.

**How to read this doc.**
- Every statement about the current code cites `path:symbol`, verified against `main` through decision #167 (the claim-to-source table with a machine check is in the delivery's `TESTING.md`). Statements about the *proposal* are labelled as such.
- Status legend used everywhere: **built** (exists and is exercised), **partial** (exists in part — the part is named), **not built** (no module, table, or code — prose or enum entries only).
- Architecture diagrams use the repository's ASCII code-fence style (§6.1, §6.3, §6.5, §6.7 — data flow between modules, plus the internal flow of each proposed module).
- This doc proposes; the forks in §7 each carry options, evidence, consequences, and a recommendation, all marked **OPEN**. Where the recommendation is "proceed unless Saqib objects", it says so.

---

## 0. Summary

1. **The goal of the first build is narrow and measurable:** one real, non-backtest `StrategyOutcome` row, produced end to end, closing D17's live half — with every population boundary (`execution_mode`: backtest / simulated / paper / live) kept honest.
2. **The code disagrees with the prose in ways that change the design** (§2): the execution-side events are declared but have no payload models for four of eight types and no publisher or subscriber for any of them; the `BrokerAdapter` contract cannot report a fill; `IBKRAdapter` connects `readonly=True` and its order methods are stubs; the Event Bus's critical lane awaits every handler serially and persists nothing; `Opportunity` has no identity; `StrategyOutcome` cannot represent a manual trade with no strategy behind it; and `is_backtest` is a two-valued flag with no way to keep *simulated-money* outcomes apart from *real-money* ones.
3. **"Only the Governor can place orders" needs one reconciliation, not a new rule** (§3, I2): the Governor is the only *authorizer*; the Execution Engine is the only *placer*; manual mode adds a human confirmation *after* the Governor, it does not replace it. The task brief's shorthand "human as Governor" for manual-first was imprecise — §18.5 keeps the Governor in the path even for manual, sized requests.
4. **Approved first slice (§5, Slice A; decision #170):** a simulated venue, the auto path, and one deliberately thin, clearly-labelled authorizer stub in place of the unbuilt Decision/Planning/Governor stages — **technically restricted to simulated execution and failing closed for `paper`/`live`** (§6.2) — with fixed sizing, protective exits monitored in-process, and an `OutcomeRecorder` that writes the outcome. It inverts the roadmap's stage order (Execution before its Phase 5 predecessors); Saqib approved that inversion (EX-1).
5. **Six forks are resolved and six requirements are added by #170 (§7, §3):** `execution_mode` and `execution_venue` are separate columns (EX-2); the venue seam is a new narrow `OrderVenue` port and an `execution` registry role, and `BrokerAdapter` is not enlarged (EX-3); Portfolio State owns accounting and `PositionClosed` is published only after its commit (EX-6); the snapshot requirement is a pre-trade gate and a reported fill is never discarded (EX-7); and every order has a stable client-order ID, fills and updates are deduplicated, restart recovery reconciles non-terminal orders with the venue, the database ledger is authoritative, persist-before-publish covers fills and closures, and the daily-loss gate counts unrealized loss and open risk (§3 I10–I15, §6.9). Initial limits: 1 concurrent position, $1,000 notional per trade, $100 daily loss cap — all configurable (§6.10).

---

## 1. Verified inventory of the downstream pipeline

All rows verified against `main` through decision #167 (`grep`/`view`, not recalled from docs).

### 1.1 Events and the bus

| Item | Status | Evidence |
|---|---|---|
| All eight execution-side `EventType` names (`OpportunitySelected`, `TradePlanned`, `GovernorDecision`, `OrderApproved`, `PlanRejected`, `OrderFilled`, `PositionAdjusted`, `PositionClosed`) | built (enum only) | `backend/app/schemas/events/envelope.py:EventType` |
| Payload models for `GovernorDecision`, `OrderApproved`, `PlanRejected`, `OrderFilled` | built | `backend/app/schemas/events/execution.py` |
| Payload models for `OpportunitySelected`, `TradePlanned`, `PositionAdjusted`, `PositionClosed` | **not built** — the enum entry exists, no Pydantic class exists (`system-design.md` §10.3 lists their fields as prose only) | `backend/app/schemas/events/` (no class); `backend/app/schemas/performance.py` mentions `TradePlanned` only inside a field-description string |
| Any publisher or subscriber of the eight, in application code | **none**, with two exceptions that are not real producers/consumers: `api/routes/dev.py` publishes one `GovernorDecision` for a critical-lane smoke test, and `api/websocket/channels.py` routes five of the eight to WebSocket channels | `backend/app/api/routes/dev.py`; `backend/app/api/websocket/channels.py:EVENT_TO_CHANNEL` |
| WebSocket routing | partial — `OrderApproved`, `PlanRejected`, `OrderFilled`, `GovernorDecision` → `orders.status`; `OpportunitySelected` → `opportunity.selected`; `TradePlanned`, `PositionAdjusted`, `PositionClosed` have no channel; no frontend code reads `orders.status` | `channels.py`; `grep -rn "orders.status" frontend/src` → empty |
| Critical dispatch lane membership | built — exactly `OrderFilled`, `PlanRejected`, `GovernorDecision`, `OrderApproved`; **`PositionClosed`, `PositionAdjusted`, `TradePlanned`, `OpportunitySelected` ride the normal lane** | `envelope.py:CRITICAL_EVENT_TYPES` |
| Prose-only events named by `trading-intelligence-architecture.md` §18: `ExecutionModeChanged`, `PlanAwaitingConfirmation`, `ManualConfirmOrder`, `ManualDiscardOrder`; plus `TradeRequest`, `TradePlan`, `ExecutionMode`, `InputCommand` | **not built** — no `EventType`, no model, no code anywhere in `backend/` or `frontend/src/` | `grep -rn "TradeRequest\|ExecutionMode\|ManualConfirm\|PlanAwaiting" backend frontend/src` → empty |
| Bus delivery semantics | built — two in-memory queues, one consumer task per lane; the consumer **awaits `asyncio.gather` over every handler of an event before taking the next event on that lane**; a handler exception is logged and swallowed; nothing is persisted | `backend/app/event_bus/bus.py:EventBus._consume`, `_safe_call` |

### 1.2 Broker layer

| Item | Status | Evidence |
|---|---|---|
| `BrokerAdapter` ABC with `place_order`, `cancel_order`, `get_positions` | built (contract only) | `backend/app/broker_adapters/base.py:BrokerAdapter` |
| Order contracts: `OrderRequest{symbol, side, qty, order_type, limit_price}`, `OrderAck{order_id, status: submitted\|rejected, reason}`, `Position{symbol, qty, avg_cost}` | built — **no fill/status callback, no order-status query, no client order id or time-in-force, no stop/target/bracket fields, no position side or P&L** | `base.py:OrderRequest`, `OrderAck`, `Position` |
| Registry roles | built — `streaming` and `historical` only; **no execution role** | `backend/app/services/broker_registry.py` |
| `IBKRAdapter.connect()` | built — connects with `readonly=True` (its own comment says a bug cannot submit an order) | `backend/app/broker_adapters/ibkr_adapter.py:IBKRAdapter.connect` |
| `IBKRAdapter.place_order()` / `cancel_order()` | **stubs** — both raise `NotImplementedError` ("only the Governor should be able to trigger a real order …") | `ibkr_adapter.py:place_order`, `cancel_order` |
| `IBKRAdapter.get_positions()` | built, **zero callers** anywhere in `backend/app`, `backend/tests`, or `frontend/src` | `ibkr_adapter.py:get_positions`; repo-wide grep |
| Live IBKR behavior | **unverified** — no real Gateway/TWS session has been reached; `future-ideas.md` #27 says to treat the adapter path as unverified live in any Phase 5/6 (execution) planning | `docs/decisions/future-ideas.md` #27 |
| Execution/dry-run setting | **not built** — `core/config.py` has IBKR host/port/client-id only | `backend/app/core/config.py` |

### 1.3 Persistence

| Item | Status | Evidence |
|---|---|---|
| `trades`, `orders`, `positions`, `ai_decisions`, `feature_snapshots`, `market_events` (`system-design.md` §4.13) | **not built** — no ORM class, no migration | `backend/app/models/*` (`__tablename__` list); `models/market_data.py` docstring says the same |
| `strategy_outcomes`, `backtests` | built — migration `0008`; `strategy_outcomes` columns for strategy identity, thesis (`structural_*`, `final_*`), `confidence_at_signal`, `evidence`, and the four snapshot dicts are all `NOT NULL`; `is_backtest` is a `Boolean`; **no DB check pairs `is_backtest` with `backtest_run_id` on this table** | `backend/app/models/trading_intelligence.py:StrategyOutcomeRecord`; `backend/alembic/versions/0008_strategy_outcomes_and_backtests.py` |
| Migration head | `0011` | `backend/alembic/versions/` |

### 1.4 Performance Intelligence (the consumer this design must feed)

| Item | Status | Evidence |
|---|---|---|
| `StrategyOutcome` contract (`origin: auto\|manual`, `is_backtest`, `direction: BUY\|SELL`, …) | built | `backend/app/schemas/performance.py:StrategyOutcome` |
| `record_strategy_outcome()` — synchronous (own `SessionLocal()`), raises on `entry_qty != exit_qty`, docstring forbids wiring it into a live pipeline "without a real Execution Engine/Position Monitor" | built; **one application user** (`BacktestRunner.run()`, which passes it to `asyncio.to_thread`), plus its own tests | `backend/app/trading_intelligence/performance.py:record_strategy_outcome`; `backend/app/backtest_runner/runner.py` |
| `capture_strategy_outcome_snapshots(symbol)` — returns `market_state`/`context`, each `None` for a cold-start symbol; the market-state dict carries the last-computed `candle_ts`, the context dict carries no timestamp | built; one real caller (Backtest Runner) | `backend/app/trading_intelligence/state_snapshot.py`; `MarketStateEngine.get_snapshot` |
| Read queries (win rate by hour, expectancy by session type) filter on `is_backtest` as a strict selector — live and backtest populations are never blended | built | `backend/app/trading_intelligence/performance_queries.py:_common_filters` |
| World View `portfolio` slot | built as `None` ("source unavailable", never "empty account") | `backend/app/world_view/composite.py` |

### 1.5 Pipeline stages between Strategy and Execution

| Stage | Status | Evidence |
|---|---|---|
| Strategy Scheduler → `OpportunityCreated` | built | `backend/app/strategy_engine/scheduler.py` |
| Opportunity Cache — latest `Opportunity` per `(symbol, strategy)`, **overwrites**; `Opportunity` itself has no id and no `symbol` (the symbol lives on the envelope) and no entry price | built | `backend/app/trading_intelligence/opportunity_cache.py`; `backend/app/strategy_engine/base_strategy.py:Opportunity` |
| Agreement/conflict view over cached opportunities | built (non-scoring, decision #121) | `backend/app/trading_intelligence/opportunity_view.py` |
| Opportunity Engine (ranking; D4) | **not built** | no module |
| Decision Engine (arbitration; D1) | **not built** | no module |
| Trade Planning Engine (`TradeRequest → TradePlan`) | **not built** | no module |
| Governor | **partial** — the widened `GovernorDecision` schema only; no rule engine | `schemas/events/execution.py:GovernorDecision` |
| Portfolio State Engine | **not built** | no module; `world_view/composite.py` returns `portfolio=None` |
| Execution Engine | **not built** | no module; nothing calls `place_order` |
| Position Monitor | **not built** | no module |
| Frontend: Positions / Trade Management / Approval Queue widgets | **not built** | `grep` for `positions`, `ApprovalQueue`, `TradeManagement` in `frontend/src` → empty |
| Backtest `fill_simulator` (entry at next candle open, stop-wins-on-tie, `eod_flatten` at session close, `qty = 1`, no slippage/commission) | built — a **look-ahead** pure-function model over a fully precomputed candle list (§2 F9) | `backend/app/backtest_runner/fill_simulator.py:simulate_entry`, `simulate_exit` |

---

## 2. What the code says that the prose doesn't

Each finding below changes a design choice later in this document.

**F1 — The downstream event vocabulary is declared, not built.** Four of the eight execution-side event types have no payload model; none of the eight has a real publisher or subscriber (§1.1). `system-design.md` §10.3's table is, for those four, a mirror of nothing — and §10.2 says the Pydantic model is the contract. Any build must start by writing the missing models and deciding their lanes.

**F2 — The two order events cannot carry a trade's story.** `OrderApproved{order_id, symbol, side, qty, order_type, limit_price}` and `OrderFilled{order_id, side, qty, fill_price, fill_ts}` carry no `fill_id`, no cumulative/remaining quantity, no venue, no link to an opportunity, plan, stop, or target, and no indication of whether the order opens or closes a position (and the two disagree on `symbol`: `OrderApproved` has it in the payload, `OrderFilled` does not — the convention elsewhere is to keep it on the envelope only). `StrategyOutcome` needs all of that at close (§4). Additive optional fields do not bump an event's `version` (`system-design.md` §10.2), so the fix is cheap — but it must be designed, not discovered.

**F3 — The `BrokerAdapter` contract cannot report a fill.** `place_order()` returns an `OrderAck` of `submitted` or `rejected`; nothing in the ABC delivers fills, partial fills, cancels, or status changes afterwards, and there is no way to query an order. Yet `system-design.md` §4.9 says the Execution Engine "tracks order lifecycle (`pending → filled/partial/rejected`)" and emits `OrderFilled`. The lifecycle has no input. `OrderRequest` also has no client order id (idempotency), no time-in-force, and no bracket/stop fields, and `Position` has no side or P&L. The registry has no execution role either, so there is no defined way for the Execution Engine to obtain "the" broker.

**F4 — `IBKRAdapter` is a read-only, unverified data path.** It connects `readonly=True` on purpose, its order methods raise, `get_positions()` has no caller, and `future-ideas.md` #27 tells planners to treat it as unverified live. Turning it into an order path is not "implement two methods": it changes the connection mode, needs a paper-only guard, a client-id policy, and fill callbacks (F3), and none of it can be validated from this environment or, today, from Saqib's (the paper Gateway is not running). So **the first venue cannot be IBKR paper**; see EX-1 and §8.

**F5 — The Governor invariant has three phrasings that need one reading.** `base.py:BrokerAdapter`'s docstring says "only the Governor should ever be able to trigger a real order"; `system-design.md` §4.9 says the Execution Engine is the "only module allowed to place orders"; `trading-intelligence-architecture.md` §18.5 puts a human `ManualConfirmOrder` between an approved plan and `place_order` in manual mode — after the Governor, not instead of it (§18's own manual pipeline is `Input Device → Input Layer → TradeRequest → Trade Planning Engine → TradePlan → Governor → Execution Engine → Broker`). These are consistent only if read as: *the Governor is the only authorizer; the Execution Engine is the only placer; manual mode adds a confirmation gate downstream of authorization.* §3 states that reading as I2. Consequence: a "manual-first" slice does **not** avoid needing an authorizer.

**F6 — The bus's critical lane is a single serial consumer, and it forgets.** `EventBus._consume` awaits all handlers of one event before dequeuing the next event on that lane, so a handler that awaits a network `place_order()` stalls every other critical event (`OrderFilled`, `GovernorDecision`, …) behind it. A handler exception is logged and dropped. Queues are in-memory: a process crash between publish and handle loses the event, and `market_events` (the durable event log) does not exist. The repository's existing answer to slow work is "subscribe, `put_nowait` onto the engine's own queue, drain in a worker" (`FeatureEngine._on_candle_closed`, `LevelInteractionEngine._on_features_updated`, `MarketStateEngine`). An execution ledger must additionally be durable *independently of the bus*.

**What the critical lane provides — and does not (verified against `bus.py`; documented here on purpose).** It provides (a) FIFO *ordering* among critical events, (b) *isolation* from normal-lane backlog, because it has its own queue and consumer task, and (c) *isolation of handler failures from each other* — `_safe_call` catches, logs, and swallows each handler's exception so one subscriber cannot break another. It does **not** provide persistence (the queue is an in-memory `asyncio.Queue`), a delivery guarantee, or crash recovery (an event published but not yet handled is lost with the process), and it does **not** propagate a handler's failure back to the publisher (`publish()` only enqueues; the publisher never learns whether any handler ran, or failed). **Anything that must survive a failure therefore has to be built into the modules, not assumed from the lane:** commit to the database first and publish afterwards (I8), make every consumer idempotent (I11), and rebuild state from the ledger at startup rather than from the bus (§6.9).

**F7 — No trade-side tables exist.** `trades`, `orders`, `positions` are prose (`system-design.md` §4.13; `trading-intelligence-architecture.md` §18.8 even says a filled manual plan is recorded in "the existing `trades` table"). Migration head is `0011`. Every table in §6.8 is new.

**F8 — `Opportunity` has no identity, and the cache forgets.** `StrategyOutcome.opportunity_id` is required, but `Opportunity` carries no id and `OpportunityCache` overwrites the previous `Opportunity` for the same `(symbol, strategy)`. The Backtest Runner minted the first id (`uuid4()` at the moment a signal is accepted; its docstring calls itself "the first thing to mint one"). A live path must choose where to mint it and must persist the thesis (`structural_*`, `confidence`, `evidence`) at that moment, because the cache will not still hold it at close.

**F9 — `fill_simulator` cannot be reused as a live venue as-is.** `simulate_exit()` walks a fully precomputed candle list forward from the entry and raises `InsufficientReplayDataError` until the entry day's session close is *in* the list. That is exactly right for a replay and unusable incrementally. What *is* reusable are its conventions (entry at the next open, stop wins a same-bar tie, `eod_flatten` at the real regular-session close, zero slippage and no commission modelled, `qty = 1`) and its two pure helpers `compute_realized_r` / `compute_realized_pnl`. A live simulated venue needs an incremental fill model (EX-8).

**F10 — `StrategyOutcome` has two representational gaps.**
(a) *No strategy, no row.* `strategy_name`, `strategy_version`, `opportunity_id`, `structural_invalidation`, `structural_target`, `final_stop`, `final_target`, `confidence_at_signal`, `evidence` are all required and `NOT NULL`. A manual trade with no corroborating strategy has none of them — and `TradeRequest` (§18.2) has no stop field while `TradePlan.stop` is required, so even the plan cannot be completed without one. `origin: "manual"` exists in the schema, but only a corroborated manual trade fits it.
(b) *No way to say "simulated money".* `is_backtest` is boolean and every performance query treats it as a hard population boundary. Outcomes from a simulated live venue would have to be stored either as `is_backtest = False` (they would blend with real-money rows the day a real venue exists, in every query, silently) or as `is_backtest = True` (which claims a `backtests` run that does not exist). Neither is honest. **Resolved by decision #170 (EX-2):** population is identified by two new, separate fields — `execution_mode` (`backtest | simulated | paper | live`, the capital mode) and `execution_venue` (`simulated | ibkr | …`, where fills come from) — and `is_backtest` is kept only as a temporary compatibility field (§6.8).

**F11 — D17's live half is a different problem from its backtest half.** Decision #128 resolved the backtest path by turning a `None` snapshot into a `DiscardedSignal` (no row). For a *live* fill the money is already on the line: discarding the outcome would drop a real (or simulated-real) win or loss from the evidence table, a survivorship bias in exactly the table meant to judge strategies. Also, `capture_strategy_outcome_snapshots()` reads *current* engine state, not state as of `fill_ts`; a fill handled late reads a later state. The market-state half carries `candle_ts` so staleness is checkable; the context half does not. **Resolved by decision #170 (EX-7):** a pre-trade gate, plus never discarding a reported fill (§6.7).

**F12 — Position-effect and direction vocabulary is not aligned.** `Opportunity`, `StrategyOutcome`, `OrderRequest`, `OrderApproved` use `BUY`/`SELL`; `TradeRequest`/`TradePlan` use `long`/`short`. A `SELL` is ambiguous between closing a long and opening a short — the same ambiguity `trading-intelligence-architecture.md` §18.6 removed from the *hotkey* vocabulary but which reappears one level down at the order. Something must carry "opens" vs "closes" (EX-14).

**F13 — Two owners for "open positions", and a lane mismatch.** `system-design.md` §4.8's table has Position Monitor emit `PositionAdjusted`/`PositionClosed` and persist `positions`, while §4.6 has Portfolio State track open positions from `OrderFilled`/`PositionClosed` — two modules owning one fact, against principle 3 ("single source of truth for shared state"). Separately, Portfolio State feeds the Governor's exposure and daily-loss checks, but `PositionClosed` rides the *normal* lane, where a burst of ticks can delay it; a Governor deciding on stale exposure is precisely what the two-lane design exists to prevent. (EX-6.)

---

## 3. Invariants this design must honor

Restated from existing decisions and code, with I2 as the one reconciliation (F5). **I4, I6, I7, I8 are amended and I10–I15 are added by decision #170.**

| # | Invariant | Source |
|---|---|---|
| I1 | The Execution Engine is the only module that calls a venue's `place_order()`. | `system-design.md` §4.9 |
| I2 | *(reconciled)* No **risk-increasing** order reaches a venue without a Governor-class authorization decision on record; the Execution Engine places, the Governor authorizes, and manual mode adds a human confirmation *after* authorization. Whether protective exits also need one is EX-5. | `base.py:BrokerAdapter` docstring; §4.9; §18.5 |
| I3 | Honest absence over fabricated state: no invented fills, snapshots, commissions, or account values; `None`/absent means "not known". | `strategy-engine-design.md` §11; `state_snapshot.py`; `world_view/composite.py` |
| I4 | Populations are identified by `execution_mode` (`backtest`, `simulated`, `paper`, `live`) and are never blended in a query; `execution_venue` says where fills came from and never substitutes for the mode. `is_backtest` survives temporarily as a derived compatibility field. | `performance_queries.py:_common_filters`; decisions #128, #140; #170 (EX-2) |
| I5 | Compute once, own once: exactly one module owns each piece of shared state (positions, in-flight orders, daily P&L, buying power). | `system-design.md` §2 principles 3, 8 |
| I6 | **Fail closed.** In this slice no configuration can make a `paper` or `live` order possible: the authorizer stub refuses to start and refuses every authorization unless `execution_mode == simulated`, the Execution Engine refuses any order whose mode the routed venue does not declare, and no non-simulated `OrderVenue` exists. An unknown, missing, or unsupported mode rejects; it never falls back to `simulated` silently. | `system-design.md` §4.9 (dry-run default), #170 (EX-1) |
| I7 | A critical-lane handler must never wait on the network. The lane gives ordering and handler-failure *isolation* — not persistence, delivery guarantees, crash recovery, or failure propagation to the publisher (F6). | `bus.py:EventBus._consume`, `_safe_call`, `publish` |
| I8 | **Persist before publish.** An order fill is committed to the ledger before `OrderFilled` is published; a position closure is committed before `PositionClosed` is published; an authorization decision is committed before `TradePlanned`/`GovernorDecision`/`OrderApproved`. An event is a notification, never the record. | F6, F7; #170 (EX-6) |
| I9 | Tests run against real PostgreSQL 16, never SQLite or mocks; strategies and engines never read wall-clock where an event timestamp exists. | project testing baseline; `fill_simulator.py`, `runner.py` |
| I10 | **Every order has a stable client-order ID** (deterministic from the trade and leg, minted before the order is persisted, unique in the ledger, and carried to the venue as its idempotency key). | #170 |
| I11 | **Order and fill updates are deduplicated** by database constraint — `orders(client_order_id)` and `fills(venue_id, venue_fill_id)` — never by an in-memory set; a duplicate is a no-op that publishes nothing; order status only moves forward. | #170 |
| I12 | **The database ledger is authoritative.** In-memory Portfolio State is a cache that must be reconstructable from the ledger alone; on any disagreement the ledger wins. | #170 (EX-6) |
| I13 | **Restart recovery** scans every non-terminal ledger order and reconciles it with the venue *before* any new authorization is accepted; unresolved discrepancies halt new entries (fail closed). | #170 |
| I14 | **A venue-reported fill is never discarded.** It is persisted and processed even when something else is missing (a snapshot, a matching order, a plan); missing data is recorded as `NULL` plus a reason, and anomalies halt *new entries*, never the recording. | #170 (EX-7) |
| I15 | **The daily-loss gate counts realized loss plus current unrealized loss and open risk** (including the candidate trade's own stop-out loss), never realized P&L alone; an unknown mark or stop counts as unbounded and rejects. | #170 (EX-4) |

---

## 4. What one live `StrategyOutcome` needs — field-by-field source map

The first build succeeds when a row exists. This table is the minimum plumbing, derived from `schemas/performance.py:StrategyOutcome` and the Backtest Runner's `_build_strategy_outcome()` (the only existing constructor).

| Field(s) | Live source | Exists? |
|---|---|---|
| `outcome_id` | minted by the writer (`uuid4()`) | trivial |
| `opportunity_id` | minted where the signal is accepted (EX-9) and persisted with the thesis | **new** (F8) |
| `schema_version`, `origin`, `is_backtest`, `backtest_run_id` | constants for the auto path: `schema_version = 2` (bumped by decision #170, §6.8), `"auto"`, `is_backtest = False` (derived from the mode), `None` — **plus `execution_mode = "simulated"` and `execution_venue = "simulated"`** so simulated money is separable (EX-2, resolved) | **new columns** (F10b) |
| `strategy_name`, `strategy_version`, `direction`, `structural_invalidation`, `structural_target`, `confidence_at_signal`, `evidence`, `setup_detected_at`, `signal_confirmed_at` | the `OpportunityCreated` payload (`Opportunity`), snapshotted at acceptance | built, but must be **persisted at acceptance** (the cache overwrites) |
| `symbol` | envelope `symbol` | built |
| `trading_day` | `MarketClock.trading_day(entry fill ts)` | built |
| `decided_at` | the authorizer's decision timestamp (`None` in the backtest path today) | **new** (small) |
| `entry_filled_at`, `entry_price`, `entry_qty` | `OrderFilled` for the entry order(s) — volume-weighted if partial | **new** (F2, F3) |
| `exit_filled_at`, `exit_price`, `exit_qty`, `holding_seconds` | `OrderFilled` for the exit order(s) | **new** |
| `exit_reason` | carried on the exit order from whoever decided to exit (stop / target / eod_flatten / time / manual / reversal) and recorded on the ledger | **new** (plumbing) |
| `commission_total` | `None` unless the venue surfaces it (I3) | honest `None` |
| `slippage_entry` | `entry_price − TradePlanned.entry` — needs a `TradePlanned` with an entry price; `None` if the slice has no Planning stage | **new / `None`** |
| `realized_pnl`, `realized_r` | `compute_realized_pnl` / `compute_realized_r` (pure, in `fill_simulator.py`; reuse question is EX-8) | built (reuse TBD) |
| `final_stop`, `final_target` | Trade Planning's numbers; in v1 equal to `structural_*` exactly as the Backtest Runner does | built convention |
| `market_state_at_entry`, `context_at_entry` | `capture_strategy_outcome_snapshots()` at the entry fill (F11); pre-trade gate, and `NULL` + a reason in `snapshot_missing_reasons` if unexpectedly missing (EX-7, resolved) | built; policy resolved; nullable columns are new (§6.8) |
| `market_state_at_exit`, `context_at_exit` | same, at the exit fill | built; policy resolved; nullable columns are new (§6.8) |
| `feature_snapshot_id` | `None` — `feature_snapshots` does not exist | honest `None` |

Reading the table: everything except **opportunity identity, order/fill linkage, exit-reason plumbing, the `execution_mode`/`execution_venue` columns, and the stable client-order ID** already exists. That is the size of the gap the Execution/Portfolio slice has to close.

---

## 5. Slice analysis

"Smallest vertical slice" = the smallest set of new modules that turns one actionable `OpportunityCreated` into one `strategy_outcomes` row for a *non-backtest* trade (`execution_mode = simulated`). Three candidates were evaluated; **Slice A was approved in principle by decision #170** (the trade-offs below are the analysis behind that approval).

### Slice A — simulated venue, auto path, thin authorizer stub

```
OpportunityCreated → [authorizer stub] → OrderApproved → Execution Engine → SimulatedVenue
   → OrderFilled → Portfolio State → Position Monitor-lite (stop / target / EOD) → exit order
   → OrderFilled → position closed → OutcomeRecorder → record_strategy_outcome()
```

- **New modules:** authorizer stub (emits `TradePlanned` → `GovernorDecision` → `OrderApproved`/`PlanRejected`), `SimulatedVenue`, Execution Engine, Portfolio State Engine, Position Monitor-lite, `OutcomeRecorder`, order/position ledger tables, and the four missing payload models.
- **Deliberately not in the slice:** ranking (D4), arbitration (D1), Kelly sizing (needs outcome data — the chicken-and-egg this slice exists to break), correlation, scaling/trailing stops, manual mode and the Approval Queue, emergency actions, real broker connectivity.
- **Pro:** needs no external service; fully testable against real Postgres with deterministic fixtures; produces the D17-live row; every module it builds is one the eventual real path also needs; the venue is behind the `OrderVenue` port, so a real venue slots in later without touching Execution.
- **Con:** inverts the roadmap's stage order (Phase 6's Execution before Phase 5's Decision/Planning/Governor), so the stub authorizer must not calcify into "the Governor" (EX-4, EX-1). Outcomes come from *simulated* fills, so EX-2 (labelling) is a precondition, and fill quality is only as good as the fill model (EX-8) and the tick feed (the free-tier Finnhub trade feed is IEX-only, not the consolidated tape — decision #95).

### Slice B — manual-first (Approval Queue, human in the loop)

`LONG`/`SHORT` hotkey → `TradeRequest` → Trade Planning → Governor → **Approval Queue** → human confirm → Execution → venue.

- **Pro:** manual trading is a first-class mode in this architecture, and `trading-intelligence-architecture.md` §18 designs it in detail.
- **Con — three separate blockers:** (1) it still needs an authorizer, Trade Planning, and a venue (F5); (2) it needs the entire frontend Input Layer, `TradeTarget`, hotkey module, and Approval Queue UI, none of which exists; (3) a manual trade with no corroborating strategy **cannot be a `StrategyOutcome` at all** (F10a), so it does not close D17's live half unless the trade happens to be corroborated. `future-ideas.md` #14's trigger ("manual trade entry ships and real usage shows demand") is also not met — manual entry has not shipped. Slice B is a good *second* slice on top of A's Execution/Portfolio core, not a first one.

### Slice C — IBKR paper venue

- **Recorded as deferred, not designed around.** `future-ideas.md` #27 is blocked and deliberately set aside; F4 and §8 list what a real venue needs — and after decision #170 it would be a *separate* `IBKROrderVenue` behind the new `OrderVenue` port with its own connection, leaving `BrokerAdapter`'s read-only market-data connection untouched — and all of it would be unverified live. §8 lists these as prerequisites. Nothing here recommends unblocking #27.

### Decision (Saqib, 2026-09-22; recorded by decision #170)

**Slice A is approved in principle**, with the venue behind a narrow `OrderVenue` port (EX-3) so Slice C is additive, the authorizer stub technically restricted to simulated execution (EX-1), and Slice B's placement-mode (`auto`/`manual`) gate present only as a seam without building the queue. The cost accepted with it: D4 and D17 stay blocked no longer than this slice takes, and the stub must not calcify into "the Governor" (EX-4).

---

## 6. Component design for the approved slice (Slice A)

Approved in principle by Saqib and amended by decision #170; this section is the implementation specification for the slice. Module names follow `system-design.md` §8's planned folder tree where it has one (`execution_engine/`, `portfolio_state/`, `position_monitor/`, `governor/`); names it lacks are suggestions. Terminology: **`execution_mode`** is the *capital mode* (`backtest | simulated | paper | live`); **`execution_venue`** is where fills come from (`simulated | ibkr | …`); **placement mode** (`auto | manual`) is what `trading-intelligence-architecture.md` §18.5 calls `ExecutionMode` — renamed here so the two never collide (§10, R7).

### 6.1 Data flow between modules

```
 Feature Engine ─► Market State Engine ─► Context Engine                                  [built]
                          │
                          ▼
              Strategy Scheduler + gate_conditions                                        [built]
                          │  OpportunityCreated  (normal lane)
            ┌─────────────┴────────────────┐
            ▼                              ▼
   Opportunity Cache  [built]      Authorizer stub  [slice A — fails CLOSED unless execution_mode == simulated]
   latest per symbol+strategy;     0 mode guard  1 session  2 actionable  3 snapshot gate (EX-7)
   overwrites; no ids              4 slots / duplicates  5 reference price + stop geometry + sizing  6 daily-loss gate (I15)
                                   ◄── reads Portfolio State (ledger-backed), MarketClock, limits (§6.10)
                                   then: mint opportunity_id + client_order_id ─► COMMIT trade + decision ─► publish
                                                │
                              ┌─────────────────┴───────────────────┐
                              ▼                                     ▼
                    PlanRejected (critical)                OrderApproved (critical; stamped execution_mode = simulated;
                    [model built]                          order_id IS the client_order_id — I10)
                                                                    │  subscribe ─► own queue ─► worker  (I7)
                                                                    ▼
                                                        Execution Engine  [slice A]
                      idempotent ledger insert (client_order_id UNIQUE) ─► COMMIT ─► mode/venue check ─► venue call
                                                                    │
                                                        OrderVenue port  (NOT BrokerAdapter — EX-3)
                                                        obtained via the `execution` registry role
                                                                    │
                                                  ┌─────────────────┴───────────────────────┐
                                                  ▼                                         ▼
                                       SimulatedVenue  [slice A]                 IBKROrderVenue  [not built; deferred, #27:
                                       supported_modes = {simulated}             a SEPARATE class with its own connection —
                                                  │                              BrokerAdapter's read-only market-data
                                                  │                              connection is not touched]
                                                  │ order / fill updates (client_order_id, venue_fill_id)
                                                  ▼
                       Execution Engine: dedupe (execution_venue, venue_fill_id) ─► COMMIT fill + order status
                                                  │ ─► publish OrderFilled (critical lane: ordering + handler-failure isolation ONLY)
                    ┌─────────────────────────────┼──────────────────────────────┐
                    ▼                             ▼                              ▼
          Portfolio State [slice A]     Position Monitor-lite [slice A]   OutcomeRecorder [slice A]
          applies fills from the LEDGER stop / target / EOD ─► reduce-only  captures ENTRY snapshots, best-effort,
          COMMIT position / closure     exit order (own client_order_id)    never on a fill's critical path
                    │ ─► publish PositionClosed (critical) — ONLY AFTER the COMMIT
                    ▼
          OutcomeRecorder: EXIT snapshots (NULL + reason if missing) ─► StrategyOutcome (execution_mode, execution_venue)
                    │ asyncio.to_thread
                    ▼
          record_strategy_outcome()  [built; backtest is its only user] ─► strategy_outcomes [built; new columns §6.8]
                    ▼
          performance_queries [built; gain an execution_mode filter] ─► World View [built; portfolio slot stays null]

 RESTART (§6.9): the bus is never the recovery source. Portfolio State is rebuilt from the fills ledger; non-terminal
 orders are reconciled with the venue; closed trades without an outcome go back to the OutcomeRecorder.
```

Read the diagram as: **the middle column is the new work**; everything above the authorizer stub and below `record_strategy_outcome()` already exists, apart from the additive columns and filters in §6.8.

### 6.2 Authorizer stub (`governor/` — name deliberately provisional)

**What it is.** One small subscriber standing in for the unbuilt Decision → Trade Planning → Governor chain, so downstream modules see the *real* vocabulary (`TradePlanned`, `GovernorDecision`, `OrderApproved`/`PlanRejected`) from day one. It is not a commitment to any of D1's shapes and it must not rank, arbitrate between strategies (D4/D1), size by Kelly, or modify a `StrategyConfig` (`strategy-engine-design.md` §6).

**Technically restricted to simulated execution — four independent layers (I6, EX-1).**
1. **Startup refusal.** Wiring reads `execution_mode` from `Settings` (§6.10). Anything other than `simulated` — `paper`, `live`, `backtest` (a per-run label, never a live-pipeline setting), or an unrecognized value — makes the stub refuse to start: the execution pipeline is not wired, an error is logged, and the app does **not** fall back to `simulated`.
2. **Per-decision refusal.** Every authorization re-checks the current mode; unless it is `simulated` the decision is `rejected` with reason `execution_mode_not_permitted`, before any other rule runs. (Defense in depth: v1 applies configuration changes only at restart, so this covers a mode changed at runtime or a wiring bug.)
3. **Mode stamp and venue check.** Every accepted decision and execution row carries `execution_mode = simulated`; rejected decision audits retain the actual requested mode (including absent/unknown) and no invented venue (entry-lifecycle-wiring); the Execution Engine routes an order only to a venue whose `supported_modes` includes that mode (§6.3, §6.4). The registry also refuses to register a venue that does not support the configured mode.
4. **No other venue exists.** No `OrderVenue` other than `SimulatedVenue` is implemented in this slice, and the stub imports no broker or venue module beyond the port's types (an import-boundary test, §9). Even a mislabelled order has nowhere real to go.

**Rules, in evaluation order — every rejection is committed and published with its reasons** (`trading-intelligence-architecture.md` §12):

| # | Rule | Reject reason |
|---|---|---|
| 0 | `execution_mode == simulated` | `execution_mode_not_permitted` |
| 1 | regular session (`MarketClock.is_regular_session`) | `outside_regular_session` |
| 2 | opportunity `status == "actionable"` | `not_actionable` |
| 3 | **snapshot gate (EX-7):** `capture_market_state_snapshot(symbol)` and `capture_context_snapshot(symbol)` are both non-`None` | `snapshot_unavailable:market_state` / `:context` |
| 4 | no open position or in-flight entry for the symbol; `open_positions + in_flight_entries < max_concurrent_positions` | `symbol_busy` / `max_concurrent_positions` |
| 5 | a reference price exists (the last observed trade price for the symbol, from the same stream the venue fills against); `structural_invalidation` lies on the correct side of it for the direction (long: below, short: above); `qty = floor(fixed_notional / reference_price) ≥ 1` | `no_reference_price` / `invalid_stop_geometry` / `notional_below_one_share` |
| 6 | **daily-loss gate (I15, below)** | `daily_loss_cap_reached` / `projected_loss_exceeds_daily_cap` / `loss_exposure_unknown` |

**The daily-loss gate (EX-4, I15).** Population-scoped: only trades with the same `execution_mode`, bucketed by `MarketClock.trading_day`.

```
 realized_loss_today  = max(0, −Σ gross realized fill P&L today, including partial reductions)
 open_exposure_loss   = Σ over open positions AND in-flight entries of
                          max( qty × |avg_entry − stop| ,  −unrealized_pnl )
                          (worst realistic loss if the stop is hit, or the loss already marked if price gapped through it;
                           a missing mark or stop makes the term UNKNOWN ⇒ treated as unbounded ⇒ reject)
 candidate_loss       = qty_new × |reference_price − structural_invalidation|

 reject  daily_loss_cap_reached            if realized_loss_today + open_exposure_loss ≥ cap
 reject  projected_loss_exceeds_daily_cap  if realized_loss_today + open_exposure_loss + candidate_loss > cap
 reject  loss_exposure_unknown             if any term is unknown
```

With the initial values (§6.10) the `open_exposure_loss` term is zero at a legitimate entry, because only one position may exist; it is specified now because the limit is configurable and the term must already be correct when the limit is raised. It counts an *in-flight* entry order as exposure so two signals cannot slip past it. The candidate term means a $1,000 trade whose stop is more than 10% away is refused even on a clean day, because its own stop-out would breach a $100 cap (§7.1, judgment call J2).

```
 OpportunityCreated ─► on_opportunity() ─► [ authorizer queue ] ─► _worker_loop()   single decision at a time
                                                     │
   ┌──────────┬──────────┬────────────┬───────────┬───────────┬────────────┬───────────────┐
   ▼ 0        ▼ 1        ▼ 2          ▼ 3         ▼ 4         ▼ 5            ▼ 6
   mode ==    regular    actionable?  both        slots &     ref price +    daily-loss gate
   simulated? session?                snapshots   symbol      stop geometry  realized + open
   (fail      (Market-                present?    free?       + qty ≥ 1?     + candidate vs cap
   closed)    Clock)                  (EX-7)
   └──── any check fails ─► COMMIT trade row (decision = rejected, reasons) ─► publish GovernorDecision(rejected) ─► PlanRejected
                                    all pass ─► mint opportunity_id, client_order_id = "<trade_id>:entry"
                                             ─► COMMIT trade row + durable entry reservation (decision inputs, thesis, limits, qty, reference price)
                                             ─► publish TradePlanned ─► GovernorDecision(approved) ─► OrderApproved
```

**Money and sizing.** USD, US equities. `TradePlanned` carries the intended notional and the integer `qty`; the actual filled notional is whatever the venue reports and is recorded as-is (no adjustment, I3). Entry is a market order in v1; stop and target are `structural_*` exactly as the Backtest Runner does (`final_* == structural_*`).

**As built (entry-lifecycle-wiring — see this task's own decision entry for the real number; found already on `main`, undocumented, when this task began, adopted after inspection and testing).** `PostgresTradeLedger` implements `TradeLedgerPort` with an owned transaction. Approval commits the trade and `trade_reservations` together; exact decision inputs (including snapshot-presence flags) are detached JSON, not fill-time market/context snapshots. Matching approval retries return the existing accepted identity; conflicting terms fail. Rejections use an audit UUID without an accepted opportunity ID, reservation, or execution venue. Migration `0014` allows missing/unknown requested modes for those audits while keeping approved mode/venue labels strict. Legacy rows remain unchanged, without guessed approval terms.

```
Authorizer rule result
    -> PostgresTradeLedger.commit_decision
       -> fresh transaction: READ COMMITTED + synchronous commit
       -> lock trades -> orders -> trade_reservations
       -> validate decision inputs and accepted identity
       -> identical approval? return existing identity after transaction
       -> insert trade + reservation (approval) OR trade only (rejection)
       -> durable COMMIT
    -> existing authorizer event publication

Committed reservation -> PostgresPositionLedger -> Portfolio State restore
                    \-> PostgresOrderLedger -> committed order -> venue
```

### 6.3 Execution Engine (`execution_engine/`)

**What it is.** The only module that talks to a venue (I1). It turns an authorization (or a reduce-only exit intent) into a durable, idempotent order, sends it, and turns every venue update into ledger state and an `OrderFilled` — deduplicated, persisted first, published second.

```
 OrderApproved (critical)                                   exit intent (Position Monitor-lite; reduce-only)
   order_id == client_order_id                                client_order_id = "<trade_id>:exit:<n>"
          │                                                            │
          ▼                                                            ▼
  on_order_approved() ── put_nowait ──► [ execution queue ] ◄── put_nowait ── on_exit_intent()
  (returns at once — I7)                        │
                                                ▼
                                       _worker_loop()   single writer
                                                │
     ┌──────────────────────────────────────────┼───────────────────────────────────────────┐
     ▼                                          ▼                                           ▼
 1. validate                          2. IDEMPOTENT LEDGER INSERT                 3. mode / venue check (fail closed)
    reduce-only guard (exits: a          INSERT orders                               order.execution_mode ∈
    matching open position must          (client_order_id UNIQUE)                    venue.supported_modes ?
    exist in Portfolio State)            conflict ⇒ duplicate ⇒ log, return          no ⇒ terminal `rejected`
    execution_mode present               the stored row, send nothing                (mode_not_supported), never routed
                                         status = approved
                                         COMMIT before ANY venue call (I8)                          │ yes
                                                                                                    ▼
                                                                                4. venue.place_order(instruction)   [OrderVenue port]
                                                                                   idempotent on client_order_id
                                                                                                    │ ack: submitted | rejected
                                                                                                    ▼
                                                                                5. COMMIT status
                                                                                   (rejection event: EX-9)

  venue.on_order_update(cb) ── put_nowait ──► [ execution queue ] ──► 6. apply update (one transaction)
                                                                          INSERT fills — (execution_venue, venue_fill_id) UNIQUE,
                                                                             conflict ⇒ duplicate ⇒ no-op and NO publish (I11)
                                                                          advance order status monotonically; a stale or
                                                                             out-of-order update is ignored and logged
                                                                          overfill, or a fill for an unknown order ⇒ STILL persist
                                                                             the fill (I14), flag it `anomaly`, halt NEW entries
                                                                          COMMIT fill + order status + flag together
                                                                                                    │
                                                                                                    ▼
                                                                          7. publish OrderFilled (critical) — only after COMMIT (I8)

  startup ─► recovery (§6.9) completes BEFORE this worker consumes its first OrderApproved (I13)
```

**As built (entry-lifecycle-wiring — see this task's own decision entry for the real number; found already on `main`, undocumented, when this task began, adopted after inspection and testing).** `PostgresOrderLedger` implements both `OrderLedgerPort` and `DecisionAuthorizationPort`. Before insert it rechecks the approved trade and reservation under the same transaction/lock prefix as the decision writer. Identity, symbol, direction, quantity, mode/venue, market order type, and open effect must match. It returns the committed instruction; Execution routes that instruction and checks both venue support and the approved venue ID. Only `inserted=True` can submit. Rejection writes retain the original execution venue, and a failed rejection commit publishes nothing. Status updates cannot regress partial/terminal/unknown states (`update_order_status()`'s own submitted/rejected-only scope, unchanged by this task — see the module docstring). **Fill ingestion** (step 6 below) is entry-lifecycle-wiring's own addition: a new, separate `FillLedgerPort`/`PostgresFillLedger` (`execution_engine/fill_ledger.py`) advances order status to `partially_filled`/`filled` and inserts the `fills` row; `OrderLedgerPort` itself is deliberately left unmodified (this task was told to reuse, not rebuild, `PostgresOrderLedger`). Restart-recovery reconciliation (§6.9) was already built by #172 (`portfolio_state/reconciliation.py`) and is now actually called from `main.py`'s `lifespan()` by this task — the diagram above is current as of entry-lifecycle-wiring.

```
OrderApproved -> committed-decision read -> insert_order
    -> lock trades -> orders -> reservations
    -> recheck durable approved terms
    -> existing identical order? return stored row; no venue call
    -> insert approved order -> COMMIT -> return durable instruction
    -> verify venue mode support + venue identity -> place_order
    -> commit submitted/rejected status -> publish rejection if applicable

Failed/uncertain insert commit -> no venue call, no event
Lost acknowledgement after commit -> order retained; duplicate sends nothing
                                     (recovery must reconcile later)
```

**Order state machine** (persisted `status`):

```
 approved ──► submitted ──► partially_filled ──► filled                       terminal: filled
    │             │  │              │
    │             │  └──► cancelled ◄┘                                       terminal: cancelled
    │                                                                        (also: approved ──► cancelled at recovery — a stale entry that was never sent)
    │             └─────► rejected                                           terminal: rejected
    └─ duplicate client_order_id ─► no new row (logged)
 approved | submitted | partially_filled ──(no update within T, or a restart)──► unknown ──► reconciled with the venue ──► any state above
 unknown, and the venue has no record (SimulatedVenue after a restart) ──► expired    terminal: expired, reason venue_lost_state_on_restart

 Non-terminal = approved, submitted, partially_filled, unknown — exactly what restart recovery scans (§6.9).
```

**Client-order ID and idempotency (I10, I11).** `OrderApproved.order_id` — an existing field — *is* the client-order ID: deterministic and stable across retries, `"<trade_id>:entry"` for the entry and `"<trade_id>:exit:<n>"` for the n-th exit attempt (`n` persisted on the position). It is minted before persistence and is unique in the ledger. Re-delivery of the same authorization, a restart-time replay, and a duplicate venue update all collapse into no-ops because the *database constraint* decides, not process memory. The venue receives the same ID as its idempotency key: `OrderVenue.place_order` returns the original acknowledgement for a known ID and never creates a second order; a venue that cannot guarantee this is wrapped by a check-before-submit using `get_order` (§6.4).

**Placement mode.** `auto | manual` (§18.5's `ExecutionMode`) exists in slice A only as a seam — the engine understands the value, only `auto` is implemented, the Approval Queue is not built (EX-13). *Dry-run* survives as an optional switch that records the order and suppresses the venue call (no fill, therefore no outcome); it is not a capital mode.

**Payload and event work this implies** (additive-optional per `system-design.md` §10.2, so no event `version` bumps; EX-9, EX-14):
- `OrderApproved` += `execution_mode`, `opportunity_id`, `position_effect` (`open`|`close`), `origin`, and for exits `exit_reason`. `order_id` semantics become "the client-order ID".
- `OrderFilled` already carries `order_id` (= the client-order ID); it gains `venue_fill_id`, `cumulative_qty`, `leaves_qty`, `execution_venue`, and an optional `commission` (`None` unless the venue supplies it).
- New models: `TradePlanned`, `PositionClosed` (§6.5), and reserved `OpportunitySelected`, `PositionAdjusted`; `PositionClosed` joins `CRITICAL_EVENT_TYPES` (EX-6); a venue-level rejection/cancel needs a representation distinct from plan-level `PlanRejected{symbol, reasons}` (EX-9).

### 6.4 `OrderVenue` port, the `execution` registry role, and `SimulatedVenue` (EX-3)

**A new narrow interface; `BrokerAdapter` is not enlarged.** `BrokerAdapter` keeps its job — market-data connectivity (it extends `MarketDataProvider`). Its dormant `place_order`/`cancel_order`/`get_positions` declarations stay exactly as they are: unwired, not extended, not implemented (their eventual removal is a later decision — §10, R8). The Execution Engine depends only on `OrderVenue`.

| `OrderVenue` member | Purpose |
|---|---|
| `venue_id` | stable string (`simulated`, `ibkr`, …); stored as `execution_venue` |
| `supported_modes` | subset of `{simulated, paper, live}`; `SimulatedVenue` = `{simulated}` |
| `connect()` / `disconnect()` / `is_connected()` | lifecycle |
| `place_order(instruction) → ack` | **idempotent on `client_order_id`**; `instruction` = client order ID, symbol, side, qty, order type, limit price, position effect |
| `cancel_order(client_order_id)` | cancel; acknowledged asynchronously through the update callback |
| `get_order(client_order_id)` | reconciliation after a restart or timeout; `None` = the venue has no record |
| `list_open_orders()` | reconciliation: venue orders the ledger does not know |
| `get_fills(client_order_id)` | reconciliation: fills the process missed |
| `get_positions()` | reconciliation only — never the source of position truth (I12) |
| `on_order_update(callback)` | pushes `client_order_id`, `venue_order_id`, status, optional `venue_fill_id`, fill qty/price, cumulative/leaves qty, venue timestamp, optional commission |

**The `execution` registry role.** `broker_registry.py` gains a third role beside `streaming` and `historical` (decision #33's pattern): typed `OrderVenue | None`, with `set_execution_venue()`, `get_execution_venue()`, `clear_execution_venue()`. It is a *separate slot with a separate type* — an `OrderVenue` is never stored in a role that holds a `BrokerAdapter`/`MarketDataProvider`, and `IBKRAdapter` connecting for market data never fills it. `set_execution_venue()` refuses a venue whose `supported_modes` does not contain the configured `execution_mode` (fail closed, I6). A future `IBKROrderVenue` is a separate class with its own connection and client-ID policy (§8).

**`SimulatedVenue` (`broker_adapters/simulated_venue.py`, name provisional).**
- **Behaves like a broker, owns no truth:** it acknowledges, then reports fills through the same update callback a real venue would use; Portfolio State and the ledger — not the venue — are the source of truth (I12).
- **Price source:** subscribes to `PriceUpdated`. A market order fills at the first tick at or after acceptance; a limit order when a tick crosses it. Fill timestamps are the tick's `exchange_ts` (event time, I9). Slippage and commission default to zero/`None` (I3) and are EX-8's parameters.
- **Deterministic identity:** `venue_fill_id = "<client_order_id>:f<n>"`, so re-reported fills dedupe by construction; its own in-memory order/fill book answers `get_order`, `list_open_orders`, and `get_fills`.
- **Not durable:** the book is lost on a restart; recovery (§6.9) marks non-terminal ledger orders it no longer knows `expired` while fills already in the ledger stand.
- **Session guard:** rejects outside the regular session in v1 (`MarketClock`). **Injectable** tick source and clock for tests, plus a partial-fill injector so the partial path is exercised even though v1 fills whole.
- **Parity with the backtest, stated not assumed:** the Backtest Runner fills at the *next candle's open* and exits *at the stop/target price*; a tick-driven venue fills at the *observed tick* and exits at the tick that breached the level. The delta is recorded, not hidden (EX-8).

### 6.5 Portfolio State Engine (`portfolio_state/`)

**Built in decisions #172–#174; integration remains partial.** Portfolio State owns position accounting, in-flight exposure, marks, and daily realized amounts. It is a cache over the ledger, never the record (I5/I12). Decision #173 revises #172's package with Saqib's approval; the same database-free arithmetic now serves both the event worker and the retained Session/reconciliation API.

**Holding period is unrestricted by this component.** Day trading is the primary use, but positions may remain open across days, months, and restarts. No daily position reset, forced EOD exit, or maximum holding period exists here. Exit policy belongs to Position Monitor. A position's UUID is persisted at opening, survives adds/reductions/restarts, and is retired at full closure. A later opening in the same symbol receives a new UUID.

```text
OrderApproved / OrderFilled / OrderStatusChanged        PriceUpdated
               |                                     held symbols only
               +------------> own queue <-------------------+
                                  |
                              single worker
                                  |
                PositionLedgerPort: committed fill/order facts
                                  |
                 pure accounting (long/short, average cost)
                                  |
       COMMIT position + per-fill realized attribution + cursor
                                  |
                    install committed read cache
                                  |
              if newly closed: PositionClosed (critical)

Session/reconciliation API -> same arithmetic -> caller COMMIT -> cache
get_snapshot() <- committed cache + memory-only marks (no I/O)
```

**Verified event limitations.** `OrderFilled` has no fill ID/sequence, mode, or position effect and still has no application publisher. Approval/fill/status events therefore trigger authoritative ledger catch-up; payload amounts are not used for arithmetic. Mode/effect/trade/symbol/side come from the persisted order; the fill supplies sequence and `(execution_venue, venue_fill_id)`. An unresolved approval is unknown exposure, never an empty account. The PostgreSQL adapter includes approved order rows and durable pre-order reservations (entry-lifecycle-wiring, found pre-built undocumented — see the decision entry). Approval restoration now works before order insertion, using the persisted quantity and exact reference price. After insertion, the order status and applied fill quantity determine remaining exposure; the retained reservation is counted only through that order. Legacy approved trades lacking both an entry order and reservation still block restoration explicitly. `OrderStatusChanged` currently represents rejection only. Read-back handles cancelled/expired/filled states, but prompt live cancellation tracking needs a future status publisher or explicit refresh trigger; this slice provides `refresh()` and restart catch-up, not polling. The bus has no symbol-filtered subscriptions: unheld ticks are dropped before queueing and checked again during processing.

**Accounting.** `accounting.py` uses immutable values and Decimal arithmetic for opens, same-side adds, average entry cost, opposite-side reductions, and full closes. Invalid quantities/prices, unintended reversals, and incompatible trade/mode/symbol inputs raise before changing state. Exposure includes filled positions and only the remaining entry-order quantity; exit orders do not duplicate exposure. Missing marks/stops remain unknown. Marks have exchange timestamps; older/pre-opening ticks are ignored, and marks are cleared at closure/reopening and restart. Cash/buying power is unavailable (`None`).

**Profit, loss, and fees are separate.** Each reducing fill contributes positive gross P&L to profit or a positive loss magnitude to loss on `MarketClock.trading_day(fill.venue_ts)` (ET calendar date). Gross P&L = profit − loss. Each fill's reported commission is tracked separately on that fill's day, including entries. Unknown commission stays unknown; `reported_fees` and an unknown-fee count distinguish known charges from a complete total. Closing a position later never moves its earlier partial realizations to closure day. Position lifetime totals and daily totals are different views. Stock splits, dividends, financing/borrow costs, and late fee corrections have no input contracts here.

**Persistence Protocol and PostgreSQL adapter (#174).** `ports.py:PositionLedgerPort` provides `load_state`, `pending_fills`, `get_order`, and `commit_fill`. Startup read-back must recover durable IDs and lifetime totals, open positions, non-terminal orders/reservations, cursor, and per-symbol/day amounts derived from individual fill attributions. Unknown history is explicit. A fill commit atomically deduplicates its stable venue key, compares the expected cursor, persists position plus realized-fill attribution (or immutable replay inputs sufficient to reproduce it), and advances the cursor. Exact duplicates publish nothing; conflicting keys/cursors raise.

`pending_fills` must expose a complete safe committed prefix. PostgreSQL Identity allocation is not commit order: an adapter must serialize ingestion or establish a safe watermark so a lower-sequence transaction cannot commit behind the cursor. `postgres.py:PostgresPositionLedger` now implements that Protocol with an owned transaction per call and the barrier described below. The retained Session path rejects skipping an earlier visible fill, shares the arithmetic, stages cache installation until `after_commit`, and rebuilds daily totals from full fill history; it still assumes serialized ledger writers. Session and Protocol ownership cannot be mixed on one instance. `full_rebuild` audits without resetting IDs/cursor. Flagged invalid fills remain persisted and block usable snapshots; reconciliation reports unavailable accounting as a discrepancy.

**PostgreSQL checkpoint barrier.** Each adapter call uses a fresh Session, READ COMMITTED, synchronous commit, and a `SHARE ROW EXCLUSIVE` lock on trades, orders, trade reservations, fills, positions, cursor, and receipt tables, acquired in that order. The locks exclude ordinary INSERT/UPDATE/DELETE and competing adapters. Reads occur only after all locks are granted, so previously uncommitted lower fill IDs are visible or aborted before any prefix is returned. Migration `0013` sets the fill Identity to `GENERATED ALWAYS`, `CACHE 1`, and advances its generator past any existing explicit IDs without rewinding it. The adapter verifies that policy on every transaction. Administrative sequence reseeding, identity override, and disabling integrity constraints are outside the ingestion contract. This conservative barrier serializes all modes and blocks ledger writers while history is read; throughput optimization is deferred until measured.

**Durable fill receipts.** `position_fill_receipts` stores the source fill FK, unique venue fill key, mode, explicit position FK, exact replay inputs (Decimal strings in JSONB), ET trading day, and unscaled numeric gross delta. Each new application revalidates the first pending source fill, expected cursor, pure arithmetic result, and attribution, then writes position + receipt + cursor in one transaction. An identical previously applied fill returns `applied=False` and current state even if another consumer generated a different provisional position UUID; conflicting source identity raises. The position projection retains its existing six-place columns; receipt replay preserves the exact average and lifetime totals. Restore checks the projection against that history and reads current stop/target/exit-attempt metadata. Orders and their statuses remain execution-owned; filled quantities come from applied receipts.

```text
Portfolio State worker -- PositionLedgerPort --> PostgresPositionLedger
                                                       |
                                          owned transaction / table barrier
                                                       |
                       +-------------------------------+-------------------+
                       | authoritative reads                               |
                       v                                                   v
              trades -> orders -> fills                positions + receipts + cursor
                       |                                                   ^
                       +--> shared accounting --> validate application ----+
                                                       |
                                                  durable COMMIT
                                                       |
                                    worker cache -> optional PositionClosed
```

```text
begin READ COMMITTED -> wait for all table locks -> verify sequence policy
       -> reconstruct/audit committed checkpoint
       -> existing receipt? identical fill: return applied=False after commit
       -> compare cursor -> require first pending fill -> recompute and compare
       -> write position -> append receipt -> advance cursor -> audit -> COMMIT
failure/uncertain acknowledgement -> raise PositionLedgerError -> reload on retry
```

**Cutover and incomplete history.** Empty ledgers and existing unapplied fills are supported. A pre-0013 applied cursor/position without complete receipts raises; no automatic legacy backfill or zero reset is performed. The old Session/reconciliation API remains available, but the two persistence paths must not own the same mode concurrently. Missing reservation metadata, flagged fills, overfills, inconsistent mode/venue/symbol facts, incomplete receipts, and corrupt projections also fail closed. Receipt history is replayed on each checkpoint, so this is correctness-first persistence, not a bounded-cost snapshot implementation. Downgrade refuses to drop nonempty receipt history.

**Read contract.** `get_snapshot(symbol=None, *, trading_day=None)` is synchronous and I/O-free. One instance serves one explicit capital mode. No symbol means the entire mode; a symbol filters positions, orders, marks, exposures, and daily totals. Unknown symbol, unrestored state, accounting backlog/failure, or unresolved order metadata yields `None`. A closed symbol with known history has a flat snapshot; a restored empty ledger is known flat. Incomplete history yields unknown daily amounts, never invented zeros. Default day comes from MarketClock; an explicit day supports historical reads. Returned dictionaries are detached and their position/order values immutable. Fields map to governor's PortfolioSnapshot/OpenExposure concepts without importing that package; consumer adaptation and honest handling of unknown state remain integration work.

```text
fill -> validate ledger identity/mode/effect and safe cursor order
                 |
       +---------+--------------------+
       | open/add                     | reduce/close
       v                              v
 new ID or same ID              opposite side and qty <= held
 weighted average cost          gross realized delta; qty decreases
       +------------------------------+
                       |
           attribute delta/fee to this fill's day
                       |
        atomic position + attribution + cursor COMMIT
             | fail                         | success
             v                              v
      publish nothing                install committed cache
      read unavailable               newly closed? -> PositionClosed
      reload before retry            duplicate? -> publish nothing
```

**Closure contract and delivery guarantee.** `PositionClosed` has the documented position ID, exit price (VWAP of all reductions), lifetime gross realized P&L, nullable achieved R, and closing timestamp, plus optional trade/mode/venue and separate profit/loss/fee fields. With no persisted immutable planned-risk contract for adds/changed stops, the worker emits `r_multiple_achieved=None` and a missing reason. It never uses the current stop to invent historical R. Missing R does not suppress a valid closure.

Only a successful newly applied closure commit can publish `PositionClosed`, on the critical lane. There is **no outbox and no exactly-once or at-least-once delivery guarantee**: commit success followed by crash, lost acknowledgement, publication failure, or an unhandled bus notification can lose the event. Committed state remains recoverable; already-applied closures are not re-published at startup. Consumers need independent ledger recovery. The Session compatibility API does not publish events.

**Not wired:** complete status notifications, authorizer/order/fill persistence integration, governor/World View adapters, live startup, position-monitor exits, and OutcomeRecorder recovery. Adapter tests now exercise real PostgreSQL transactions, concurrent writers/consumers, restart accounting, migration safety, and the real event worker. They do not prove durable event delivery or end-to-end venue ingestion. Details: decisions #173–#174 and `TESTING.md`.

### 6.6 Position Monitor-lite (`position_monitor/`)

**As built (`position-monitor-portfolio-reader`, then `position-monitor-observer-wiring`).** `PortfolioStatePositionReader` implements
Position Monitor's existing `PositionReader` port using the synchronous, detached
`PortfolioState.get_snapshot()` of one supplied Portfolio State instance. The
adapter has no database or bus work. `main.py` starts one monitor against the running,
restored Portfolio State instance only after clean venue reconciliation and successful
entry-pipeline startup. A blocked or failed pipeline leaves the monitor unavailable.

```
Postgres position ledger ──restore/fill commits──► Portfolio State
                                                │ get_snapshot() (detached)
                                                ▼
                                 PortfolioStatePositionReader
                                                │ tuple[PositionView, ...]
PriceUpdated / CandleClosed ──► Event Bus ───────┤
                                                ▼
                                       Position Monitor
                                                │ in-memory ExitIntent latch
                                                ▼
                              GET /intelligence/exit-intents
                                  observed_only diagnostic
                                                │ read on panel expansion / Refresh
                                                ▼
                              ExecutionLifecyclePanel: Observed exit triggers
                              (separate from order-lifecycle WebSocket events)
```

```
get_open_positions()
       │
       ▼
PortfolioState.get_snapshot() ── None ──► PositionSnapshotUnavailable
       │ restored snapshot
       ▼
snapshot.positions.values() ──► keep qty > 0 (open or closing)
       │                         ignore snapshot.in_flight / exposures
       ▼
copy id, symbol, side, qty, opened_at; Decimal stop/target → float or None
       │
       ▼
tuple[PositionView, ...] (empty when restored and flat)
```

**Internal observer flow (as built):**

```
lifespan startup: reconcile venue + ledger
       ├─ discrepancy/failure ──► app.state.position_monitor = None
       └─ clean ──► await PortfolioState.start() (restore snapshot)
                          └─ start entry pipeline
                                └─ PositionMonitor(bus, PortfolioStatePositionReader(portfolio_state)).start()
                                      └─ app.state.position_monitor = monitor

received PriceUpdated/CandleClosed ──► held-symbol filter ──► monitor queue/worker
       └─ fresh PositionView read ──► stop, target, EOD precedence
              └─ first trigger per position ──► in-memory ExitIntent

GET /intelligence/exit-intents?symbol=... ──► monitor.get_exit_intents(symbol)
       └─ sort by symbol, trigger_ts, position_id ──► observed_only list
          (running + empty is distinct from unavailable + empty)

ExecutionLifecyclePanel expands / Refresh ──► typed GET /intelligence/exit-intents
       └─ unavailable / running-empty / fetch error / observed rows
          (no order or position-closure event is inferred)

lifespan shutdown ──► clear app reference ──► stop bus/monitor ──► stop Portfolio State
```

The diagnostic exposes position ID, symbol, side, quantity, reason, trigger price,
and trigger timestamp. It is a point-in-time read of received events, not an order,
fill, or closed position. Intents are lost at process shutdown and are not rebuilt
from the ledger. No price/candle event means no observation, and this observer does
not protect or flatten a position. The frontend reads this snapshot when the
Execution panel expands and on manual refresh, in a section separate from its
existing order-lifecycle event list; it does not poll or subscribe to another
WebSocket channel. Exit placement, durable re-arm, EX-5, and EX-12
remain open; the output bullet below describes the broader design target, not this
observer's current behavior.

**What it is (and isn't).** Only the three exit rules the Backtest Runner already models — stop, target, and `eod_flatten` at the real regular-session close — evaluated live for symbols Portfolio State reports open. It is **not** the module `trading-intelligence-architecture.md` §13 describes (is the thesis still valid, is momentum weakening, move the stop, take a partial, exit, reverse, hold); those questions, manual-position handling (`future-ideas.md` #14), and emergency actions (#16) are out of scope.

- **Inputs (as built):** `PriceUpdated` and `CandleClosed` for held symbols; `MarketClock` for the EOD instant (the derivation `fill_simulator.regular_session_close_utc` uses).
- **Output (design target, not yet built):** one reduce-only exit intent per position (idempotent: the position moves to `closing` first), carrying `exit_reason ∈ {stop, target, eod_flatten}` and its own client-order ID `"<trade_id>:exit:<n>"`; after a restart it is re-armed from the ledger (§6.9). The current `ExitIntent` is in-process only, has no client-order ID, and does not change the persisted position status.
- **Stop/target enforcement is in-process** — acceptable for a simulated venue with no broker, **not** for a real one (a crash would leave a position without a stop): broker-side protective orders are a hard prerequisite before any real venue (§8, EX-11).

### 6.7 `OutcomeRecorder` and D17's live half (`trading_intelligence/`)

**What it is.** The one place that turns a closed position into a `StrategyOutcome` and calls `record_strategy_outcome()` — the live-path caller D17 says does not exist.

```
 OrderFilled (entry) ─► on_order_filled() ─► [ recorder queue ] ─► _worker_loop()
                                                    │   best-effort; NEVER on the fill's critical path (I14)
                                                    ▼
                            capture_strategy_outcome_snapshots(symbol)   ← market_state carries its own candle_ts
                               ├─ both present ─► store snapshots + captured_at on the trade row
                               └─ missing, or raises ─► store NULL + a reason on the trade row
                                    ("engine_cold_start" | "engine_state_lost_on_restart" | "snapshot_capture_error"
                                     | "recorder_unavailable") — the fill and the position are unaffected

 PositionClosed (critical) ─► on_position_closed() ─► [ recorder queue ] ─► _worker_loop()
                                                    ▼
                            load trade row: thesis, entry snapshots + reasons, order/fill ids, exit_reason, mode/venue
                            capture EXIT snapshots — same rule: NULL + reason, never a discard
                            build StrategyOutcome (§4 map + execution_mode + execution_venue + snapshot_missing_reasons)
                            asyncio.to_thread(record_strategy_outcome, outcome)      ← same call shape BacktestRunner uses
                               ├─ ok    ─► trades.outcome_id set (idempotent: a repeated PositionClosed is a no-op)
                               └─ raises ─► trades.outcome_status = pending_retry; log loudly; never drop

 startup ─► scan trades WHERE status = closed AND outcome_id IS NULL ─► the same path
            (the bus lost nothing the ledger still holds — I8, I12)
```

**D17's live policy is resolved (EX-7, decision #170).**
1. **Pre-trade gate.** The authorizer refuses a symbol unless both snapshot halves exist (rule 3, §6.2). This keeps the missing case rare; it is not the safety net.
2. **A reported fill is never discarded (I14).** Fill persistence, position accounting, and `OrderFilled` publication do not depend on snapshot capture succeeding.
3. **If a snapshot is unexpectedly unavailable** (entry or exit — e.g. the engine restarted mid-position, or capture raised), the outcome is still recorded with that snapshot field `NULL` and a machine-readable reason in `snapshot_missing_reasons`. Never a fabricated `{}`, never a sentinel inside the dict, never a discarded row.
4. **Backtests are unchanged:** decision #128's discard-on-`None` stays for `execution_mode = backtest` rows, and the schema keeps those four columns `NOT NULL` for them (§6.8).
5. **Capture timing is visible.** Snapshots are read at fill-handling time, not as of `fill_ts`; the market-state dict carries its `candle_ts` and the trade row stores `captured_at`, so late capture is measurable rather than hidden.

### 6.8 Persistence sketch (implemented incrementally by #172 and entry-lifecycle-wiring — #174 was frontend-only and built no table here)

Names follow `system-design.md` §4.13; columns are illustrative. Every write goes through `asyncio.to_thread` (the repository's sync-engine pattern) and precedes the corresponding event (I8). **The ledger tables are authoritative (I12).**

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `trades` | One row per authorization, approved or rejected; holds the thesis snapshot the cache will not keep | `trade_id` (= accepted `opportunity_id` for approvals; audit identity for rejections), `decision_record` (entry-lifecycle-wiring exact authorization inputs), `execution_mode`, `execution_venue`, `origin`, strategy name/version, `direction`, thesis (`structural_*`, `final_*`, `confidence`, `evidence`), `decision`, `reasons`, `limits_snapshot` (the three limits in effect — §6.10), `status`, entry snapshots + reasons, `outcome_id`, `outcome_status` |
| `trade_reservations` (entry-lifecycle-wiring) | Durable approval terms before order insertion, retained after handoff | `trade_id` PK/FK, deterministic `client_order_id` UNIQUE, positive `qty`, finite positive exact `reference_price`, `created_at`; migration downgrade refuses to discard reservations or decision records |
| `orders` | The order ledger and state machine | `client_order_id` **UNIQUE**, `trade_id`, `execution_mode`, `execution_venue`, `venue_order_id`, `symbol`, `side`, `position_effect`, `qty`, `order_type`, `limit_price`, `status`, `exit_reason`, timestamps |
| `fills` | Every fill, deduplicated, in ledger order | `ledger_seq` (monotonic), `client_order_id`, `execution_venue`, `venue_fill_id`, `qty`, `price`, `venue_ts`, `commission` (nullable), `anomaly` (nullable: `overfill` \| `unmatched_order`); **UNIQUE (`execution_venue`, `venue_fill_id`)** |
| `positions` | Position accounting (owner: Portfolio State), a deterministic function of `fills` | `position_id`, `trade_id`, `execution_mode`, `execution_venue`, `symbol`, `side`, `qty`, `avg_price`, `stop`, `target`, `opened_at`, `closed_at`, `status`, `realized_pnl`, `exit_attempt` |
| `portfolio_state_cursor` | Where Portfolio State's replay resumes | `execution_mode`, `last_applied_ledger_seq` |
| `position_fill_receipts` (entry-lifecycle-wiring — mislabeled "#174" in an earlier edit of this row; #174 was frontend-only, "no backend logic changed" per its own decision entry, and never built this table) | Durable fill application and exact replay inputs | `ledger_seq` PK/FK, unique `(execution_venue, venue_fill_id)`, `execution_mode`, `position_id` FK, `fill_data` JSONB, `trading_day`, `gross_pnl` |

**`strategy_outcomes` changes (EX-2, EX-7; the build task owns the migration):**
- Add **`execution_mode`** (`backtest | simulated | paper | live`) and **`execution_venue`** (`simulated | ibkr | …`); both set on every new row, both part of the `StrategyOutcome` contract. For `backtest` rows the venue is `simulated` (the `fill_simulator` replay model); for `simulated` rows it is `SimulatedVenue`; the *mode* says which.
- **Keep `is_backtest` temporarily** for compatibility — no reader is broken — but it is a *derived* value, not the classifier: `CHECK (is_backtest = (execution_mode = 'backtest'))`. New reads filter on `execution_mode`; retiring `is_backtest` is a later decision.
- **Mode/venue can never disagree with reality:** `CHECK ((execution_venue = 'simulated') = (execution_mode IN ('backtest','simulated')))` — a simulated venue cannot label its rows `paper` or `live`, and a real venue cannot label them `simulated`.
- **Backfill without guessing:** existing rows with `is_backtest = true` become `execution_mode = 'backtest'`, `execution_venue = 'simulated'`. No legitimate writer of `is_backtest = false` rows exists today, so the migration first counts them and **aborts if any exist**, reporting them for Saqib to review, rather than labelling them `live`.
- **Snapshots:** the four snapshot columns become nullable and `snapshot_missing_reasons` (JSONB) is added, with `CHECK (execution_mode <> 'backtest' OR all four are NOT NULL)` (preserving #128) and a check that every `NULL` snapshot has its key in `snapshot_missing_reasons`.
- **`schema_version` bumps** (1 → 2): the record's own rule (`schemas/performance.py:StrategyOutcome.schema_version`) says a change of meaning bumps, and four fields become nullable for non-backtest rows.
- **Queries:** `performance_queries` gain an `execution_mode` filter; a population is one exact mode, never an implicit union (I4). The Backtest Runner sets `execution_mode = 'backtest'` and `execution_venue = 'simulated'` on its rows (part of the build task's footprint).

`market_events` (the durable bus log) is **not** required: the ledger is the durable record, which is all I8/I12 need (EX-10).

### 6.9 Restart recovery and reconciliation (I12, I13)

The bus is in-memory and forgets (F6); recovery runs from the ledger, **before** the pipeline accepts a single new authorization.

```
 process start
   │
   ├─ 1. connect to the database; refuse to wire the execution pipeline unless execution_mode == simulated   (fail closed, I6)
   │
   ├─ 2. Portfolio State: rebuild_from_ledger()
   │        apply every fill with ledger_seq > cursor, in order ─► assert in-memory == replay ─► ledger wins (I12)
   │
   ├─ 3. Execution Engine recovery — for every order with status IN (approved, submitted, partially_filled, unknown):
   │        venue.get_order(client_order_id) and venue.get_fills(client_order_id)
   │          ├─ venue knows it ─► apply missing fills (dedupe on venue_fill_id), advance status monotonically
   │          └─ venue has no record ─► approved, never sent:  entry ⇒ cancelled (stale opportunity, not re-submitted)
   │                                                            exit  ⇒ re-submitted (idempotent on client_order_id)
   │                                    submitted / partially_filled ⇒ expired (venue_lost_state_on_restart);
   │                                    fills already in the ledger stand
   │        venue.list_open_orders() vs ledger ─► an order the ledger does not know ⇒ DISCREPANCY
   │        venue.get_positions() vs ledger-derived positions ─► a mismatch ⇒ DISCREPANCY
   │        any DISCREPANCY ⇒ halt NEW entries, alert; never auto-adopt, never discard (I13, I14)
   │
   ├─ 4. OutcomeRecorder: trades WHERE status = closed AND outcome_id IS NULL ─► build outcomes (NULL + reason where needed)
   │
   ├─ 5. Position Monitor-lite: re-arm stop / target / EOD for every open position from the ledger
   │
   └─ 6. only now subscribe to the bus and accept OrderApproved; new entries stay halted while any discrepancy is unresolved
```

Position closures and outcomes are recovered the same way: a `PositionClosed` published but never handled left its committed closure in the ledger, and step 4 finds it.

**Running app startup and rollback (as built, `main.py`).** The diagram above
states the broader recovery design; OutcomeRecorder and exit placement remain
unwired. The actual entry startup keeps the bus and unrelated market-data /
intelligence engines running when reconciliation or execution startup fails.

```
Event Bus + market-data / intelligence subscribers start
        │
        ▼
SimulatedVenue.connect() ──► Session-mode PortfolioState rebuild ──► reconcile ledger / venue
        │                                                         │
        │                                      discrepancy ────────┴──► disconnect venue
        │                                                             entry pipeline unavailable
        └─ clean ──► register execution venue ──► PortfolioState.start() (restore)
                         ──► AuthorizerStub.start() ──► ExecutionEngine.start()
                         ──► PositionMonitor.start()
                         ──► publish app World View reader + monitor references
                         ──► serve requests with entry pipeline active

Exception at any startup step ──► rollback below ──► serve unrelated routes
```

```
Exception handler, before lifespan yields:
  clear execution venue registry role + app read references
       ──► deactivate authorizer and execution callbacks together
       ──► stop authorizer ──► stop execution engine ──► stop monitor
       ──► stop Portfolio State ──► disconnect venue
       ──► clear local lifecycle references

Event Bus unsubscribe removes each stopped pipeline handler from future dispatch.
A handler already copied by a bus dispatch checks its stopped flag; authorizer
and execution workers discard queued items during rollback and finish before
requests are served. The bus keeps serving market-data and intelligence routes.
Normal shutdown still stops the bus first, then drains the execution pipeline
in its existing producer-to-consumer order.
```

### 6.10 Configuration (EX-4) — three limits, configurable, not hardcoded

All values live in `core/config.py`'s `Settings` (the repository's single source of configuration — nothing else reads the environment), overridable by environment/`.env`, validated at startup (each must be positive), and **recorded on every authorization** as `limits_snapshot` so a decision's basis is auditable after the limits change. Changes take effect at restart in v1.

| Setting (names provisional) | Initial value | Meaning |
|---|---|---|
| `execution_max_concurrent_positions` | **1** | maximum open positions plus in-flight entries at any moment |
| `execution_fixed_notional_usd` | **$1,000** | notional per trade; `qty = floor(notional / reference_price)` |
| `execution_daily_loss_cap_usd` | **$100** | the daily-loss gate's cap (§6.2) |
| `execution_mode` | `simulated` | the capital mode; only `simulated` is accepted in this slice, anything else fails closed (§6.2) |

**These are conservative first-slice defaults for validating the lifecycle, not final trading-risk settings** (Saqib, 2026-09-22); the code and its config comments must say so.

### 6.11 Safety invariants → mechanisms

| Invariant | Proposed mechanism | Proposed test |
|---|---|---|
| I1 only Execution places | the venue is reachable only through the Execution Engine's router via the `execution` registry role | a grep-style test that no other module imports an `OrderVenue`'s `place_order` |
| I2 authorization precedes any risk-increasing order | Execution refuses an entry `OrderApproved` lacking a committed `trades` decision row | an `OrderApproved` published with no decision row is rejected and logged |
| I3 honest absence | `commission`, `slippage`, `buying_power` stay `None`; no fabricated snapshot | outcome rows with `commission_total IS NULL` on the simulated venue |
| I4 population separation | `execution_mode`/`execution_venue` on every row, DB `CHECK`s (§6.8), reads filter by exact mode | a query for one mode never returns another; a `simulated` row cannot be inserted as `live` |
| I5 single owner | only Portfolio State writes `positions`; others read `get_snapshot()` | a second-writer grep test |
| I6 fail closed | four layers (§6.2), registry refusal, no non-simulated venue | `paper`/`live`/unknown/missing mode ⇒ pipeline unwired and every decision rejected; no fallback to `simulated` |
| I7 no network on the critical lane | handlers only `put_nowait` | a slow fake venue does not delay an unrelated critical event |
| I8 persist before publish | fill, closure, and authorization commit first, publish second | fault injection between commit and publish, then restart, recovers state and outcome without a duplicate |
| I9 event time | fills stamped from tick `exchange_ts`; no `datetime.now()` in fill logic | fixture replay yields identical rows across runs |
| I10 stable client-order ID | deterministic `"<trade_id>:entry"` / `":exit:<n>"`; `UNIQUE` in `orders` | the same authorization delivered twice yields one order and one venue submission |
| I11 dedupe | `UNIQUE` constraints; monotonic status | a replayed `venue_fill_id` is a no-op and publishes nothing; a stale status update is ignored |
| I12 ledger authoritative | `rebuild_from_ledger()` with a cursor; assert in-memory == replay | corrupt the in-memory state, rebuild, and the ledger wins |
| I13 restart recovery | §6.9, before the pipeline accepts anything | kill with non-terminal orders, restart, assert reconciliation and no duplicate order |
| I14 never discard a fill | fill commit is independent of snapshots, plans, and matching orders; anomalies flag and halt entries | an overfill and an unmatched fill are persisted and flagged; a missing snapshot still yields an outcome with `NULL` + reason |
| I15 daily-loss gate | realized + open exposure + candidate vs cap; unknown ⇒ reject (§6.2) | tables of cases including unrealized loss, an in-flight entry, a missing mark, and a limit raised above 1 |

---

## 7. Forks — six resolved by decision #170, the rest still open

Provisional labels **EX-1 … EX-14**. On 2026-09-22 Saqib resolved EX-1, EX-2, EX-3, EX-4, EX-6 and EX-7 (the resolutions below are binding for the slice); added six requirements (§3, I10–I15); and set the three initial limits (§6.10). EX-10 is settled by the "ledger is authoritative" requirement (I12) — an inference stated openly so it can be overruled (§7.1). The other forks keep their recommendations, which the build task may proceed on unless Saqib objects — except **EX-5 and EX-12, which still need his confirmation** (§7.1).

| Fork | Question | Status | Outcome / recommendation |
|---|---|---|---|
| EX-1 | Build order | **RESOLVED (#170)** | Execution first with the stub authorizer and `SimulatedVenue`; the stub is technically restricted to simulated execution and fails closed for paper/live |
| EX-2 | Labelling simulated-money outcomes | **RESOLVED (#170)** | separate `execution_mode` (`backtest\|simulated\|paper\|live`) and `execution_venue` (`simulated\|ibkr\|…`); `is_backtest` kept temporarily for compatibility |
| EX-3 | Venue port and registry role | **RESOLVED (#170)** | new narrow `OrderVenue` interface + an `execution` registry role; `BrokerAdapter` not enlarged |
| EX-4 | Authorizer stub shape and numbers | **RESOLVED (#170)** | one stub; 1 concurrent position, $1,000 notional per trade, $100 daily loss cap — all configurable |
| EX-5 | Do protective exits need authorization? | OPEN — needs confirmation | no, but reduce-only and Portfolio-State-checked |
| EX-6 | Position accounting, in-flight orders, `PositionClosed` lane | **RESOLVED (#170)** | Portfolio State owns them; `PositionClosed` on the critical lane only after the commit |
| EX-7 | D17 live policy for missing snapshots | **RESOLVED (#170)** | pre-trade gate; a reported fill is never discarded; else nullable fields + a missing-data reason |
| EX-8 | Simulated fill model; `fill_simulator` reuse | OPEN — proceed on recommendation | conventions shared, incremental model new, parity delta documented |
| EX-9 | Identity and payloads | OPEN in part — proceed on recommendation | the client-order-ID part is settled by I10; `opportunity_id` minting, new models, venue-level rejection event remain |
| EX-10 | Durability | **Settled by I12** | ledger-first; `market_events` stays independent future work |
| EX-11 | Exit enforcement | OPEN — proceed on recommendation | in-process for simulated; broker-side mandatory before any real venue |
| EX-12 | Who writes `StrategyOutcome`; what belongs in it | OPEN — needs confirmation | `OutcomeRecorder`; strategy-attributed trades only |
| EX-13 | Manual mode in the first build | OPEN — proceed on recommendation | no — keep the placement-mode seam |
| EX-14 | `BUY/SELL` vs `long/short`; position effect | OPEN — proceed on recommendation | keep `BUY/SELL` on orders + explicit `position_effect` |

### EX-1 — Slice order  · RESOLVED (decision #170)
**Question.** Build the Execution/Portfolio core first (Slice A, §5) with a stubbed authorizer, start with manual mode (Slice B), or wait for a real Decision/Planning/Governor?
**Resolution.** Slice A: Execution first, using the stub authorizer and `SimulatedVenue`. **The stub must be technically restricted to simulated execution and must fail closed for paper or live modes** — implemented as four independent layers (§6.2) and required by I6.
**Why (evidence).** Decision Engine/Governor "genuinely wait for real outcome data" (D4, D17); the only source is a trade lifecycle; the IBKR path is unverified (#27); no manual-trading code or UI exists (F4, §1); a strategy-less manual trade cannot be a `StrategyOutcome` (F10a).

### EX-2 — Population labels for simulated-money outcomes  · RESOLVED (decision #170)
**Question.** `is_backtest` is boolean and every query treats it as a hard boundary (I4). Where do outcomes from a *simulated live venue* go?
**Resolution.** **`execution_venue` alone is not enough — venue identity and capital mode are different concepts.** Add **`execution_mode`** (`backtest | simulated | paper | live`) and **`execution_venue`** (`simulated | ibkr | …`). Retain `is_backtest` **temporarily for compatibility** only; it is derived (`is_backtest = (execution_mode = 'backtest')`) and is not the long-term classifier. Details, constraints, and the migration's fail-closed backfill: §6.8. The population label is decided before the first row is written; retrofitting one onto already-stored rows would mean guessing.
**Rejected.** A synthetic `backtests` row per paper session (a semantic lie in the most-queried table); a separate table (a parallel query layer).

### EX-3 — Venue port and registry role  · RESOLVED (decision #170)
**Question.** How does the Execution Engine talk to a venue, given F3 (no fill callback) and that `BrokerAdapter` extends `MarketDataProvider`?
**Resolution.** A **new narrow `OrderVenue` interface** and an **`execution` registry role**. **`BrokerAdapter` is not enlarged** — its responsibility stays market-data connectivity, and it does not inherit the port (this replaces the earlier draft's option (b), which had it inherit). The port, its idempotency contract, and the registry role's typing and mode check: §6.4. A real venue later is a separate class with its own connection (§8).
**Evidence.** `base.py`; `broker_registry.py` has `streaming`/`historical` only; `system-design.md` §2 principle 1 ("Everything talks to a `BrokerAdapter` interface") — which the build task must qualify for the execution path (§10, R9).

### EX-4 — Authorizer stub: shape and rule numbers  · RESOLVED (decision #170)
**Resolution.** **Shape:** one stub emitting `TradePlanned → GovernorDecision → OrderApproved/PlanRejected` (§6.2), not a commitment to any D1 shape. **Initial values, all configurable rather than hardcoded (§6.10):** maximum concurrent positions **1**; fixed size **$1,000 notional** per trade; daily loss cap **$100**. These are conservative first-slice defaults for validating the lifecycle, not final trading-risk settings. **The daily-loss gate considers realized loss plus current unrealized loss and open risk, not realized P&L alone** (I15, §6.2).

### EX-5 — Do protective exits need authorization?  · OPEN · amends I2 · needs Saqib's confirmation
**Question.** I2 as reconciled covers *risk-increasing* orders. Does a stop, target, or EOD-flatten exit also need a Governor-class decision?
**Options.** (a) **No** — exits are *reduce-only*, checked by the Execution Engine against Portfolio State, carrying an `exit_reason` and their own client-order ID. (b) **Yes** — every order, including exits, gets a `GovernorDecision`.
**Evidence.** `trading-intelligence-architecture.md` §12 frames the Governor as a *risk gate* on new exposure and §13's Position Monitor issues exits; an authorization round-trip on a stop adds latency exactly when it hurts; the emergency-action design (#16) is the same shape.
**Recommendation.** (a), with the reduce-only guard as the enforced mechanism (§6.3 step 1).

### EX-6 — Position accounting owner, in-flight orders, `PositionClosed` lane  · RESOLVED (decision #170)
**Resolution.** **Portfolio State owns position accounting, in-flight orders, and daily P&L** (I5), as a cache over the authoritative ledger (I12). **`PositionClosed` may use the critical lane, but only after the position closure has been committed to the database** (I8). **Documented explicitly: the critical lane provides ordering and handler-failure isolation — not persistence, delivery guarantees, crash recovery, or failure propagation to the publisher** (F6, §6.5); recovery comes from the ledger (§6.9). Departure from `system-design.md` §4.8, which has Position Monitor emit `PositionClosed`: Position Monitor is a decision module that reads Portfolio State and issues exit intents (§6.5, §6.6).

### EX-7 — D17 live policy for missing snapshots  · RESOLVED (decision #170)
**Resolution.** **The snapshot requirement is a pre-trade gate** (§6.2, rule 3). **Once any venue reports a fill, that fill is always persisted and processed** (I14). **If a snapshot is unexpectedly unavailable, the outcome is recorded with nullable snapshot fields plus a missing-data reason — never discarded** (§6.7, §6.8). Decision #128's discard-on-`None` remains for backtest rows only. Rejected: discarding (survivorship bias for a real fill); an in-dict "unavailable" sentinel (pollutes readers, stretches #128's "never a fabricated `{}`").

### EX-8 — Simulated fill model and `fill_simulator` reuse  · OPEN
**Options.** (a) **Reuse conventions only** (session-close EOD, stop-wins-tie, zero slippage/commission), write a new incremental model. (b) **Extract shared pure helpers** from `fill_simulator.py` — changes an existing, decision-locked module. (c) **Call `fill_simulator` incrementally** — impossible as written (F9).
**Consequence.** (a) leaves two implementations of the same conventions, mitigated by a **parity table** and an acceptance test that replays a fixture through both; (b) is cleaner but expands the build's file footprint into `backtest_runner/`.
**Recommendation.** (a); record the fills-at-next-tick vs fills-at-next-candle-open delta in the build's decision entry. Proceed unless Saqib objects.

### EX-9 — Identity and payloads  · OPEN in part
**Settled by decision #170 (I10, I11):** every order has a stable, deterministic client-order ID (`OrderApproved.order_id`), minted before persistence and unique in the ledger; venue order IDs are stored separately as `venue_order_id`; fills carry a venue-supplied (simulated: deterministic) `venue_fill_id`; updates and fills are deduplicated by database constraint.
**Still open.** *`opportunity_id`:* (a) minted at the **authorizer's acceptance** (matches #128's "mint when the signal is accepted"; no change to `Opportunity`) or (b) at `OpportunityCreated` publish (needs an `Opportunity`/payload field, a `base_strategy` change). *Payloads:* the additive fields in §6.3; new models `TradePlanned`, `PositionClosed` (and reserved `OpportunitySelected`, `PositionAdjusted`); a **venue-level rejection/cancel** needs either a new critical event (e.g. an `OrderStatusChanged`) or reuse of `PlanRejected` (a semantic stretch — that event is plan-level). `TradePlanned`'s two prose definitions disagree (§10, R2).
**Recommendation.** (a) for id minting; a new order-status event rather than stretching `PlanRejected`; proceed unless Saqib objects.

### EX-10 — Durability  · SETTLED by I12
**Outcome.** **Ledger-first:** `orders`/`fills`/`trades`/`positions` are committed before events are published (I8) and are authoritative (I12); no durable event log is required. `market_events` remains independent future work. *(Inferred from Saqib's "the database ledger is authoritative" requirement; stated openly so it can be overruled.)*

### EX-11 — Exit enforcement  · OPEN
**Options.** (a) **In-process monitoring** (Position Monitor-lite) — the only option for a simulated venue. (b) **Broker-side protective orders** (bracket/OCO) — required for any real venue because a process crash must not leave an unprotected position; the venue port's instruction has no stop fields yet.
**Recommendation.** (a) for slice A; **(b) recorded as a hard prerequisite** for any real-money venue (§8).

### EX-12 — Who writes `StrategyOutcome`; what belongs in it  · OPEN · needs Saqib's confirmation
**Question.** F10a and F13: `PositionClosed{position_id, exit_price, realized_pnl, r_multiple_achieved, closed_ts}` (prose) cannot build a `StrategyOutcome`, and a strategy-less manual trade cannot be one at all.
**Options.** (a) A dedicated **`OutcomeRecorder`** joins the `trades`/`orders`/`fills` ledger with persisted snapshots; **`strategy_outcomes` holds strategy-attributed trades only** (corroborated manual trades included), while `trades` records everything. (b) **Enrich `PositionClosed`** so Performance Intelligence can build the row from the event alone. (c) **Relax the NOT NULL columns** so strategy-less manual trades fit `strategy_outcomes`.
**Consequence.** (b) fattens an event with data the ledger already holds; (c) is a broad schema change that dilutes what `strategy_outcomes` means (evidence about strategy configurations). The `execution_mode`/`execution_venue` columns (EX-2) apply to whichever rows it holds.
**Recommendation.** (a). The §6.7 design already assumes it.

### EX-13 — Manual mode in the first build  · OPEN
**Options.** (a) **Not in scope** — the placement-mode gate (`auto|manual`, §18.5's `ExecutionMode`) exists as a seam (`auto` only); Approval Queue, Input Layer, and `TradeRequest` wait. (b) **In scope.**
**Evidence.** §5 Slice B blockers; `TradeRequest` has no stop field while `TradePlan.stop` is required (F10a) — the manual design must answer where a manual trade's stop comes from, and that answer is not in `trading-intelligence-architecture.md` §18.
**Recommendation.** (a).

### EX-14 — Direction vocabulary and position effect  · OPEN
**Options.** (a) **Keep `BUY/SELL` on orders and outcomes** and add an explicit `position_effect` (`open|close`) on `OrderApproved`; `long/short` stays a planning-layer word. (b) Derive the effect in Execution from Portfolio State (implicit, fragile on a reversal or a late fill). (c) Change orders to `long/short`.
**Evidence.** F12; `StrategyOutcome.direction` is `BUY|SELL`.
**Recommendation.** (a).

### 7.1 Before a build task starts — what still needs Saqib

1. **EX-5** — confirm that stop, target, and EOD-flatten exits need no fresh Governor-class decision, only the reduce-only guard (this amends I2).
2. **EX-12** — confirm that `strategy_outcomes` holds strategy-attributed trades only, with `trades` recording everything and `OutcomeRecorder` as the writer.
3. **Judgment calls made in this revision — confirm or overrule:**
   - **J1 — naming.** `trading-intelligence-architecture.md` §18.5's `ExecutionMode` (`auto|manual`) is called *placement mode* here, so that `execution_mode` means the capital mode and nothing else (§10, R7).
   - **J2 — the daily-loss gate also counts the candidate trade's own stop-out loss** ("open risk"), so at the initial values a $1,000 trade whose stop is more than 10% away is refused even on a clean day.
   - **J3 — schema details:** `StrategyOutcome.schema_version` bumps 1 → 2; backtest rows become `execution_mode = 'backtest'`, `execution_venue = 'simulated'`; the migration aborts if any `is_backtest = false` rows exist.
   - **J4 — recovery policy:** an entry order that was approved but never sent is *cancelled*, not re-submitted, at recovery (its opportunity is stale); exit orders are always re-submitted.
   - **J5 — EX-10** is treated as settled by the ledger requirement.

Everything else open (EX-8, the rest of EX-9, EX-11, EX-13, EX-14) proceeds on its recommendation unless Saqib objects.

---

## 8. Deferred prerequisites — not forks, and not designed here

These are recorded so they are not rediscovered; none is recommended for now.

- **A real venue — IBKR paper/live** (`future-ideas.md` #27, blocked): after decision #170 this is a **separate `IBKROrderVenue`** implementing the `OrderVenue` port with **its own connection and client-ID policy**; `BrokerAdapter`'s read-only market-data connection is not reused, widened, or made writable. It also needs: a paper-only guard (the configured port default is the paper Gateway; a live port must be structurally unreachable, not merely unconfigured); broker-side protective orders (EX-11); updates mapped into the port including partials, cancels, and commissions; on-connect reconciliation of positions **and open orders** (the port has both); an explicit policy for *unmatched* fills (recorded and flagged today; whether a real venue's unmatched fill may ever be adopted into positions is a decision for that design); short-selling availability; a `supported_modes` of `{paper}` or `{live}` and the registry refusal that goes with it. **All of it is unverifiable until a real session is reached.**
- **Emergency actions** (`future-ideas.md` #16, PANIC / flatten): a real-money venue should not be enabled before flatten-everything exists. Recommendation, not a locked rule.
- **Manual mode** — Input Layer, `TradeTarget`, hotkeys, Approval Queue UI, placement-mode change events, and the manual trade's stop source (F10a).
- **A real Governor rule engine, Decision Engine (D1), and Opportunity Engine (D4)** — the stub is designed so these replace it without changing Execution.
- **Position Monitor proper** — thesis-validity checks, stop management, partials, reversal, manual-position handling (`future-ideas.md` #14).
- **Frontend** — Positions / Trade Management / order-status widgets, and any consumer of `orders.status`.
- **World View `portfolio` slot** — reads the running restored Portfolio State through `main.py`'s separate lifecycle dependency; see `trading-intelligence-architecture.md` §15. Position Monitor is now wired separately as an observed-only diagnostic; neither path places an exit.
- **Retiring `is_backtest`** — kept only for compatibility; its removal is a later decision.

---

## 9. Acceptance criteria proposed for the eventual build task

**Lifecycle**
1. **End-to-end fixture:** a scripted `OpportunityCreated` produces exactly one `strategy_outcomes` row — `execution_mode = 'simulated'`, `execution_venue = 'simulated'`, `is_backtest = false`, `origin = 'auto'`, every field per §4 — via the real Execution Engine, `SimulatedVenue`, Portfolio State, Position Monitor-lite, and `OutcomeRecorder`, against real PostgreSQL 16.
2. **Parity:** the same fixture replayed through the Backtest Runner and through the live path yields outcomes whose differences are exactly those tabulated for EX-8.

**Fail closed (EX-1, I6)**
3. With `execution_mode` set to `paper`, `live`, `backtest`, an unknown value, or unset-with-invalid-config, the authorizer stub does not start, the execution pipeline is not wired, and no order is ever created; there is no fallback to `simulated`.
4. Changing the mode after startup (in a test, by replacing the mode holder) makes every subsequent decision `rejected` (`execution_mode_not_permitted`).
5. `set_execution_venue()` refuses a venue whose `supported_modes` lacks the configured mode; the Execution Engine refuses an order whose mode the venue does not support.
6. The stub imports no broker/venue module beyond the port's types (import-boundary test), and no other module reaches a venue's `place_order` (I1).

**Ledger, idempotency, recovery (I10–I13)**
7. The same `OrderApproved` delivered twice creates one `orders` row and one venue submission; the client-order ID is deterministic across retries.
8. A replayed `venue_fill_id` inserts nothing and publishes nothing; a stale or out-of-order status update is ignored and logged.
9. **Persist-before-publish:** fault injection between the commit and the publish of a fill, then of a position closure, followed by a restart, recovers Portfolio State and the outcome from the ledger with no duplicate order, fill, or outcome.
10. **Restart recovery:** killing the process with orders in each non-terminal status and restarting reconciles each with the venue as §6.9 specifies (including `expired` for a venue that lost state, cancelled-not-resent for a stale entry, re-submitted exit) *before* any new authorization is accepted; a venue/ledger discrepancy halts new entries.
11. **Ledger authoritative:** corrupting in-memory Portfolio State and calling `rebuild_from_ledger()` restores the ledger-derived state; a disagreement is logged and the ledger wins.
12. A critical-lane test documents the boundary: a published-but-unhandled critical event is lost across a restart and everything it announced is still recovered from the ledger.

**Never discard a fill (EX-7, I14)**
13. An overfill and a fill for an unknown order are persisted, flagged `anomaly`, and halt new entries; they are not dropped.
14. A missing entry snapshot, a missing exit snapshot, and a capture that raises each still yield an outcome with the field `NULL` and a reason in `snapshot_missing_reasons`; the database checks reject a `NULL` snapshot without a reason and any `NULL` snapshot on a `backtest` row.
15. The pre-trade gate rejects a symbol lacking either snapshot half.

**Limits (EX-4, I15)**
16. Each of the three limits is read from `Settings`, overridable by environment, validated positive, and recorded in `limits_snapshot` on every decision; none appears as a literal in the authorizer.
17. Daily-loss gate table tests: realized loss only; unrealized loss on an open position; an in-flight entry; a gap through the stop; a missing mark; a missing stop; the candidate's own stop-out loss; and a raised `max_concurrent_positions` (the open-exposure term active).

**Populations (EX-2, I4)**
18. A query for one `execution_mode` never returns another; a `simulated`-venue row cannot be inserted as `paper` or `live` and vice versa; `is_backtest` always equals `(execution_mode = 'backtest')`; the migration aborts on pre-existing `is_backtest = false` rows.

**Other**
19. **Authorization gate (I2):** an entry `OrderApproved` with no committed authorizer decision is refused and logged. **Reduce-only:** an exit with no matching open position is refused.
20. **Critical-lane isolation (I7):** a venue whose `place_order()` blocks does not delay an unrelated critical event.
21. **Honesty (I3):** no fabricated commission, slippage, or snapshot; `outcome_status = 'pending_retry'` (not a silent drop) when `record_strategy_outcome()` raises.
22. **Regression:** the existing suite's known-clean baseline is unchanged except where the schema change (`schema_version`, new columns) is deliberately reflected.

---

## 10. Findings outside this task's boundary (reported, not fixed — AGENTS.md §9)

- **R1 — Related follow-up.** `system-design.md` §4.1 still says "`IBKRAdapter` and `AlpacaAdapter` implement this" although decision #1 revised the Alpaca plan (its own folder tree already says "Alpaca deferred, not stubbed"; §2 principle 1 and the §3 diagram also still name Alpaca). Docs-only fix, unrelated to this design's correctness; left untouched except for the pointer sentences this delivery adds.
- **R2 — Related follow-up.** `TradePlanned`'s prose definitions disagree: `system-design.md` §10.3 has `max_hold_minutes`, `scaling_plan`, `trailing_stop_rule`, while `trading-intelligence-architecture.md` §18.3's `TradePlan` has `max_hold_seconds`, `origin`, `corroboration`. The model does not exist yet (F1), so nothing is broken; the build task must reconcile them (EX-9).
- **R3 — Related follow-up.** `trading-intelligence-architecture.md` §18.8 says a filled manual plan is recorded in "the existing `trades` table"; no such table exists (F7).
- **R4 — Related follow-up.** `performance.py`'s docstring forbids a live caller without a real Execution Engine; when the build lands, that docstring and D17's "live half" note in `strategy-engine-open-decisions.md` need the corresponding update (a docs step for the build task, not for this one).
- **R5 — Related follow-up.** `system-design.md` §4.9 names the `OrderApproved` payload `ApprovedOrder`; no such class exists — the model is `OrderApproved` itself (`schemas/events/execution.py`). Docs-only naming drift, folded into R1's fix.
- **R6 — Unrelated.** `IBKRAdapter.get_positions()` has no caller and no test today (§1.2). Noted, untouched.
- **R7 — Related follow-up (naming).** `trading-intelligence-architecture.md` §18.5 defines `ExecutionMode` as `auto | manual` (who triggers placement). Decision #170 introduces `execution_mode` (`backtest | simulated | paper | live`, the capital mode). This design calls §18.5's concept *placement mode* to keep the two apart; §18.5 (and `system-design.md` §4.9's "Mode-aware" wording) should be reconciled to the same name when the manual-mode design is next touched. Left untouched here.
- **R8 — Related follow-up.** `BrokerAdapter` still declares `place_order`/`cancel_order`/`get_positions` (and `IBKRAdapter` still stubs them) even though, after decision #170, order placement belongs to the `OrderVenue` port. They stay unwired and unchanged; whether to remove them is a later decision for the build task or after it.
- **R9 — Related follow-up.** `system-design.md` §2 principle 1 says everything talks to a `BrokerAdapter` interface. For the execution path it will be an `OrderVenue`; the principle needs one clarifying sentence when the build lands. Left untouched here.
