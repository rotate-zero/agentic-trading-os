# Execution Engine & Portfolio State — Design (approved in principle; amended by decision #170)
**Owner:** Saqib
**Status:** Approved in principle by Saqib (2026-09-22), amended by decision #170. Decisions #171–#185 and the simulated protective/EOD deliveries implement the simulated entry and exit paths described in §§6.2–6.6. This delivery implements EX-12 option (a), the simulated auto-trade `OutcomeRecorder` in §6.7.1. The original inventory in §§1–2 and build prerequisites in §7.1 are historical. Decision #185 governs the executable simulated EOD path. Paper/live venues, manual mode and the Decision/Planning/Governor stages are not built. Sections labelled historical keep their original text. Baseline of the original design: `main` through decision #169. Fork labels `EX-n` are provisional, not D-numbers.
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
6. **Current status (items 1–5 are the original design summary).** *Built, `execution_mode = simulated` only:* the authorizer stub, entry-order Execution Engine, execution ledger, `SimulatedVenue`, Portfolio State, restart reconciliation and `main.py` wiring (#171–#178, #179–#180); Position Monitor-lite stop/target observation (#175, #178); durable position-bound stop/target closes (#184); simulated EOD flatten — monitor timer, exit-ledger state machine (migration `0016`), Execution worker, startup restore and read-only panel (#185 and its slug-only deliveries); `MarketClock` holiday/early-close coverage for 2026–2028 only; session-aware protective retries; and the EX-12 `OutcomeRecorder` for closed, strategy-attributed simulated auto trades (§6.7.1). *Not built:* a paper/live venue (`IBKRAdapter.place_order()` still raises `NotImplementedError`), broker-side protective orders, manual mode, Decision/Planning/Opportunity ranking and a real Governor rule engine.

---

## 1. Verified inventory of the downstream pipeline

**Historical.** This inventory (and §2) describes `main` through decision #167, before any execution code existed; its "not built" rows for the ledger tables, Portfolio State, Execution Engine and Position Monitor no longer hold — see §0 item 6 for current status. All rows were verified against `main` through decision #167 (`grep`/`view`, not recalled from docs).

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
| I2 | No **risk-increasing** order reaches a venue without a Governor-class authorization decision on record; the Execution Engine places, the Governor authorizes, and manual mode adds a human confirmation *after* authorization. Simulated stop/target exits follow EX-5's position-bound reduce-only resolution. | `base.py:BrokerAdapter` docstring; §4.9; §18.5 |
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

**As built (governor-approval-evidence — slug only, no decision number reserved).** An approval now also stores the accepted `Opportunity.evidence` in `trades.thesis["evidence"]`, inside the same transaction as the trade and its reservation. Nothing else persisted it before: `OpportunityCache` overwrites in memory and `TradeDecisionRecord` had no field for it. Scope is exactly that. It does not write entry snapshots, does not persist `Opportunity.confirmed_at` (no strategy sets it today), changes no authorization rule, and is not the `OutcomeRecorder` or an EX-12 decision.

* **Carry.** `AuthorizerStub` sets `TradeDecisionRecord.evidence = opportunity.evidence` only when the rule result is `approved`. A rejection never carries evidence, so a bad payload cannot change a rule outcome or stop a rejection from being audited.
* **Validation (`governor/evidence.py`, pure).** `detach_evidence()` returns a deep, detached copy or raises `EvidenceError`. It accepts a dict of `str` keys holding `str`/`int`/`float`/`bool`/`None`/`list`/`dict`. It refuses NaN and ±Infinity, non-`str` keys (JSON would silently turn `1` into `"1"`), tuples, sets, datetimes, Decimals, bytes, any other object, and nesting deeper than 32 levels (which also refuses reference cycles). Nothing is coerced, dropped, truncated or replaced with `{}`; an empty dict from a strategy is stored as given. By reading their source, all seven strategies build `evidence` from nested dicts of plain scalars (floats, ints, strings, `None`), so the strictness should cost nothing on the production path; a strategy that starts emitting anything else will have its approvals refused and logged, not silently repaired.
* **Where it is stored.** `trades.thesis["evidence"]`, next to `structural_*`/`final_*`/`confidence`/`setup_detected_at`. It is deliberately kept out of `decision_record`, whose shape and replay comparison are unchanged. An approval committed without evidence (`evidence=None`, i.e. any row written before this delivery) has no `"evidence"` key at all; it is never back-filled and never a stand-in `{}`. No migration: `thesis` is already JSONB.
* **Refusal.** `PostgresTradeLedger.commit_decision()` validates before opening its transaction. A bad payload raises `LedgerCommitError`, so no trade and no reservation exist, and because the engine publishes only after a successful commit, no `TradePlanned`/`GovernorDecision`/`OrderApproved` is emitted (the pre-existing "commit failed, publish nothing" path). A rejected record that carries evidence is also refused.
* **Replay.** An identical approval returns the existing identity (evidence compared structurally; `True` is not `1`, an int/float of equal value is the same number). Different, added or missing evidence on a replay raises `LedgerCommitError("conflicting evidence …")` and the stored evidence is untouched, including evidence offered against a legacy approval that has none.

```
OpportunityCreated ─► AuthorizerStub ─ rules 0-6 ─┬─ rejected ─► TradeDecisionRecord(evidence=None) ─► audit row, PlanRejected
                                                  └─ approved ─► TradeDecisionRecord(evidence=opportunity.evidence)
                                                                        │
                                             PostgresTradeLedger.commit_decision
                                                                        │
             detach_evidence()  ── EvidenceError ─► LedgerCommitError ─► nothing written, nothing published
                    │ ok (detached, JSON-safe copy)
                    ▼
      one transaction: trades(thesis{…, evidence}) + trade_reservations ─ COMMIT ─► TradePlanned ─► GovernorDecision ─► OrderApproved
      replay of the same accepted id: stored evidence equal? ─ yes ─► existing identity │ no ─► LedgerCommitError("conflicting evidence")
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

**As built (decision #181, `execution-orders-route`).** `GET /intelligence/execution-orders` is the first reader of the `orders` ledger anywhere in this codebase (confirmed by grep before writing it: `governor/`, `execution_engine/`, and `portfolio_state/` all import `Order` to WRITE it; nothing reads it back). Hard-scoped to `execution_mode == "simulated"` — not a query parameter, since `simulated` is the only mode the authorizer stub can produce today (EX-1) and the only mode any row can honestly carry until a real venue exists (§8). Optional exact `symbol` match; `limit` bounded `[1, 100]`, default 50; ordered by `orders.id` (the ledger's own monotonic primary key) descending. Curated fields only — order identity (`id`, `client_order_id`), `trade_id`, `symbol`, `side`, `position_effect`, `qty`, `status`, `execution_venue`, `exit_reason`, `reject_reason`, `created_at`, `updated_at` — not a full-row dump (`order_type`, `limit_price`, `venue_order_id` stay out). Same honest-empty convention as every route in this file: an empty table, or a `symbol` with no matches, is `{"orders": []}`, never an error. The synchronous SQLAlchemy read runs through `asyncio.to_thread` (`api/routes/scanner.py`'s established convention, decision `scanner-route-db-offload`). The older `GET /strategy-outcomes` and `GET /backtest-runs` routes now use the same worker-thread pattern; see `backtest-runner-design.md`. Observation only: no order placement or ledger write. The route's original delivery had no frontend consumer; the later read-only panel consumer is documented below. `orders.status` still has no WebSocket UI widget.

```
governor/ AuthorizerStub ──INSERT trades──┐
execution_engine/ ExecutionEngine ──INSERT/UPDATE orders, fills──┤
                                                                  ▼
                                                    orders  [ledger — authoritative, I12]
                                                                  │
                                                                  │ SELECT ... WHERE execution_mode = 'simulated'
                                                                  │   [AND symbol = :symbol]
                                                                  │   ORDER BY id DESC LIMIT :limit
                                                                  │   (asyncio.to_thread — worker thread, not the event loop)
                                                                  ▼
                                        GET /intelligence/execution-orders
                                            curated fields; observation only — no write path
                                                                  │ read on demand
                                                                  ▼
                                         caller (ops diagnostic / Execution panel)
```

**Internal flow (as built):**

```
GET /intelligence/execution-orders?symbol=...&limit=...
       │
       ▼
FastAPI query validation ── limit outside [1, 100] ──► 422  (rejected before any DB touch)
       │ limit valid (default 50), symbol optional
       ▼
await asyncio.to_thread(_fetch_execution_orders, symbol, limit)   ── event loop free while this runs
       │
       ▼
_fetch_execution_orders()  [worker thread — opens AND closes its own Session]
       SessionLocal() ──► filters = [execution_mode == 'simulated'] (+ symbol == :symbol if given)
                     ──► SELECT * FROM orders WHERE <filters> ORDER BY id DESC LIMIT :limit
                     ──► build curated dicts (id, client_order_id, trade_id, symbol, side,
                         position_effect, qty, status, execution_venue, exit_reason,
                         reject_reason, created_at, updated_at)
                     ──► session.close()  (finally — always, even on a query error)
       │
       ▼  list[dict] crosses back to the event loop
{"orders": [...]}   200 always; [] for an empty table or a non-matching symbol, never an error
```

**Frontend read path (as built, `execution-panel-order-history`).** The Execution
panel reads the route's unfiltered default result set when expanded and on
manual Refresh. This is the latest 50 simulated ledger rows by ID, with no
pagination or polling. It shows symbol, side, open/close effect, quantity,
status, venue, updated time, and any exit or rejection reason. The created
time is available on the time label's tooltip. Request failure, loading, and
an empty ledger have separate displays. The WebSocket activity feed remains
transient and independent: an event cannot establish that an order persisted,
and a ledger refresh never adds an event to that feed.

```
orders ledger ──► GET /intelligence/execution-orders ──► fetchExecutionOrders()
                      simulated only; default 50                │
                                                                 ▼
                                             ExecutionLifecyclePanel
                                             Recent simulated orders

Event Bus ──► orders.status WebSocket ──► useOrderLifecycle()
                                             │
                                             ▼
                                    WebSocket activity feed
```

```
panel collapsed ──► expand ──► mount RecentSimulatedOrders ──► loading
manual Refresh ──► refreshKey increment ────────────────────────┘
       │
       ▼
fetchExecutionOrders() ──► HTTP error ──► request failure message
       │ 200
       ├─ orders: [] ──► empty ledger message
       └─ rows ──► render in server order, keyed by ledger id
                   (symbol/side/effect/qty/status/venue/time/reasons)
collapse ──► unmount and ignore any late response
```

**As built (decision #183, `execution-fills-route`).** `GET /intelligence/execution-fills` is the first HTTP route
that exposes the `fills` ledger. `execution_engine/fill_ledger.py` has been
inserting real rows since `entry-lifecycle-wiring`, and internal consumers
already read them (Portfolio State's rebuild/reconciliation, the governor's
`PortfolioStateReader`, `fill_ledger.py`'s own dedupe lookups — confirmed by
grep) — but nothing an operator can reach showed what was actually
persisted. `Fill` carries no `execution_mode` column at all
(`models/execution_ledger.py`'s own module docstring: "a fill's mode is
derivable via its `orders` row"), so hard-scoping to `execution_mode ==
"simulated"` — same fixed, non-parameterized scope as `GET
/execution-orders`, same EX-1/§8 reasoning — is expressed by an `INNER
JOIN` to the owning `orders` row (`fills.client_order_id` is a NOT NULL
`FOREIGN KEY` into `orders.client_order_id`, so the join never silently
drops a row). `symbol`, similarly not a `Fill` column, is an exact match on
the joined order's `symbol`. `limit` is bounded `[1, 100]`, default 50,
same diagnostic-tail sizing as the orders route. Rows are ordered by
`fills.ledger_seq` descending — the ledger's own strictly-monotonic
`BigInteger Identity(always=True)` primary key, the same "ledger order" role
`orders.id` plays for the sibling route.

Curated fields: `ledger_seq`, `client_order_id`, `trade_id` and `symbol`
(both pulled from the joined `orders` row), `execution_venue` (`Fill`'s own
column — independent of `orders.execution_venue`: an order's venue is where
it was placed, a fill's is where it actually executed), `venue_fill_id`,
`qty`, `price`, `venue_ts`, `commission`, `anomaly`, `created_at`. **`price`
and `commission` are serialized as exact decimal strings, not JSON
numbers** — a deliberate, narrower departure from `GET /execution-orders`'
posture of leaving every field to FastAPI's default `jsonable_encoder`: both
are `Numeric(18, 6)` columns, and the default `Decimal`→float conversion can
misrepresent trailing precision for values a caller needs to treat as exact
money — the same reasoning `PositionFillReceipt`'s own docstring gives and
the same `str(value) if isinstance(value, Decimal)` convention
`portfolio_state/postgres.py`'s `_encode()` already uses on this schema.
`commission` stays `null`, never a fabricated `0`, when the venue never
reported one (I3). The synchronous read runs through a new
`_fetch_execution_fills()` helper via `asyncio.to_thread` — the same
`scanner-route-db-offload` convention `GET /execution-orders` already
follows. An empty table, or a `symbol` with no matches, returns
`{"fills": []}`, 200, never an error. Read-only: no fill is inserted, no
order status is advanced.

```
execution_engine/ ExecutionEngine ──INSERT/UPDATE orders──┐
execution_engine/fill_ledger.py ──INSERT fills, UPDATE orders.status──┤
                                                     ▼
                              orders + fills  [ledger — authoritative, I12]
                                     │                         │
                     (existing, internal)                (new, this delivery)
                     Portfolio State / governor          SELECT fills JOIN orders
                     reconciliation reads                  ON fills.client_order_id = orders.client_order_id
                                                           WHERE orders.execution_mode = 'simulated'
                                                           [AND orders.symbol = :symbol]
                                                           ORDER BY fills.ledger_seq DESC LIMIT :limit
                                                           (asyncio.to_thread — worker thread)
                                                                 ▼
                                                  GET /intelligence/execution-fills
                                                    curated fields; price/commission as decimal strings
                                                    observation only — no write path
                                                                 │ read on demand
                                                                 ▼
                                                        caller (ops diagnostic)
```

**Internal flow (as built):**

```
GET /intelligence/execution-fills?symbol=...&limit=...
       │
       ▼
FastAPI query validation ── limit outside [1, 100] ──► 422  (rejected before any DB touch)
       │ limit valid (default 50), symbol optional
       ▼
await asyncio.to_thread(_fetch_execution_fills, symbol, limit)   ── event loop free while this runs
       │
       ▼
_fetch_execution_fills()  [worker thread — opens AND closes its own Session]
       SessionLocal() ──► filters = [orders.execution_mode == 'simulated'] (+ orders.symbol == :symbol if given)
                     ──► SELECT fills.*, orders.trade_id, orders.symbol
                           FROM fills JOIN orders ON fills.client_order_id = orders.client_order_id
                           WHERE <filters> ORDER BY fills.ledger_seq DESC LIMIT :limit
                     ──► build curated dicts (ledger_seq, client_order_id, trade_id, symbol,
                         execution_venue, venue_fill_id, qty, price [str], venue_ts,
                         commission [str | null], anomaly, created_at)
                     ──► session.close()  (finally — always, even on a query error)
       │
       ▼  list[dict] crosses back to the event loop
{"fills": [...]}   200 always; [] for an empty table or a non-matching symbol, never an error
```

At the route delivery there was no frontend consumer — the orders route's
original delivery took the same posture before its panel consumer. The route
adds no order placement, ledger write, or schema migration.

**Frontend read path (as built, `execution-panel-fill-history`).** The Execution
panel mounts a separate "Recent simulated fills" section when expanded. It
fetches the route's unfiltered default 50 rows on mount and on that section's
manual Refresh. Rows remain in the server's descending `ledger_seq` order,
keyed by `ledger_seq`; the panel shows symbol, quantity, exact price string,
venue timestamp, known commission (exact string), and any anomaly. Unknown
commission is omitted rather than shown as zero. Loading, empty, and request
failure have distinct displays. Collapse unmounts the section and its
in-flight response is ignored. The persisted fill snapshot does not enter
the transient WebSocket activity feed and has no polling or action control.

```
fills JOIN orders ──► GET /intelligence/execution-fills ──► fetchExecutionFills()
 simulated only; default 50; ledger_seq DESC                │
                                                            ▼
                                       ExecutionLifecyclePanel
                                       Recent simulated fills

Event Bus ──► execution WebSocket ──► useOrderLifecycle()
                                           │
                                           ▼
                                  WebSocket activity feed
```

```
panel expands ──► mount RecentSimulatedFills ──► loading ──► fetchExecutionFills()
manual Refresh ──► refreshKey increment ───────────────────────────┘
       │
       ├─ request error ──► error message
       └─ 200 ──► fills: [] ──► empty ledger message
                  └─ rows ──► render in server order, keyed by ledger_seq
                               (symbol/qty/exact price/venue time/known fee/anomaly)
collapse ──► unmount and ignore any late response
```

**Frontend symbol filter (as built, `execution-fills-symbol-filter-implementation`;
decision number assigned at integration).** The route's exact `symbol` parameter
(decision #183) had no frontend caller. The "Recent simulated fills" section now
has a symbol box with Apply and Clear, and `fetchExecutionFills(symbol?)` sends
`?symbol=<encoded>` only when a symbol is applied; with none, the request is the
unfiltered default 50 as before. It follows the orders section's filter: the box
uppercases as typed, Apply and Enter trim and uppercase, and an empty value means
no filter (never `symbol=`). Refresh keeps the last applied symbol. A response for
a superseded filter is ignored (controls stay enabled mid-flight — see
`execution-panel-refresh-recovery` below). The
empty text distinguishes an empty ledger from "no fills for this symbol". The
section's state is independent of the orders section's, so neither filter
refetches or changes the other. The server still hard-scopes to simulated fills
and orders newest `ledger_seq` first; `price` and `commission` remain exact
strings. No backend, migration or trading behavior changed.

```
fills JOIN orders ──► GET /intelligence/execution-fills[?symbol=X] ──► fetchExecutionFills(symbol?)
 simulated only; exact symbol match; default 50; ledger_seq DESC            │
                                                                            ▼
                                                        RecentSimulatedFills (own filter state)
                                          independent of ──► RecentSimulatedOrders (own filter state)
```

```
type ──► symbolInput (uppercased)
Apply / Enter ──► normalizeSymbolFilter: trim + uppercase; "" ──► undefined
       │
       ▼ appliedSymbol changes ──► effect re-runs ──► loading ──► fetchExecutionFills(appliedSymbol)
Refresh ──► refreshKey++ ─────────────────────────────┘   (keeps appliedSymbol)
Clear ──► input "" + appliedSymbol undefined ──► unfiltered fetch
       │
       ├─ request error ──► error message
       └─ 200 ──► [] ──► "No simulated fills recorded yet." (no filter) / "...for X." (filtered)
                  └─ rows ──► render in server order, keyed by ledger_seq
superseded filter or collapse ──► cleanup marks the run inactive; late response ignored
```

**Refresh recovery (as built, `execution-panel-refresh-recovery`; frontend only, no decision
number assigned — slug only).** "Recent simulated orders", "Recent simulated fills", "Startup
status" and "Observed exit triggers" used to disable Refresh while their request was loading, so
one hung request left no way to ask for a fresh snapshot. The other sections (positions, recorded
exit requests, simulated outcome recording) already allowed it. All four now behave like them:

| Rule | As built |
|---|---|
| Refresh | Always enabled. Each click bumps that section's `refreshKey`; the effect cleanup marks the previous run inactive, so an older success **or** failure is discarded and can never replace the newer request's loading state, result or error. |
| Collapse / unmount | The same cleanup runs; a response arriving after collapse is ignored, and re-expanding mounts a fresh section that fetches again. |
| Orders / fills filter | Apply, Clear and the symbol box stay enabled mid-flight (needed so a hung *filtered* request can be replaced by a different or cleared filter). Refresh refetches the **applied** symbol, never the unapplied text in the box. Apply with an unchanged applied symbol issues no request (use Refresh). The two sections' filter state stays independent. |
| Filter match | A settled result carries the symbol it was requested for and is displayed only while that equals the applied symbol; otherwise the section reads as loading. This closes the render between Apply/Clear and its effect starting the new fetch, which previously showed the old rows (or "No simulated orders for <new symbol>.") for a result that was fetched for the previous filter. |
| States | Unchanged: loading, error, empty and populated stay distinct. A failed request is never shown as an empty ledger or as "Position Monitor unavailable". |
| Not added | No polling, no request cancellation (a superseded request still completes in the browser and its response is dropped), no shared or global state, no API-contract change. `api-client.ts` is untouched. |

```
                          ExecutionLifecyclePanel (frontend only; no backend, API or contract change)
 expand ──► mounts, per section, its own load state + refreshKey (no state shared between sections)

 StartupStatusLine ─────────► GET /health/execution-startup
 ObservedExitTriggers ──────► GET /intelligence/exit-intents
 RecentSimulatedOrders ─────► GET /intelligence/execution-orders[?symbol=X]   (appliedSymbol, own)
 RecentSimulatedFills ──────► GET /intelligence/execution-fills[?symbol=X]    (appliedSymbol, own)
   Refresh / Apply / Clear in one section re-run only that section's request
```

```
 per section (orders / fills shown; startup status and exit triggers are the same without a filter)

 Refresh ──► refreshKey++ ┐
 Apply ───► appliedSymbol ├─► effect cleanup: previous run.active = false
 Clear ───► appliedSymbol ┘        │
 collapse / unmount ───────────────┘ (cleanup only)
                                   ▼
                    new run: active = true; requested = appliedSymbol; settled = loading
                                   ▼
                           fetch(requested) ───────────────┐
                              │ resolves / rejects          │ (older runs may still resolve later)
                              ▼                             ▼
                  run.active ? settled = {requested, ready|error}     run.active false ──► dropped
                              ▼
        render: settled.symbol !== appliedSymbol ? loading : settled
                 ├─ loading  ──► "Loading …" (Refresh/Apply/Clear still enabled)
                 ├─ error    ──► "Could not fetch …: <message>"   (never the empty state)
                 └─ ready    ──► empty text (filter-aware) or rows
```

**As built (`execution-positions-route`; decision number assigned at integration).** `GET /intelligence/execution-positions` is the
third read-only HTTP view over the execution ledger, after `execution-orders`
(#181) and `execution-fills` (#183), and the first over `positions` — Portfolio
State's durable projection of `fills` (§6.5, §6.8). Internal code already reads
the table directly (Portfolio State's restore and Session API, startup
reconciliation, the Execution Engine's exit ledger — confirmed by grep; the
Position Monitor and governor see positions only through Portfolio State), but
no HTTP route exposed the persisted rows to an operator. `Position` carries its
own `execution_mode`, so unlike the fills route no join is needed: the read is
hard-scoped to `execution_mode == "simulated"` (not a parameter; same EX-1/§8
reasoning as the sibling routes, and `backtest`-mode rows are a different
population, I4). `symbol` is an exact match on `positions.symbol`; `limit` is
bounded `[1, 100]`, default 50.

**Ordering.** `opened_at` descending, then `position_id` descending. `opened_at`
is the venue timestamp of the opening fill and can be shared by several
positions; `position_id` is the primary key, so the pair is a strict total
order and repeated reads never reshuffle tied rows — including which tied rows
survive a `limit` that lands inside a tie. The tie-break is deterministic but
**not chronological**: `position_id` is a random Python-side `uuid4`, so among
rows with equal `opened_at` the order is stable but arbitrary. Nothing else on
the table is both unique and time-ordered; a sequence column would be a schema
change, outside this route's scope. The table's only secondary indexes are
`(symbol, status)` and `trade_id`, so the `opened_at` sort is a scan of the
mode/symbol-filtered rows — acceptable at this ledger's diagnostic volume (one
concurrent position per EX-4), and deliberately not fixed with a migration here.

**Curated fields:** `position_id`, `trade_id`, `symbol`, `side`, `qty`, `status`,
`avg_price`, `stop`, `target`, `opened_at`, `closed_at`, `realized_pnl`.
Semantics are exactly what Portfolio State persists (§6.5 "Accounting"), not
re-derived by the route: `qty` is the quantity **currently held** (decreases on
reductions, `0` once `closed`); `avg_price` is the weighted-average **entry**
cost (adds re-average it, reductions do not move it) — the column name is kept
rather than renamed `entry_price`, matching the sibling routes' raw-column
naming; `status` is `open`, `closing` (partially reduced) or `closed`;
`realized_pnl` is the lifetime **gross** realized amount (profit − loss, before
commissions, which Portfolio State tracks separately and this route does not
expose). `avg_price`, `stop`, `target` and `realized_pnl` are `Numeric(18, 6)`
and are returned as **exact decimal strings**, the same narrower-than-default
exception `execution-fills` documents; `stop`, `target`, `closed_at` and
`realized_pnl` are JSON `null` when unset — never omitted, never `0` (I3:
"no stop recorded" and "nothing realized yet" are not zero). `execution_mode`
(constant by the filter), `execution_venue` and `exit_attempt` are left out.

**A persisted-row view, not a live portfolio.** Rows come straight from the
table, not from `PortfolioState.get_snapshot()`'s in-process cache: no mark
price, unrealized P&L, exposure or daily total is computed or returned. A fill
that is committed but not yet applied by Portfolio State is not reflected until
it is (positions are a projection of `fills`, §6.5). The synchronous read runs
through a new `_fetch_execution_positions()` helper via `asyncio.to_thread`,
the same `scanner-route-db-offload` convention as the sibling routes. An empty
table, or a `symbol` with no matches, returns `{"positions": []}`, 200. Read-only:
no position is created, reduced or closed, no exit is placed.

```
execution_engine/ ── INSERT orders / fills ──► orders + fills  [ledger — authoritative, I12]
                                                       │
                              Portfolio State  (single writer of `positions`)
                              apply fill ─► accounting.py ─► COMMIT position + receipt + cursor
                                                       │
                                                       ▼
                                              positions  [durable projection of fills]
                       ┌───────────────────────────────┴─────────────────────────────┐
          (existing, internal)                                        (new, this delivery)
          restore / reconciliation /                     SELECT positions
          exit_ledger read the rows                        WHERE execution_mode = 'simulated'
                                                             [AND symbol = :symbol]
                                                           ORDER BY opened_at DESC, position_id DESC
                                                           LIMIT :limit   (asyncio.to_thread — worker thread)
                                                                       ▼
                                                     GET /intelligence/execution-positions
                                                       curated fields; money as decimal strings;
                                                       observation only — no write path
                                                                       │ read on demand
                                                                       ▼
                                                             caller (ops diagnostic)

not read here: PortfolioState.get_snapshot() cache · marks · unrealized P&L · exposure
```

**Internal flow (as built):**

```
GET /intelligence/execution-positions?symbol=...&limit=...
       │
       ▼
FastAPI query validation ── limit outside [1, 100] or non-integer ──► 422  (before any DB touch)
       │ limit valid (default 50), symbol optional
       ▼
await asyncio.to_thread(_fetch_execution_positions, symbol, limit)   ── event loop free while this runs
       │
       ▼
_fetch_execution_positions()  [worker thread — opens AND closes its own Session]
       SessionLocal() ──► filters = [positions.execution_mode == 'simulated'] (+ positions.symbol == :symbol)
                     ──► SELECT positions.* WHERE <filters>
                           ORDER BY opened_at DESC, position_id DESC LIMIT :limit
                     ──► build curated dicts
                           avg_price / stop / target / realized_pnl : str(Decimal), or None if NULL
                           position_id / trade_id / opened_at / closed_at : native types (jsonable_encoder)
                     ──► session.close()  (finally — always, even on a query error)
       │
       ▼  list[dict] crosses back to the event loop
{"positions": [...]}   200 always; [] for an empty table or a non-matching symbol, never an error
```

At the route delivery there was no frontend consumer — that delivery added no
order placement, ledger write, schema migration, position accounting, exit
placement, or trading-control change. The panel consumer is documented next.

**Frontend read path (as built, `execution-panel-position-history`; decision
number assigned at integration if one is needed).** The Execution panel mounts a
separate "Recent simulated positions" section when expanded. A typed
`fetchExecutionPositions()` (`api-client.ts`, wire types
`ExecutionPositionWireShape` / `ExecutionPositionsWireShape`) requests the bare
route, so the server's default 50 rows, and the section fetches on mount and on
its own manual Refresh. It has no `symbol` filter, no `limit` argument and no
polling. Rows render in the server's order (`opened_at` descending, `position_id`
tie-break) and are keyed by `position_id`; the panel never re-sorts them.

Each row shows symbol and side, status (`open`, `closing`, `closed`), current
quantity, average entry price (labelled "Avg entry"), stop and target when
known, and gross realized P&L when known. `avg_price`, `stop`, `target` and
`realized_pnl` stay exact decimal strings end to end, so they render verbatim
(`185.100000`), never through `Number`. A `null` stop, target or realized P&L
is omitted rather than shown as zero or a dash (I3: unknown is not zero);
a known `0.000000` P&L is shown, in the neutral tone. Sign is read from the
string only to pick the gain/loss colour. A closed position whose quantity is
zero reads "Qty 0 — closed, nothing held" and is dimmed; a `closed` row that
somehow carries a non-zero quantity shows its real quantity and is not called
flat. The section states that it is a persisted snapshot, not the live portfolio,
that quantity is what is currently held, and that P&L is gross, before
commissions.

Loading, empty ("No simulated positions recorded yet.") and request failure have
distinct displays. Each effect run owns an `active` flag that its cleanup clears,
so a response that arrives after collapse/unmount, or after a newer Refresh has
superseded it, is discarded — success and failure alike. Refresh here stays
enabled while a request is in flight, so a slow request can be superseded rather
than waited out (the orders, fills, startup-status and exit-trigger sections now
follow the same rule — `execution-panel-refresh-recovery` above).

The snapshot is independent of everything else in the panel: it is not merged
into the WebSocket activity feed (`useOrderLifecycle`), does not read or write
`PortfolioState.get_snapshot()` or the live World View portfolio, computes no
mark price, unrealized P&L or exposure, and Refreshing it does not refetch the
sibling sections. No backend, migration, ledger-write or trading-control change.

```
positions [durable projection of fills, Portfolio State the single writer]
   │  SELECT ... simulated ... ORDER BY opened_at DESC, position_id DESC LIMIT 50
   ▼
GET /intelligence/execution-positions ──► fetchExecutionPositions()   (no query string)
 money as decimal strings; null = unknown                │
                                                         ▼
                                    ExecutionLifecyclePanel
                                    Recent simulated positions  (own load state, own Refresh)

Event Bus ──► execution WebSocket ──► useOrderLifecycle() ──► WebSocket activity feed
PortfolioState in-process snapshot ──► live World View portfolio
        (neither is read by, nor merged into, the positions section)
```

```
panel expands ──► mount RecentSimulatedPositions ──► loading ──► fetchExecutionPositions()
manual Refresh ──► refreshKey++ ─► cleanup marks previous run inactive ─┘   (enabled mid-flight)
       │
       ├─ request error ──► error line ("Could not fetch simulated positions: ...")
       └─ 200 ──► positions: [] ──► "No simulated positions recorded yet."
                  └─ rows ──► render in server order, keyed by position_id
                       per row: symbol · side · status · Qty (or "Qty 0 — closed, nothing held")
                                · Avg entry · [Stop · Target if non-null] · [Gross realized P&L if non-null]
collapse / unmount ──► cleanup marks run inactive; a late response or late failure is ignored
```

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

**Tick eligibility at the venue boundary (as built, `simulated-venue-invalid-tick-guard`; no new decision number).** `SimulatedVenue.ingest_tick()` is the shared boundary for direct injection and parsed `PriceUpdated` EventBus delivery. It rejects a price unless it is a finite, strictly positive number, and rejects an exchange timestamp unless it is a usable timezone-aware `datetime`. This is necessary even for EventBus delivery: `PriceUpdated`'s schema accepts non-finite/nonpositive prices and naive timestamps. The check runs before the venue reads pending orders, checks a limit crossing, advances a partial-fill plan, constructs an update, or invokes a callback. A rejected tick leaves every pending order on its symbol unchanged: status, filled/leaves quantity, plan index, fill history and numbering, and pending membership. The next valid qualifying tick uses the same next tranche and fill ID it would have used without the invalid tick.

```
direct ingest_tick() ──────────────────────────────────────┐
PriceUpdated ─► EventBus ─► parse venue payload ──────────┤
                                                           ▼
                                            SimulatedVenue.ingest_tick()
                                                           │
                                              tick eligibility check
                                                │           │
                                          reject│           │accept
                                                ▼           ▼
                                       bounded warning   pending orders by symbol
                                       no book change     ─► acceptance-order matching
                                       no callback            ─► _apply_fill()
                                                               ─► OrderUpdate callback
                                                               ─► Execution / ledger accounting
```

```
ingest_tick(symbol, price, exchange_ts)
  ├─ non-finite / nonpositive price ──────────────────────► return unchanged
  ├─ naive / unusable exchange_ts ────────────────────────► return unchanged
  └─ eligible tick
       └─ for each pending order on symbol, in acceptance order
            ├─ no market/limit match ─────────────────────► keep pending
            └─ match ─► consume next planned quantity ─► assign :f<n>
                         └─ append fill, update status/pending, dispatch callback
```

Invalid input produces at most one venue-wide warning per 60 monotonic seconds, without a per-tick traceback. An unparseable bus payload or missing envelope symbol is also ignored through this bounded warning path; EventBus processing continues. Valid offset-aware `exchange_ts` values are reported unchanged, without normalization. This input guard adds no tick recency limit, arrival deduplication, out-of-order filter, timestamp-versus-order-acceptance comparison, or fill-session policy. The existing placement session guard and market/limit matching rules remain as before.

### 6.5 Portfolio State Engine (`portfolio_state/`)

**Built in decisions #172–#174; integration remains partial.** Portfolio State owns position accounting, in-flight exposure, marks, and daily realized amounts. It is a cache over the ledger, never the record (I5/I12). Decision #173 revises #172's package with Saqib's approval; the same database-free arithmetic now serves both the event worker and the retained Session/reconciliation API.

**Holding period is unrestricted by this component.** Day trading is the primary use, but positions may remain open across days, months, and restarts. No daily position reset, forced EOD exit, or maximum holding period exists here (the simulated EOD flatten in §6.6 is Position Monitor/Execution exit policy, not Portfolio State behavior). Exit policy belongs to Position Monitor. A position's UUID is persisted at opening, survives adds/reductions/restarts, and is retired at full closure. A later opening in the same symbol receives a new UUID.

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

**At decision #173, not yet wired:** complete status notifications, authorizer/order/fill persistence integration, governor/World View adapters, live startup, position-monitor exits, and OutcomeRecorder recovery. Later deliveries wired entry persistence, World View reads, startup, and simulated stop/target exits; OutcomeRecorder and complete status notifications remain open. Adapter tests exercise real PostgreSQL transactions, concurrent writers/consumers, restart accounting, migration safety, and the event worker. They do not prove durable event delivery. Details: later as-built notes in §§6.3–6.6 and `TESTING.md`.

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
                                      │                 │
                      ordered pending │                 │ diagnostic intent
                                      ▼                 ▼
                              Execution worker   GET /intelligence/exit-intents
                                      │                     │ panel read / Refresh
                                      ▼                     ▼
                              PostgresExitLedger   ExecutionLifecyclePanel
                                      │             (observed trigger view)
                                      ▼
                              SimulatedVenue ─► real fills ─► Portfolio State
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
lifespan startup: rebuild/reconcile venue + ledger
       ├─ discrepancy/failure ──► app.state.position_monitor = None
       └─ clean ──► Portfolio State refresh ─► advance persisted EOD expiry
                          └─ restore original/fallback monitor slots
                                └─ bind/start Execution, start PositionMonitor
                                      └─ app.state.position_monitor = monitor

received PriceUpdated/CandleClosed ──► held-symbol filter ──► monitor queue/worker
       └─ fresh PositionView read ──► stop/target or pulse-driven EOD
              └─ slot PENDING ──► callback wakes Execution
                    └─ observe_exit commit ──► ACKNOWLEDGED
                       DB error ──► pending retry; EOD window closed ──► EXPIRED

GET /intelligence/exit-intents?symbol=... ──► monitor.get_exit_intents(symbol)
       └─ sort by symbol, trigger_ts, position_id ──► observed_only list
          (running + empty is distinct from unavailable + empty)

ExecutionLifecyclePanel expands / Refresh ──► typed GET /intelligence/exit-intents
       └─ unavailable / running-empty / fetch error / observed rows
          (no order or position-closure event is inferred)

lifespan shutdown ──► clear app reference ──► stop monitor timer/subscriptions ─► stop bus
                    └─ drain Execution ──► stop Portfolio State / disconnect venue
```

The diagnostic exposes position ID, symbol, side, quantity, reason, trigger price,
and trigger timestamp. It is a point-in-time read of received events, not an order,
fill, or closed position. New observations are in memory; committed request slots
are restored from the ledger after clean startup reconciliation. No price/candle
event means no new observation. The monitor alone
does not place an order; its observation callback wakes Execution's worker.
The frontend reads this snapshot when the
Execution panel expands and on manual refresh, in a section separate from its
existing order-lifecycle event list; it does not poll or subscribe to another
WebSocket channel. The `observed_only` label describes the route's own read
surface, not whether an observation was handed to Execution.
EOD observations also reach the durable handoff; EX-12 is implemented for simulated auto trades in §6.7.1.

**Trigger recovery as built (`position-monitor-trigger-recovery`).** The earlier
held-symbol filter in the diagram above now applies only to candles. Every valid
`PriceUpdated` enters an in-memory journal before a Portfolio State read. A
`OrderFilled` notification marks its symbol as fill-pending until its position
becomes visible; it is a diagnostic hint, while the ledger remains the source of
accounting truth. Portfolio State retries transient synchronization failures on
its existing worker with one coalesced retry task and 0.25–5 second backoff. It
does not clear unresolved order IDs or treat a nonexistent order as healthy.

```
Event Bus PriceUpdated ──► Position Monitor journal ──► monitor worker
Event Bus CandleClosed ───────────────────────────────► monitor worker
Event Bus OrderFilled ──► fill-pending marker          │
                                                      │ PositionReader snapshot
Postgres fill ledger ──► Portfolio State worker ◄─────┘
     │                   │ failed read: bounded retry   │ first touch after visibility
     │                   └─ committed snapshot ─────────┤
     └─ durable receipt ───────────────────────────────► Execution exit handoff
                                                          │
                                                          ▼
                                                     exit_requests / close order
GET /intelligence/exit-intents ◄── intents + protection diagnostics
```

```
subscriber: validate tick ─► append(symbol, arrival seq, exchange ts, price, monotonic arrival)
                             └─ coalesced replay wake-up
worker: queued candle / EOD pulse / replay wake-up ─► sequence boundary
       └─ read visible positions ─► for each position, replay eligible ticks
          with seq <= boundary and exchange_ts >= opened_at, in arrival order
          ├─ first stop/target touch ─► register PENDING observation ─► advance cursor
          ├─ no touch ─► advance cursor
          └─ read/register failure ─► retain cursor and tick for next wake-up
       └─ EOD pulse uses the latest eligible tick inside its own boundary
```

The limits are configurable and validated: 100 symbols, 2,000 ticks per
symbol, and 60 seconds of monotonic retention for unowed ticks are starting
defaults, not measured guarantees. A tick is owed while a snapshot is
unavailable, its symbol has a fill-pending marker, or a visible position has
not yet safely evaluated it. The latest post-opening EOD label and the latest
label before a queued pulse also remain owed while that open position has no
observation. Owed ticks do not expire solely with time, but capacity may evict
them. Per-position cursors and slots retire when a position disappears;
fill markers are limited to the symbol capacity. Incident history retains 100
records and cumulative cause counts preserve evidence after recovery.

The additive `protection_diagnostics` object reports current snapshot
availability, journal usage, fill-pending symbols, overflow, symbol-capacity
loss, invisible fills, and a sticky `lost_window` flag. An absent monitor is
`unavailable`. Loss of a retained window means the monitor can no longer prove
which touch came first for a position that later appears. An in-memory journal
cannot recover process downtime or guarantee protection after overflow; for
retained eligible ticks, replay produces the first actionable touch in arrival
order using its original exchange timestamp. This visibility change adds no
entry block, external alert, emergency liquidation, or broker action.

**As built (`simulated-protective-exits`).** A stop or target observation is
handed to Execution Engine's queue. `PostgresExitLedger` commits one durable
`exit_requests` row for the position. It cancels any unfinished entry first,
waits for committed fill receipts, then reserves one position-linked close
order for the committed remaining quantity. The order ID is
`<trade_id>:exit:<attempt>`; `positions.exit_attempt` advances in the same
transaction. Rejected or cancelled closes retry after a bounded delay with
a new ID and the remaining quantity, but only while `MarketClock` says regular
hours are open: outside them a stop/target close (including an EOD row's fallback)
is retained and neither reserved nor dispatched until the next regular open
(`simulated-protective-session-retry`, below). The existing fill ledger and Portfolio
State worker apply fills; full closure marks the trade closed. EOD flatten
uses the same position-bound close path with stored window, fallback and
dispatch claim. Paper/live execution is unaffected.

```
PriceUpdated / CandleClosed -> Position Monitor -> stop/target ExitIntent
                                               |            |
                                               |            v
                                               |     Execution Engine queue
                                               |            |
                                               |            v
                                               |     PostgresExitLedger -> exit_requests
                                               |            |             + orders.position_id
                                               |            v
                                               |     SimulatedVenue close order
                                               |            |
                                               +---- OrderFilled -> Portfolio State
                                                                  -> position/trade closure
```

```
observe(position_id) -> commit request once
       -> prepare: unfinished entry? cancel and collect late fills
       -> pending fill receipt? wait
       -> active close? wait or reuse approved reservation
       -> reserve remaining qty + increment attempt, COMMIT
       -> confirm matching position and no new entry/pending fill
       -> place close -> submitted/rejected status
       -> rejection/cancellation: retry_after -> next attempt
```

Startup applies committed fills before reconciliation. Reconciliation
validates approved exit reservations but does not send them; the execution
worker rechecks immediately before placement. A fresh simulated venue has
no position book. If a persisted position is open, startup detects the
quantity discrepancy and leaves execution off, so durable observation does
not imply protection across that restart.

**As built (`execution-exit-requests-route`; decision number assigned at integration).**
`GET /intelligence/execution-exit-requests` is a read-only HTTP view of the
persisted `exit_requests` rows above — the fourth ledger read route after
`execution-orders` (#181), `execution-fills` (#183) and `execution-positions`
(§6.3). Until now nothing outside `PostgresExitLedger` read that table.

**It is not `/intelligence/exit-intents`.** The two answer different questions
and never share a source:

| | `GET /intelligence/exit-intents` | `GET /intelligence/execution-exit-requests` |
|---|---|---|
| Source | the running Position Monitor's in-memory intents | PostgreSQL `exit_requests`, joined to `positions` |
| Survives restart | no | yes |
| Depends on the monitor | yes (`monitor_status` running / unavailable) | no — works with no monitor, and does not read it |
| Includes EOD observations | yes, including a restored committed original after clean recovery (diagnostic only) | a durable *original* `eod_flatten` request with stored window, expiry and optional fallback (`simulated-eod-exit-request-visibility`) |
| Row means | "the monitor saw or restored a trigger this process" | "Execution durably recorded an exit observation for this position" |
| Envelope | `monitor_status`, `intent_status: observed_only`, `exit_intents` | `exit_requests` only |

**Scope and shape.** Hard-scoped to `Position.execution_mode == 'simulated'`
through the join (`exit_requests` has no mode, symbol or quantity column; it is
keyed one-to-one by `position_id`, so the inner join drops nothing). Optional
exact `symbol` (on the position, no case-folding); `limit` bounded `[1, 100]`,
default 50. Ordered by `trigger_ts` descending, then `position_id` descending —
`position_id` is the primary key, so the pair is a strict total order and a
`limit` inside a tie keeps the same rows every read; the tie-break is stable but
**not chronological** (random `uuid4`).

**Curated fields:** from the request — `position_id`, `exit_reason` (`stop` |
`target` | `eod_flatten`, always the ORIGINAL request's reason), `trigger_price`
(exact decimal string, the `Numeric(18, 6)` value), `trigger_ts`, `retry_after`
(JSON `null` when unset), `created_at`; from the position — `symbol`, and the
position's **current** `position_status` (`open` | `closing` | `closed`) and
`remaining_qty`. Status and quantity are read at request time, not as of the
trigger. Six EOD/fallback fields (added by `simulated-eod-exit-request-visibility`,
below) are always present and are `null` on `stop` / `target` rows.

**What the route does not claim.** It returns no order status, no
"protected" flag and no retry outcome, and infers none: a request row is a
recovery record (see above), `retry_after` is only the stored timestamp set
when a close was rejected or cancelled (it does not show a retry happened or
succeeded), and a position's status does not say which order closed it.
`eod_expired_at` ends EOD placement eligibility only, and a stored fallback is an
observation only (details below). Orders and fills remain on their own routes.
An empty table or non-matching symbol returns `{"exit_requests": []}`, 200.

```
Position Monitor ──► stop/target ExitIntent (in memory) ──► GET /intelligence/exit-intents
       │                                                    (unchanged; observed_only)
       ▼
Execution Engine ──► PostgresExitLedger.observe() ──► exit_requests  [durable, PK position_id]
                                                          │  set retry_after on rejected/cancelled close
                                                          │
                              positions (execution_mode, symbol, status, qty)
                                                          │ INNER JOIN on position_id
                                                          ▼
                              GET /intelligence/execution-exit-requests   (new, read-only)
                                                          │ read on demand
                                                          ▼
                                                 caller (ops diagnostic)

not read here: the Position Monitor · orders · fills · receipts · any retry or protection state
```

```
GET /intelligence/execution-exit-requests?symbol=...&limit=...
       │
       ▼
FastAPI query validation ── limit outside [1, 100] or non-integer ──► 422  (before any DB touch)
       ▼
await asyncio.to_thread(_fetch_execution_exit_requests, symbol, limit)   ── event loop free
       ▼
_fetch_execution_exit_requests()  [worker thread — opens AND closes its own Session]
       SessionLocal() ──► filters = [positions.execution_mode == 'simulated'] (+ positions.symbol == :symbol)
                     ──► SELECT exit_requests, positions
                           JOIN positions ON positions.position_id = exit_requests.position_id
                           WHERE <filters>
                           ORDER BY exit_requests.trigger_ts DESC, exit_requests.position_id DESC LIMIT :limit
                     ──► build curated dicts
                           trigger_price : str(Decimal)
                           retry_after : native datetime or None
                           position_status / remaining_qty : positions.status / positions.qty
                           eod_flatten_at / eod_close_at / eod_expired_at : native datetime or None
                           fallback_reason : str or None ; fallback_trigger_ts : native datetime or None
                           fallback_trigger_price : str(Decimal) or None
                     ──► session.close()  (finally — always)
       ▼  list[dict] crosses back to the event loop
{"exit_requests": [...]}   200 always; [] for an empty table or a non-matching symbol
```

The route delivery had no frontend consumer, migration, exit-policy or
placement change; the read-only panel consumer is documented next.

**Frontend read path (as built, `execution-panel-exit-requests`; decision number
assigned at integration if one is needed).** The Execution panel mounts a separate
"Recorded exit requests" section when expanded, directly after "Observed exit
triggers". A typed `fetchExecutionExitRequests()` (`api-client.ts`, wire types
`ExecutionExitRequestWireShape` / `ExecutionExitRequestsWireShape`) requests the
bare route, so the server's default 50 rows, and the section fetches on mount
(panel expansion) and on its own manual Refresh. It has no `symbol` filter, no
`limit` argument, no polling and no action control. Rows render in the server's
order (`trigger_ts` descending, `position_id` tie-break) and are keyed by
`position_id`; the panel never re-sorts them.

Each row shows the symbol and the reason ("Stop" / "Target" / "EOD"), the trigger time,
the exact trigger price, the position's *current* status (`open`, `closing`,
`closed`) and remaining quantity, and — only when the stored value is non-null —
`Retry after <time>`. `trigger_price` stays the server's exact decimal string
(`10.123400`, `1E+3`) and is never passed through `Number`. A `null` `retry_after`
is omitted, not shown as "none" or as a completed retry. The section states that
a recorded request does not prove an order was placed or that the position is
protected, and that status and remaining quantity are current, not as of the
trigger. `retry_after` is displayed as the stored timestamp only; the UI infers
no retry outcome.

Loading, empty ("No exit requests recorded yet.") and request failure have
distinct displays. Each effect run owns an `active` flag that its cleanup clears,
so a response or failure that arrives after collapse/unmount, or after a newer
Refresh has superseded it, is discarded. As in the positions section, Refresh
stays enabled while a request is in flight so a slow request can be superseded.

It is deliberately a different surface from its neighbours. "Observed exit
triggers" reads `GET /intelligence/exit-intents` (the running monitor's in-memory,
`observed_only` view, empty after a restart); this section reads only the durable
`exit_requests` rows and never the monitor. It reads no orders or fills, so it
cannot show which order closed a position or at what price; those stay in their
own sections. An `eod_flatten` row additionally shows its stored window, expiry and
fallback (see `simulated-eod-exit-request-visibility` below). Refreshing it refetches no sibling section, and it is not merged
into the WebSocket activity feed. No backend, migration, ledger-write, exit-policy
or trading-control change.

```
Position Monitor ──► ExitIntent (memory) ──► GET /intelligence/exit-intents ──► "Observed exit triggers"
       │                                       (unchanged; observed_only)         (separate section)
       ▼
Execution Engine ──► PostgresExitLedger.observe() ──► exit_requests  [durable, PK position_id]
                                                          │  INNER JOIN positions (simulated only)
                                                          ▼
                     GET /intelligence/execution-exit-requests ──► fetchExecutionExitRequests()   (no query string)
                       trigger_price = exact decimal string                     │
                                                                                ▼
                                                             ExecutionLifecyclePanel
                                                             "Recorded exit requests"  (own load state, own Refresh)

not read by this section: /exit-intents · orders · fills · the WebSocket feed · any retry or protection state
```

```
panel expands ──► mount RecordedExitRequests ──► loading ──► fetchExecutionExitRequests()
manual Refresh ──► refreshKey++ ─► cleanup marks previous run inactive ─┘   (enabled mid-flight)
       │
       ├─ request error ──► error line ("Could not fetch recorded exit requests: ...")
       └─ 200 ──► exit_requests: [] ──► "No exit requests recorded yet."
                  └─ rows ──► render in server order, keyed by position_id
                       per row: symbol · Stop|Target|EOD · trigger time · Trigger price <exact string>
                                · Position <status> · remaining qty <n>
                                · [EOD block if exit_reason = eod_flatten — see below]
                                · [Retry after <time> if non-null]
                       header note: a recorded request does not prove an order was placed
                                    or that the position is protected
collapse / unmount ──► cleanup marks run inactive; a late response or late failure is ignored
```

**As built (`simulated-eod-exit-request-visibility`; decision #185 already exists, no new
number).** The existing route and "Recorded exit requests" section now show a durable
original `eod_flatten` request, its stored placement window and expiry, and the optional
first stop/target fallback, using the columns of migration `0016` exactly
(`simulated-eod-ledger-handoff`, verified against `main` `c1d09e4`). Read path only: no
model, migration, ledger, worker, monitor or trading-control change, and no new route.
Query, filters, ordering, `limit` bounds, exact-decimal strings and the
`asyncio.to_thread` read are unchanged. **The ledger that writes these rows is still not
wired into a running system**, so today the new fields are `null` and no `eod_flatten` row
exists outside tests; the read path is verified against hand-inserted rows only.

| Field (all always present) | Type | Meaning when non-null | Null means |
|---|---|---|---|
| `exit_reason` | `stop` \| `target` \| `eod_flatten` | the ORIGINAL request's reason; never replaced by a fallback | (never null) |
| `eod_flatten_at`, `eod_close_at` | UTC timestamp | the stored placement window `[flatten_at, close_at)`, ledger-derived and immutable | not an EOD request |
| `eod_expired_at` | UTC timestamp | when placement **eligibility** durably ended | no expiry recorded (see below) |
| `fallback_reason` | `stop` \| `target` | the FIRST later protective observation, immutable | no fallback stored (the three fallback fields move together) |
| `fallback_trigger_price` | exact decimal string | that observation's price | as above |
| `fallback_trigger_ts` | UTC timestamp | that observation's time | as above |

**What these fields do not say.** `eod_expired_at` is recorded lazily, when the ledger next
touches the request, so `null` does **not** mean the window is still open and the route
never compares the window with a clock. A recorded expiry means only that EOD *placement
eligibility* ended: it is not proof an order was cancelled, that any order existed or that
the position closed (`position_status` / `remaining_qty` stay the position's current
state, and orders/fills stay on their own routes). A stored fallback is an observation, not
proof of a working protective order. `trigger_price` / `trigger_ts` (and therefore the
ordering) remain the original request's; a later fallback never reorders a row.

**UI.** For an `eod_flatten` row the section adds a small block: "EOD placement window
`<flatten>` → `<close>`"; either "Placement eligibility ended `<time>`" or "No expiry
recorded"; and either "First stop|target observation stored: price `<exact string>` at
`<time>`" or "No stop or target fallback stored". A missing field from an older backend
renders as "not recorded" rather than being guessed. Stop/target rows render exactly as
before, and the section's note now states the expiry and fallback caveats above. Still no
polling and no control besides the existing Refresh.

```text
exit_requests (migration 0016)         positions
  original reason / trigger_*            execution_mode, symbol, status, qty
  eod_flatten_at, eod_close_at,               │
  eod_expired_at, fallback_*                  │  INNER JOIN on position_id, simulated only
        └──────────────────────┬──────────────┘
                               ▼
        GET /intelligence/execution-exit-requests   (same query, order, bounds, worker thread)
          + 6 nullable EOD/fallback fields, passed through as stored, nothing derived
                               ▼
        fetchExecutionExitRequests() ──► ExecutionLifecyclePanel ► "Recorded exit requests"
                                            stop/target row: unchanged
                                            eod_flatten row: + window / expiry / fallback block

written by (NOT wired today): PostgresExitLedger.observe_exit() / advance_eod_expiry()
not read here: orders · fills · Position Monitor · any clock
```

```text
row build (worker thread):  request, position ─► existing 9 fields (unchanged)
   ├─ eod_flatten_at / eod_close_at / eod_expired_at ─► native datetime or None
   ├─ fallback_reason ─► str or None
   ├─ fallback_trigger_price ─► None, else str(Decimal)     (never float)
   └─ fallback_trigger_ts ─► native datetime or None

panel row:  exit_reason != eod_flatten ─► stop/target row exactly as before
            exit_reason  = eod_flatten ─► EodRequestDetail
                 window       : both bounds set ─► "flatten → close"   else "not recorded"
                 expiry       : eod_expired_at set ─► "Placement eligibility ended <t>"
                                else ─► "No expiry recorded"           (no clock consulted)
                 fallback     : reason+price+ts set ─► "First <stop|target> observation stored: ..."
                                else ─► "No stop or target fallback stored"
```

**As built (`simulated-eod-monitor-handoff`; decision #185 already exists, no new number).**
Position Monitor owns the EOD timer, a validated tick cache and a
pending/acknowledged observation handoff. *Delivery-time limits (historical, superseded):*
when this slice landed it was the monitor half only — nothing consumed the EOD observation,
and `exit_ledger.py`, `execution_engine/engine.py`, the migration, `main.py`, reconciliation
and the exit-requests route/panel were unchanged. The later deliveries
`simulated-eod-ledger-handoff` (exit ledger, migration `0016`),
`simulated-eod-exit-request-visibility` (route and panel) and
`simulated-eod-flatten-integration` (Execution worker, `main.py` restore) added those
halves; the current end-to-end state is in the subsections below. This delivery superseded two earlier
statements in this section for the monitor only: `_evaluate()` no longer produces
`eod_flatten` from an event timestamp (stop/target only), and one `ExitIntent` per position
is no longer the control latch (two slots are; the diagnostic map is unchanged).

```
PriceUpdated ─► Event Bus ─► _on_market_event (cheap, sync)
                               ├─ every tick: seq = next arrival number
                               ├─ symbol NOT held ─► tick cache offer ─► drop
                               └─ symbol held ─► queue: _QueuedEvent(seq, envelope)
CandleClosed ─► Event Bus ─► same callback ─► held? ─► queue (never cached, never EOD)

timer task (1 s, injectable/disabled) ─► enqueue_pulse() ─► SAME queue: _Pulse(seq)
                                                         (coalesced: one queued at a time)
                                    single worker, arrival order
                                                │
        ┌───────────────────────────────────────┴──────────────────────────────┐
   _QueuedEvent                                                            _Pulse
   cache held tick (queue order)                            wall = injected clock (aware UTC)
   fresh PositionView read                                  fresh PositionView read
   stop ─► target (per position,                            per position, no slot yet:
   bar/tick; stop wins a tie)                                 eod_session_window(clock, opened_at, lead)
        │                                                        None / unsupported year ─► skip (bounded log)
        ▼                                                        window.contains(wall)? ─ no ─► skip
   protective slot PENDING                                       eligible tick? ─ no ─► bounded log, retry
        │                                                        stop/target on that tick ─► protective slot
        │                                                        else ─► eod slot PENDING (+ window bounds)
        └────────────────┬───────────────────────────────────────┘
                         ▼
   on_exit_intent (legacy, stop/target only) + on_observation (both kinds) — wake-up hints only
                         ▼
   pending_observations() ◄── retry source ── consumer commits ──► acknowledge_observation()
                                                        window over ──► release_observation(WINDOW_CLOSED)
```

```
Slots per position:   protective: ∅ ─► PENDING ─► ACKNOWLEDGED
                      eod:        ∅ ─► PENDING ─► ACKNOWLEDGED
                                            └────► EXPIRED (WINDOW_CLOSED, terminal)
                      INVALID: slot discarded (EOD needs a strictly newer tick; protective re-forms on the next event)
                      POSITION_CLOSED: both slots discarded, none created again

EOD never suppresses protective evaluation.
A protective slot (created first) suppresses EOD creation and duplicate protective slots.
Callback/queue success and DB failure change nothing: a PENDING slot stays PENDING.
```

**Tick cache and EOD label.** `PriceUpdated` offers are validated (aware `exchange_ts`, finite
price > 0, `exchange_ts` not after wall time at arrival) and kept per symbol by maximum
exchange time; an older tick never moves it back, and on equal time the first received wins.
Unheld ticks are offered before the held-symbol filter; held ticks when the worker reaches
them in queue order. A pulse ignores a cached tick that arrived after that pulse was queued.
An EOD label needs `position.opened_at <= tick.exchange_ts <= wall now` on the entry ET day,
with no maximum age; otherwise nothing is created and nothing is substituted.
`trigger_ts` is the tick's exchange time, `trigger_price` its price. Candles keep driving
stop/target only.

**`ExitIntent` (additive).** Optional `eod_flatten_at` / `eod_close_at` (UTC, ordered, both
set) for `eod_flatten` only; a stop/target intent must leave them `None`. Existing
positional construction is unchanged.

**Handoff API for integration** (`position_monitor/handoff.py`, methods on `PositionMonitor`):

| Member | Contract |
|---|---|
| `PositionMonitor(..., on_observation=, wall_clock=, eod_lead_seconds=, pulse_interval_seconds=)` | All optional. `wall_clock` returns aware UTC. `eod_lead_seconds=None` reads settings once and is passed explicitly to `eod_session_window`; `1..900`. `pulse_interval_seconds=None` disables the timer (tests). `on_exit_intent` keeps its meaning: stop/target only, never EOD. |
| `on_observation(Observation)` | Called once per new slot with a `PENDING` copy: a wake-up hint. Its success is not a commit; an exception is logged and the slot stays pending. |
| `pending_observations()` | Slots not yet acknowledged, oldest first (`sequence` = monitor creation order across kinds/positions). The retry source after a failed commit or lost ack. |
| `get_observations(symbol=None)` | All slots with state. |
| `acknowledge_observation(position_id, kind)` | Call only after the durable commit (or readback proving it). Idempotent; `False` if no live slot. |
| `release_observation(position_id, kind, reason)` | `WINDOW_CLOSED` (EOD only, not after an acknowledgement) → `EXPIRED`; `POSITION_CLOSED`; `INVALID`. A DB failure needs no release. |
| `get_exit_intents()` | Unchanged diagnostic: first intent per position, any kind, in-memory. |
| `enqueue_pulse()` | Manual pulse on the shared queue; `False` if coalesced or not accepting. |

Execution drains `pending_observations()` in order, commits each
through the ledger, then acknowledge; on a window-closed disposition release
`WINDOW_CLOSED`; hydrate at startup by acknowledging committed kinds before events flow.
The callback only wakes the worker; a failed transaction leaves the slot pending.
A later observation for the same position waits behind a failed earlier one,
while other positions continue.

**Limits.** Arrival ordering only, not global exchange-time ordering. Uncommitted slots
and the tick cache are lost on restart; committed slots are restored. Retained per-process
bookkeeping (closed-position and log-throttle maps) is not pruned. A tick stamped ahead of
the wall clock (clock skew) is rejected. EOD placement remains best-effort.

#### Simulated EOD flatten contract — policy approved and integrated

**Policy approved by Saqib on 2026-09-29 (`simulated-eod-flatten-contract`).** The
window helper, config default, calendar coverage accessor, monitor handoff,
durable exit-ledger state machine, migration `0016`, reader/frontend and
Execution/startup integration are built. EOD is a **best-effort attempt to flatten**, not a guarantee
of closure by the bell or of no overnight exposure. Paper/live exits
and unrelated modules remain outside this contract.

#### Historical baseline before decision #185 integration

- `position_monitor/engine.py`: `_evaluate()` checks stop, target, then an EOD event timestamp
  at/after the entry day's close. `_exit_intents` suppresses every later intent for that
  position. Only stop/target reached the callback then.
- `execution_engine/exit_ledger.py`: `observe()` accepts stop/target only and returns `True`
  for an existing row without changing it. `exit_requests.position_id` is the primary key;
  there is no request lifecycle or fallback slot. `prepare()` reuses an approved close or
  waits for another active close; rejected/cancelled attempts retry after five seconds.
- `execution_engine/engine.py`: `_pending_intents` holds one intent per position; a later
  handoff could overwrite an earlier uncommitted observation. `confirm_recovery_exit()`
  checks before sending, but `approved` alone does not prove an order was never sent:
  a crash can occur between venue acceptance and status commit.
- `broker_adapters/simulated_venue.py`: placement is refused at the exact close. An accepted
  market order fills on the next **delivered** tick for its symbol; `ingest_tick()` has no
  session or acceptance-timestamp check. A delayed tick stamped before acceptance can fill
  it too. Default fills are whole-order; the injected planner supports partial fills.
- `portfolio_state/reconciliation.py`: rebuild/reconcile precede activation. Approved closes
  absent from the venue are checked for matching position/request identity, not submitted
  during reconciliation. A fresh venue with an open ledger position causes a quantity
  discrepancy. The earlier proposal's specific `unreserved approved exit` assertion was
  incorrect for that normal approved-close branch.

The previous proposal extended only the reason CHECK, stopped EOD retries at close, and
kept “first durable reason wins” forever. That strands an EOD row after rejection,
cancellation or an unsent reservation: clearing the monitor latch cannot make a later
stop/target replace it. Its “stop/target remain armed” claim was false for this state.
The approved correction calls for **a durable fallback observation separate from EOD
placement eligibility**, in the existing single request row. Active/uncertain orders remain
exclusive. That state machine is now implemented by the ledger and worker below.

#### Window, observation freshness and ordering

Use the position's entry ET trading day, close 16:00 ET or 13:00 on a supported half-day.
Configured `execution_eod_flatten_lead_seconds = 60`, validated `1..900`;
`flatten_at = close_at - lead`. Only wall time in `[flatten_at, close_at)` authorizes a new
EOD attempt. Persist both bounds with the request: restart/config changes cannot move the
deadline. No next-day EOD catch-up. A one-second poll is a scheduling target, not a latency
guarantee; backlog, downtime or a clock jump can miss the window.

**Verified 2026-2028 coverage** (`market-clock-2027-2028-coverage`; originally 2026 only).
MarketClock's holiday and 13:00 ET early-close tables are checked against NYSE's official
"Holidays & Trading Hours" page (https://www.nyse.com/trade/hours-calendars) for exactly
2026, 2027 and 2028, and `has_calendar_for_year(year)` is true for exactly those years. Its
membership methods (`is_holiday`, `is_half_day`, `current_session`, ...) still do not
reject other years: outside 2026-2028 they simply know no holidays or early closes. The
built EOD helper rejects an uncovered **entry year** (for example 2025 and 2029), and
returns no window for covered holidays/weekends. A current instant in another year is
necessarily outside the stored entry-day window. Do not guess a 16:00 close in an
uncovered year. Future callers should log missing entry-year coverage once per relevant
position; a covered non-trading day simply has no window. Notable dates: 2027 observes
Friday Jan 1, Friday Jun 18 (Juneteenth observed), Monday Jul 5 (Independence Day
observed) and Friday Dec 24 (Christmas observed), with one early close (Fri Nov 26);
2028 has **no** New Year's Day holiday (Sat Jan 1 is not observed on Fri Dec 31, 2027),
and early closes on Mon Jul 3 and Fri Nov 24. The page lists no early close on Fri
2027-07-02 or Thu 2027-12-23. The options 1:15 p.m. close and other venues' 5:00 p.m. late
sessions are not modelled. Adding a year is a data change (two sets plus one entry in the
verified-years set); it does not change the helper or any other MarketClock consumer.

**Shared foundation as built.** `backend/app/core/session_window.py` exports
`eod_session_window(clock: MarketClock, opened_at: datetime, lead_seconds: int = 60)
-> EodSessionWindow | None`. `opened_at` must be timezone-aware; the injected clock's
`trading_day(opened_at)` supplies the ET entry date. The helper validates integer lead
`1..900`, asks `clock.has_calendar_for_year(entry_day.year)`, then asks that clock about
holiday/half-day membership. For a supported (2026-2028) trading day it returns frozen
`EodSessionWindow(flatten_at, close_at)` with both instants UTC. `window.contains(now)`
requires an aware, caller-supplied instant and is true exactly on
`[flatten_at, close_at)`. A covered holiday/weekend returns `None`; an unsupported year
raises `UnsupportedEodCalendarError` before assuming ordinary hours. Naive datetimes and
invalid lead values raise `ValueError`. The caller must pass the configured lead explicitly
if it differs from the helper's default. Neither the helper nor its `contains` method reads
wall time or places an order. The accessor is read-only and leaves all other MarketClock
session behavior unchanged. Backtest Runner is not imported by this live helper; its close
derivation is checked for parity in tests over all supported 2026-2028 trading days.

Component data flow for the shared window helper and its running callers:

```text
Settings.execution_eod_flatten_lead_seconds (60; 1..900)
                         |
position.opened_at ------+----> core.session_window.eod_session_window(...)
                         |          ^
MarketClock.trading_day / has_calendar_for_year / is_holiday / is_half_day
                                    |
                                    v
                          EodSessionWindow or None
                          / UnsupportedEodCalendarError
                                    |
                   Position Monitor pulse and PostgresExitLedger guards
```

Calendar data flow (verified years only; data, not interface, changes per year):

```text
NYSE "Holidays & Trading Hours" page (2026, 2027, 2028 columns + footnotes)
        |  transcribed by hand, cross-checked by tests against an independent copy
        v
market_clock.py  _HOLIDAYS_<year> / _HALF_DAYS_<year>   _VERIFIED_CALENDAR_YEARS
        |                        \                              |
        |  union                  \                             v
        v                          \               has_calendar_for_year(year)
is_holiday(d) / is_half_day(d)      \                            |
        |                            \                           |
        +--> current_session, is_market_open, session_bounds,    |
        |    next_session_boundary (skips closed days, 13:00 half-|
        |    day close; unverified year = no holidays, no raise) |
        +--> Backtest Runner regular_session_close_utc           |
        +--> core.session_window.eod_session_window <------------+
                       |  unverified entry year -> UnsupportedEodCalendarError
                       v
              EodSessionWindow [flatten_at, close_at) in UTC
```

Internal window flow (no implicit clock read):

```text
aware opened_at + integer lead in 1..900
             | invalid -> ValueError
             v
clock.trading_day(opened_at) -> supported year? no -> UnsupportedEodCalendarError
             | yes
             v
covered holiday/weekend? yes -> None
             | no
             v
13:00 ET on half-day, else 16:00 ET -> close_at UTC
             |                            flatten_at = close_at - lead
             v
EodSessionWindow.contains(aware now): flatten_at <= now < close_at
```

**Approved EOD labels: ticks only.** Cache the latest valid `PriceUpdated` per symbol
before held-symbol filtering: aware `exchange_ts`, finite positive price, no future timestamp.
Keep the maximum exchange timestamp; on equality retain the first received tick. Older
arrivals cannot move the cache backwards; future/invalid ticks cannot displace a valid tick.
At both poll and `observe()`, require the same position identity,
`position.opened_at <= trigger_ts <= now`, and entry ET date. A same-day pre-opening price
cannot label EOD. Reopening a symbol uses a new position ID and freshness check. Missing
eligible ticks means no request or EOD latch; log at a bounded rate and retry within the
window. Never substitute entry average, wall time or fabricated price.

A post-opening tick five minutes old is eligible under this policy: it labels intent,
not executable price. No additional age bound applies.
`trigger_ts` remains the exchange timestamp. The ledger revalidates against committed
`positions.opened_at`, not just the monitor's snapshot.

**Tick/candle ordering.** `CandleClosed.candle_ts` is bar-open time; OHLC spans an interval.
It is not the instant of the close price. Candles never overwrite the EOD tick cache or label
EOD. This avoids delayed candles displacing newer ticks and entry-spanning bars supplying
pre-entry labels. Existing event-driven stop/target tick/candle evaluation and stop-wins-
within-one-bar precedence remain as built; broader historical-candle semantics are not fixed.

Remove EOD from `_evaluate()`. Enqueue timer pulses on the monitor's **same queue** as market
events. Process preceding events, then re-read positions and check stop/target against the
eligible cached tick before EOD. A pending protective trigger wins; stop wins if that tick
touches both. This is arrival ordering, not global exchange-time ordering. A delayed tick or
candle arriving after the pulse is later work even if stamped earlier. Once EOD commits,
a later protective observation becomes fallback; no reserved order is relabelled.

#### Durable representation (as built; more than a reason CHECK)

Keep one `exit_requests` row per position and its original `exit_reason`, `trigger_price`,
`trigger_ts`, `created_at`. Never delete/reinsert the request or rewrite an old order reason.

| Record | Additive fields and meaning (migration 0016) |
|---|---|
| `exit_requests` | Allow `eod_flatten`; add `eod_flatten_at`, `eod_close_at`, `eod_expired_at`. Ordered bounds required only for EOD and immutable. Expiry records placement eligibility ending, not order cancellation or position closure. |
| `exit_requests` | Nullable `fallback_reason`, `fallback_trigger_price`, `fallback_trigger_ts`: all absent or all present, reason stop/target, finite positive price, aware timestamp; only on original EOD rows. First protective observation wins this immutable slot. |
| `orders` | Nullable `exit_dispatch_started_at` for closes in this EOD lifecycle, including fallback attempts. Commit before calling the venue. NULL on a new reservation proves no dispatch started under this protocol; non-NULL with approved status means uncertain, not safely unsent. |

Preserve `uq_orders_active_exit_per_position` for approved/submitted/partially_filled/unknown,
monotonic `positions.exit_attempt` and IDs `<trade_id>:exit:<attempt>`. Existing protective
rows have no EOD fields and retain decision #184 behavior. Migration `0016` enforces
field groups, preserves existing rows and refuses downgrade if it would lose EOD/fallback/
dispatch evidence.

**Effective reason:** original EOD is eligible inside its stored window unless durably
expired; after expiry only a stored fallback is eligible. Without fallback, the row is
**dormant**, still able to accept a later protective observation. An active close overrides
both eligibility paths. A terminal EOD attempt keeps its reason; a new fallback attempt gets
stop/target. Full closure makes all work inert. `retry_after` stays a per-position lower bound
and is not reset on fallback capture/expiry. There is no immediate retry burst on handoff.

#### Safe transition contract

“Terminal and settled” means confirmed rejected/cancelled/filled status, every reported fill
ingested and every committed fill receipted, without contradictory venue state. A missing
acknowledgement or lost venue book is not terminal proof.

| Case | Durable transition and next permitted action |
|---|---|
| No EOD request; eligible window/tick | `observe()` inserts once with bounds; `prepare()` may reserve after safety checks. No tick means no row. |
| No EOD request; window missed/closed | No EOD row or permanent latch; later stop/target creates an ordinary request. No EOD catch-up or synthetic fill. |
| EOD row, no order at close | Persist expiry even if entry cancellation or receipts delayed reservation. Dormant without fallback; with fallback, protective guards and retry delay apply. |
| Approved EOD reservation, proven unsent at close | Atomically cancel order with `eod_window_closed` and expire EOD eligibility. Keep row/history/counter. Never reuse ID. Fallback may reserve a new ID only after all guards. |
| Rejected or cancelled EOD inside window | Commit terminal status/delay and settle fills. Retry EOD on a new ID only inside window; captured fallback waits. |
| Rejected or cancelled EOD at/after close | Expire EOD; no new EOD attempt. Dormant without fallback. A stored or later protective observation enables guarded residual close after settlement/delay **and only in regular hours** (`simulated-protective-session-retry`): the bell that expires EOD is the bell that closes the venue, so a fallback places no earlier than the next regular open. |
| Submitted, unfilled at close | Expire placement eligibility but leave order working under recommended policy. No replacement/cancel-at-bell. Fallback may be recorded but cannot place. A later tick can fill after hours; no tick can mean indefinite non-fill. |
| Partially filled at close | Apply only actual fills; retain same active order for its leaves quantity. No second close for remainder. If later terminal and settled with quantity remaining, stored/new fallback may close only committed remainder. Without fallback, dormant. |
| Approved with dispatch marker, or unknown | Reconcile same ID and fills; preserve exclusivity. Never locally cancel as unsent, reset marker, blindly resend, or mint replacement without resolution. |
| Later stop/target on EOD row | Store first fallback even while EOD active/retrying. Observation alone cannot authorize second order. Action requires EOD expiry, no active/uncertain close, and regular hours. Flat positions reject/no-op. |
| Stop/target (original or fallback) outside regular hours | Request retained; no reservation, no `exit_attempt` increment, no venue call. An unsent approved reservation is held, not sent. Prior active/uncertain close stays exclusive (checked first). Resumes through the same worker at the next regular open with the full fresh checks; see the session-guard subsection. |
| Protective request before EOD | Original row/retry behavior wins; EOD adds nothing. |
| Full closure | Only fills/receipts close position/trade; fallback cannot reopen/reverse. |
| Restart | Rebuild, reconcile and restore durable slots/deadlines before workers. Recover missed expiry from stored bounds. Uncertain dispatch/lost position state blocks; never fabricate closure. |

“Stop/target remain armed” now means observations can be retained and eventually authorize
fallback **subject to these guards**. They cannot displace an active EOD order, guarantee an
outside-session execution, or recover events lost before commit.

#### Monitor, observe(), prepare() and recovery cooperation

**Latch/handoff.** Replace EOD's permanent suppression with separate EOD and protective
slots, each pending or durably acknowledged. EOD never suppresses protective evaluation.
A pending/durable protective slot suppresses duplicate protective observations and, if first,
EOD creation. Keep the original diagnostic intent as `observed_only`; that record is not the
control latch or a claim that the diagnostic route exposes fallback lifecycle.

Execution retains ordered pending observations instead of overwriting by position ID.
Deduplicate per position/kind, retaining the first protective trigger. `observe()` returns
explicit disposition and committed slot state: stored/already stored, window closed, position
closed, or invalid. Acknowledgement follows commit. DB failure keeps pending work for retry;
lost ack is resolved by replay/readback. Expired EOD handoff clears only its pending slot,
not protective eligibility. This is not a durable event bus: a crash before observation commit
can lose the trigger; fresh data must retrigger it.

**`observe()`.** Within existing ledger serialization, validate position, simulated mode/
venue, price/time and reason. New EOD additionally checks coverage/window/post-opening
freshness. A protective observation on an EOD row populates only its empty fallback slot;
EOD window restrictions must not reject that protective observation. Existing protective
rows retain #184 semantics. Advance due EOD expiry; acknowledge only committed state.
Concurrent observers cannot overwrite the first protective reason or insert a second row.

**`prepare()`.** Recognize flat positions and advance expiry independently of entry/receipt
delays. Classify all closes before choosing effective reason: cancel only a proven-unsent
expired EOD reservation; active/dispatched/unknown means wait/reconcile. Never return an
approved EOD action before checking deadline. For eligible work retain matching approved
trade, identity, opposite side, simulated mode/venue checks; cancel unfinished entries,
collect fills, wait for receipts, honor retry delay, size from committed `positions.qty`.
Reserve one ID and increment attempt atomically. Reuse only an eligible unsent reservation;
do not resize/relabel an old attempt.

**Final guard.** Extend `confirm_recovery_exit()` for this lifecycle to return send/wait/
window-expired/unsafe, not “any False is benign.” Recheck identity, qty, effective reason,
receipts, no working entry/competing close and deadline under transaction. On send, atomically
claim the NULL dispatch marker and commit. Only the claiming Execution worker can make the
one venue call; stale actions/second callers cannot claim again. Expiry cancellation is
conditional on still-unclaimed state. Single-owner Execution serializes dispatch/cancellation;
process takeover requires reconciliation. Marker committed then crash before call is also
conservatively ambiguous. If the call crosses close after a successful guard, the venue rejects
it: commit the real rejection and expire EOD, never bypass the venue session guard.
Timeout/exception keeps exclusivity pending reconciliation. Window expiry is a normal skip;
identity/quantity mismatch remains an unsafe error, not disguised as expiry.

**Recovery.** Rebuild receipts, reconcile same-ID reports/fills and venue positions before
activation. Known active orders remain exclusive after close. With clean retained venue state,
a proven-unsent reservation may be sent inside window after revalidation; outside window it
is cancelled and EOD expired. Dispatch-marked approved/unknown absent from venue is unresolved,
not unsent. Retain that block durably as active/unknown so a second restart cannot silently
make fallback actionable. Existing recovery's expired status is not permission to bypass a
lost-position discrepancy. A fresh SimulatedVenue has no book: an open ledger position still
blocks startup even if its unsent EOD order can be safely cancelled. No monitor/Execution
activation in that state; no promise of protection across a real simulated-venue restart.

After clean recovery hydrate slots before subscriptions: no row means both eligible; EOD
row means EOD acknowledged and protective slot free unless fallback exists; protective row
or fallback means protective acknowledged. Expired EOD without fallback resumes protective
observation. New EOD needs a fresh eligible cache after restart. Recovery never places directly;
existing lifespan owns ordering. Actual fills remain authoritative, including late reports.

#### Component data flow (as built)

```text
MarketClock + injected wall clock -> covered entry-day window -> timer pulse
PriceUpdated -> validated monotonic tick cache                       |
PriceUpdated / CandleClosed ----------------------------------------+-> Monitor queue
                                                                        |
                                      fresh PositionView + EOD/protective slots
                                                                        |
                                                         ordered pending intents
                                                                        v
                     Execution worker -> observe_exit() -> exit_requests
                          ^                |          original + fallback + bounds/expiry
                          |                +-> commit ack/readback -> monitor slots
                          |
                     prepare_exit() -> orders + attempt counter (one active close)
                          |
                 final guard + durable dispatch claim
                          v
                  SimulatedVenue (placement session guard)
                          |
                    real updates/fills (possibly after hours)
                          v
                fill ledger -> receipts -> Portfolio State -> PositionClosed

Startup: rebuild -> reconcile same IDs/fills + venue quantities
                    -> discrepancy: blocked (no workers)
                    -> clean: hydrate slots -> activate monitor/Execution
```

#### Internal state flow

```text
Monitor event -> stop/target evaluation -> first protective slot -> ordered handoff
        pulse -> prior events processed -> position/tick fresh + window open?
                       -> protective pending? skip EOD
                       -> otherwise first EOD slot -> ordered handoff
        EOD slot NEVER suppresses protective event evaluation
        commit ack -> acknowledged; DB failure -> pending retained

Ledger: no row --eligible EOD observe--> EOD eligible
           |                                 |
           +--stop/target--> protective       +--later stop/target--> store fallback
                                             |
                                        deadline crossed
                                             v
                                       EOD expired
                                             |
                 active/uncertain close? ----+----yes--> wait/reconcile same ID
                                             |
                                            no
                                             |
                     fallback absent <-------+-------> fallback present
                          dormant                       protective eligible
                             |                               |
                             +--later protective observe-----+
                                                             v
                                      entry/receipt/retry/reduce-only guards
                                                             |
                                     reserve ID -> final guard + claim -> submit
                                                             |
                            terminal settled, qty remains <--+--> real fills -> flat
                                  -> retry eligible reason

Approved EOD at deadline:
  marker NULL    -> atomic cancel(eod_window_closed) + expiry
  marker present -> uncertain; reconcile, never cancel as unsent
```

#### Exit ledger EOD state machine — as built (`simulated-eod-ledger-handoff` and integration)

**Built:** `PostgresExitLedger` (`execution_engine/exit_ledger.py`), the `ExitRequest`/`Order`
models (`models/execution_ledger.py`) and migration `0016_simulated_eod_exit_state.py`
(parent `0015`). Execution now calls the explicit methods; `main.py` hydrates monitor
slots after clean reconciliation, and the readers/frontend show the durable request.
EOD remains a
best-effort attempt, and a submitted order can still fill after hours or never.

**Schema (names as proposed above, now real).** `exit_requests` gains `eod_flatten_at`,
`eod_close_at`, `eod_expired_at`, `fallback_reason`, `fallback_trigger_price`,
`fallback_trigger_ts`; `orders` gains `exit_dispatch_started_at`. CHECKs: reason allows
`eod_flatten`; EOD bounds present and ordered only for EOD rows, expiry only at/after
`close_at`; the fallback group is all-or-none, EOD rows only, reason stop/target, finite
positive price; the marker is close-only; `eod_window_closed` can never sit on an order that
has a marker. A `BEFORE UPDATE` trigger makes the original request fields, bounds, expiry,
fallback and marker immutable once written (`retry_after` stays mutable).
`uq_orders_active_exit_per_position` and the `positions.exit_attempt` counter are unchanged.
Downgrade refuses when any EOD row, fallback, expiry, marker or `eod_window_closed` order
exists; otherwise it restores the 0015 shape and keeps legacy rows.

**Two surfaces over one serialization barrier** (the existing all-table `SHARE ROW EXCLUSIVE`
lock, so observe/prepare/claim/status calls never interleave):

| Surface | Methods | Used by |
|---|---|---|
| Legacy, unchanged signatures | `observe`, `pending_position_ids`, `prepare`, `confirm_recovery_exit`, `set_status` | compatibility for older stop/target callers; the monitor uses the explicit surface below. |
| Explicit | `observe_exit`, `prepare_exit`, `claim_dispatch`, `advance_eod_expiry`, `pending_exit_position_ids`, `slot_state`, plus `set_status` | the running Execution worker and startup hydration. Returns typed dispositions instead of booleans. |

`confirm_recovery_exit()` is now `claim_dispatch(...).send`, so even a legacy caller cannot
reach an EOD-lifecycle venue call without the durable claim.

**Result types the integration must handle.**

| Method | Result | Values (what the caller does) |
|---|---|---|
| `observe_exit(intent)` | `ObserveResult(disposition, position_id, reason, slot)`; `.acknowledged`, `.retry` | `STORED`, `ALREADY_STORED`, `FALLBACK_STORED`, `FALLBACK_ALREADY_STORED`, `SUPERSEDED` (protective request already exists) → committed, clear the slot. `WINDOW_NOT_OPEN` → keep and retry. `WINDOW_CLOSED`, `POSITION_CLOSED`, `INVALID(reason)` → resolved, drop; only the EOD slot is dropped, protective eligibility is unaffected. Identity that differs from the committed position **raises** `ExitLedgerError`. |
| `prepare_exit(position_id)` | `PrepareResult(disposition, action, eod_expired, expired_now, cancelled_order_id, reason)` | `SUBMIT` (action to claim), `CANCEL_ENTRY` (action), `WAIT_PENDING_FILL`, `WAIT_ACTIVE_ORDER`, `WAIT_UNCERTAIN_DISPATCH`, `WAIT_RETRY_DELAY`, `WAIT_WINDOW_NOT_OPEN`, `WAIT_OUTSIDE_REGULAR_SESSION` (stop/target/fallback held until regular hours; only ever replaces what would have been `SUBMIT`), `DORMANT`, `NO_REQUEST`, `POSITION_CLOSED`. Unsafe identity/trade/mode raises. |
| `claim_dispatch(order_id)` | `ClaimResult(disposition, action, reason)`; `.send` | **`CLAIMED` is the only value that permits a venue call.** `ALREADY_CLAIMED` (uncertain, never resend), `STALE`, `WINDOW_EXPIRED` (normal skip; unsent order already cancelled), `WAIT_WINDOW_NOT_OPEN`, `WAIT_OUTSIDE_REGULAR_SESSION` (the boundary passed between `prepare_exit` and here: no marker, no venue call, reservation kept), `WAIT_ENTRY_ACTIVITY`, `WAIT_PENDING_FILL`, `UNSAFE(reason)` (an error, not an expiry). |
| `advance_eod_expiry(position_id)` | `EodExpiryResult(disposition, cancelled_order_id)` | `EXPIRED`, `ALREADY_EXPIRED`, `NOT_DUE`, `NOT_EOD`, `NO_REQUEST`, `POSITION_CLOSED`. Called on restart (recover from stored bounds) and by any timer. |
| `slot_state(position_id)` | `ExitSlotState | None` | Original reason, bounds, expiry, fallback, `retry_after`; `.dormant`. For slot hydration before subscriptions. |

**Mapping to the monitor's handoff (`5fc4dfb`).** `observe_exit()` results map onto the
monitor's slot calls as: `STORED`, `ALREADY_STORED`, `FALLBACK_STORED`,
`FALLBACK_ALREADY_STORED`, `SUPERSEDED` → `acknowledge_observation(position_id, kind)`;
`WINDOW_CLOSED` → `release_observation(..., WINDOW_CLOSED)` (EOD slot only);
`POSITION_CLOSED` → `release_observation(..., POSITION_CLOSED)`;
`INVALID` → `release_observation(..., INVALID)`; `WINDOW_NOT_OPEN` → leave the slot
pending; an `ExitLedgerError` (database failure or unsafe identity) → leave it pending and
surface the error. The monitor's `ExitIntent` fields already match what the ledger reads.

**Independent validation.** A new EOD row is created only if, at the transaction's
injected-clock `now`: the position is committed, simulated, open and its symbol/side match
the intent; `position.opened_at <= trigger_ts <= now`; `trigger_ts` falls on the entry ET
trading day; `core.session_window.eod_session_window(clock, opened_at, lead)` yields a
window (covered holiday/weekend and unsupported years are `INVALID`); any bounds the
intent supplies equal that window (one bound only, naive bounds or a mismatch are
`INVALID`; absent bounds are derived); and `flatten_at <= now < close_at`. The intent's
`qty` is never read: every close is sized from the committed `positions.qty`. Stored
bounds are the deadline; a later config change or clock movement cannot reopen an expired
request.

**Component data flow (as built).**

```text
Settings.execution_eod_flatten_lead_seconds ─┐
positions.opened_at (committed) ─────────────┼─► core.session_window.eod_session_window ─► window
injected ledger clock ───────────────────────┘                                              │
                                                                                            ▼
EventBus PriceUpdated/CandleClosed ─► Position Monitor slots ─► on_observation wake-up
                                                  │                     │
                                                  └─ ordered pending ──► Execution worker
                                                                   observe_exit/prepare_exit/
                                                                   claim_dispatch/set_status
                                                                               │
                                                                               ▼
                                                                      PostgresExitLedger
                                     ┌────────────────────────────────────────────┴────────────┐
                                     ▼                                                         ▼
                             exit_requests (one row/position)                        orders (close attempts)
                             original + EOD bounds + expiry                          exit_reason, status,
                             + first-wins fallback + retry_after                     exit_dispatch_started_at
                                     └──────────── positions.exit_attempt (monotonic IDs) ─────┘
                                     fills / position_fill_receipts ── settled-fill checks
                                             │
                                             ▼
                                Portfolio State ─► restored position snapshot
                                             │
                                      clean reconciliation
                                             ▼
                         main.py: advance expiry + slot_state ─► restore monitor slots
```

**`observe_exit()` internal flow.**

```text
reason/price/ts malformed ─────────────────────────────► INVALID(reason)   (nothing written)
position unknown ──► INVALID | flat ──► POSITION_CLOSED | identity differs ──► raise
row exists? ── yes ─► advance due expiry, then:
      │                 EOD intent:  EOD row ─► ALREADY_STORED | protective row ─► SUPERSEDED
      │                 stop/target: protective row ─► ALREADY_STORED (first wins)
      │                              EOD row: fallback empty ─► FALLBACK_STORED
      │                                       fallback set   ─► FALLBACK_ALREADY_STORED
      no
      ▼
stop/target ─► insert original request ─► STORED
EOD ─► label in [opened_at, now], entry day? ─► window (helper) ─► bounds match? ─┐ any failure ─► INVALID
       now < flatten_at ─► WINDOW_NOT_OPEN     now >= close_at ─► WINDOW_CLOSED   │ (nothing written)
       otherwise ─► insert EOD row with derived bounds ─► STORED
```

**`prepare_exit()` / `claim_dispatch()` internal flow and order state.**

```text
prepare_exit:  identity/trade guards (raise) ─► advance expiry FIRST ─► effective reason
   (protective ▸ that reason | EOD in [flatten_at, close_at) ▸ eod_flatten | expired+fallback ▸ fallback
    | expired, no fallback ▸ DORMANT | before flatten_at ▸ WAIT_WINDOW_NOT_OPEN)
   ─► working entry? CANCEL_ENTRY ─► fill without receipt? WAIT_PENDING_FILL
   ─► active close: submitted/partial/unknown ▸ WAIT_ACTIVE_ORDER
                     approved + marker ▸ WAIT_UNCERTAIN_DISPATCH
                     approved, no marker ▸ SUBMIT (reuse the same ID)
                                           [stop/target/fallback and NOT regular session ▸ WAIT_OUTSIDE_REGULAR_SESSION]
   ─► retry_after in future? WAIT_RETRY_DELAY
   ─► stop/target/fallback and NOT MarketClock.is_regular_session(now)? WAIT_OUTSIDE_REGULAR_SESSION (nothing reserved)
   ─► reserve <trade>:exit:<attempt+1>, qty = committed positions.qty, marker NULL ─► SUBMIT

expiry at now >= close_at:  unsent (approved, marker NULL) EOD order ─► cancelled 'eod_window_closed'
                            submitted / partial / unknown / marker set ─► left untouched
                            eod_expired_at recorded either way (eligibility ended; nothing cancelled at the venue)

claim_dispatch:  not a position close ─► UNSAFE | flat/not approved ─► STALE | marker set ─► ALREADY_CLAIMED
   identity differs ─► UNSAFE | EOD: expiry due ─► cancel unsent ─► WINDOW_EXPIRED
   stop/target/fallback and NOT regular session ─► WAIT_OUTSIDE_REGULAR_SESSION (no marker)
   entry working ─► WAIT_ENTRY_ACTIVITY | fill without receipt ─► WAIT_PENDING_FILL
   qty != committed ─► UNSAFE | else write marker, COMMIT ─► CLAIMED  (only now may the venue be called)

order (EOD lifecycle):  approved/marker NULL ──claim──► approved/marker set ──venue──► submitted ─► fills / terminal
        │ deadline, proven unsent                              │ crash here = uncertain: reconcile, never resend
        └──► cancelled 'eod_window_closed' (DB forbids this reason once a marker exists)
```

**Behaviors chosen where the design left room** (all reversible in the integration review):
a proven-unsent expiry cancellation does not touch `retry_after`; an EOD offered after a
protective row is `SUPERSEDED`, not silently "stored"; `set_status()` refuses a transition
on an EOD-lifecycle close with no dispatch claim and refuses the reserved
`eod_window_closed` reason; a terminal update at/after `close_at` records the expiry itself.
A stale unsent reservation whose quantity no longer matches the committed position is
reported `UNSAFE` at claim time; the ledger does not cancel and re-reserve it (no such rule
was approved), so that state needs operator/integration handling.

**Integration as built (`simulated-eod-flatten-integration`).** The monitor's
`on_observation` callback wakes Execution, which drains `pending_observations()` in
sequence and acknowledges only committed `observe_exit()` results. A failed observation
stays pending; subsequent observations for that position wait, while other positions
continue. The worker serves `pending_exit_position_ids()` through `prepare_exit()` and
`claim_dispatch()`. Only `CLAIMED` reaches `SimulatedVenue.place_order()`. For an EOD-lifecycle
close, a venue exception or lost status acknowledgement leaves the dispatch marker and remains uncertain; the
worker cannot resend it. Rejection stores the venue's reason and permits a new attempt
after the ledger retry delay if the stored window remains open. Fills pass through the
existing fill ledger and Portfolio State receipt worker.

At startup, reconciliation treats an approved close with a dispatch marker and no venue
report as a discrepancy and blocks activation. An approved close without a marker remains
eligible for the worker's full revalidation after clean reconciliation. A fresh
`SimulatedVenue` with an open ledger position still fails the venue-position check.
After clean reconciliation and Portfolio State refresh, `main.py` advances EOD expiry
from stored bounds, reads `slot_state()`, and restores the original and fallback monitor
slots before starting the authorizer, Execution and monitor. A proven-unsent expiry
cancellation triggers another Portfolio State refresh before those subscriptions.
PostgreSQL `timestamptz` bounds are converted to UTC
for `ExitIntent`; the persisted instant is unchanged. Shutdown stops the monitor before
Execution so no new observation enters its draining queue. The read-only request panel
continues to distinguish the original reason, expiry and fallback; orders and fills are
separate evidence. Acceptance-case test mapping and limits are in `TESTING.md`.

#### Session-aware protective close (`simulated-protective-session-retry`)

**Defect found on `main` (`b6d1e57`).** `SimulatedVenue` rejects every order outside regular
hours (`outside_regular_session`), and `set_status()` answers any rejected close with
`retry_after = now + 5 s`. `prepare_exit()` had no session check, so after the bell each
service pass (the worker polls every 0.5 s) reserved a **new** attempt every five seconds,
called the venue, was rejected, and repeated all night. Reproduced at ledger level (real
PostgreSQL, injected clock, a stand-in venue that rejects every order as the real one does
after the bell): 400 service passes over 40 minutes produced 400 venue calls and 400 distinct
`<trade>:exit:N` IDs, all but the first five after 16:00 ET. The same loop applied to an original stop/target observed after hours and to the
fallback of an expired EOD row (the fallback is, by construction, first actionable at or after
the regular close).

**Rule.** A stop/target close is neither reserved nor dispatched while
`MarketClock.is_regular_session(now)` is false, `now` being the ledger's injected clock. The
durable `exit_requests` row is kept; `positions.exit_attempt`, order rows and rejection history
are untouched, so IDs stay monotonic and the real `outside_regular_session` reasons remain.
When regular hours return the same worker pass resumes the request through the unchanged checks
(committed position quantity, working entry, fill without receipt, retry delay). The venue is
called at most once per permitted attempt. `eod_flatten` keeps its own `[flatten_at, close_at)`
rule (that interval always lies inside regular hours); the guard never applies to it. Nothing
here invents a fill or promises overnight protection: a stop/target that fires after the bell
is an observation until the next open.

```text
                       wall clock (injected in tests)
                              │ now
Position Monitor ──observe──► Execution worker ──prepare_exit()/claim_dispatch()──► PostgresExitLedger
 (stop/target/EOD)            _service_exits()   ▲   dispositions                    │  │ is_regular_session(now)
                              (every 0.5 s)      │                                  │  ▼
                                                 │                                  │ MarketClock (read-only; unchanged)
                                   SUBMIT / CLAIMED only                            ▼
                                                 │                       exit_requests · orders · positions.exit_attempt
                                                 ▼                                  (retained; no new row/ID outside hours)
                                        SimulatedVenue.place_order()
                                        (its own session check stays as the second line of defence)
```

```text
prepare_exit() / claim_dispatch() with the session guard (only the new branches shown)

 protective row or EOD fallback ─► effective reason = stop | target
   working entry ▸ CANCEL_ENTRY (unchanged; not a close)     fill w/o receipt ▸ WAIT_PENDING_FILL
   active close: submitted/partial/unknown ▸ WAIT_ACTIVE_ORDER          } exclusivity is checked FIRST
                 approved + marker         ▸ WAIT_UNCERTAIN_DISPATCH    } and wins over the session wait
                 approved, unsent          ▸ regular hours ? SUBMIT (same ID) : WAIT_OUTSIDE_REGULAR_SESSION
   retry_after in future ▸ WAIT_RETRY_DELAY
   no close:                regular hours ? reserve <trade>:exit:<attempt+1> ▸ SUBMIT
                                          : WAIT_OUTSIDE_REGULAR_SESSION  (no order, no counter change)
 claim_dispatch: ... identity/lifecycle checks ─► not regular hours ▸ WAIT_OUTSIDE_REGULAR_SESSION
                 (before the dispatch marker is written) ─► entry/fill/quantity guards ─► CLAIMED

timeline, entry day 2026-09-16 (EDT):
 19:59:30Z  stop observed, reserve :exit:1 ─► venue rejects (bell) ─► retry_after +5 s
 20:00:00Z  16:00 ET closed ───┐
   ... service passes ...      │ WAIT_OUTSIDE_REGULAR_SESSION every pass: 0 orders, 0 venue calls, exit_attempt = 1
 13:29:59Z  still closed ──────┘
 13:30:00Z  09:30 ET open ─► one permitted attempt :exit:2 ─► claim ─► venue ─► (normal retry delay applies again)
 half-day 2026-11-27: closed from 13:00 ET (18:00Z); next open Mon 2026-11-30 09:30 ET (14:30Z)
```

**Consequences and limits.** (1) A fallback after EOD expiry cannot place on the entry day;
its first chance is the next regular open, after which the ordinary safety checks decide. The
EOD approved-policy wording "actionable only after EOD placement expires and prior
orders/fills are safely settled" therefore gains a regular-hours condition. (2) An already
`submitted`/`partially_filled`/`unknown` close, and an approved close carrying a dispatch
marker, keep their existing exclusive handling; this change does not cancel, replace or
resolve them. (3) An unsent approved reservation found outside hours is held rather than
sent, including a legacy #184 reservation that has no dispatch marker. (4) Working-entry
cancellation is not a close and is not gated. (5) The worker still polls and calls
`prepare_exit()` (one short transaction per waiting position) while waiting; there is no
next-open timer, so resumption is the first worker pass at or after 09:30 ET. (6) The ledger
judges `is_regular_session(now)` with its own injected clock; the venue's own check remains the
second line of defence, and a disagreement between the two clocks only costs one venue
rejection under the existing retry delay. (7) `MarketClock` covers 2026-2028 holidays and
early closes; outside them a year has no holidays, so weekdays 09:30-16:00 ET count as regular.

#### Approved policy (Saqib, 2026-09-29)

1. **Authorization/timing:** simulated, position-bound reduce-only EOD requires no new
   Governor decision. Attempt placement on the entry trading day only inside
   `[close - 60 s, close)`; no next-day EOD catch-up.
2. **Failure handoff:** retain the first stop/target observation as a durable fallback.
   It becomes actionable only after EOD placement expires and prior orders/fills are safely
   settled. A stored trigger remains actionable if price later recovers, as for existing
   durable protective requests.
3. **At the bell:** leave accepted orders working. After-hours/later fills are possible;
   indefinite non-fill is possible. Neither placement nor acceptance guarantees closure.
4. **Label freshness:** use a valid post-opening, entry-day tick with no additional maximum
   age. Candles do not label EOD; their bar-open timestamp cannot stand for close-price time.

#### Historical staged implementation footprint

The staged plan called for monitor slots and handoff, explicit ledger dispositions,
deadline checks and dispatch claiming, migration `0016`, then reconciliation and
lifespan recovery/hydration. These stages are complete. Settings,
MarketClock coverage and `core/session_window.py` supply the shared clock/window definition.
SimulatedVenue and Backtest Runner semantics stay unchanged: candle-close backtest exits are not price/time-equivalent to
live tick-driven attempts.

The existing exit-request reader and frontend types/panel include EOD bounds/expiry and
optional fallback observation, preserving current fields/read-only behavior. An expired row
must not appear to promise active protection. Order rows remain the source of attempt reason/
status; monitor diagnostics remain `observed_only`. This exceeds the old one-CHECK footprint
because durable handoff requires it. No unrelated UI/module changes. The approved policy is
recorded in decision #185; the order path is the explicit integration described above.

Parallel task file boundaries, using this foundation's API without editing `core/`:

| Task | Owned application/test files | Output boundary |
|---|---|---|
| Position Monitor timer/handoff | `backend/app/position_monitor/engine.py`, its narrow ports only if needed, `backend/tests/test_position_monitor_engine.py`, new focused monitor EOD tests | Emits ordered EOD/protective observations with a valid tick label; imports `eod_session_window()` but does not mutate the exit ledger or place orders. |
| Exit ledger state machine | `backend/app/execution_engine/exit_ledger.py`, `backend/app/models/execution_ledger.py`, one next-head `backend/alembic/versions/` migration, `backend/tests/test_exit_ledger_postgres.py`, new focused ledger EOD tests | Commits original request/fallback/window/dispatch evidence and returns explicit actions; imports the same helper but does not add the monitor timer or Execution worker wiring. |

`backend/app/execution_engine/engine.py`, `backend/app/portfolio_state/reconciliation.py`,
`backend/app/main.py`, reader/frontend files, and lifespan tests belong to the later
integration task. A later delivery owns its own `CHANGES.md`, `TESTING.md` and decision-log
updates at packaging; parallel tasks should not assign competing decision numbers.

#### Acceptance cases for the integrated EOD path

Use real PostgreSQL for atomic transitions, injected clocks and the real lifespan/bus/
SimulatedVenue for delivery and fills. Assert absence of extra orders, venue calls and fills,
not only final reasons. Each case below is required.

| ID | Scenario | Required assertions |
|---|---|---|
| A1 | No request, pulse before/at flatten time, repeated pulses | No early request; one eligible EOD row and at most one close, stable bounds/label. No boundary tick needed. |
| A2 | No tick; missed window; clock skips window | No EOD row/order/fill; bounded diagnostic. Later stop and separate target cases create ordinary protective requests. No next-day catch-up. |
| A3 | EOD row delayed by working entry or pending receipt at close | Expiry persists despite delay; no new EOD reservation. Dormant without fallback; guarded protective work with fallback. |
| A4 | Proven-unsent approved EOD expires before prepare or between prepare/final guard | Atomic cancellation/expiry, zero old-ID venue calls, no ID reuse; later fallback uses next ID. Concurrent claim/expiry cannot send cancelled order. |
| A5 | Rejected and separately cancelled EOD inside window | Five-second delay, next EOD ID, committed quantity; retries stop at close. Duplicate terminal updates create no extra attempt. |
| A6 | Rejection/cancellation at/after close then stop; separate target case | EOD history retained, fallback stored once, new protective ID/reason after guards/delay. No fallback means dormant. Repeated triggers cannot overwrite first fallback. |
| A7 | Submitted unfilled through close, then protective touch | Expiry + fallback retained; original order sole active close. No cancellation/replacement. No tick means indefinite submitted/open state, no synthetic fill. |
| A8 | Pre-close acceptance, after-hours/next-session tick | Real price/time and receipts close position. Delayed tick stamped before acceptance demonstrates existing venue behavior; no invented timestamp guard. |
| A9 | Partial planner [3,7] on qty 10, close after first fill | Qty 7, one active order with leaves 7; next fill closes once. Cancellation variant settles all fills then fallback qty 7/new ID; no fallback means dormant. |
| A10 | Cancellation races fill/receipt, or full closure before fallback | No new close until settlement/receipts; full closure makes fallback inert. Duplicate fills dedupe; no over-close/reversal. |
| A11 | Protective before EOD; same-bar tie; queued event before pulse; reverse arrival | Original protective reason wins; stop wins same-bar tie. EOD-first then delayed protective stores fallback without changing active order. No claim of global timestamp priority. |
| A12 | EOD then protective before DB commit; DB failure/lost ack | Both ordered observations retained; no pending-map overwrite or durable latch before commit. Replay/readback idempotent. Expired EOD handoff cannot suppress protective handoff. |
| A13 | Same-day pre-entry tick; reopened symbol; future/invalid/missing ticks | No ineligible EOD label. Tick at opened_at eligible; five-minute-old post-opening tick eligible under approved policy. Ledger independently rejects pre-entry label. |
| A14 | Older/equal-time ticks; delayed candle; candle spanning entry | Tick cache monotonic, first equal-time tick kept. Candles never label EOD/overwrite tick; existing protective candle evaluation retained. Test both event/pulse arrival orders. |
| A15 | Crash after marker before call; crash after acceptance before ack commit | Both exclusive until reconciliation; retained venue report/fills resolve same ID. No blind resend/replacement. Missing uncertain order blocks through repeated restart. |
| A16 | Clean retained-venue recovery: no row, dormant EOD, fallback, unsent inside/outside window, submitted/partial | Correct hydrated slots before subscriptions; recover expiry from bounds. Revalidate eligible unsent, cancel expired unsent, retain active close, size fallback from reconciled remainder. |
| A17 | Actual fresh SimulatedVenue restart with open position, including unsent EOD | Lost-position reconciliation blocks activation; no orphaned close. Do not require obsolete `unreserved approved exit` message. Flat restart produces no fallback. |
| A18 | Regular/half-day bounds, DST-season UTC, holiday/weekend, year boundaries, unsupported 2025/2029 | Parity with close helper on all supported 2026-2028 trading days, 2026 Nov 27/Dec 24, 2027 Nov 26 and 2028 Jul 3/Nov 24 at 13:00; no unsupported-date EOD. Exact bounds/invalid lead checked. Config changes/backward clock jumps never reopen expired request. |
| A19 | Concurrent observe/prepare/claim, stale qty, wrong identity/mode/side, entry/pending fill | One row/first fallback/active close, monotonic IDs, one dispatch claimant, qty from ledger. Unsafe guard is error; expiry is skip; later positions still serviced. |
| A20 | Migration and reader/lifespan integration | Existing rows preserved, new field-group constraints enforced, lossy downgrade refused. Readers show original/expiry/fallback honestly; shutdown removes timer and handoff callbacks. |

Regression targets: `test_position_monitor_engine.py` (replace the exact-close EOD expectation),
`test_exit_ledger_postgres.py`, `test_execution_engine.py`, `test_reconciliation.py`,
`test_simulated_venue.py`, `test_market_clock.py`, `test_main_execution_pipeline.py`;
add focused A1–A20 coverage, then backend suite and frontend type/build checks if readers change.
The case-to-test map and exact verification results are in `TESTING.md`.

**Related follow-ups, not implemented:** #184 stop/target retries can accumulate rejected
orders every five seconds outside session; a promoted fallback inherits this limitation.
Calendar replacement is separate; this proposal refuses unsupported EOD dates. Broader
protective candle freshness and delayed-tick venue fill semantics remain documented limitations.


**What it is (and isn't).** Only the three exit rules the Backtest Runner already models — stop, target, and `eod_flatten` at the real regular-session close — evaluated live for symbols Portfolio State reports open. It is **not** the module `trading-intelligence-architecture.md` §13 describes (is the thesis still valid, is momentum weakening, move the stop, take a partial, exit, reverse, hold); those questions, manual-position handling (`future-ideas.md` #14), and emergency actions (#16) are out of scope.

- **Inputs (as built):** `PriceUpdated` and `CandleClosed` for held symbols; `MarketClock` for the EOD instant (the derivation `fill_simulator.regular_session_close_utc` uses).
- **Output:** the monitor's `ExitIntent` remains in process and has no client-order ID. For stop/target and eligible EOD flatten, Execution stores the observation and mints a durable position-linked order ID `"<trade_id>:exit:<n>"`. A stored request is serviced while the process runs, but a fresh simulated venue with a lost position book blocks startup reconciliation rather than placing an orphaned close.
- **Stop/target enforcement is in-process** — acceptable for a simulated venue with no broker, **not** for a real one (a crash would leave a position without a stop): broker-side protective orders are a hard prerequisite before any real venue (§8, EX-11).

### 6.7 `OutcomeRecorder` and D17's live half (`trading_intelligence/`)

**Status: historical sketch.** The diagram below predates the EX-12 implementation. §6.7.1 is the as-built contract: `PositionClosed` is only a wake-up hint, and the insert and trade link commit together through the same-session writer.

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

### 6.7.1 `OutcomeRecorder` contract (`outcome-recorder-contract`) — EX-12 option (a) built for simulated auto trades

**Status.** Saqib approved option (a). The Governor evidence writer was already merged at `79650ad`; this delivery reuses it and adds the same-session performance writer, entry hook, recorder, and lifespan wiring. No migration is needed. `PositionClosed` wakes the recorder but supplies no outcome facts; the target position's durable receipts, trade, reservation and closing order do. `trades.outcome_status = blocked` records an unrecordable closure without fabricating evidence or R; a reason code is logged. The historical baseline findings below are retained where useful, with A1/A6/A7 and missing-source statements updated to the delivered state.

#### A. As-built inventory in this delivery

| # | Fact | Evidence |
|---|---|---|
| A1 | `OutcomeRecorder` is the sole non-backtest writer, using `record_strategy_outcome_in_session()`; `BacktestRunner.run()` retains its existing `record_strategy_outcome()` wrapper. | `backend/app/trading_intelligence/outcome_recorder.py`; `backend/app/backtest_runner/runner.py` |
| A2 | The durable closure marker already exists. `PostgresPositionLedger.commit_fill` sets `trades.status = 'closed'` in the same transaction as the closing fill's `positions` row, its `position_fill_receipts` row and the cursor advance. `PortfolioState._synchronize` publishes `PositionClosed` only after that commit and has no outbox, so a crash between commit and publish loses only the notification. | `backend/app/portfolio_state/postgres.py:PostgresPositionLedger.commit_fill`; `backend/app/portfolio_state/engine.py:PortfolioState._synchronize` |
| A3 | `position_fill_receipts.fill_data` keeps every fill's exact inputs (order ID, side, effect, qty, price, venue timestamp, commission) per `position_id`. The recorder replays only that position's receipts through canonical `apply_fill`, checking each against its source fill/order and the stored position projection. It never calls the all-position `PostgresPositionLedger._state` replay. | `backend/app/models/execution_ledger.py:PositionFillReceipt`; `backend/app/portfolio_state/accounting.py:apply_fill`, `PositionState`; `backend/app/trading_intelligence/outcome_recorder.py` |
| A4 | `PositionClosed` carries `exit_price` (VWAP of all reducing fills), gross `realized_pnl`, `fees`, `closed_ts`, `trade_id`, mode and venue, with `r_multiple_achieved = None`. It carries no entry price or quantity, no thesis, no evidence, no exit reason, no snapshots. | `backend/app/schemas/events/execution.py:PositionClosed` |
| A5 | `trades.outcome_id` (FK to `strategy_outcomes.outcome_id`) and `trades.outcome_status` are written by `OutcomeRecorder`. `outcome_status` has no CHECK constraint. The four `entry_*` snapshot columns are written by its first-fill hook, or remain NULL if the hook was missed. | `backend/app/models/execution_ledger.py:Trade`; `backend/app/trading_intelligence/outcome_recorder.py`; migration `0012` |
| A6 | `record_strategy_outcome_in_session()` validates equal quantities and stages all outcome fields, including execution mode, venue and missing-snapshot reasons, without committing or closing the caller's session. The original wrapper retains its own session and commit for Backtest Runner. The database has no uniqueness on `opportunity_id`; the recorder's trade lock and recheck enforce one linked outcome among recorder workers. | `backend/app/trading_intelligence/performance.py`; migration `0012` |
| A7 | The merged Governor persists accepted, strictly plain finite JSON `Opportunity.evidence` in `trades.thesis["evidence"]` with the trade and reservation. It rejects non-JSON values without coercion. It does not persist `Opportunity.confirmed_at`; no current strategy produces it, so `signal_confirmed_at` remains NULL. Older approvals without evidence stay unchanged and block on closure. | `backend/app/governor/postgres.py`; `backend/app/governor/evidence.py` |
| A8 | `trade_reservations.reference_price` is retained after handoff and is the value the Governor publishes as `TradePlanned.entry`. | `backend/app/governor/engine.py` (`entry=result.reference_price`); `backend/app/models/execution_ledger.py:TradeReservation` |
| A9 | `positions` has no uniqueness on `trade_id` (scratch run: two rows for one trade). Protective exits cancel an unfinished entry order before submitting a close, so a second position per trade is prevented by procedure, not by a constraint. Close orders are `<trade_id>:exit:<attempt>`, sized to the remaining position, each carrying its own `exit_reason`. | `backend/app/execution_engine/exit_ledger.py`; `backend/app/models/execution_ledger.py:Position` |
| A10 | `SimulatedVenue` reports `commission = None` on every fill. The as-built exit path writes `orders.exit_reason` as `stop`, `target` or `eod_flatten` (EOD is built and integrated; `exit_ledger.py` reserves and claims EOD closes, and `test_simulated_eod_integration.py` asserts `eod_flatten` orders). `eod_flatten` therefore flows through the same `orders.exit_reason` source with no change to this contract. | `backend/app/broker_adapters/simulated_venue.py`; `backend/app/execution_engine/exit_ledger.py` |
| A11 | The Backtest Runner stamps `schema_version = 1`; §6.8 and decision #170 say writers of the new shape write 2. | `backend/app/backtest_runner/runner.py:_build_strategy_outcome` |

#### B. Field-by-field source map for `StrategyOutcome` (as built)

`Persisted` = read from a ledger column. `Derived` = computed from persisted values. `NULL` = honest absence (I3).

| Field | Source | Status |
|---|---|---|
| `outcome_id` | `uuid4()` minted inside the linking transaction | Derived |
| `opportunity_id` | `trades.trade_id` (equals the accepted `opportunity_id` for approvals) | Persisted |
| `schema_version` | constant `2` | Derived |
| `strategy_name`, `strategy_version` | `trades.strategy_name`, `trades.strategy_version` | Persisted |
| `symbol` | `trades.symbol` | Persisted |
| `origin` | `trades.origin` (`auto` only in this slice, EX-13) | Persisted |
| `is_backtest` | `False` | Derived |
| `backtest_run_id` | `NULL` | NULL |
| `execution_mode`, `execution_venue` | `trades.execution_mode`, `trades.execution_venue`; anything other than `simulated` is refused (I6) | Persisted |
| `trading_day` | `position_fill_receipts.trading_day` of the first opening fill; must equal that of the closing fill | Persisted |
| `setup_detected_at` | `trades.decision_record.setup_detected_at` (ISO string, parsed to an aware UTC datetime) | Persisted |
| `signal_confirmed_at` | No producer sets `Opportunity.confirmed_at` today; stored as `NULL` | NULL |
| `decided_at` | `trades.decision_record.decided_at` | Persisted |
| `entry_filled_at` | `venue_ts` of the first opening fill (equals `positions.opened_at`) | Persisted |
| `exit_filled_at` | `venue_ts` of the closing fill (equals `positions.closed_at`) | Persisted |
| `holding_seconds` | `int((exit_filled_at - entry_filled_at).total_seconds())`, as the Backtest Runner does | Derived |
| `direction` | `trades.direction` | Persisted |
| `entry_price` | `PositionState.avg_price` after replaying the position's receipts (VWAP of opening fills; reductions never change it) | Derived |
| `entry_qty`, `exit_qty` | `PositionState.entry_qty`, `PositionState.exit_qty`; equal for a fully closed position | Derived |
| `exit_price` | `PositionState.exit_price` (VWAP of all reducing fills) | Derived |
| `commission_total` | `PositionState.fees`; `NULL` if any fill's commission is `NULL` | Derived / NULL |
| `slippage_entry` | `entry_price - trade_reservations.reference_price` (`TradePlanned.entry`), signed as the contract states | Derived |
| `realized_pnl` | `PositionState.realized_pnl` (gross) minus `commission_total` when it is known; gross when it is `NULL` | Derived |
| `realized_r` | direction-aware `(exit_price - entry_price) / abs(entry_price - structural_invalidation)`; see C4 | Derived |
| `exit_reason` | `orders.exit_reason` of the order that carries the closing fill | Persisted |
| `structural_invalidation`, `structural_target`, `final_stop`, `final_target`, `confidence_at_signal` | `trades.thesis` (`confidence` maps to `confidence_at_signal`); checked against `trades.decision_record` | Persisted |
| `evidence` | `trades.thesis["evidence"]`, persisted by the merged Governor approval path; an older approval without it blocks as `evidence_unavailable` | Persisted or blocked |
| `market_state_at_entry`, `context_at_entry` | First-entry-fill recorder hook writes `trades.entry_market_state`, `trades.entry_context` and capture time or missing reasons | Persisted / NULL |
| `market_state_at_exit`, `context_at_exit` | Captured by the recorder when it handles the closure (C3). No persisted home on `trades` | Derived / NULL |
| `snapshot_missing_reasons` | union of `trades.entry_snapshot_missing_reasons` and the recorder's exit-side reasons, keyed by the four snapshot field names | Derived |
| `feature_snapshot_id` | `NULL` (`feature_snapshots` does not exist) | NULL |

**Source resolution.** M1 is closed by the merged Governor evidence delivery. M2 remains honestly NULL because no strategy sets `confirmed_at`. M3 is closed by the best-effort first-fill hook; if missed, the outcome uses NULL with `recorder_unavailable`. M4 is captured by the recorder within 60 seconds of closure, or stored as NULL with a reason. Authorization-time snapshots are never relabelled as entry snapshots.

#### C. Writer behavior (as built)

| Case | As-built behavior |
|---|---|
| **C1 Partial reductions** | No outcome until the position is fully closed. The recorder acts only on a trade whose `trades.status = 'closed'` **and** whose replayed position ends at `qty = 0`. A `closing` position is invisible to it. Reductions and adds fold into one row: entry VWAP over all opening fills, exit VWAP over all reducing fills, `exit_filled_at` = last reduction, `entry_filled_at` = first opening fill. `exit_reason` is the closing fill's order's reason; earlier partial reductions under a different reason are not represented (`StrategyOutcome` has no field for it). |
| **C2 Commissions** | Sum only what fills reported. If every fill carries a commission: `commission_total = fees` and `realized_pnl = gross - fees` (a negative fee, i.e. a rebate, adds). If any fill's commission is `NULL`: `commission_total = NULL` and `realized_pnl = gross`, exactly what the Backtest Runner does; nothing is estimated. Every simulated outcome is in the second case until `SimulatedVenue` reports fees. Consequence to accept: a `NULL` commission means "not surfaced", and `realized_pnl` is then gross. |
| **C3 Missing snapshots** | Never discard, never `{}`. Entry side: read the `trades` columns; if `entry_snapshot_captured_at` and `entry_snapshot_missing_reasons` are both `NULL`, the hook never ran and the reason is `recorder_unavailable`. Exit side: capture with `capture_strategy_outcome_snapshots()` only if `now - closed_at <= outcome_snapshot_max_lag_seconds` (proposed default 60); a later recovery pass records `NULL` with `recorder_unavailable`, because a snapshot taken minutes late is a wrong snapshot, not a missing one. A capture that returns `None` records `engine_cold_start`; one that raises records `snapshot_capture_error`; `engine_state_lost_on_restart` is used only when the entry fill predates this process's start and the capture returns `None`. |
| **C4 Missing R basis** | `realized_r` is `NOT NULL` and is never invented. The basis is the authorization-time `structural_invalidation` (checked equal in `trades.thesis` and `trades.decision_record`), never `positions.stop`; the `PositionClosed` docstring says the current stop is not that basis. If it is absent or non-finite, if the two copies disagree, or if `abs(entry_price - structural_invalidation)` quantizes to zero, the trade is `blocked` with reason `r_basis_unavailable`. Prices are quantized to 6 places (`ROUND_HALF_UP`, as the `positions` projection does) before `float` conversion. |
| **C5 Duplicate closure notifications** | Harmless by construction. The event only enqueues `trade_id`; the worker de-duplicates the queue by `trade_id`; and the linking transaction takes `SELECT ... FOR UPDATE` on the `trades` row and returns `already_recorded` if `outcome_id` is set. A second recorder process is serialized by the same row lock. Verified in scratch: the second call inserted nothing. |
| **C6 Failed writes** | Transient errors (connection, lock timeout) roll back, then a separate small transaction sets `outcome_status = 'pending_retry'` if `outcome_id IS NULL`; if that also fails, `NULL` already means pending. The next sweep retries. Deterministic failures set `blocked` and log at ERROR or CRITICAL with `trade_id` and a reason code: `evidence_unavailable`, `r_basis_unavailable`, `unsupported_mode`, `multi_position_trade`, `multi_day_position`, `exit_reason_unavailable`, `ledger_inconsistent` (replay disagrees with `positions`, or `entry_qty != exit_qty`), `contract_validation_failed`, `write_rejected` (an `IntegrityError`). Nothing is dropped: the ledger still holds the closure. No reason column exists, so reasons live in logs; a `blocked` trade is re-armed by setting `outcome_status` back to `NULL`. |
| **C7 Restart recovery** | After reconciliation and `portfolio_state.refresh()` succeed, the recorder subscribes and scans approved, closed, simulated auto trades without a linked outcome, excluding `blocked`. The scan is driven from `trades` and left-joins `positions` pre-aggregated to one row per trade, so a closed trade whose `PositionClosed` was lost **and** that has zero `positions` rows is still discovered; the record path then blocks it as `multi_position_trade` (C8) without fabricating a position. Order and cursor are `(coalesce(min(positions.closed_at), 1970-01-01), trade_id)`: oldest close first, trades with no usable close time first, never NULL, so keyset paging cannot skip them. A trade with several position rows takes one page slot. Startup walks bounded pages; the periodic sweep visits one bounded page every `outcome_sweep_interval_seconds` (default 60), rotating its cursor. This covers fills applied by `portfolio_state.start()` before recorder subscription and lost `PositionClosed` events. Startup failure logs CRITICAL without changing an otherwise `ready` execution status. Reconciliation-blocked startup never starts the recorder. |
| **C8 Trade-to-position cardinality** | The recorder requires exactly one `positions` row for the trade, `closed`, with no entry order still holding unfilled quantity. Zero or several, or a still-open sibling, gives `multi_position_trade`. A fill dated on a different trading day than the entry gives `multi_day_position` (`StrategyOutcome.trading_day` covers entry and exit, day trading only, D8). |

#### D. Atomic linkage through `trades.outcome_id` / `outcome_status` (as built)

`trades.outcome_status` vocabulary: `NULL` = pending (the value `pending` is never written), `pending_retry`, `recorded`, and the new `blocked`. No migration is needed: the column has no CHECK.

One synchronous transaction on a fresh session, run through `asyncio.to_thread`:

1. Cheap pre-read (no lock): if `trades.outcome_id` is set, stop. This avoids capturing snapshots for a duplicate.
2. Read the trade symbol and closure timestamp, then capture exit snapshots **outside** any lock and only within the 60-second lag bound.
3. Acquire `trades, orders, trade_reservations` table locks in the same order as `ledger_transaction` and Portfolio State, then `SELECT ... FOR UPDATE` on the trade. If `outcome_id` is set, return without writing.
4. Read and replay only the target position's receipts, validate the closure and attribution, then build and validate `StrategyOutcome` (Pydantic) before any insert.
5. Insert the `strategy_outcomes` row through `record_strategy_outcome_in_session()`, then set `trades.outcome_id = :o` and `outcome_status = 'recorded'`.
6. Commit. Either both rows change or neither does; a crash between 5 and 6 leaves nothing behind (verified in scratch: rollback left zero outcome rows).

The ledger stays authoritative because every quantity, price and time in the outcome is read back from ledger tables; the bus contributes only a `trade_id` wake-up, and losing any event costs latency (and, for the entry hook only, an honestly `NULL` entry snapshot).

#### E. Diagrams

**Data flow between components (as built)**

```
                    LEDGER (PostgreSQL) — authoritative, I12
   ┌──────────────────────────────────────────────────────────────────────────┐
   │ trades ─ thesis, decision_record, entry_* snapshot cols, status,          │
   │          outcome_id, outcome_status                                       │
   │ trade_reservations (reference_price)   orders (exit_reason)               │
   │ positions          position_fill_receipts (fill_data, trading_day)        │
   └──────▲───────────────────────▲───────────────────────────▲───────────────┘
          │ closure commit (I8)   │ reads (one tx)            │ link tx: insert + update
          │                       │                           │
  Portfolio State ──PositionClosed──►  Event Bus  ──hint: trade_id only──►  OutcomeRecorder
  (commit_fill)      (after commit,   (no outbox)                          │  │   ▲
                      no guarantee)                                        │  │   │ startup scan
  Execution Engine ──OrderFilled(entry)──► Event Bus ──hint──► (entry hook)│  │   │ + periodic sweep
                                                                           │  │
     MarketState / Context engines ◄── capture_strategy_outcome_snapshots ─┘  │
       (in-memory reads, exit side within max lag; entry side at first entry fill)
                                                                              ▼
                                  record_strategy_outcome_in_session() ──► strategy_outcomes
                                  (forwards mode, venue, missing reasons)      (simulated rows,
                                                                              append-only)
```

**Internal flow of `OutcomeRecorder` (as built)**

```
 triggers:  PositionClosed hint │ startup scan │ periodic sweep      OrderFilled(entry) hint
                 └──────────┬───────┘                                        │
                            ▼                                                 ▼
                 queue of trade_id (de-duplicated)                  entry hook: first open fill only
                            │                                        capture snapshots ─► UPDATE trades.entry_*
                            ▼                                        WHERE captured_at IS NULL AND reasons IS NULL
                 worker loop (one trade at a time)                   (NULL + reason if None/raises; never blocks fills)
                            │
        1. Cheap pre-read: outcome linked/blocked or trade not closed? ──yes──► done
        2. Exit capture outside locks, only within 60 s; otherwise NULL + reason
        3. BEGIN; acquire ledger table locks, then SELECT trade FOR UPDATE
           Recheck linked/blocked/closed ──not eligible──► done
        4. Read this trade, reservation, position, receipts and closing order
           Replay ONLY this position's receipts through apply_fill
           Validate closed projection, one day, evidence, structural R basis,
           closing-order reason and all required fields ──invalid──► blocked + reason log
        5. Build StrategyOutcome, stage through same-session writer,
           INSERT strategy_outcomes; UPDATE trades.outcome_id/status; COMMIT
                 ├─ ok ───────────────────────────► recorded
                 ├─ transient error ─► ROLLBACK ──► pending_retry (best effort; NULL = pending) ─► next sweep
                 └─ IntegrityError / validation ──► ROLLBACK ──► blocked (+ ERROR/CRITICAL log)
```

#### F. EX-12 option selected

| Option | Evidence / implementation | Verdict |
|---|---|---|
| **(a) Dedicated `OutcomeRecorder` joining the ledger; `strategy_outcomes` strategy-attributed only** | Saqib approved this option. The merged Governor supplies evidence; this delivery adds the first-fill snapshot hook and same-session writer. The recorder uses target-position receipts and a locked atomic link. No migration is needed. | **Built for simulated auto trades** |
| (b) Enrich `PositionClosed` | The event already carries exit VWAP, gross P&L and fees (A4) but still lacks entry facts, thesis, evidence and exit reason, and it has no delivery guarantee or outbox (A2). Filling it would make Portfolio State read strategy attribution (an I5 breach) and would make a lossy notification the record (an I8/I12 breach). | Rejected as the source. Kept as a wake-up hint |
| (c) Relax the `NOT NULL` columns | `trades.strategy_name` is `NOT NULL` and EX-13 keeps this slice `auto` only, so no strategy-less trade can reach the ledger today. Relaxing `evidence` or `realized_r` would let placeholders into a table whose meaning is evidence about strategy configurations (EX-12's own consequence note), and would need a `schema_version` bump and a migration. | Rejected |

#### G. Delivered footprint

- **P1 was merged earlier (`governor-approval-evidence`).** Approval evidence is strictly JSON validated and persisted in `trades.thesis`; neither datetimes nor other non-JSON values are coerced. `signal_confirmed_at` remains NULL.
- **P2:** `OutcomeRecorder` subscribes to `OrderFilled`, queues the order ID, and captures at the first opening fill only. Fill persistence never waits for this hook. A missed hook leaves NULL entry snapshots with `recorder_unavailable` in the outcome.
- **P3:** `record_strategy_outcome_in_session()` stages the full row in its caller's session and does not commit or close it; `record_strategy_outcome()` remains the Backtest Runner wrapper.
- **P4:** `trading_intelligence/outcome_recorder.py` starts only after successful reconciliation, replays the target position's receipts through `apply_fill`, and links the outcome atomically. Its startup failure is isolated. Settings default to 60 seconds for both maximum exit-snapshot lag and recovery sweep.
- **Database guard (delivered, decision #187, `outcome-unique-opportunity-guard`; was optional in #186).** Migration `0017` adds the partial unique index `uq_strategy_outcomes_non_backtest_opportunity` on `strategy_outcomes(opportunity_id) WHERE is_backtest IS FALSE`, declared identically on `StrategyOutcomeRecord`. A duplicate non-backtest outcome is now structurally impossible even for a second writer; backtest rows keep sharing an opportunity ID across runs. The recorder's under-lock recheck is unchanged and remains the first line of defense; the index is the backstop. The recorder code, retry/blocking policy, outcome payload and read routes are untouched.

```
  WRITE PATH (unchanged code, new backstop)

  OutcomeRecorder --(locks, recheck outcome_id)--> record_strategy_outcome_in_session()
                                                        |  INSERT strategy_outcomes
                                                        v
                         +----------------------------------------------+
                         | uq_strategy_outcomes_non_backtest_opportunity |
                         |   UNIQUE (opportunity_id) WHERE NOT backtest  |
                         +----------------------------------------------+
        is_backtest = false, new opportunity_id ---> accepted
        is_backtest = false, existing opportunity_id -> IntegrityError (nothing written)
        is_backtest = true (any run, any repeat) ----> accepted (outside the predicate)

  Backtest Runner --> record_strategy_outcome() (is_backtest = true) -------> unaffected

  MIGRATION 0017 upgrade                                   downgrade
  -----------------------------------------------          -------------------------
  LOCK strategy_outcomes IN SHARE MODE                     DROP INDEX
  count non-backtest opportunity_ids with > 1 row            uq_strategy_outcomes_
     > 0 : RAISE (names up to 10, no row touched,            non_backtest_opportunity
           revision stays 0016, no index)                  (rows and all other
     = 0 : CREATE UNIQUE INDEX ... WHERE is_backtest         objects untouched)
           IS FALSE; revision -> 0017
```

  Existing duplicates are never deleted or chosen automatically: the operator decides which outcome is authoritative, resolves the rest deliberately, then re-runs the upgrade.

**Verification.** The focused tests cover same-session rollback and the PostgreSQL NULL-snapshot CHECK, replayed partial fills and reductions, known/unknown commissions, duplicate and concurrent writers, blocked evidence/R/exit reason, injected failure between insert and link, entry snapshot capture and failure, delayed exit capture, startup scan, sweep, and startup failure isolation, plus lost-event recovery of zero-position, multi-position and NULL-`closed_at` trades across page boundaries (`outcome-recorder-zero-position-recovery`). Backtest wrapper regression is included in the full backend suite. See `TESTING.md` for exact commands and results.

#### H. Approved policy

Saqib confirmed EX-12 option (a): `OutcomeRecorder` is the only writer of non-backtest `strategy_outcomes`; the ledger supplies facts and events only wake it. The table remains strict: missing required attribution blocks the trade and logs a reason code. The delivered scope is simulated, strategy-attributed auto trades. Snapshot lag and sweep default to 60 seconds; the first opening fill defines `entry_filled_at`, the closing fill's order defines `exit_reason`, and blocked reasons remain in logs only.

#### I. Frontend reader — "Recent Closed Trades" (`frontend-outcomes-simulated-reader`; decision #186 is the backend it reads, no new decision number)

**As built (frontend only).** The Info tab's existing "Recent Closed Trades" section
(`InfoTab.tsx`, `RecentClosedTrades`) now reads the rows the `OutcomeRecorder` writes. It uses the
existing `GET /intelligence/strategy-outcomes` route unchanged, still with a strict
`is_backtest=false` request, and labels the result as **simulated execution — not real-money
trading**. The rows are `execution_mode: "simulated"` outcomes of closed, strategy-attributed
simulated auto trades; no paper, live or manual writer exists, and the section does not claim
otherwise (each row prints its own `execution_mode · execution_venue`). Before this delivery the
reader fetched once on mount, folded a failed request into the "no closed trades" empty state, and
carried comments saying no live writer existed.

- `StrategyOutcomeWireShape` (`api-client.ts`) was corrected against `schemas/performance.py`
  `StrategyOutcome`: added `execution_mode`, `execution_venue` and `snapshot_missing_reasons`
  (`Record<string, string> | null`), and the four `market_state_*`/`context_*` snapshots are now
  `Record<string, unknown> | null`. `BacktestResultsPanel.tsx`'s JSON-blob field list was widened to
  accept `null` (type only; backtest rows still carry all four snapshots).
- `useStrategyOutcomes.ts` returns `outcomes`, `loading`, `error`, `hasLoaded`, `lastLoadedAt` and
  `refetch`. A failed request keeps the last loaded rows and sets `error`; only the newest request
  may write state (monotonic request id), so an older response can neither replace a newer
  refresh's rows nor resurrect a stale error. `StrategyOutcomeRow` additionally carries
  `executionMode`, `executionVenue` and `snapshotMissingReasons`.
- Refresh is manual only. No polling and no WebSocket subscription: the recorder writes Postgres
  and publishes no event for `strategy_outcomes`. No backend endpoint, trading control or
  trade-detail view was added.

**Data flow between components**

```
 OutcomeRecorder (backend, #186) ──INSERT──► strategy_outcomes
                                             (is_backtest=false, execution_mode='simulated')
                                                        │
        GET /intelligence/strategy-outcomes?limit=10&is_backtest=false   (existing route)
                                                        │  StrategyOutcome.model_dump(mode="json")
                                                        ▼
 api-client.ts  fetchStrategyOutcomes() ─► StrategyOutcomeWireShape
                (+ execution_mode / execution_venue / snapshot_missing_reasons,
                 four snapshots nullable)
                                                        ▼
 useStrategyOutcomes(10)  normalize() ─► StrategyOutcomeRow[]
                          state: outcomes · loading · error · hasLoaded · lastLoadedAt
                                                        ▼
 InfoTab.tsx  RecentClosedTrades  ── "Simulated" badge + "not real-money trading" subtitle
                                  ── Refresh button ──► hook.refetch()   (manual; no poll / no WS)
```

**Internal flow of the reader**

```
 mount │ limit change │ Refresh click
                 ▼
   load():  id = ++latestRequestId ; loading = true
                 ▼
   fetchStrategyOutcomes(limit, false)
        ├─ resolves, id == latest ─► outcomes = rows ; error = null ; lastLoadedAt = now ; loading = false
        ├─ rejects,  id == latest ─► error = message ; outcomes UNCHANGED ; loading = false
        └─ id != latest (superseded, or unmounted / limit changed) ─► response dropped, no state write

   render (first match wins for the message; rows render whenever outcomes is non-empty):
     loading ∧ ¬hasLoaded ∧ no error ─► Loading
     error ∧ ¬hasLoaded              ─► Error   ("Could not load closed trades", never "no trades")
     error ∧ hasLoaded               ─► banner "Refresh failed … showing last loaded result (time)" + rows/empty
     hasLoaded ∧ no rows             ─► Empty   ("No simulated closed trades recorded yet.")
     rows                            ─► Populated (mode · venue, "N snapshots unavailable" when reasons exist)
```

#### J. Read-path verification (`outcome-read-path-integration`; decision #186 is the writer, no new decision number)

**As built (tests and read-side comments only).** The row `OutcomeRecorder` writes is proven readable through every
existing reader, against real PostgreSQL, with no change to the recorder, the `strategy_outcomes` schema, or any
reader's behavior. `tests/test_outcome_read_path_integration.py` lets the real recorder write two simulated rows
(one with an honest NULL entry market-state snapshot and its reason, one with both entry snapshots NULL and
`recorder_unavailable`), seeds two backtest rows through `record_strategy_outcome()`, and asserts exact values on each
path. The reader docstrings that still said no live writer exists (`GET /strategy-outcomes`, `GET /win-rate-by-hour`,
`performance_queries.py`) and `trading-intelligence-architecture.md`'s "OutcomeRecorder remains unwired" sentence were
corrected to the #186 state. No reader defect was found.

```
 OutcomeRecorder (#186) ──INSERT──► strategy_outcomes ◄──INSERT── BacktestRunner (#128)
 is_backtest=false, mode=simulated        │               is_backtest=true, mode=backtest
   NULL snapshot + reason kept            │
        ┌───────────────┬─────────────────┼──────────────────────────┐
        ▼               ▼                 ▼                          ▼
 GET /strategy-   GET /win-rate-    GET /expectancy-        GET /world-view
 outcomes         by-hour           by-session-type         .performance
 row-level,       GROUP BY ET hour  GROUP BY                {live, backtest} each =
 limit, strict    of entry_filled_at context_at_entry->     the two aggregates with
 is_backtest      (AT TIME ZONE     'calendar'->>'session'  is_backtest=False / True
 selector         market_timezone)  (NULL → session=None)   (never blended, system-wide)
```

Every path applies `is_backtest` as a strict equality selector, never a blend; a NULL snapshot stays `null` on the wire
(never `{}`), and a NULL entry context is reported as the `session_type = null` group rather than dropped or labelled.

#### K. Read-only recorder status route (`execution-outcome-status-route`; decision #186 is the writer, no new decision number)

**As built (this subsection is the backend route; its UI reader is §L, also merged).** `GET /intelligence/execution-outcome-status?limit=50` reports how far the
`OutcomeRecorder` has got over the trades it is allowed to record, so an operator no longer needs the logs to learn
that a closed trade is waiting, retrying or blocked. It reads `trades` only. It writes nothing, takes no lock, triggers
no recovery sweep and does not touch recorder behavior, the `trades.outcome_status` vocabulary (§D) or any migration.
Its one consumer is the Execution panel's "Simulated outcome recording" section (§L), which was delivered separately
against the response shape below and is merged; nothing else reads the route.

**Response (exact):**
`{"counts": {"pending", "pending_retry", "blocked", "recorded", "other"}, "trades": [{"trade_id", "symbol", "strategy_name", "outcome_status", "outcome_id", "updated_at"}]}`

| Rule | As built |
|---|---|
| Population (not parameters) | `decision = 'approved'`, `status = 'closed'`, `execution_mode = 'simulated'`, `origin = 'auto'` — the same set the recorder's scan may record (§C7). Rejected, open/closing, backtest/paper/live and manual trades are never counted or listed. |
| `counts` | Cover **every** matching trade, independent of `limit`. SQL `NULL` → `pending`; `pending_retry`, `blocked`, `recorded` → their own bucket; any other non-NULL value, including the literal `"pending"` the recorder never writes → `other`. Buckets are exclusive and sum to the population. |
| `trades` | The recent bounded list: `updated_at` descending, then `trade_id` descending (primary key, so a strict total order; a `limit` inside a tie never reshuffles rows). `limit` is 1–100, default 50, else 422. |
| `outcome_status` | Returned **as stored**: `NULL` stays JSON `null` (never rewritten to `"pending"`); unexpected values are returned verbatim. |
| `outcome_id` | The linked `strategy_outcomes.outcome_id`, or `null`. Reported as stored; the route does not cross-check it against `outcome_status`. |
| Blocked reason | **Not exposed.** #186 keeps reason codes in logs, and no reason column exists. |
| `updated_at` | `trades.updated_at` normalised to UTC (ISO 8601). It is the last time the trade row changed, **not** the close time or the outcome time. |
| Concurrency | Counts and list are read in one `REPEATABLE READ` transaction so they describe one snapshot. The whole read runs through `asyncio.to_thread` (`scanner-route-db-offload` convention) and opens and closes its own `Session` in the worker. |

**Data flow between components (as built)**

```
 OutcomeRecorder (#186) ──writes──► trades.outcome_status / outcome_id / updated_at     [unchanged, sole writer]
                                          │
                                          │ SELECT only (REPEATABLE READ, no lock, no write)
                                          ▼
          GET /intelligence/execution-outcome-status?limit=N
                                          │ JSON {counts, trades}
                                          ▼
                     Execution panel ► "Simulated outcome recording" (§L, merged)

 not read here: strategy_outcomes · positions · orders · fills · logs (blocked reasons) · recorder state
```

**Internal flow of the route**

```
GET /intelligence/execution-outcome-status?limit=N
   │
   ▼
FastAPI validation ── limit outside [1, 100] or non-integer ──► 422  (before any DB touch)
   ▼
await asyncio.to_thread(_fetch_execution_outcome_status, limit)        ── event loop free
   ▼
_fetch_execution_outcome_status()  [worker thread — opens AND closes its own Session]
   BEGIN ISOLATION LEVEL REPEATABLE READ
   population = approved AND closed AND simulated AND auto
   1. SELECT count(*) FILTER (status IS NULL)                      → pending
             count(*) FILTER (status = 'pending_retry')            → pending_retry
             count(*) FILTER (status = 'blocked')                  → blocked
             count(*) FILTER (status = 'recorded')                 → recorded
             count(*) FILTER (status NOT NULL AND NOT IN the three)→ other
      WHERE population                                             (all rows, no LIMIT)
   2. SELECT trade_id, symbol, strategy_name, outcome_status, outcome_id, updated_at
      WHERE population ORDER BY updated_at DESC, trade_id DESC LIMIT :limit
   ROLLBACK (read-only)  ──► {counts, trades[updated_at → UTC]}
```

**Limits.** `updated_at` is maintained by the ORM's `onupdate` and the `now()` default only; a raw-SQL update that does
not set it (for example a manual re-arm of a `blocked` trade to `NULL`) does not move the row in the list. The order is
"most recently changed through the ORM", not "most recently closed". `outcome_id` and `outcome_status` are not
cross-validated. Empty population → all-zero counts and `"trades": []`, 200.

**Verification.** `tests/test_execution_outcome_status_route.py` (real PostgreSQL 16, hand-inserted `trades` rows, no
lifespan): population isolation, every bucket incl. unexpected values, SQL NULL, counts independent of `limit`,
order/tie/limit behaviour, an empty population (scratch-schema `trades`), serialization, read-only, and event-loop
offload. See `TESTING.md`.

#### L. Frontend reader — "Simulated outcome recording" (`execution-panel-outcome-status`; decision #186 is the writer, no new decision number)

**As built (frontend only).** The Execution panel mounts a compact "Simulated outcome recording" section
when expanded, after "Recent simulated positions" and before the lifecycle event feed. It is the first
consumer of §K's `GET /intelligence/execution-outcome-status`, through a typed
`fetchExecutionOutcomeStatus(limit = 50)` (`api-client.ts`; wire types
`ExecutionOutcomeStatusCountsWireShape`, `ExecutionOutcomeStatusTradeWireShape`,
`ExecutionOutcomeStatusWireShape`). No backend file, route, migration, dependency, polling or control was
added. It compiles against §K as it stands on `main`.

What it answers: *how far has the simulated `OutcomeRecorder` got over closed simulated auto trades?* It is
not a portfolio, not a real-money result, and not the Info tab's "Recent Closed Trades" (§I), which reads
the outcome rows themselves; this section reads the recording status on the `trades` row.

| Rule | As built |
|---|---|
| Request | `?limit=50` (explicit) on panel expansion and on the section's own manual Refresh. No `limit` control, no polling. |
| Counts | `Pending`, `Pending retry`, `Blocked`, `Recorded`, `Other`, exactly as the server returns them (whole population, independent of `limit`). The UI derives only the total (sum of the five) to decide empty vs populated and to print "Showing the N most recently changed of M" when the list is shorter than the population. |
| List | The server's order is preserved (`updated_at` descending, `trade_id` tie-break), keyed by `trade_id`, never re-sorted. Each row: symbol · strategy, the time the trade record last changed, the status label and "outcome linked" / "no outcome link". The `outcome_id` value itself is not printed. |
| Status display | `null` → "Pending". `pending_retry` → "Pending retry". `blocked` → "Blocked". `recorded` → "Recorded". **Anything else is shown verbatim as `Unexpected status "<value>"`** (error tone) and is never called recorded; this includes the literal `"pending"`, which the recorder never writes and the server counts under `Other`, so the list and the counts agree. |
| Blocked reason | Not shown and not invented; the section states that it is in the server logs, not this API. |
| Wording | States that the section covers closed simulated auto trades only, is not a live portfolio or a real-money result, that pending may still be recovered by the recorder, that the time is when the trade record last changed (not a close or outcome time), and that it loads on expansion and Refresh and is not a live feed. |
| States | Loading, error (a failed request is never shown as empty), empty ("No closed simulated auto trades yet."), populated. Same Refresh convention as "Recorded exit requests": Refresh stays enabled mid-flight, and the effect cleanup discards a superseded, late or post-collapse response, success or failure. |

```text
OutcomeRecorder (#186) ──writes──► trades.outcome_status / outcome_id / updated_at   [unchanged]
                                         │ SELECT only (§K)
                                         ▼
              GET /intelligence/execution-outcome-status?limit=50
                                         │ {counts, trades}
                                         ▼
   fetchExecutionOutcomeStatus() ──► ExecutionLifecyclePanel ► "Simulated outcome recording"
                                         (own load state, own Refresh; not merged into the WebSocket feed)

 not read here: strategy_outcomes rows (Info tab §I) · orders · fills · positions · logs (blocked reasons)
```

```text
expand panel / Refresh ──► load = loading ──► fetch(limit=50)
   ├─ failure  ──► "Could not fetch outcome recording status: <message>"   (not the empty state)
   └─ success  ──► total = pending + pending_retry + blocked + recorded + other
         ├─ total = 0 ──► "No closed simulated auto trades yet."
         └─ total > 0 ──► counts line (Other highlighted when > 0)
                          [if trades.length < total] "Showing the N most recently changed of M"
                          rows in server order:
                            outcome_status ─► describeOutcomeStatus()
                               null | pending_retry | blocked | recorded ─► fixed label
                               anything else ─► Unexpected status "<value>"
                            outcome_id !== null ─► "outcome linked", else "no outcome link"
collapse / unmount / newer Refresh ──► cleanup marks the run inactive; a late response or failure is ignored
```

**Limits.** The row time is `trades.updated_at` (last ORM change), so it is neither a close time nor an
outcome time. `outcome_id` and `outcome_status` are reported as stored and are not cross-checked (a
"Recorded" row with "no outcome link" is shown as stored). A `limit` of 50 bounds the list only; there is no
paging. Not verified against a running backend with real recorder rows (see `TESTING.md`).

### 6.8 Persistence sketch (implemented incrementally by #172 and entry-lifecycle-wiring — #174 was frontend-only and built no table here)

Names follow `system-design.md` §4.13; columns are illustrative. Every write goes through `asyncio.to_thread` (the repository's sync-engine pattern) and precedes the corresponding event (I8). **The ledger tables are authoritative (I12).**

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `trades` | One row per authorization, approved or rejected; holds the thesis snapshot the cache will not keep | `trade_id` (= accepted `opportunity_id` for approvals; audit identity for rejections), `decision_record` (entry-lifecycle-wiring exact authorization inputs), `execution_mode`, `execution_venue`, `origin`, strategy name/version, `direction`, thesis (`structural_*`, `final_*`, `confidence`, `evidence`), `decision`, `reasons`, `limits_snapshot` (the three limits in effect — §6.10), `status`, entry snapshots + reasons, `outcome_id`, `outcome_status` (the recorder's progress is exposed read-only, for closed simulated auto trades, by `GET /intelligence/execution-outcome-status` — §6.7.1 K — and shown by the Execution panel's "Simulated outcome recording" section — §6.7.1 L) |
| `trade_reservations` (entry-lifecycle-wiring) | Durable approval terms before order insertion, retained after handoff | `trade_id` PK/FK, deterministic `client_order_id` UNIQUE, positive `qty`, finite positive exact `reference_price`, `created_at`; migration downgrade refuses to discard reservations or decision records |
| `orders` | The order ledger and state machine. First read anywhere in this codebase by `GET /intelligence/execution-orders` (decision #181, §6.3) — curated, `execution_mode = 'simulated'`-only, bounded `[1, 100]` | `client_order_id` **UNIQUE**, `trade_id`, `execution_mode`, `execution_venue`, `venue_order_id`, `symbol`, `side`, `position_effect`, `qty`, `order_type`, `limit_price`, `status`, `exit_reason`, timestamps |
| `fills` | Every fill, deduplicated, in ledger order. Exposed over HTTP by `GET /intelligence/execution-fills` (decision #183, §6.3) — curated, joined to `orders` for `execution_mode = 'simulated'`-only scoping, bounded `[1, 100]` | `ledger_seq` (monotonic), `client_order_id`, `execution_venue`, `venue_fill_id`, `qty`, `price`, `venue_ts`, `commission` (nullable), `anomaly` (nullable: `overfill` \| `unmatched_order`); **UNIQUE (`execution_venue`, `venue_fill_id`)** |
| `positions` | Position accounting (owner: Portfolio State), a deterministic function of `fills`. Exposed over HTTP by `GET /intelligence/execution-positions` (`execution-positions-route`, §6.3) — curated, `execution_mode = 'simulated'`-only, bounded `[1, 100]`, newest `opened_at` first (ties: `position_id` descending) | `position_id`, `trade_id`, `execution_mode`, `execution_venue`, `symbol`, `side`, `qty`, `avg_price`, `stop`, `target`, `opened_at`, `closed_at`, `status`, `realized_pnl`, `exit_attempt` |
| `exit_requests` (decision #184) | Durable first stop/target observation per position and its retry delay; a recovery record, not a protection guarantee. Exposed over HTTP by `GET /intelligence/execution-exit-requests` (`execution-exit-requests-route`, §6.6) — joined to `positions` for `execution_mode = 'simulated'`-only scoping, bounded `[1, 100]`, newest `trigger_ts` first (ties: `position_id` descending). Distinct from the in-memory `GET /intelligence/exit-intents`. Displayed read-only by the Execution panel's "Recorded exit requests" section (`execution-panel-exit-requests`, §6.6), which also shows an `eod_flatten` row's stored window, expiry and fallback (`simulated-eod-exit-request-visibility`, §6.6) | `position_id` PK/FK, `exit_reason` (`stop` \| `target` \| `eod_flatten`), `trigger_price` (> 0), `trigger_ts`, `retry_after` (nullable), `created_at`; EOD/fallback columns per migration 0016 (`simulated-eod-ledger-handoff`) |
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

**Startup status surface (as built, `execution-startup-status`).** Decision #179
made partial startup fail closed but gave the running UI no way to tell *which*
of the three real outcomes above (§6.9) actually happened — a route caller could
only infer "not ready" from `/intelligence/world-view`'s `portfolio: null` or
`/intelligence/exit-intents`' `monitor_status: "unavailable"`, neither of which
distinguishes a clean-but-blocked reconciliation from an exception mid-startup.
`main.py` now tracks an explicit `app.state.execution_startup_status` through
the same try/reconcile/else/except/finally structure §6.9 already diagrams —
one of `"ready"`, `"reconciliation_blocked"`, or `"startup_failed"`, set at the
same point in the existing sequence that already determines the outcome, never
a fourth "in progress" value (routes are only served after this section has
already finished, one way or another). The `finally` block resets it to unset
on shutdown, the same reset `world_view_portfolio_reader`/`position_monitor`
already get — so a route hit with no active lifespan, before startup finishes,
or after shutdown, reports `"unavailable"`.

```
main.py lifespan, execution-pipeline section (§6.9 above, unmodified control flow)
        │
        ├─ app.state.execution_startup_status = None            (init, same line as the other two resets)
        │
        ▼
venue.connect() ──► reconcile ledger / venue
        │
        ├─ discrepancy ──► app.state.execution_startup_status =
        │                     {"status": "reconciliation_blocked",
        │                      "reason_code": "reconciliation_discrepancy",
        │                      "discrepancy_count": len(discrepancies)}      ◄── plain count only;
        │                                                                       the discrepancy list
        │                                                                       itself never leaves main.py
        │
        ├─ clean ──► restore Portfolio State ──► start authorizer/engine/monitor
        │               └─ app.state.execution_startup_status = {"status": "ready", ...}
        │
        └─ any exception ──► rollback (§6.9 above, unmodified)
                        └─ app.state.execution_startup_status =
                              {"status": "startup_failed",
                               "reason_code": "startup_exception", ...}       ◄── fixed constant;
                                                                                  never str(exc) —
                                                                                  no stack detail, no DSN
        │
        ▼
GET /health/execution-startup ──► getattr(app.state, "execution_startup_status", None)
        │
        ├─ None (no active lifespan / pre-startup / post-shutdown) ──► {"status": "unavailable", ...}
        └─ set ──► that dict, unchanged

lifespan shutdown (`finally`, §6.9 above) ──► app.state.execution_startup_status = None
        (same line as the world_view_portfolio_reader / position_monitor resets)
```

```
ExecutionLifecyclePanel (frontend, new "Startup status" section, above
"Observed exit triggers")
        │
        ▼ on panel expand / manual Refresh
fetchExecutionStartupStatus() ──► GET /health/execution-startup
        │
        ├─ fetch error ──► "Could not fetch startup status: ..."
        └─ 200 ──► label by status (Started / Blocked — reconciliation /
                    Startup failed / Unavailable) + discrepancy count
                    when reconciliation_blocked
        (no WebSocket subscription — same manual-refresh-only posture
        "Observed exit triggers" already established for this panel)
```

This is a startup diagnostic, not a live trading-readiness check: `"ready"`
means the pipeline finished startup successfully, not that any particular
opportunity will clear Governor's rules (§6.2), that Portfolio State will stay
ready, or that an open position's exit is protected (§6.6's `PositionMonitor`
is an observer, not a guarantee) — the route's own docstring and the panel's
own copy both say so. No new event, no polling, no change to §6.9's actual
control flow or to any entry rule, exit placement, or EX-5/EX-12 decision.

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

Provisional labels **EX-1 … EX-14**. On 2026-09-22 Saqib resolved EX-1, EX-2, EX-3, EX-4, EX-6 and EX-7; added six requirements (§3, I10–I15); and set the three initial limits (§6.10). EX-10 is settled by the ledger requirement (I12). `simulated-protective-exits` resolves EX-5 for simulated stop/target closes; Saqib approved simulated EOD policy on 2026-09-29 (decision #185), and the simulated EOD path is now built end to end (`simulated-eod-flatten-foundation` through `simulated-eod-flatten-integration`), with session-aware protective retries added by `simulated-protective-session-retry`. EX-12 option (a) is built for simulated auto trades in §6.7.1.

| Fork | Question | Status | Outcome / recommendation |
|---|---|---|---|
| EX-1 | Build order | **RESOLVED (#170)** | Execution first with the stub authorizer and `SimulatedVenue`; the stub is technically restricted to simulated execution and fails closed for paper/live |
| EX-2 | Labelling simulated-money outcomes | **RESOLVED (#170)** | separate `execution_mode` (`backtest\|simulated\|paper\|live`) and `execution_venue` (`simulated\|ibkr\|…`); `is_backtest` kept temporarily for compatibility |
| EX-3 | Venue port and registry role | **RESOLVED (#170)** | new narrow `OrderVenue` interface + an `execution` registry role; `BrokerAdapter` not enlarged |
| EX-4 | Authorizer stub shape and numbers | **RESOLVED (#170)** | one stub; 1 concurrent position, $1,000 notional per trade, $100 daily loss cap — all configurable |
| EX-5 | Do protective exits need authorization? | RESOLVED and built for simulated stop/target (`simulated-protective-exits`, #184) and simulated EOD (#185; integrated) | no fresh Governor decision; a durable position-bound reduce-only guard; manual/paper/live exits have no policy |
| EX-6 | Position accounting, in-flight orders, `PositionClosed` lane | **RESOLVED (#170)** | Portfolio State owns them; `PositionClosed` on the critical lane only after the commit |
| EX-7 | D17 live policy for missing snapshots | **RESOLVED (#170)** | pre-trade gate; a reported fill is never discarded; else nullable fields + a missing-data reason |
| EX-8 | Simulated fill model; `fill_simulator` reuse | OPEN — proceed on recommendation | conventions shared, incremental model new, parity delta documented |
| EX-9 | Identity and payloads | OPEN in part — proceed on recommendation | the client-order-ID part is settled by I10; `opportunity_id` minting, new models, venue-level rejection event remain |
| EX-10 | Durability | **Settled by I12** | ledger-first; `market_events` stays independent future work |
| EX-11 | Exit enforcement | OPEN — proceed on recommendation | in-process for simulated; broker-side mandatory before any real venue |
| EX-12 | Who writes `StrategyOutcome`; what belongs in it | RESOLVED for simulated auto trades | Dedicated ledger-driven `OutcomeRecorder`; strategy-attributed trades only (§6.7.1) |
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

### EX-5 — Do protective exits need authorization? · RESOLVED and built for simulated stop/target and simulated EOD; manual, paper and live exits have no policy
**Question.** I2 as reconciled covers *risk-increasing* orders. Does a stop, target, or EOD-flatten exit also need a Governor-class decision?
**Options.** (a) **No** — exits are *reduce-only*, checked by the Execution Engine against Portfolio State, carrying an `exit_reason` and their own client-order ID. (b) **Yes** — every order, including exits, gets a `GovernorDecision`.
**Evidence.** `trading-intelligence-architecture.md` §12 frames the Governor as a *risk gate* on new exposure and §13's Position Monitor issues exits; an authorization round-trip on a stop adds latency exactly when it hurts; the emergency-action design (#16) is the same shape.
**Recommendation.** (a), with the reduce-only guard as the enforced mechanism (§6.3 step 1).

**As-built resolution.** Simulated stop and target closes use option (a).
Execution requires a committed approved trade and matching open position,
cancels unfinished entries, waits for fill receipts, reserves at most one
active close per position, and rechecks before venue placement. *(Historical wording at
`simulated-protective-exits`: that delivery did not implement EOD, manual, paper, or live
exits; simulated EOD was added later, below. Manual, paper and live exits are still not
implemented.)*

**Approved EOD policy (decision #185; `simulated-eod-flatten-contract`, 2026-09-29).** Option (a)
extends to simulated, position-bound reduce-only EOD. The window is the covered entry-day UTC placement interval `[close − L, close)` with `L=60` by default
(configurable 1–900 s; §6.6).

**As built (`eca3573`).** Later deliveries implemented the monitor timer and tick cache, the durable
exit-ledger state machine (migration `0016`: stored window, expiry, immutable first stop/target
fallback, dispatch marker), the Execution worker and startup restore, and the read-only
reader/panel. EOD is a best-effort attempt to flatten, not a guarantee of closure by the bell.
The calendar is verified for 2026–2028 only and EOD fails closed for any other entry year. A
stop/target close, including the fallback after EOD expiry, waits while the regular session is
closed (`simulated-protective-session-retry`). Manual, paper and live exits have no
authorization policy here.

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

### EX-12 — Who writes `StrategyOutcome`; what belongs in it  · RESOLVED for simulated auto trades
**Question.** F10a and F13: `PositionClosed{position_id, exit_price, realized_pnl, r_multiple_achieved, closed_ts}` (prose) cannot build a `StrategyOutcome`, and a strategy-less manual trade cannot be one at all.
**Options.** (a) A dedicated **`OutcomeRecorder`** joins the `trades`/`orders`/`fills` ledger with persisted snapshots; **`strategy_outcomes` holds strategy-attributed trades only** (corroborated manual trades included), while `trades` records everything. (b) **Enrich `PositionClosed`** so Performance Intelligence can build the row from the event alone. (c) **Relax the NOT NULL columns** so strategy-less manual trades fit `strategy_outcomes`.
**Consequence.** (b) fattens an event with data the ledger already holds; (c) is a broad schema change that dilutes what `strategy_outcomes` means (evidence about strategy configurations). The `execution_mode`/`execution_venue` columns (EX-2) apply to whichever rows it holds.
**Recommendation.** (a). The §6.7 design already assumes it.
**Approved and built (`outcome-recorder-contract`).** §6.7.1 records option (a)'s as-built field map, writer behavior, atomic linkage and diagrams. Manual and paper/live paths remain outside this delivery.

### EX-13 — Manual mode in the first build  · OPEN
**Options.** (a) **Not in scope** — the placement-mode gate (`auto|manual`, §18.5's `ExecutionMode`) exists as a seam (`auto` only); Approval Queue, Input Layer, and `TradeRequest` wait. (b) **In scope.**
**Evidence.** §5 Slice B blockers; `TradeRequest` has no stop field while `TradePlan.stop` is required (F10a) — the manual design must answer where a manual trade's stop comes from, and that answer is not in `trading-intelligence-architecture.md` §18.
**Recommendation.** (a).

### EX-14 — Direction vocabulary and position effect  · OPEN
**Options.** (a) **Keep `BUY/SELL` on orders and outcomes** and add an explicit `position_effect` (`open|close`) on `OrderApproved`; `long/short` stays a planning-layer word. (b) Derive the effect in Execution from Portfolio State (implicit, fragile on a reversal or a late fill). (c) Change orders to `long/short`.
**Evidence.** F12; `StrategyOutcome.direction` is `BUY|SELL`.
**Recommendation.** (a).

### 7.1 Historical build prerequisites and remaining choices

1. **EX-5 (historical prerequisite)** — simulated stop/target is implemented by `simulated-protective-exits`. Simulated EOD policy was approved on 2026-09-29 and is now implemented (window helper, monitor timer, exit ledger, Execution worker, startup restore; §6.6). No EX-5 prerequisite remains open for the simulated slice.
2. **EX-12** — resolved for simulated auto trades; manual and paper/live attribution remain future work.
3. **Judgment calls made in this revision — confirm or overrule:**
   - **J1 — naming.** `trading-intelligence-architecture.md` §18.5's `ExecutionMode` (`auto|manual`) is called *placement mode* here, so that `execution_mode` means the capital mode and nothing else (§10, R7).
   - **J2 — the daily-loss gate also counts the candidate trade's own stop-out loss** ("open risk"), so at the initial values a $1,000 trade whose stop is more than 10% away is refused even on a clean day.
   - **J3 — schema details:** `StrategyOutcome.schema_version` bumps 1 → 2; backtest rows become `execution_mode = 'backtest'`, `execution_venue = 'simulated'`; the migration aborts if any `is_backtest = false` rows exist.
   - **J4 — recovery policy (historical, amended by #184):** an approved entry never sent is cancelled as stale. An approved simulated exit is validated during reconciliation and placed later by the execution worker only if the position and venue still agree; a missing simulated venue position blocks startup.
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
- **Frontend** — Positions / Trade Management / live order-status widgets, and any consumer of `orders.status`. The separate read-only simulated order history uses the ledger route (§6.3).
- **World View `portfolio` slot** — reads the running restored Portfolio State through `main.py`'s separate lifecycle dependency; see `trading-intelligence-architecture.md` §15. Position Monitor's diagnostic route remains read-only; simulated stop/target and EOD observations also enter the durable execution path.
- **Retiring `is_backtest`** — kept only for compatibility; its removal is a later decision.

---

## 9. Acceptance criteria proposed for the eventual build task

*Historical proposal, not a status checklist. EX-12's recorder is now built for simulated auto trades; the tests actually shipped are listed in `TESTING.md`.*

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

- **R1 — Related follow-up. RESOLVED (`execution-doc-drift-r1-r5-r9`, docs-only).** `system-design.md` §4.1 still says "`IBKRAdapter` and `AlpacaAdapter` implement this" although decision #1 revised the Alpaca plan (its own folder tree already says "Alpaca deferred, not stubbed"; §2 principle 1 and the §3 diagram also still name Alpaca). Docs-only fix, unrelated to this design's correctness; left untouched except for the pointer sentences this delivery adds. **Fixed:** §2 principle 1, the §3 diagram's broker box, and §4.1's prose now say IBKR only, with Alpaca noted as deferred (decision #1) rather than implemented.
- **R2 — Related follow-up.** `TradePlanned`'s prose definitions disagree: `system-design.md` §10.3 has `max_hold_minutes`, `scaling_plan`, `trailing_stop_rule`, while `trading-intelligence-architecture.md` §18.3's `TradePlan` has `max_hold_seconds`, `origin`, `corroboration`. The model does not exist yet (F1), so nothing is broken; the build task must reconcile them (EX-9).
- **R3 — Related follow-up.** `trading-intelligence-architecture.md` §18.8 says a filled manual plan is recorded in "the existing `trades` table"; no such table exists (F7).
- **R4 — Related follow-up.** `performance.py`'s docstring forbids a live caller without a real Execution Engine; when the build lands, that docstring and D17's "live half" note in `strategy-engine-open-decisions.md` need the corresponding update (a docs step for the build task, not for this one).
- **R5 — Related follow-up. RESOLVED (`execution-doc-drift-r1-r5-r9`, docs-only).** `system-design.md` §4.9 names the `OrderApproved` payload `ApprovedOrder`; no such class exists — the model is `OrderApproved` itself (`schemas/events/execution.py`). Docs-only naming drift, folded into R1's fix. **Fixed:** §4.9's first paragraph now names the payload class correctly and no longer routes it through `BrokerAdapter.place_order` (see R9).
- **R6 — Unrelated.** `IBKRAdapter.get_positions()` has no caller and no test today (§1.2). Noted, untouched.
- **R7 — Related follow-up (naming).** `trading-intelligence-architecture.md` §18.5 defines `ExecutionMode` as `auto | manual` (who triggers placement). Decision #170 introduces `execution_mode` (`backtest | simulated | paper | live`, the capital mode). This design calls §18.5's concept *placement mode* to keep the two apart; §18.5 (and `system-design.md` §4.9's "Mode-aware" wording) should be reconciled to the same name when the manual-mode design is next touched. Left untouched here.
- **R8 — Related follow-up.** `BrokerAdapter` still declares `place_order`/`cancel_order`/`get_positions` (and `IBKRAdapter` still stubs them) even though, after decision #170, order placement belongs to the `OrderVenue` port. They stay unwired and unchanged; whether to remove them is a later decision for the build task or after it.
- **R9 — Related follow-up. RESOLVED (`execution-doc-drift-r1-r5-r9`, docs-only).** `system-design.md` §2 principle 1 says everything talks to a `BrokerAdapter` interface. For the execution path it will be an `OrderVenue`; the principle needs one clarifying sentence when the build lands. Left untouched here. **Fixed:** §2 principle 1, §4.1, §4.9, the §3 diagram, and the §5 "Opportunity → Execution" walkthrough now all distinguish market-data connectivity (`BrokerAdapter`) from order execution (`OrderVenue`, decision #170) — `BrokerAdapter.place_order`/`cancel_order`/`get_positions` are described as declared-but-unwired (R8, left open) rather than as the execution path.
