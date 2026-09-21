# Execution Engine & Portfolio State — Design (DRAFT, forks open)
**Owner:** Saqib
**Status:** DRAFT — a design pass only. **Nothing in this document is decided, built, or migrated.** Baseline: `main` through decision #167. Recorded by decision #168 (temp id `execution-engine-design`; number assigned at merge after the three-source re-check). Design forks carry provisional labels **EX-1 … EX-14** (§7) — they are not D-numbers and not decision numbers; Saqib assigns those when a fork is resolved.
**Companion documents:** [`system-design.md`](./system-design.md) §4.4 (Event Bus), §4.6 (Portfolio State Engine), §4.9 (Execution Engine), §4.13 (Database), §10 (event contracts) — the prose this doc turns into a design; [`trading-intelligence-architecture.md`](./trading-intelligence-architecture.md) §6, §10–§13, §18 (Portfolio State, Decision Engine, Trade Planning, Governor, Position Monitor, Manual Trading & Execution Modes) — the reasoning behind each module; [`strategy-engine-design.md`](./strategy-engine-design.md) §5 (`StrategyOutcome`), §6 (Decision Engine vs Governor), §9 (the full feedback loop); [`strategy-engine-open-decisions.md`](./strategy-engine-open-decisions.md) (D1, D4, D17 — the three rows this design touches); [`backtest-runner-design.md`](./backtest-runner-design.md) §7 (the only existing writer of `StrategyOutcome`, and the precedent for decision #128's option (a)); [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (#6, #9, #89, #120, #128, #158); [`../decisions/future-ideas.md`](../decisions/future-ideas.md) (#14, #16, #21, #27).

**Why this doc exists.** Everything downstream of the Strategy Engine — Decision Engine, Trade Planning, Governor, Portfolio State, Execution Engine, Position Monitor — exists only as prose (`system-design.md` §4.6/§4.9, `trading-intelligence-architecture.md` §6/§10–§13/§18). Performance Intelligence is built and tested, but its live half is empty: `record_strategy_outcome()` has no live caller (D17's live half, decision #158), and Decision Engine's arbitration (D4) is explicitly waiting for real outcome data. The Execution Engine is the missing writer, so it is the module whose design most gates the rest. The prose was written before the surrounding code existed; several of its premises no longer match the as-built repository (§2). This project's pattern is *design → forks resolved by Saqib → build*; this is the design pass, and it stops at the forks.

**How to read this doc.**
- Every statement about the current code cites `path:symbol`, verified against `main` through decision #167 (the claim-to-source table with a machine check is in the delivery's `TESTING.md`). Statements about the *proposal* are labelled as such.
- Status legend used everywhere: **built** (exists and is exercised), **partial** (exists in part — the part is named), **not built** (no module, table, or code — prose or enum entries only).
- Architecture diagrams use the repository's ASCII code-fence style (§6.1, §6.3, §6.5, §6.7 — data flow between modules, plus the internal flow of each proposed module).
- This doc proposes; the forks in §7 each carry options, evidence, consequences, and a recommendation, all marked **OPEN**. Where the recommendation is "proceed unless Saqib objects", it says so.

---

## 0. Summary

1. **The goal of the first build is narrow and measurable:** one real, non-backtest `StrategyOutcome` row, produced end to end, closing D17's live half — with every population boundary (live / backtest / simulated) kept honest.
2. **The code disagrees with the prose in ways that change the design** (§2): the execution-side events are declared but have no payload models for four of eight types and no publisher or subscriber for any of them; the `BrokerAdapter` contract cannot report a fill; `IBKRAdapter` connects `readonly=True` and its order methods are stubs; the Event Bus's critical lane awaits every handler serially and persists nothing; `Opportunity` has no identity; `StrategyOutcome` cannot represent a manual trade with no strategy behind it; and `is_backtest` is a two-valued flag with no way to keep *simulated-money* outcomes apart from *real-money* ones.
3. **"Only the Governor can place orders" needs one reconciliation, not a new rule** (§3, I2): the Governor is the only *authorizer*; the Execution Engine is the only *placer*; manual mode adds a human confirmation *after* the Governor, it does not replace it. The task brief's shorthand "human as Governor" for manual-first was imprecise — §18.5 keeps the Governor in the path even for manual, sized requests.
4. **Recommended first slice (§5, Slice A):** a simulated venue, the auto path, and one deliberately thin, clearly-labelled authorizer stub in place of the unbuilt Decision/Planning/Governor stages — with fixed sizing, one position per symbol, protective exits monitored in-process, and an `OutcomeRecorder` that writes the outcome. It inverts the roadmap's stage order (Execution before its Phase 5 predecessors), which is itself a fork (EX-1).
5. **Fourteen forks are open (§7).** The five that unblock a build task are EX-1 (slice order), EX-2 (how simulated outcomes are labelled), EX-3 (the venue port), EX-6 (who owns position accounting), and EX-7 (D17's live policy).

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

**F7 — No trade-side tables exist.** `trades`, `orders`, `positions` are prose (`system-design.md` §4.13; `trading-intelligence-architecture.md` §18.8 even says a filled manual plan is recorded in "the existing `trades` table"). Migration head is `0011`. Every table in §6.8 is new.

**F8 — `Opportunity` has no identity, and the cache forgets.** `StrategyOutcome.opportunity_id` is required, but `Opportunity` carries no id and `OpportunityCache` overwrites the previous `Opportunity` for the same `(symbol, strategy)`. The Backtest Runner minted the first id (`uuid4()` at the moment a signal is accepted; its docstring calls itself "the first thing to mint one"). A live path must choose where to mint it and must persist the thesis (`structural_*`, `confidence`, `evidence`) at that moment, because the cache will not still hold it at close.

**F9 — `fill_simulator` cannot be reused as a live venue as-is.** `simulate_exit()` walks a fully precomputed candle list forward from the entry and raises `InsufficientReplayDataError` until the entry day's session close is *in* the list. That is exactly right for a replay and unusable incrementally. What *is* reusable are its conventions (entry at the next open, stop wins a same-bar tie, `eod_flatten` at the real regular-session close, zero slippage and no commission modelled, `qty = 1`) and its two pure helpers `compute_realized_r` / `compute_realized_pnl`. A live simulated venue needs an incremental fill model (EX-8).

**F10 — `StrategyOutcome` has two representational gaps.**
(a) *No strategy, no row.* `strategy_name`, `strategy_version`, `opportunity_id`, `structural_invalidation`, `structural_target`, `final_stop`, `final_target`, `confidence_at_signal`, `evidence` are all required and `NOT NULL`. A manual trade with no corroborating strategy has none of them — and `TradeRequest` (§18.2) has no stop field while `TradePlan.stop` is required, so even the plan cannot be completed without one. `origin: "manual"` exists in the schema, but only a corroborated manual trade fits it.
(b) *No way to say "simulated money".* `is_backtest` is boolean and every performance query treats it as a hard population boundary. Outcomes from a simulated live venue would have to be stored either as `is_backtest = False` (they would blend with real-money rows the day a real venue exists, in every query, silently) or as `is_backtest = True` (which claims a `backtests` run that does not exist). Neither is honest. This is EX-2, and it must be settled before the first row is written.

**F11 — D17's live half is a different problem from its backtest half.** Decision #128 resolved the backtest path by turning a `None` snapshot into a `DiscardedSignal` (no row). For a *live* fill the money is already on the line: discarding the outcome would drop a real (or simulated-real) win or loss from the evidence table, a survivorship bias in exactly the table meant to judge strategies. Also, `capture_strategy_outcome_snapshots()` reads *current* engine state, not state as of `fill_ts`; a fill handled late reads a later state. The market-state half carries `candle_ts` so staleness is checkable; the context half does not.

**F12 — Position-effect and direction vocabulary is not aligned.** `Opportunity`, `StrategyOutcome`, `OrderRequest`, `OrderApproved` use `BUY`/`SELL`; `TradeRequest`/`TradePlan` use `long`/`short`. A `SELL` is ambiguous between closing a long and opening a short — the same ambiguity `trading-intelligence-architecture.md` §18.6 removed from the *hotkey* vocabulary but which reappears one level down at the order. Something must carry "opens" vs "closes" (EX-14).

**F13 — Two owners for "open positions", and a lane mismatch.** `system-design.md` §4.8's table has Position Monitor emit `PositionAdjusted`/`PositionClosed` and persist `positions`, while §4.6 has Portfolio State track open positions from `OrderFilled`/`PositionClosed` — two modules owning one fact, against principle 3 ("single source of truth for shared state"). Separately, Portfolio State feeds the Governor's exposure and daily-loss checks, but `PositionClosed` rides the *normal* lane, where a burst of ticks can delay it; a Governor deciding on stale exposure is precisely what the two-lane design exists to prevent. (EX-6.)

---

## 3. Invariants this design must honor

Restated from existing decisions and code; **I2 is the one reconciliation** (F5), proposed here, not decided.

| # | Invariant | Source |
|---|---|---|
| I1 | The Execution Engine is the only module that calls a venue's `place_order()`. | `system-design.md` §4.9 |
| I2 | *(reconciled)* No **risk-increasing** order reaches a venue without a Governor-class authorization decision on record; the Execution Engine places, the Governor authorizes, and manual mode adds a human confirmation *after* authorization. Whether protective exits also need one is EX-5. | `base.py:BrokerAdapter` docstring; §4.9; §18.5 |
| I3 | Honest absence over fabricated state: no invented fills, snapshots, commissions, or account values; `None`/absent means "not known". | `strategy-engine-design.md` §11; `state_snapshot.py`; `world_view/composite.py` |
| I4 | Live, backtest, and simulated-money populations are never blended in a query. | `performance_queries.py:_common_filters`; decisions #128, #140 |
| I5 | Compute once, own once: exactly one module owns each piece of shared state (positions, buying power, daily P&L, execution mode). | `system-design.md` §2 principles 3, 8 |
| I6 | Dry-run is the default; nothing leaves the process without an explicit switch. | `system-design.md` §4.9 |
| I7 | A critical-lane handler must never wait on the network. | `bus.py:EventBus._consume` (F6) |
| I8 | Order and fill facts are durable before they are announced. | F6, F7 (proposal) |
| I9 | Tests run against real PostgreSQL 16, never SQLite or mocks; strategies and engines never read wall-clock where an event timestamp exists. | project testing baseline; `fill_simulator.py`, `runner.py` |

---

## 4. What one live `StrategyOutcome` needs — field-by-field source map

The first build succeeds when a row exists. This table is the minimum plumbing, derived from `schemas/performance.py:StrategyOutcome` and the Backtest Runner's `_build_strategy_outcome()` (the only existing constructor).

| Field(s) | Live source | Exists? |
|---|---|---|
| `outcome_id` | minted by the writer (`uuid4()`) | trivial |
| `opportunity_id` | minted where the signal is accepted (EX-9) and persisted with the thesis | **new** (F8) |
| `schema_version`, `origin`, `is_backtest`, `backtest_run_id` | constants for the auto/live path (`1`, `"auto"`, `False`, `None`) — **plus a venue label** so simulated money is separable (EX-2) | **new label** (F10b) |
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
| `market_state_at_entry`, `context_at_entry` | `capture_strategy_outcome_snapshots()` at the entry fill (F11 / EX-7) | built, policy open |
| `market_state_at_exit`, `context_at_exit` | same, at the exit fill | built, policy open |
| `feature_snapshot_id` | `None` — `feature_snapshots` does not exist | honest `None` |

Reading the table: everything except **opportunity identity, order/fill linkage, exit-reason plumbing, and the population label** already exists. That is the size of the gap the Execution/Portfolio slice has to close.

---

## 5. Slice analysis

"Smallest vertical slice" = the smallest set of new modules that turns one actionable `OpportunityCreated` into one `strategy_outcomes` row for a *non-backtest* trade. Three candidates were evaluated; none is locked here.

### Slice A — simulated venue, auto path, thin authorizer stub

```
OpportunityCreated → [authorizer stub] → OrderApproved → Execution Engine → SimulatedVenue
   → OrderFilled → Portfolio State → Position Monitor-lite (stop / target / EOD) → exit order
   → OrderFilled → position closed → OutcomeRecorder → record_strategy_outcome()
```

- **New modules:** authorizer stub (emits `TradePlanned` → `GovernorDecision` → `OrderApproved`/`PlanRejected`), `SimulatedVenue`, Execution Engine, Portfolio State Engine, Position Monitor-lite, `OutcomeRecorder`, order/position ledger tables, and the four missing payload models.
- **Deliberately not in the slice:** ranking (D4), arbitration (D1), Kelly sizing (needs outcome data — the chicken-and-egg this slice exists to break), correlation, scaling/trailing stops, manual mode and the Approval Queue, emergency actions, real broker connectivity.
- **Pro:** needs no external service; fully testable against real Postgres with deterministic fixtures; produces the D17-live row; every module it builds is one the eventual real path also needs; the venue is behind a port, so IBKR paper slots in later.
- **Con:** inverts the roadmap's stage order (Phase 6's Execution before Phase 5's Decision/Planning/Governor), so the stub authorizer must not calcify into "the Governor" (EX-4, EX-1). Outcomes come from *simulated* fills, so EX-2 (labelling) is a precondition, and fill quality is only as good as the fill model (EX-8) and the tick feed (the free-tier Finnhub trade feed is IEX-only, not the consolidated tape — decision #95).

### Slice B — manual-first (Approval Queue, human in the loop)

`LONG`/`SHORT` hotkey → `TradeRequest` → Trade Planning → Governor → **Approval Queue** → human confirm → Execution → venue.

- **Pro:** manual trading is a first-class mode in this architecture, and `trading-intelligence-architecture.md` §18 designs it in detail.
- **Con — three separate blockers:** (1) it still needs an authorizer, Trade Planning, and a venue (F5); (2) it needs the entire frontend Input Layer, `TradeTarget`, hotkey module, and Approval Queue UI, none of which exists; (3) a manual trade with no corroborating strategy **cannot be a `StrategyOutcome` at all** (F10a), so it does not close D17's live half unless the trade happens to be corroborated. `future-ideas.md` #14's trigger ("manual trade entry ships and real usage shows demand") is also not met — manual entry has not shipped. Slice B is a good *second* slice on top of A's Execution/Portfolio core, not a first one.

### Slice C — IBKR paper venue

- **Recorded as deferred, not designed around.** `future-ideas.md` #27 is blocked and deliberately set aside; F4 lists what the adapter needs (writable connection, paper-only guard, client-id policy, fill callbacks, `fetchFields` decision) and all of it would be unverified live. §8 lists these as prerequisites. Nothing here recommends unblocking #27.

### Recommendation (not a decision)

**Slice A**, with the venue behind a narrow port (EX-3) so Slice C is additive, and with Slice B's `ExecutionMode` gate present as a seam (a mode value the engine already understands) without building the queue. If Saqib prefers to wait for a real Decision/Planning/Governor before any Execution work, that is a legitimate answer to EX-1 — the cost is that D4 and D17 stay blocked for as long as those stages take.

---

## 6. Component design for the recommended slice (Slice A)

Everything in this section is **proposal**. Module names follow `system-design.md` §8's planned folder tree where it has one (`execution_engine/`, `portfolio_state/`, `position_monitor/`, `governor/`); names it lacks are suggestions.

### 6.1 Data flow between modules

```
 Feature Engine ─► Market State Engine ─► Context Engine                       [built]
                          │
                          ▼
              Strategy Scheduler + gate_conditions                             [built]
                          │  OpportunityCreated  (normal lane)
            ┌─────────────┴────────────────┐
            ▼                              ▼
   Opportunity Cache  [built]      Authorizer stub  [slice A — EX-4; D1 stays open]
   latest per symbol+strategy;     1. mint opportunity_id, persist the thesis   (EX-9)
   overwrites; no ids              2. v0 rules  ◄── Portfolio State (positions, in-flight
                                      orders, daily realized loss), MarketClock
                                   3. emit TradePlanned ─► GovernorDecision
                                                │
                              ┌─────────────────┴───────────────────┐
                              ▼                                     ▼
                    PlanRejected (critical)                OrderApproved (critical)
                    [model built]                          [model built; extended — EX-9]
                                                                    │  subscribe ─► own queue ─► worker  (I7)
                                                                    ▼
                                                        Execution Engine  [slice A]
                                        write-ahead ledger ─► mode gate ─► venue port  (EX-3)
                                          orders/fills (new)                │ place_order()
                                                 ▲                          ▼
                                                 │        ┌────────────────┴────────────────────┐
                                                 │        ▼                                     ▼
                                                 │  SimulatedVenue [slice A]        IBKRAdapter [partial: readonly=True,
                                                 │  fills on PriceUpdated ticks     place_order = NotImplementedError,
                                                 │        │                         unverified live — §8, #27]
                                                 └────────┘ order updates (new callback — EX-3)
                                                 │
                                                 │  OrderFilled (critical) — payload extended (EX-9)
                    ┌────────────────────────────┼─────────────────────────────┐
                    ▼                            ▼                             ▼
          Portfolio State [slice A]     Position Monitor-lite [slice A]   OutcomeRecorder [slice A]
          owns positions, in-flight     reads PriceUpdated/CandleClosed   captures ENTRY snapshots
          orders, daily P&L (EX-6)      for held symbols; stop / target /  (state_snapshot.py)
                    │                   EOD ─► reduce-only exit order            │
                    │                            │ (back into Execution Engine)  │
                    │ PositionClosed             ▼                               │
                    │ (lane: EX-6;        OrderFilled (exit) ─► Portfolio State  │
                    │  model not built)                                          │
                    └────────────────────────────────────────────────────────────┤
                                                                                  ▼
                              OutcomeRecorder: capture EXIT snapshots ─► build StrategyOutcome
                                              (population label EX-2; snapshot policy EX-7)
                                                                                  │ asyncio.to_thread
                                                                                  ▼
                       record_strategy_outcome()  [built; backtest is its only caller] ─► strategy_outcomes [built]
                                                                                  ▼
                       performance_queries [built] ─► World View [built; portfolio slot stays null until Portfolio State exists]
```

Read the diagram as: **solid new work is the middle column** (authorizer stub → Execution → venue → Portfolio State / Position Monitor-lite / OutcomeRecorder); everything above the stub and below `record_strategy_outcome()` already exists.

### 6.2 Authorizer stub (`governor/` — name deliberately provisional)

**What it is.** One small subscriber that stands in for the unbuilt Decision → Trade Planning → Governor chain so downstream modules see the *real* vocabulary (`TradePlanned`, `GovernorDecision`, `OrderApproved`/`PlanRejected`) from day one. It is not a commitment to any of D1's shapes (merged Decision+Governor, or two components): it emits the three events in order and nothing more.

**What it does (proposal, all parameters Saqib's to set — EX-4):**
1. For an `OpportunityCreated` with `status == "actionable"`: mint `opportunity_id`, persist the `Opportunity` snapshot (thesis fields) to the `trades` ledger row (F8).
2. Evaluate v0 rules, every one reading state rather than owning it: regular session only (`MarketClock`, same shape as `gate_conditions` `{"session": "regular"}`); no open position or in-flight order for the symbol (Portfolio State); max concurrent positions; daily realized-loss cap (Portfolio State); **both snapshot halves available for the symbol** (the pre-trade gate that lets EX-7 avoid discarding a real fill later); fixed quantity.
3. Publish `TradePlanned` (entry = none/"market", stop/target = `structural_*`, size = fixed — mirroring the Backtest Runner's `final_* == structural_*` and `qty = 1` conventions), then `GovernorDecision` (`approved` or `rejected`, `reasons` always populated — a rejection is logged like an approval, `trading-intelligence-architecture.md` §12), then `OrderApproved` or `PlanRejected`.

**What it must not do:** rank, arbitrate between strategies (D4/D1), size by Kelly, or modify a `StrategyConfig` (`strategy-engine-design.md` §6's boundary).

### 6.3 Execution Engine (`execution_engine/`)

**What it is.** The only module that talks to a venue (I1). It turns an authorization (or a reduce-only exit intent) into a durable order, sends it, and turns every venue update into ledger state and an `OrderFilled`.

```
 OrderApproved (critical lane)                     exit intent (Position Monitor-lite; reduce-only)
          │                                                        │
          ▼                                                        ▼
  on_order_approved()  ── put_nowait ──►  [ execution queue ]  ◄── put_nowait ── on_exit_intent()
  (returns at once: the bus's critical                 │
   lane never waits on the network — I7)               ▼
                                              _worker_loop()   single writer
                                                       │
        ┌──────────────────────────────────────────────┼──────────────────────────────┐
        ▼                                              ▼                              ▼
 1. validate + dedupe                        2. WRITE-AHEAD ledger row          3. mode gate
    order_id already seen? drop                orders.status = approved          dry_run → log intent, stop
    reduce-only guard (exit orders:            (asyncio.to_thread; durable       manual  → approval queue  [deferred]
    Portfolio State must show a                before any venue call — I8)       auto    → venue.place_order()
    matching open position)                                                                │
                                                                                            ▼
                                              4. OrderAck: submitted | rejected  ◄──────────┘
                                                 persist status; publish rejection [event: EX-9]
                                                                │
   venue.on_order_update(cb) ── put_nowait ──► [ execution queue ] ──► 5. apply update
                                                                          dedupe (order_id, fill_id)
                                                                          cumulative qty ≤ ordered qty (else: halt, alert)
                                                                          status → partially_filled | filled | cancelled
                                                                          persist fill, THEN publish OrderFilled (critical)
```

**Order state machine** (proposal; the persisted `status` column):

```
 approved ──► submitted ──► partially_filled ──► filled
    │             │  │              │
    │             │  └──► cancelled ◄┘          (cancel_order acknowledged)
    │             └─────► rejected              (venue refused; reason kept)
    └─ duplicate order_id ─► dropped (no new row; logged)
 submitted | partially_filled ──(no update within T)──► unknown ──► reconciled against the venue ──► any of the above
```

**Mode gate.** Three orthogonal ideas that the prose uses interchangeably are separated here (EX-2, EX-13): *dry-run* = the order never leaves the process and **no fill exists** (so no outcome is produced); *simulated* = an in-process venue produces fills (outcomes are produced and must be labelled); *paper/live* = a real broker. `ExecutionMode` (`auto` | `manual`, `trading-intelligence-architecture.md` §18.5) is a separate axis — *who triggers placement* — and is present in slice A only as a seam (`auto`), with the Approval Queue not built.

**Payload and event work this implies** (all additive-optional per `system-design.md` §10.2, so no `version` bumps; EX-9):
- `OrderApproved` += `opportunity_id`, `position_effect` (`open`|`close`), `origin`, and for exits `exit_reason`.
- `OrderFilled` += `fill_id`, `cumulative_qty`, `leaves_qty`, `venue`, optional `commission` (`None` unless the venue supplies it).
- New models: `TradePlanned`, `OpportunitySelected` (reserved, unused in slice A), `PositionAdjusted` (reserved), `PositionClosed` (§6.5).
- A venue-level rejection/cancel needs a representation distinct from the plan-level `PlanRejected{symbol, reasons}` (EX-9).

### 6.4 `SimulatedVenue` (`broker_adapters/`, behind the venue port — EX-3)

- **Behaves like a broker, owns no truth:** `place_order()` acks; fills arrive through the same order-update callback a real adapter would use; its own `get_positions()` exists for reconciliation tests only — Portfolio State is the source of position truth (I5).
- **Price source:** subscribes to `PriceUpdated`. A market order fills at the first tick at or after acceptance; a limit order when a tick crosses it. Fill timestamps are the tick's `exchange_ts` (event time, I9). Slippage and commission are `None`/zero by default (I3) and parameters of EX-8.
- **Session guard:** rejects outside the regular session in v1 (`MarketClock`).
- **Deterministic under test:** tick source and clock are injectable; a partial-fill injector exists so the state machine's partial path is exercised even though v1 fills whole.
- **Parity with the backtest, stated not assumed:** the Backtest Runner fills at the *next candle's open* and exits *at the stop/target price*; a tick-driven venue fills at the *observed tick* and exits at the tick that breached the level. Those differ, and expectancy from one is not directly comparable with the other until EX-8 fixes the convention and the doc records the delta.

### 6.5 Portfolio State Engine (`portfolio_state/`)

**What it is.** The single owner of account/position truth (I5): positions, in-flight orders, realized P&L for the trading day, gross exposure, execution mode. Read synchronously by the authorizer, the Position Monitor, the Execution Engine's reduce-only guard, and (later) World View.

```
 OrderApproved (critical) ─┐                       [in-flight order recorded — see below]
 OrderFilled   (critical) ─┼─► on_*() ── put_nowait ──► [ portfolio queue ] ──► _worker_loop()   single writer
 PlanRejected  (critical) ─┘                                                        │
                                                                     apply(event)
                              ┌───────────────────────────────┬───────────────────┴────────────┐
                              ▼                               ▼                                ▼
                    OrderApproved: add to           OrderFilled, position_effect=open:  OrderFilled, position_effect=close:
                    in_flight[order_id]             positions[symbol] = qty, avg_price,  realized_pnl_today += (exit − avg) × qty × sign
                    (so a second signal on the      opened_at, opportunity_id, side;     if qty == 0 → position closed
                    same symbol sees it before      remove/reduce in_flight              │
                    the first fill lands)                        │                        │
                              └───────────────────────────────┴────────────┬───────────┘
                                                                            ▼
                                                        persist positions row (asyncio.to_thread)
                                                                            │
                                            ┌───────────────────────────────┴───────────────┐
                                            ▼                                               ▼
                                  in-memory snapshot updated               publish PositionClosed (when closed)
                                  (get_snapshot(), sync, no I/O)           [lane and owner: EX-6; model not built]
```

- **State (proposal):** `positions{symbol → side, qty, avg_price, opened_at, opportunity_id, status: open|closing}`, `in_flight{order_id → symbol, side, qty, position_effect}`, `realized_pnl_today` (bucketed by `MarketClock.trading_day`), `gross_exposure`, `open_position_count`, `execution_mode`. **`buying_power`/cash is `None`** unless a venue supplies it (I3); slice A's rules do not need it.
- **Deviation from §4.6 worth naming:** the prose has Portfolio State consume only `OrderFilled` and `PositionClosed`. That leaves a race: two `OpportunityCreated` events for one symbol milliseconds apart would both see "no position" before the first fill lands. Consuming `OrderApproved` as an *in-flight* record closes it without a second owner of the fact. (Part of EX-6.)
- **Honesty convention:** `get_snapshot(symbol=None)` mirrors `MarketStateEngine.get_snapshot()` — absent means not-yet, never a fabricated default; World View's `portfolio=None` stays `None` until this exists and is wired in a *separate* task.
- **Restart:** rebuild `positions` and in-flight orders from the ledger before accepting events; venue reconciliation for real venues (§8); the simulated venue's book is rebuilt from the ledger, and pending orders older than the restart are marked `unknown` then cancelled.
- **Not in slice A:** pairwise correlation, buying power, risk-budget consumption, `ExecutionModeChanged`.

### 6.6 Position Monitor-lite (`position_monitor/`)

**What it is (and isn't).** Only the three exit rules the Backtest Runner already models — stop, target, and `eod_flatten` at the real regular-session close — evaluated live for symbols Portfolio State reports open. It is **not** the module `trading-intelligence-architecture.md` §13 describes (is the thesis still valid, is momentum weakening, move the stop, take a partial, exit, reverse, hold); those questions, manual-position handling (`future-ideas.md` #14) and emergency actions (#16) are out of scope.

- **Inputs:** `PriceUpdated` and `CandleClosed` for held symbols; `MarketClock` for the EOD instant (the same derivation `fill_simulator.regular_session_close_utc` uses).
- **Output:** one reduce-only exit intent per position (idempotent: the position moves to `closing` first), carrying `exit_reason ∈ {stop, target, eod_flatten}`.
- **Stop/target enforcement is in-process here** — acceptable for a simulated venue with no broker. It is *not* acceptable for a real venue (a crash would leave a position without a stop): broker-side protective orders become a hard prerequisite before any real venue (§8, EX-11).

### 6.7 `OutcomeRecorder` and D17's live half (`trading_intelligence/`)

**What it is.** The one place that turns a closed position into a `StrategyOutcome` and calls `record_strategy_outcome()` — the live-path caller D17 says does not exist.

```
 OrderFilled (entry) ─► on_order_filled() ─► [ recorder queue ] ─► _worker_loop()
                                                   │   entry fill?
                                                   ▼
                     capture_strategy_outcome_snapshots(symbol)   ← market_state carries its own candle_ts
                     persist {market_state, context, captured_at} on the trade row   (survives a restart)

 PositionClosed ─► on_position_closed() ─► [ recorder queue ] ─► _worker_loop()
                                                   ▼
                     load trade row: thesis, entry snapshots, order/fill ids, exit_reason
                     capture EXIT snapshots ──► EX-7 policy applied
                     build StrategyOutcome  (field-by-field map: §4)
                     asyncio.to_thread(record_strategy_outcome, outcome)      ← same call shape BacktestRunner uses
                        ├─ ok    ─► trade.outcome_id set
                        └─ raises ─► trade.outcome_status = pending_retry; log loudly — never drop
                                     (record_strategy_outcome() raises by design, performance.py docstring)
```

**D17's live choice is EX-7.** The four honest options: *(a)* discard, as #128 does for backtests — rejected as a default for live because the trade already happened (F11); *(b)* make the four snapshot columns nullable — a migration and a change to every query that reads them; *(c)* an explicit "unavailable" sentinel inside the dicts — no schema change, but pollutes readers of `trend_score` keys and skirts #128's "never a fabricated `{}`"; *(d)* **a pre-trade gate** — the authorizer refuses to approve a symbol unless both snapshot halves exist, so the remaining `None` case is only "exit after a restart wiped in-memory engine state", which then needs (b) or (c) as its narrow fallback.

### 6.8 Persistence sketch (proposal — no migration is created by this design)

Names follow `system-design.md` §4.13; columns are illustrative. All writes go through `asyncio.to_thread` (the repository's sync-engine pattern) and precede the corresponding event (I8).

| Table | Purpose | Key columns |
|---|---|---|
| `trades` | One row per authorized plan, approved or rejected; holds the thesis snapshot the cache will not keep | `trade_id` (= `opportunity_id`), `origin`, strategy name/version, `direction`, thesis (`structural_*`, `final_*`, `confidence`, `evidence`), authorizer decision + `reasons`, `status`, entry snapshots (JSONB), `outcome_id`, `outcome_status`, population label (EX-2) |
| `orders` | The order ledger and state machine | `order_id`, `trade_id`, `venue`, `venue_order_id`, `symbol`, `side`, `position_effect`, `qty`, `order_type`, `limit_price`, `status`, `exit_reason`, timestamps |
| `fills` | Every fill, deduplicated | `fill_id` (unique with `order_id`), `qty`, `price`, `fill_ts`, `commission` (nullable) |
| `positions` | Position accounting (owner per EX-6) | `position_id`, `trade_id`, `symbol`, `side`, `qty`, `avg_price`, `opened_at`, `closed_at`, `status`, `realized_pnl` |

`market_events` (the durable bus log) is **not** required by this design: the ledger is the durable record, which is what I8 needs; `market_events` remains separate future work (EX-10).

### 6.9 Safety invariants → mechanisms

| Invariant | Proposed mechanism | Proposed test |
|---|---|---|
| I1 only Execution places | the venue is reachable only through the Execution Engine's router; no route/`Depends` hands a venue to any other module | a grep-style test that no other module imports a venue's `place_order` |
| I2 authorization precedes any risk-increasing order | Execution refuses an entry `OrderApproved` lacking a persisted authorizer decision for its `trade_id` | an `OrderApproved` published with no decision row is rejected and logged |
| I3 honest absence | `commission`, `slippage`, `buying_power` stay `None`; no fabricated snapshot | outcome rows with `commission_total IS NULL` on the simulated venue |
| I4 population separation | the population label (EX-2) is written on every row and defaulted into every query | a live-labelled query never returns a simulated-labelled row |
| I5 single owner | only Portfolio State writes `positions`; others read `get_snapshot()` | a second-writer grep test |
| I6 dry-run default | venue selection defaults to `dry_run`; simulated/paper/live need an explicit setting | default-config test produces no fill |
| I7 no network on the critical lane | handlers only `put_nowait` | a slow fake venue does not delay an unrelated critical event |
| I8 durable before announced | ledger write awaited before `publish` | kill-between-write-and-publish restart test |
| I9 event time | fills stamped from tick `exchange_ts`; no `datetime.now()` in fill logic | fixture replay yields identical rows across runs |

---

## 7. Open forks (nothing below is decided)

Provisional labels **EX-1 … EX-14**. "Needs Saqib" marks forks where the answer depends on product, risk, or ordering judgment; the rest carry a recommendation the build task can proceed on unless Saqib objects.

| Fork | Question | Recommendation | Needs Saqib |
|---|---|---|---|
| EX-1 | Build order: Execution-first with a stub authorizer, manual-first, or wait for the roadmap's Phase 5 stages? | Slice A | **yes** |
| EX-2 | How are simulated-money outcomes kept apart from real-money ones? | an explicit `execution_venue` label | **yes** |
| EX-3 | What is the venue port, and how does Execution obtain a venue? | a narrow order-venue port + an `execution` registry role | **yes** |
| EX-4 | Shape of the authorizer stub and its v0 rule *numbers* | one stub emitting the real event vocabulary | **yes (numbers)** |
| EX-5 | Do protective exits need a Governor-class decision? | no, but reduce-only and Portfolio-State-checked | yes (amends I2) |
| EX-6 | Who owns position accounting, in-flight orders, and `PositionClosed`'s lane? | Portfolio State; in-flight from `OrderApproved`; `PositionClosed` on the critical lane | **yes** |
| EX-7 | D17's live policy for missing snapshots | pre-trade gate + a narrow post-restart fallback | **yes** |
| EX-8 | Simulated fill model; reuse of `fill_simulator` | conventions shared, incremental model new, parity delta documented | no |
| EX-9 | Identity and payloads: `opportunity_id`, `order_id`, `fill_id`, new models, venue-level rejection | mint at acceptance; additive fields | no (mostly) |
| EX-10 | Durability: ledger tables vs a durable event log | ledger only; `market_events` stays separate | no |
| EX-11 | Exit enforcement: in-process vs broker-side | in-process for simulated; broker-side mandatory before any real venue | no |
| EX-12 | Who writes `StrategyOutcome`, and does every trade become one? | `OutcomeRecorder`; strategy-attributed trades only | yes |
| EX-13 | Is manual mode / the Approval Queue in the first build? | no — keep the `ExecutionMode` seam | no |
| EX-14 | `BUY/SELL` vs `long/short`, and how an order says "opens" vs "closes" | keep `BUY/SELL` on orders + explicit `position_effect` | no |

### EX-1 — Slice order  · OPEN · needs Saqib
**Question.** Build the Execution/Portfolio core first (Slice A, §5) with a stubbed authorizer, start with manual mode (Slice B), or wait for a real Decision/Planning/Governor?
**Options.** (a) **Slice A** — simulated venue, auto path, stub authorizer. (b) **Slice B** — manual-first with Approval Queue and Input Layer. (c) **Roadmap order** — build Opportunity Engine → Decision → Planning → Governor first, then Execution.
**Evidence.** Decision Engine/Governor "genuinely wait for real outcome data" (D4, D17); the only source of that data is a trade lifecycle; the IBKR path is unverified (#27); no manual-trading code or UI exists (F4, §1); manual trades cannot be `StrategyOutcome`s without a strategy (F10a).
**Consequence.** (a) unblocks D17-live and D4's data starvation soonest but must keep the stub honest; (b) needs the largest frontend build and still needs an authorizer and a venue; (c) preserves stage order but leaves every downstream module blocked on data that cannot exist yet, and Kelly-style sizing in Planning has no edge estimate to use.
**Recommendation.** (a).

### EX-2 — Population label for simulated-money outcomes  · OPEN · needs Saqib
**Question.** `StrategyOutcome.is_backtest` is a boolean and every query treats it as a hard boundary (I4). Where do outcomes from a *simulated live venue* go?
**Options.** (a) **Add an `execution_venue` value** (`simulated | paper | live`; backtests keep `is_backtest = True`) as an additive column and make every live query default-filter to the venue it means. (b) **A synthetic `backtests` row per paper session** with `is_backtest = True` — no migration, but claims a run that is not a backtest and inherits `config_hash`/data-version meaning that does not apply. (c) **A separate table** for simulated live outcomes — clean separation, but a parallel query layer.
**Evidence.** F10b; `performance_queries.py:_common_filters`; `strategy_outcomes` has no venue column and no DB check tying `is_backtest` to `backtest_run_id` (§1.3).
**Consequence.** (a) touches `StrategyOutcome` (additive field — `schema_version` policy per `strategy-engine-design.md` §5 must be checked), one migration, and every read query gains a venue predicate; (b) hides a semantic lie in the most-queried table; (c) doubles read code.
**Recommendation.** (a), decided **before the first row is written** — retrofitting a label onto rows already stored as `is_backtest = False` is not possible without guessing.

### EX-3 — Venue port and registry role  · OPEN · needs Saqib
**Question.** How does the Execution Engine talk to a venue, given F3 (no fill callback) and that `BrokerAdapter` extends `MarketDataProvider` (a simulated venue would have to stub streaming and history)?
**Options.** (a) **Extend `BrokerAdapter`** with an order-update callback, client order id, and open-order query; the simulated venue subclasses it and stubs the data methods. (b) **A new narrow `OrderVenue` port** (`place_order`, `cancel_order`, `get_positions`, `on_order_update`, open-order query) that `BrokerAdapter` inherits, so `IBKRAdapter` satisfies it and `SimulatedVenue` implements *only* it. (c) **Composition** — an adapter class wrapping a `BrokerAdapter`; more code, no contract change.
**Evidence.** `base.py`; `broker_registry.py` has `streaming`/`historical` only; `system-design.md` §2 principle 1 ("nothing above the adapter layer knows IBKR or Alpaca exists. Everything talks to a `BrokerAdapter` interface").
**Consequence.** (a) is smallest but makes the simulated venue a lie about being a data provider; (b) is a small refactor of `base.py` (a code change for the build task, not this one) and keeps principle 1; (c) grows a wrapper layer. Whichever is chosen, the registry needs an `execution` role distinct from streaming/historical so Execution can be pointed at `simulated` while IBKR keeps streaming.
**Recommendation.** (b) plus the `execution` registry role.

### EX-4 — Authorizer stub: shape and rule numbers  · OPEN · needs Saqib (numbers)
**Question.** What stands in for Decision → Planning → Governor in slice A, and what are its v0 numbers?
**Options.** (a) **One stub** emitting `TradePlanned → GovernorDecision → OrderApproved/PlanRejected` (§6.2). (b) **Two thin modules** — a fixed-size Planning passthrough and a rules-only Governor v0. (c) **A human approving each order** (needs the Approval Queue, EX-13).
**Evidence.** D1 (Decision Engine and Governor separate or merged) is open; §6.2's stub is not a commitment to either; the four rule inputs it needs exist or are in this design.
**Consequence.** (b) is closer to the final shape but pre-empts D1; (a) keeps D1 open at the cost of one module that will be split or absorbed later. **The numbers are risk policy, not engineering:** maximum concurrent positions, daily realized-loss cap, and the fixed quantity (or fixed dollar-risk) per trade. This design will not invent them.
**Recommendation.** (a); Saqib supplies the three numbers (the simulated venue makes any values safe to start with).

### EX-5 — Do protective exits need authorization?  · OPEN · amends I2
**Question.** I2 as reconciled covers *risk-increasing* orders. Does a stop, target, or EOD-flatten exit also need a Governor-class decision?
**Options.** (a) **No** — exits are *reduce-only*, checked by the Execution Engine against Portfolio State, carrying an `exit_reason`. (b) **Yes** — every order, including exits, gets a `GovernorDecision`.
**Evidence.** `trading-intelligence-architecture.md` §12 frames the Governor as a *risk gate* on new exposure and §13's Position Monitor issues exits; an authorization round-trip on a stop adds latency exactly when it hurts; the emergency-action design (#16) is the same shape (reduce/flatten without approval).
**Recommendation.** (a) with the reduce-only guard as the enforced mechanism (§6.3 step 1).

### EX-6 — Position accounting owner, in-flight orders, `PositionClosed` lane  · OPEN · needs Saqib
**Question.** F13: two modules are documented as owning open positions, and the lane for `PositionClosed` can lag Governor decisions.
**Options.** (a) **Portfolio State owns accounting** (fills → positions → closure), tracks in-flight orders from `OrderApproved`, and emits `PositionClosed`; Position Monitor is a decision module that reads it and emits exit intents; `PositionClosed` joins the critical lane. (b) **Position Monitor owns accounting** as `system-design.md` §4.8's table has it; Portfolio State becomes a read aggregate over Position Monitor's `positions`. (c) **Keep §4.6/§4.8 as written** (both consume/emit; `PositionClosed` on the normal lane).
**Evidence.** §4.6, §4.8, `CRITICAL_EVENT_TYPES`; the double-entry race in §6.5.
**Consequence.** (a) departs from §4.8's "Position Monitor emits `PositionClosed`" and adds one member to the critical set (two lines in `envelope.py` plus a doc row); (b) leaves exposure reads one hop stale; (c) keeps the double-owner problem and the lane lag.
**Recommendation.** (a).

### EX-7 — D17 live policy for missing snapshots  · OPEN · needs Saqib
**Question.** What happens when `capture_strategy_outcome_snapshots()` returns `None` for a real (or simulated-real) fill (F11)?
**Options.** (a) **Discard** the outcome (#128's backtest convention). (b) **Nullable snapshot columns** (migration + reader changes). (c) **Explicit "unavailable" sentinel** inside the dicts. (d) **Pre-trade gate**: the authorizer will not approve a symbol lacking both snapshots, leaving only the post-restart exit case, which then uses (b) or (c).
**Evidence.** #128; `state_snapshot.py`; the market-state dict carries `candle_ts`, the context dict does not.
**Consequence.** (a) is survivorship bias for live money; (b) is honest but the widest change; (c) is cheap but pollutes readers and stretches #128's "never a fabricated `{}`"; (d) shrinks the problem to a rare case and keeps rows honest.
**Recommendation.** (d), with (b) as the narrow fallback — and capture entry snapshots *at fill handling with `captured_at` and the state's `candle_ts` stored alongside*, so late capture is visible rather than hidden.

### EX-8 — Simulated fill model and `fill_simulator` reuse  · OPEN
**Options.** (a) **Reuse conventions only** (session-close EOD, stop-wins-tie, zero slippage/commission), write a new incremental model. (b) **Extract shared pure helpers** from `fill_simulator.py` — changes an existing, decision-locked module. (c) **Call `fill_simulator` incrementally** — impossible as written (F9).
**Consequence.** (a) leaves two implementations of the same conventions, mitigated by a **parity table** and an acceptance test that replays a fixture through both; (b) is cleaner but expands the build's file footprint into `backtest_runner/`.
**Recommendation.** (a); record the fills-at-next-tick vs fills-at-next-candle-open delta in the build's decision entry. Proceed unless Saqib objects.

### EX-9 — Identity and payloads  · OPEN
**Question.** Where is `opportunity_id` minted, who owns `order_id`, and what do the events carry?
**Options for `opportunity_id`.** (a) At the **authorizer's acceptance** (matches #128's "mint when the signal is accepted"; no change to `Opportunity`). (b) At `OpportunityCreated` publish (needs an `Opportunity`/payload field, a `base_strategy` change).
**`order_id`.** Minted by the stage that authorizes (`OrderApproved` already carries it); the venue's own id is stored as `venue_order_id`; Execution dedupes on `order_id`. **`fill_id`** is venue-supplied (simulated: deterministic).
**Payloads.** Additive fields in §6.3; new models `TradePlanned`, `PositionClosed` (and reserved `OpportunitySelected`, `PositionAdjusted`); **venue-level rejection/cancel** needs either a new critical event (e.g. an `OrderStatusChanged`) or reuse of `PlanRejected` (a semantic stretch — that event is plan-level and has no `order_id`). `TradePlanned`'s two prose definitions disagree (§10, finding R2).
**Recommendation.** (a) for id minting; a new order-status event rather than stretching `PlanRejected`; proceed unless Saqib objects.

### EX-10 — Durability  · OPEN
**Options.** (a) **Ledger-first**: `orders`/`fills`/`trades`/`positions` written before events are published (I8); no durable event log. (b) **Build `market_events`** as the durable log and derive state from it.
**Consequence.** (b) is a much larger piece of infrastructure than the slice needs; (a) satisfies I8 for execution and leaves `market_events` as independent future work.
**Recommendation.** (a).

### EX-11 — Exit enforcement  · OPEN
**Options.** (a) **In-process monitoring** (Position Monitor-lite) — the only option for a simulated venue. (b) **Broker-side protective orders** (bracket/OCO) — required for any real venue because a process crash must not leave an unprotected position; `OrderRequest` has no stop fields today.
**Recommendation.** (a) for slice A; **(b) recorded as a hard prerequisite** for any real-money venue (§8).

### EX-12 — Who writes `StrategyOutcome`; what belongs in it  · OPEN · needs Saqib
**Question.** F10a and F13: `PositionClosed{position_id, exit_price, realized_pnl, r_multiple_achieved, closed_ts}` (prose) cannot build a `StrategyOutcome`, and a strategy-less manual trade cannot be one at all.
**Options.** (a) A dedicated **`OutcomeRecorder`** joins the `trades`/`orders`/`fills` ledger with persisted snapshots; **`strategy_outcomes` holds strategy-attributed trades only** (corroborated manual trades included), while `trades` records everything. (b) **Enrich `PositionClosed`** so Performance Intelligence can build the row from the event alone. (c) **Relax the NOT NULL columns** so strategy-less manual trades fit `strategy_outcomes`.
**Consequence.** (b) fattens an event with data the ledger already holds; (c) is a broad schema change that dilutes what `strategy_outcomes` means (evidence about strategy configurations).
**Recommendation.** (a).

### EX-13 — Manual mode in the first build  · OPEN
**Options.** (a) **Not in scope** — the `ExecutionMode` gate exists as a seam (`auto` only); Approval Queue, Input Layer, and `TradeRequest` wait. (b) **In scope.** 
**Evidence.** §5 Slice B blockers; `TradeRequest` has no stop field while `TradePlan.stop` is required (F10a) — the manual design must answer where a manual trade's stop comes from, and that answer is not in `trading-intelligence-architecture.md` §18.
**Recommendation.** (a).

### EX-14 — Direction vocabulary and position effect  · OPEN
**Options.** (a) **Keep `BUY/SELL` on orders and outcomes** and add an explicit `position_effect` (`open|close`) on `OrderApproved`; `long/short` stays a planning-layer word. (b) Derive the effect in Execution from Portfolio State (implicit, fragile on a reversal or a late fill). (c) Change orders to `long/short`.
**Evidence.** F12; `StrategyOutcome.direction` is `BUY|SELL`.
**Recommendation.** (a).

---

## 8. Deferred prerequisites — not forks, and not designed here

These are recorded so they are not rediscovered; none is recommended for now.

- **IBKR paper/live venue** (`future-ideas.md` #27, blocked): writable connection (today `readonly=True`); a paper-only guard (the configured endpoint is the paper Gateway port, and a live port must be structurally unreachable, not merely unconfigured); a client-id policy for an order-owning connection versus the streaming connection; broker-side protective orders (EX-11); fill/status callbacks mapped into the venue port (F3, EX-3), including partials, cancels, and commissions; on-connect reconciliation of positions **and open orders** (the contract has no open-orders query); short-selling availability. **All of it is unverifiable until a real session is reached** — the adapter path is unverified live.
- **Emergency actions** (`future-ideas.md` #16, PANIC / flatten): a real-money venue should not be enabled before flatten-everything exists. Recommendation, not a locked rule.
- **Manual mode** — Input Layer, `TradeTarget`, hotkeys, Approval Queue UI, `ExecutionModeChanged`, and the manual trade's stop source (F10a).
- **A real Governor rule engine, Decision Engine (D1), and Opportunity Engine (D4)** — the stub is designed so these replace it without changing Execution.
- **Position Monitor proper** — thesis-validity checks, stop management, partials, reversal, manual-position handling (`future-ideas.md` #14).
- **Frontend** — Positions / Trade Management / order-status widgets, and any consumer of `orders.status`.
- **World View `portfolio` slot** — filled by a separate task once Portfolio State exists.

---

## 9. Acceptance criteria proposed for the eventual build task

1. **End-to-end fixture:** a scripted `OpportunityCreated` produces exactly one `strategy_outcomes` row — `is_backtest = False`, the EX-2 label set, `origin = "auto"`, every field per §4 — via the real Execution Engine, `SimulatedVenue`, Portfolio State, Position Monitor-lite, and `OutcomeRecorder`, against real PostgreSQL 16.
2. **Idempotency:** a duplicate `OrderApproved` (same `order_id`) creates no second order; a duplicate `fill_id` creates no second fill.
3. **Authorization gate:** an entry `OrderApproved` with no persisted authorizer decision is refused and logged (I2).
4. **Reduce-only guard:** an exit order with no matching open position is refused.
5. **Critical-lane isolation:** a venue whose `place_order()` blocks does not delay an unrelated critical event (I7).
6. **Durability/restart:** killing the process between the ledger write and the publish, then restarting, rebuilds Portfolio State and resumes without a duplicate order (I8).
7. **Honesty:** no fabricated commission, slippage, or snapshot; `outcome_status = pending_retry` (not a silent drop) when `record_strategy_outcome()` raises.
8. **Population separation:** a live-labelled query never returns a simulated-labelled or backtest row (I4).
9. **Parity:** the same fixture replayed through the Backtest Runner and through the live path yields outcomes whose differences are exactly those tabulated for EX-8.
10. **Regression:** the existing suite's known-clean baseline is unchanged; `record_strategy_outcome()` gains no new signature.

---

## 10. Findings outside this task's boundary (reported, not fixed — AGENTS.md §9)

- **R1 — Related follow-up.** `system-design.md` §4.1 still says "`IBKRAdapter` and `AlpacaAdapter` implement this" although decision #1 revised the Alpaca plan (its own folder tree already says "Alpaca deferred, not stubbed"; §2 principle 1 and the §3 diagram also still name Alpaca). Docs-only fix, unrelated to this design's correctness; left untouched except for the pointer sentences this delivery adds.
- **R2 — Related follow-up.** `TradePlanned`'s prose definitions disagree: `system-design.md` §10.3 has `max_hold_minutes`, `scaling_plan`, `trailing_stop_rule`, while `trading-intelligence-architecture.md` §18.3's `TradePlan` has `max_hold_seconds`, `origin`, `corroboration`. The model does not exist yet (F1), so nothing is broken; the build task must reconcile them (EX-9).
- **R3 — Related follow-up.** `trading-intelligence-architecture.md` §18.8 says a filled manual plan is recorded in "the existing `trades` table"; no such table exists (F7).
- **R4 — Related follow-up.** `performance.py`'s docstring forbids a live caller without a real Execution Engine; when the build lands, that docstring and D17's "live half" note in `strategy-engine-open-decisions.md` need the corresponding update (a docs step for the build task, not for this one).
- **R5 — Related follow-up.** `system-design.md` §4.9 names the `OrderApproved` payload `ApprovedOrder`; no such class exists — the model is `OrderApproved` itself (`schemas/events/execution.py`). Docs-only naming drift, folded into R1's fix.
- **R6 — Unrelated.** `IBKRAdapter.get_positions()` has no caller and no test today (§1.2). Noted, untouched.
