# Personal AI Trading Workspace — System Design Document
**Version:** 2.3 (Pre-Implementation)
**Status:** Phase 2 kickoff — Event Bus dispatch lanes and shared update-policy utility added
**Owner:** Saqib
**Companion documents:** [`trading-intelligence-architecture.md`](./trading-intelligence-architecture.md) — how the system thinks (market state, context, strategy, decision logic). [`../decisions/future-ideas.md`](../decisions/future-ideas.md) — concepts raised and deliberately deferred, with reasoning intact, so they aren't lost or re-argued from scratch. [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) — the running settled-decisions log. [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md) — phased delivery plan and exit criteria. This doc explains how the system is built. Read all of them; they're deliberately kept separate. See [`../README.md`](../README.md) for how the whole `docs/` tree is organized.

---

## 1. Purpose & Non-Goals

This is not a TradingView clone. It is a **personal trading operating system**: a modular pipeline that takes broker data in, runs it through a data engine and a Feature Engine, builds market and portfolio state, hands that state to a Strategy → Opportunity → Decision → Planning → Governor pipeline (the trading-intelligence reasoning; see companion doc) in parallel with visualization, and — eventually — out through an execution engine back to the broker.

**Non-goals for v1:**
- No multi-tenant / multi-user auth complexity. Single operator.
- No generic "add any indicator" plugin marketplace. Indicators are backend-computed and pushed as chart objects.
- No microservices-over-network split yet. This is a **modular monolith**: one FastAPI process, cleanly separated internal modules, so it can be split into services later without a rewrite.
- No Replay Engine, Simulation Mode, or `replay_sessions` storage yet — explicitly deferred. The design keeps the door open (market data source is already behind an interface) without building the feature now.

---

## 2. Architectural Principles (non-negotiable)

1. **Broker independence** — nothing above the adapter layer knows IBKR or Alpaca exists. Everything talks to a `BrokerAdapter` interface.
2. **Separation of concerns** — chart doesn't calculate indicators, agents don't fetch data, execution doesn't generate signals. Each module has exactly one job.
3. **Single source of truth for shared state** — the Market Data Engine owns market state, the Portfolio State Engine owns account/position state. Nothing else caches or recomputes either independently.
4. **Push, don't poll** — all frontend updates arrive over WebSocket channels.
5. **Everything is an interface first** — `BrokerAdapter`, `Agent`, `RiskRule`, `ExecutionRouter` are abstract base classes before they're implementations.
6. **Blackboard, not pipeline, for strategies** — strategies write opportunities independently into shared state; a downstream arbitration layer reads the blackboard and decides. This matches the multi-arm architecture already in use on the parallel trading-system design — same pattern, reused here.
7. **Decoupled via events, not direct calls** — modules publish typed events onto an in-process Event Bus rather than calling each other's methods. A module doesn't need to know who (if anyone) consumes what it emits.
8. **Compute once, consume everywhere** — indicators, session/time logic, and account state are each computed in exactly one module and shared, never duplicated across consumers.

---

## 3. High-Level Architecture

This diagram shows the **engineering** layering only. The domain logic inside the "Trading Intelligence Pipeline" box (Market State → Context → Strategy → Opportunity → Decision → Planning → Governor) is detailed in the companion doc — duplicating it here would drift the two documents apart.

```
                    ┌────────────────────┐
                    │     Broker APIs      │
                    │  (IBKR / Alpaca)     │
                    └──────────┬──────────┘
                               │
                     BrokerAdapter interface
                               │
                    ┌──────────▼──────────┐
                    │  Market Data Engine   │
                    └──────────┬──────────┘
                               │
                         publishes onto
                               │
                    ┌──────────▼──────────┐        ┌─────────────────┐
                    │      Event Bus        │◄──────┤   Market Clock    │
                    └──────────┬──────────┘        └─────────────────┘
                               │
        ┌──────────────────────┼──────────────────────────┐
        │                      │                           │
        ▼                      ▼                           ▼
┌───────────────┐   ┌───────────────────────┐    ┌─────────────────────┐
│ Feature Engine  │   │ Portfolio State Engine  │    │  WebSocket Gateway    │
└───────┬───────┘   └───────────┬───────────┘    └──────────┬──────────┘
        │                       │                            │
        ▼                       │                            ▼
┌─────────────────────────┐     │                  ┌──────────────────────┐
│ Trading Intelligence      │◄────┘ (read by            │ Trading Workspace UI  │
│ Pipeline                  │      Decision/Governor)   │  (React)               │
│ (see companion doc)       │                            └──────────────────────┘
└────────────┬─────────────┘
             │  Trade Plan (approved)
             ▼
     ┌───────────────┐
     │ Execution Engine │──────► BrokerAdapter interface ──────► Broker API
     └───────┬───────┘
             │ fills (via Event Bus)
             ▼
     ┌───────────────┐        ┌──────────────────────┐
     │ Position Monitor │──────► Performance Intelligence │
     └───────────────┘        └──────────────────────┘
```

All cross-module traffic flows through interfaces or the Event Bus — no module reaches into another's internals. The WebSocket Gateway is just one Event Bus subscriber among several; it exists to re-publish a subset of internal events to the frontend, not to define the internal communication pattern.

---

## 4. Component Breakdown

### 4.1 Broker Adapter Layer
Abstract contract every broker integration must satisfy:

```python
class BrokerAdapter(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe(self, symbols: list[str]) -> None: ...
    async def unsubscribe(self, symbols: list[str]) -> None: ...
    async def get_historical(self, symbol: str, timeframe: str,
                              start: datetime, end: datetime) -> list[Candle]: ...
    async def place_order(self, order: OrderRequest) -> OrderAck: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def get_positions(self) -> list[Position]: ...
    def on_tick(self, callback: Callable[[Tick], None]) -> None: ...
```

`IBKRAdapter` and `AlpacaAdapter` implement this. The Market Data Engine and Execution Engine depend only on `BrokerAdapter`, never on a concrete class. Adding a new broker = one new adapter file, zero changes elsewhere.

### 4.2 Market Data Engine
The only module allowed to talk to a broker for data.

Responsibilities:
- Own the live broker connection (reconnect/backoff logic lives here)
- Normalize raw broker payloads into a single internal `Tick` / `Candle` schema
- Maintain an in-memory **latest raw state cache** keyed by symbol (this is *raw* data — derived market state lives in the Market State Engine, §4.8)
- Publish every update (`PriceUpdated`, `CandleClosed`) onto the Event Bus
- Persist candles/ticks to PostgreSQL via a write-behind recorder (non-blocking)

Internal structure: `ConnectionManager` → `Normalizer` → `StateCache` → `Publisher` + `HistoricalWriter`, run as independent async tasks fed by one queue so a slow DB write never blocks live price fan-out.

### 4.3 Market Clock
The single source of truth for anything time/session-related. Every other module asks the Market Clock rather than computing this itself.

```python
class MarketClock:
    def current_session(self) -> Session: ...      # pre_market | open | lunch | power_hour | closed
    def is_market_open(self, ts: datetime = None) -> bool: ...
    def is_holiday(self, date: date) -> bool: ...
    def is_half_day(self, date: date) -> bool: ...
    def minutes_since_open(self, ts: datetime = None) -> int: ...
    def next_session_boundary(self) -> datetime: ...
```
Handles exchange holidays, half-days, and DST in one place. The Scanner's cadence schedule (§4.7) and the Strategy Scheduler (§4.8) both key off `current_session()` rather than raw wall-clock math.

### 4.4 Event Bus
A lightweight, in-process, typed pub/sub — not a full event-sourcing system, not a message broker. Modules publish typed events; anyone can subscribe without the publisher knowing or caring who's listening.

Representative event types: `PriceUpdated`, `CandleClosed`, `FeaturesUpdated`, `MarketStateChanged`, `ContextChanged`, `OpportunityCreated`, `OpportunitySelected`, `TradePlanned`, `OrderApproved`, `PlanRejected`, `OrderFilled`, `PositionAdjusted`, `PositionClosed`. Of these, `OrderFilled`, `PlanRejected`, `GovernorDecision`, and `OrderApproved` ride the critical lane described below; the rest ride the normal lane.

**Two dispatch lanes, not a general priority system.** The Event Bus stays a single pub/sub mechanism — no N-tier priority queue, no message-broker semantics. The one distinction that earns its complexity: execution-critical events (`OrderFilled`, `PlanRejected`, `GovernorDecision`, `OrderApproved`) are dispatched on their own lane, isolated from whatever queue depth exists on the market-data lane (`PriceUpdated`, `MarketStateChanged`, `OpportunityCreated`, etc.). This exists so a burst of price ticks — or a slow subscriber like a chart re-render — can never delay a risk or execution event. Everything else shares the normal lane. This is deliberately not a 5-tier priority framework (Critical/High/Normal/Low/Background) — that's message-broker-shaped complexity with no demonstrated need at this scale (single process, ~100-symbol universe, Scanner-narrowed to a handful). If the normal lane itself ever shows evidence of needing finer-grained prioritization, that's the trigger to revisit — not something to build ahead of the gap showing up.

The WebSocket Gateway (§4.12) is just one more subscriber — it re-publishes a filtered subset of these events to the frontend. It does not define the internal communication pattern; if the UI disappeared entirely, the backend pipeline would still function identically.

Every event listed above has a formal, versioned payload schema — see §10 for the full contract and versioning rules. That section is what Phase 2 actually implements; the list here is just the vocabulary.

### 4.5 Feature Engine
Computes every technical indicator exactly once per symbol per update: EMA(s), VWAP, ATR, previous day high/low, relative volume, gap %, opening range, average volume, trend slope, signed-volume / uptick-downtick imbalance (feeds Market State's Participation dimension — see `trading-intelligence-architecture.md` §4). Publishes a `FeatureSet` object via `FeaturesUpdated`.

This exists specifically to prevent the failure mode where Momentum, Breakout, and VWAP strategies each compute their own EMA and quietly disagree. **Nothing downstream — not the Scanner, not the Market State Engine, not any Strategy — computes an indicator itself. They all consume the same `FeatureSet`.**

Feature values that a strategy actually acted on get persisted to `feature_snapshots` (§4.13) at decision time, not on every tick — that's what makes "why did the AI do that" answerable later without recomputing anything.

### 4.6 Portfolio State Engine
Owns the system's live view of *itself* — the account/position side, as opposed to the Market Data Engine which owns the market side. Tracks: open positions, aggregate exposure, pairwise correlation across open positions, available capital/buying power, realized + unrealized daily P&L, and risk budget consumed so far today.

Updated by consuming `OrderFilled` and `PositionClosed` off the Event Bus — never polled, never recomputed by a consumer. Read by the Decision Engine (exposure/correlation), the Governor (capital/buying power/daily-loss limits), Position Monitor (unrealized P&L, average cost), and Performance Intelligence (historical position data). Treat it with the same seriousness as Market State — it's the other half of "what is true right now."

### 4.7 Market Activity Scanner
Runs on the 100-stock universe and narrows it down using a composite activity score built from **Feature Engine output** — relative volume, ATR expansion, gap %, spread tightness — never recomputed independently (see §4.5). Outputs the top **N candidates** (default 4, configurable) which are the *only* symbols promoted to full Strategy Engine analysis. This keeps compute bounded regardless of universe size.

**Cadence is a schedule, not a constant.** Activity isn't uniform through the session — the open is chaotic and worth scanning tightly, midday chop doesn't need it, power hour picks back up. Instead of a single interval, the scanner reads a `ScanCadenceSchedule` keyed against `MarketClock.current_session()`:

```python
DEFAULT_SCAN_SCHEDULE = [
    ScanWindow(start="09:30", end="10:00", interval_seconds=5),    # open — high volatility
    ScanWindow(start="10:00", end="11:30", interval_seconds=20),   # early session settling
    ScanWindow(start="11:30", end="14:30", interval_seconds=90),   # midday — low activity
    ScanWindow(start="14:30", end="15:30", interval_seconds=20),   # ramp back up
    ScanWindow(start="15:30", end="16:00", interval_seconds=5),    # power hour / close
]
```

This list lives in a `scanner_schedule` config table (not hardcoded), so cadence can be retuned without a redeploy — and the same mechanism can hold a separate, sparser schedule for pre-market/after-hours if you extend into those sessions later.

### 4.8 Trading Intelligence Pipeline — Technical Shape Only
This section gives each module's software interface — what it consumes, what it emits, what it persists. The *reasoning* behind each one (what "market state" means, how context is defined, how strategies decide, how the Governor vetoes, why Market State has memory) lives entirely in the companion doc, `trading-intelligence-architecture.md`. Duplicating that reasoning here would let the two documents drift apart, which defeats the point of splitting them.

| Module | Consumes | Emits | Persists to |
|---|---|---|---|
| Market State Engine | `FeaturesUpdated` | `MarketStateChanged` | `market_state_history` |
| Context Engine | `MarketStateChanged`, Market Clock, event calendar | `ContextChanged` | derived, not stored independently |
| Strategy Engine | `MarketStateChanged`, `ContextChanged`, `FeaturesUpdated` | `OpportunityCreated` | `ai_decisions`, `feature_snapshots` |
| Opportunity Engine | `OpportunityCreated` (all strategies, per symbol) | ranked opportunity list | `ai_decisions` |
| Decision Engine | ranked opportunities + Portfolio State | `OpportunitySelected` | `ai_decisions` |
| Trade Planning Engine | `OpportunitySelected` | `TradePlanned` | `trades` (draft) |
| Governor | `TradePlanned` + Portfolio State + Context | `OrderApproved` / `PlanRejected` | `trades` (approved/rejected) |
| Position Monitor | `OrderFilled`, ongoing `MarketStateChanged` | `PositionAdjusted` / `PositionClosed` | `positions` |
| Performance Intelligence | `PositionClosed`, `feature_snapshots`, `market_events` | strategy reweighting signal | `strategy_performance` |

Each module implements a narrow interface (`evaluate()`, `rank()`, `arbitrate()`, `plan()`, `authorize()`) so any single stage can be unit-tested without standing up the full pipeline.

**Strategy interface** — this replaces the earlier `Agent` ABC from v1 of this doc. Same shape, renamed to match the trading-intelligence vocabulary, and it returns an `Opportunity` rather than a directional `Signal` — a strategy's job is to surface *what exists*, not decide *what to do*:

```python
class Strategy(ABC):
    name: str
    trigger: ScheduleTrigger   # e.g. every_candle(), after_time("09:35"), on_event("VolumeSpike")
    async def evaluate(self, market_state: MarketState,
                        features: FeatureSet,
                        context: Context) -> Opportunity | None: ...
```

**Opportunity object** — what a strategy hands upward. No execution commitment:
```json
{
  "symbol": "NVDA",
  "strategy": "ORB",
  "direction": "BUY",
  "confidence": 85,
  "reason": "Volume expansion with VWAP confirmation",
  "suggested_entry": 132.50,
  "suggested_stop": 130.80,
  "suggested_target": 136.00,
  "timestamp": "2026-07-21T13:32:00Z"
}
```

### 4.9 Execution Engine
Only module allowed to place orders. Consumes `OrderApproved` (payload: `ApprovedOrder`), routes through `BrokerAdapter.place_order`, tracks order lifecycle (`pending → filled/partial/rejected`), and emits `OrderFilled` onto the Event Bus so the UI and Position Monitor both update without polling. Must support a **dry-run mode** by default — mirrors the pattern already used in the Polymarket bot.

### 4.10 Visualization Engine / Chart Overlay Protocol
The backend is the only source of chart truth. It pushes typed **chart objects** over WebSocket; the frontend (TradingView Lightweight Charts as a renderer only) draws whatever it receives — it computes nothing.

```json
{ "type": "horizontal_line", "price": 225.50, "label": "PDH" }
{ "type": "marker", "position": "BUY", "price": 225.10, "confidence": 87 }
{ "type": "rectangle", "top": 230, "bottom": 225, "label": "Resistance Zone" }
```

A discriminated-union Pydantic schema (`ChartObject`) validates all overlay types server-side before they're ever sent.

### 4.11 Trading Workspace UI
Component-based, workspace is **data, not code** — a JSON layout describes which widgets are mounted where. Core widgets: Chart, Watchlist, AI Analysis Panel, Trade Management, Positions, Strategy Monitor, Market Scanner, Trade Journal. A `WidgetRegistry` maps widget type → React component, so new widget types don't require touching the layout engine.

**Render-rate is a widget concern, not a transport concern.** The WebSocket Gateway and Event Bus push at full frequency — throttling never happens upstream, and the backend stays simple: push everything, always. Widgets that don't need sub-second fidelity (Watchlist rows, Positions summary, P&L) batch incoming updates and flush on a fixed render cadence (e.g. once per animation frame or ~250ms) inside the widget/store layer. Chart needs no such throttling — Lightweight Charts already coalesces to the browser's paint cycle. Execution/risk-relevant UI (order status, Governor rejections) never batches — full latency, always. This gives each widget control over its own responsiveness-vs-render-cost trade-off without adding any backend complexity.

### 4.12 WebSocket Gateway
Single multiplexed connection, topic-tagged envelopes — a thin re-publisher sitting on top of the Event Bus (§4.4), not a separate source of truth:

```json
{ "channel": "market.tick", "symbol": "NVDA", "payload": {...} }
{ "channel": "opportunity.new", "symbol": "NVDA", "payload": {...} }
{ "channel": "orders.status", "order_id": "...", "payload": {...} }
{ "channel": "chart.overlay", "symbol": "NVDA", "payload": {...} }
```
Frontend subscribes/unsubscribes to channels per symbol as widgets mount/unmount. This avoids one-socket-per-widget sprawl.

### 4.13 Database (PostgreSQL)
Core tables: `symbols`, `candles` (natively partitioned by month, sub-partitioned by timeframe), `trades`, `orders`, `positions`, `ai_decisions`, `strategy_performance`, `workspace_layouts`, `watchlists`. Alembic manages migrations from day one so schema drift never becomes a manual-SQL problem.

New in this revision, added to support explainability and Market State's temporal memory (see companion doc):
- **`feature_snapshots`** — the exact `FeatureSet` values at the moment a strategy produced an opportunity. Answers "why did the AI say this" by query, not by recomputation.
- **`market_events`** — a durable log of typed events crossing the Event Bus. This is what makes claims like "momentum has been weakening for 12 minutes" checkable after the fact, not just live.
- **`market_state_history`** — periodic/on-change snapshots of `MarketState` per symbol, so "what was the market state at 10:14am" is a query.

Deferred, not created in v1 migrations (names reserved so the schema doesn't fight the future): `replay_sessions`, `backtests`.

`candles` uses Postgres's built-in **declarative partitioning** (no extension needed) from day one — partitioning isn't a "later" concern, it's cheap to set up now and expensive to retrofit onto a live table. See §6.1 for the full reasoning on plain Postgres vs. TimescaleDB.

---

## 5. Two End-to-End Data Flow Walkthroughs

**Tick → Chart:**
Broker push → `BrokerAdapter` → `Normalizer` → `StateCache` update → `Publisher` emits `PriceUpdated` onto the Event Bus → WebSocket Gateway (a subscriber, not a special path) fans out to subscribed Chart widgets → Lightweight Charts renders. In parallel, `HistoricalWriter` persists the candle asynchronously, and Feature Engine recomputes affected features.

**Opportunity → Execution:**
Scanner promotes a symbol using Feature Engine's already-computed activity metrics → Strategy Engine strategies evaluate in parallel off the same `MarketState` / `FeatureSet` / `Context` snapshot → each emits `OpportunityCreated` → Opportunity Engine ranks the field per symbol → Decision Engine arbitrates against Portfolio State (exposure, correlation) → Trade Planning Engine drafts entry/stop/target/size → Governor authorizes or rejects against Portfolio State + Context → if approved, Execution Engine places the order via `BrokerAdapter` → `OrderFilled` flows back over the Event Bus → UI updates via WebSocket, and Position Monitor takes over the open position.

---

## 6. Technology Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend language/framework | Python + FastAPI | async-first, matches existing stack |
| Realtime transport | WebSocket (native FastAPI) | single multiplexed connection |
| Database | PostgreSQL | candles, trades, decisions, config |
| In-process cache/queue | asyncio queues + in-memory dict cache | simplest option that satisfies v1; revisit Redis only if scaling to multi-process |
| Frontend | React + Vite | fast dev loop |
| Styling | Tailwind CSS | matches your existing dashboards |
| Charting | TradingView Lightweight Charts | render-only, no TradingView embed |
| State management (frontend) | Zustand | lighter than Redux for a single-user workspace |
| Migrations | Alembic | schema versioning from Phase 2 onward |
| Broker SDKs | `ib_async` (IBKR) — see `../decisions/confirmed-decisions.md` #13; `ib_insync` is unmaintained | wrapped behind adapters, never imported outside them |

**Confirmed:** in-process cache, no Redis, for v1. Revisit only when/if agents move to separate processes — Redis pub/sub is the natural upgrade at that point, not before.

### 6.1 PostgreSQL vs. TimescaleDB — the actual trade-off

Worth clearing up first: **TimescaleDB isn't a different database** — it's a PostgreSQL extension. Choosing plain Postgres now doesn't lock you out of Timescale later; you install the extension and convert a table to a "hypertable" without a data migration or rewrite. So this decision is really "what do we set up now vs. defer," not "which database do we commit to."

**Where plain Postgres genuinely struggles as candle data grows:**

1. **Query pruning at scale.** A single `candles` table with hundreds of millions of rows means every time-range query has to lean on B-tree index scans that get slower as the table grows, unless you partition it yourself. Native declarative partitioning (built into Postgres, no extension) solves most of this for a personal system — partition by month, and a query for "last 30 days of NVDA 1-min candles" only touches 1–2 partitions. This is what the design already calls for in §4.10.
2. **Compression.** This is where Timescale actually pulls ahead. It has native columnar compression for older chunks — routinely 10–20x on OHLCV data — with zero app-level effort. Plain Postgres has no equivalent; you'd either eat the storage cost or hand-roll archiving to cold storage.
3. **Continuous aggregates.** If you want, say, 5-minute and daily bars automatically and incrementally derived from 1-minute candles, Timescale's continuous aggregates update incrementally as new data lands. On plain Postgres you're either refreshing a materialized view in full (expensive, and gets more expensive over time) or maintaining the aggregation logic yourself in application code.
4. **Retention policies.** Timescale can auto-drop or auto-downsample data older than N days via policy. On plain Postgres this is a cron job you write and maintain.

**Why this maps to your two answers:**

- **"Definitely multi-symbol"** — this is squarely a partitioning problem, not a compression/aggregation problem. Native Postgres partitioning (by month, and you can sub-partition by symbol-hash if a single month gets too large even across 100 symbols) handles this cleanly without Timescale.
- **"Not sure about multi-timeframe backtesting at scale"** — this is exactly where compression and continuous aggregates start to matter, because backtesting across years of 1-minute data for many symbols *and* multiple derived timeframes is where row counts and derived-table maintenance become genuinely painful to hand-roll.

**Recommendation:** build `candles` on plain Postgres with native monthly partitioning now (cheap, needed regardless of Timescale). Treat "add the Timescale extension and convert `candles` to a hypertable" as a Phase 4/5 checkpoint you revisit once backtesting scope is real, not a v1 requirement. Because it's additive, that later decision costs you nothing today.

---

## 7. Phased Roadmap

The phased delivery plan — six phases, deliverables, and exit criteria — lives in [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md), kept in its own file so it can be checked off and updated per-phase (a living status, not just a static plan) without touching this document's architecture content.

---

## 8. Folder Structure

```
trading-workspace/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py                 # settings, env loading
│   │   │   ├── logging.py
│   │   │   ├── security.py
│   │   │   ├── market_clock.py           # session/holiday/DST — single source of truth
│   │   │   └── debounce_scheduler.py     # event-driven + min/max interval update policy, shared utility
│   │   ├── event_bus/
│   │   │   ├── bus.py                    # in-process typed pub/sub
│   │   │   └── events.py                 # event envelope + dispatch, imports schemas below
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   ├── market.py
│   │   │   │   ├── charts.py
│   │   │   │   ├── opportunities.py
│   │   │   │   ├── orders.py
│   │   │   │   └── workspace.py
│   │   │   └── websocket/
│   │   │       ├── manager.py            # connection registry
│   │   │       └── channels.py           # topic definitions
│   │   ├── broker_adapters/
│   │   │   ├── base.py                   # BrokerAdapter ABC
│   │   │   └── ibkr_adapter.py       # Alpaca deferred, not stubbed — see ../decisions/confirmed-decisions.md #1
│   │   ├── market_data_engine/
│   │   │   ├── engine.py
│   │   │   ├── normalizer.py
│   │   │   ├── state_cache.py
│   │   │   └── historical_writer.py
│   │   ├── feature_engine/
│   │   │   ├── engine.py
│   │   │   └── indicators.py             # EMA, VWAP, ATR, PDH/PDL, rel. volume, gap %, signed-volume imbalance, ...
│   │   ├── scanner/
│   │   │   ├── scanner.py
│   │   │   └── schedule.py               # ScanCadenceSchedule, keyed to Market Clock sessions
│   │   ├── portfolio_state/
│   │   │   └── engine.py                 # exposure, correlation, capital, daily P&L
│   │   ├── trading_intelligence/
│   │   │   ├── market_state_engine.py
│   │   │   ├── context_engine/
│   │   │   │   ├── engine.py             # aggregator only — owns no logic itself
│   │   │   │   └── providers/            # one file per context question
│   │   │   │       ├── calendar_provider.py
│   │   │   │       ├── gap_provider.py
│   │   │   │       ├── levels_provider.py
│   │   │   │       ├── volatility_regime_provider.py
│   │   │   │       ├── sector_correlation_provider.py
│   │   │   │       └── news_flag_provider.py
│   │   │   ├── strategy_engine/
│   │   │   │   ├── base_strategy.py
│   │   │   │   ├── orb_strategy.py
│   │   │   │   ├── momentum_strategy.py
│   │   │   │   ├── pullback_strategy.py
│   │   │   │   ├── vwap_strategy.py
│   │   │   │   ├── gap_strategy.py
│   │   │   │   ├── reversal_strategy.py
│   │   │   │   └── volume_spike_strategy.py
│   │   │   ├── opportunity_engine.py     # ranks, does not decide
│   │   │   ├── decision_engine.py        # arbitrates using Portfolio State
│   │   │   └── trade_planning_engine.py  # entry/stop/target/size/R
│   │   ├── world_view/
│   │   │   └── composite.py              # read-only facade over Market/Portfolio/Context/Performance
│   │   ├── governor/
│   │   │   ├── governor.py               # final risk/policy veto
│   │   │   ├── position_sizing.py        # fractional Kelly
│   │   │   └── risk_rules.py
│   │   ├── execution_engine/
│   │   │   ├── order_manager.py
│   │   │   └── execution_router.py
│   │   ├── position_monitor/
│   │   │   └── monitor.py                # still valid? weakening? partial? exit?
│   │   ├── performance_intelligence/
│   │   │   └── analyzer.py               # attribution → feeds Strategy Engine + Trade Planning
│   │   ├── models/                       # SQLAlchemy ORM models
│   │   ├── schemas/                      # Pydantic request/response + ChartObject union
│   │   │   └── events/                   # one Pydantic model per event type — the actual contract (§10)
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   └── migrations/               # Alembic
│   │   └── services/                     # cross-cutting helpers
│   ├── tests/
│   ├── requirements.txt
│   └── alembic.ini
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── chart/
│   │   │   ├── watchlist/
│   │   │   ├── ai-panel/
│   │   │   ├── trade-management/
│   │   │   ├── positions/
│   │   │   ├── strategy-monitor/
│   │   │   ├── scanner/
│   │   │   ├── journal/
│   │   │   └── workspace/                # layout engine + widget registry
│   │   ├── hooks/
│   │   ├── services/
│   │   │   ├── websocket-client.ts
│   │   │   └── api-client.ts
│   │   ├── store/                        # Zustand slices
│   │   ├── types/
│   │   ├── pages/
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── docs/                                  # authoritative home for all architecture/design docs
│   ├── README.md                         # index — how this tree is organized, and the update-with-code rule
│   ├── architecture/
│   │   ├── system-design.md              # this document — how it's built
│   │   └── trading-intelligence-architecture.md  # companion — how it thinks
│   ├── decisions/
│   │   ├── README.md                     # ADR-lite convention for this folder
│   │   ├── confirmed-decisions.md        # settled decision log (extracted from §9)
│   │   └── future-ideas.md               # deferred concepts, parked with reasoning + triggers
│   ├── roadmap/
│   │   └── phase-roadmap.md              # phased delivery plan + exit criteria (extracted from §7)
│   ├── diagrams/
│   │   └── README.md                     # placeholder — rendered diagrams (mermaid/svg) land here
│   └── api/
│       └── README.md                     # placeholder — REST/WebSocket contracts, generated/maintained separately
├── docker-compose.yml                    # postgres (+ backend/frontend for local dev)
├── .env.example
└── README.md
```

---

## 9. Confirmed Decisions

The full, numbered decision log lives in [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) — kept in its own file so decisions accumulate in one place instead of drifting across a growing architecture document. Any new confirmed decision gets appended there, as part of the same change that makes it.

**Deferred, not rejected** — Knowledge Engine, Attention Engine, uncertainty propagation across the pipeline, the full write-everywhere World Model, Replay Engine, and Simulation Mode all live in [`../decisions/future-ideas.md`](../decisions/future-ideas.md) with the reasoning for why each is parked, and what would trigger revisiting it.

No open items remain blocking Phase 1–3. Next natural checkpoint is Phase 4/5, where the Timescale question gets revisited against real backtesting requirements.

---

## 10. Event Data Contracts

Every event crossing the Event Bus (§4.4) is a versioned, schema-defined payload — not a loose dict. This is the backbone of the entire event-driven design, and getting it right before Phase 1 code is cheaper than discovering the shape of these payloads by refactoring six consumers at once later.

### 10.1 Envelope

Every event, regardless of type, is wrapped the same way:

```json
{
  "event_type": "OpportunityCreated",
  "version": 1,
  "timestamp": "2026-07-22T09:32:04Z",
  "symbol": "NVDA",
  "payload": { ... }
}
```

`symbol` is omitted for market-wide events (e.g. a Fed-day `ContextChanged` that isn't symbol-specific).

### 10.2 Versioning Rules

- **Additive, optional fields with a sensible default do not bump `version`.** Old consumers keep working unchanged.
- **Removing a field, changing a field's type or meaning, or promoting an optional field to required bumps `version`.** The `event_type` name stays the same; consumers branch on `version` if they need to support both during a migration window.
- **A structurally different event (not just a changed payload) gets a new `event_type` name**, not a version bump — e.g. if Governor's output ever needed to fundamentally restructure beyond what `GovernorDecision` supports, that would be a new event type, not `GovernorDecision` v2.
- **The Pydantic model in `schemas/events/` is the actual contract.** This section is a readable mirror of it, not the source of truth — if code and doc disagree, fix the doc.

### 10.3 Payload Schemas (v1)

| Event | Key fields |
|---|---|
| `PriceUpdated` | `price`, `size`, `exchange_ts` |
| `CandleClosed` | `timeframe`, `open`, `high`, `low`, `close`, `volume`, `candle_ts` |
| `FeaturesUpdated` | `features: {ema_9, ema_20, vwap, atr_14, pdh, pdl, rel_volume, gap_pct, opening_range_high, opening_range_low, avg_volume_20d, trend_slope}` |
| `MarketStateChanged` | `dimension` (trend\|volatility\|volume\|vwap_relation\|session\|breadth), `value`, `duration_seconds`, `strength` (increasing\|decreasing\|stable), `confidence`, `previous_value`, `changed_at` |
| `ContextChanged` | `providers: {calendar: {...}, gap: {...}, levels: {...}, volatility_regime: {...}}` — one key per active provider (§5 of the companion doc) |
| `OpportunityCreated` | `strategy`, `direction`, `confidence`, `reason`, `suggested_entry`, `suggested_stop`, `suggested_target` |
| `OpportunitySelected` | `chosen_strategy`, `rejected_alternatives: [...]`, `rationale` |
| `TradePlanned` | `entry`, `stop`, `target`, `size`, `r_multiple`, `max_hold_minutes`, `scaling_plan`, `trailing_stop_rule` |
| `GovernorDecision` | `action` (approved\|approved_reduced\|delayed\|watch_only\|rejected), `size_multiplier` (nullable), `delay_seconds` (nullable), `reasons: [...]` |
| `OrderFilled` | `order_id`, `side`, `qty`, `fill_price`, `fill_ts` |
| `PositionAdjusted` | `position_id`, `action` (move_stop\|take_partial\|scale_in), `detail` |
| `PositionClosed` | `position_id`, `exit_price`, `realized_pnl`, `r_multiple_achieved`, `closed_ts` |

Each row above is a starting contract, not a final one — expect it to gain optional fields as strategies get more specific. What it shouldn't do is change shape silently; any change to this table should be a deliberate edit to both the Pydantic model and this section in the same commit.
