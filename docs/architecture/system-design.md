# Personal AI Trading Workspace — System Design Document
**Version:** 2.9 (Pre-Implementation) — §1, §4.5, §4.11 updated for the chart→Feature Engine migration (`confirmed-decisions.md` #50–51; full plan: `feature-engine-chart-migration.md`)
**Status:** Phase 2 kickoff — Event Bus dispatch lanes and shared update-policy utility added
**Owner:** Saqib
**Companion documents:** [`trading-intelligence-architecture.md`](./trading-intelligence-architecture.md) — how the system thinks (market state, context, strategy, decision logic). [`feature-engine-chart-migration.md`](./feature-engine-chart-migration.md) — the stage-by-stage plan moving chart-drawn indicators onto Feature Engine as their single source (confirmed decision #50), tracked there rather than re-derived from this doc each session. [`../decisions/future-ideas.md`](../decisions/future-ideas.md) — concepts raised and deliberately deferred, with reasoning intact, so they aren't lost or re-argued from scratch. [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) — the running settled-decisions log. [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md) — phased delivery plan and exit criteria. This doc explains how the system is built. Read all of them; they're deliberately kept separate. See [`../README.md`](../README.md) for how the whole `docs/` tree is organized.

---

## 1. Purpose & Non-Goals

This is not a TradingView clone. It is a **personal trading operating system**: a modular pipeline that takes broker data in, runs it through a data engine and a Feature Engine, builds market and portfolio state, hands that state to a Strategy → Opportunity → Decision → Planning → Governor pipeline (the trading-intelligence reasoning; see companion doc) in parallel with visualization, and — eventually — out through an execution engine back to the broker.

**Non-goals for v1:**
- No multi-tenant / multi-user auth complexity. Single operator.
- No generic "add any indicator" plugin marketplace — a person still can't bolt on an arbitrary third-party indicator library. Within the indicators this system actually knows about, though, **there is exactly one computation, not two**: Feature Engine computes every indicator once; the Chart, Level Interaction Engine, and anything else that needs one are consumers of that single output, never independent calculators of it (confirmed decision #50, superseding the "distinct category" framing this line carried through decisions #40/#41 — see §4.11 and `feature-engine-chart-migration.md` for the migration itself, now genuinely running for SMA/EMA/VWAP — decision #54 — with the remaining indicator families still migrating one at a time).
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

**The `HistoricalWriter` piece of this exists today, ahead of the rest.** `CandleRecorder` (`app/services/candle_recorder.py`) implements exactly the "persist candles via a write-behind recorder (non-blocking)" responsibility above, wired directly onto the current `TickIngestBridge` → `EventBus` pipeline rather than the formal `ConnectionManager`/`Normalizer`/`StateCache` split, which is still Phase 4 work. It only ever writes `1m` rows — `TickIngestBridge`'s fixed bucket size. `GET /market/candles` checks this self-recorded data before ever reaching an external provider — for `1m` specifically it's the only source that can ever exist on a free-tier data plan (§6.1's caveat, decision #39). See `../decisions/confirmed-decisions.md` #43.

**5m/15m/1h are derived on read, not recorded separately.** `candle_aggregator.py` builds them hierarchically from the same recorded `1m` rows (`1m → 5m → 15m → 1h`, each level from the nearest coarser level already computed), bucketed session-locally via `MarketClock.session_bounds()` (§4.3) rather than a continuous 24h clock, so a bucket never straddles a session boundary. `GET /market/candles` tries this before falling through to an external provider, same priority as the raw `1m` self-recorded path. `1d` deliberately stays sourced from Polygon's real daily EOD bars rather than reconstructed from however much `1m` history happens to be recorded; `4h` is deliberately unsupported (a clean 400, not an attempt at a session-boundary definition nobody's confirmed — the regular session's 6.5h doesn't divide evenly by 4h). See decision #44.

### 4.3 Market Clock
The single source of truth for anything time/session-related. Every other module asks the Market Clock rather than computing this itself.

```python
class MarketClock:
    def current_session(self) -> Session: ...      # pre_market | open | lunch | power_hour | after_hours | closed
    def is_market_open(self, ts: datetime = None) -> bool: ...
    def is_holiday(self, date: date) -> bool: ...
    def is_half_day(self, date: date) -> bool: ...
    def minutes_since_open(self, ts: datetime = None) -> int: ...
    def next_session_boundary(self) -> datetime: ...
    def session_bounds(self, ts: datetime = None) -> tuple[datetime, datetime] | None: ...
```
Handles exchange holidays, half-days, and DST in one place. The Scanner's cadence schedule (§4.7) and the Strategy Scheduler (§4.8) both key off `current_session()` rather than raw wall-clock math.

`session_bounds()` (decision #44) is the anchor candle aggregation buckets off of (§4.2) — `open`/`lunch`/`power_hour` collapse to one continuous "regular session" domain for this purpose (same bounds for all three), so a bucket only ever resets at a genuine session-type change (pre-market → regular, regular → after-hours), never at the lunch/power-hour sub-boundaries `current_session()` still distinguishes for other callers.

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

**Implementation status (confirmed decisions #45, #51, #52, #53):** SMA is the first indicator actually computed here — `app/feature_engine/{indicators/,engine.py}` — published as `FeaturesUpdated` on every `1m` `CandleClosed`, in-memory rolling window per `(symbol, timeframe)` seeded from `candle_store`/`candle_aggregator` on first sight of a symbol+timeframe, restart-safe by design (same "rebuilt from persisted history" shape as Market State — §4 of the companion doc). **EMA is also computed (decision #52)**, reading from the same rolling window — a full-window recompute (seed with SMA of the window's oldest bars, then recurse forward) rather than true incremental recursion, since EMA has no exact finite-window equivalent to SMA's; see `indicators/ema.py::ema()`'s own docstring for the convergence math and the stricter warm-up (`period * seed_multiplier` closes, not just `period`) this implies. **VWAP is also computed (decision #53)** — a genuinely different shape from SMA/EMA, not a third rolling-window variant: keyed by symbol alone rather than `(symbol, timeframe)`, since it's a session-level statistic that should read identically regardless of chart timeframe, computed once from `1m` bars and attached to every timeframe's `FeatureSet` on that same close; a monotonically growing session accumulator, not a sliding window, reset via `MarketClock.is_regular_session()`/`session_bounds()`, excluding pre-market/after-hours volume entirely (matching `frontend/src/indicators/vwap.ts`'s own convention). Everything else in the paragraph above (ATR, PDH/PDL, relative volume, gap %, opening range, average volume, trend slope, signed-volume imbalance) is still the target shape, not yet built. **`5m`/`15m`/`1h` SMA/EMA are also computed (decision #51)** — event-triggered off the same `1m` `CandleClosed`, the moment it completes a higher-timeframe bucket (`candle_aggregator.completes_bucket()`), not a separate poll or its own event source; cold-start history for those timeframes comes from `candle_aggregator.aggregate_from_recorded()` rather than `candle_store` directly, since `CandleRecorder` only ever persists `1m` rows. `FeatureSet` gained a `close` field in decision #46, for the Level Interaction Engine below (§4.8) — the first real consumer of this output. **The Chart is now also a real consumer, for SMA/EMA/VWAP specifically (decision #54)** — via `GET /intelligence/series` (`feature_engine/historical.py`'s batch computation, not this live engine directly — see that route's own docstring for why the two need different shapes) rather than `frontend/src/indicators/sma.ts`/`ema.ts`/`vwap.ts`'s independent local math; see §4.11 for the Chart-side detail.

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
| Level Interaction Engine | `FeaturesUpdated` | `LevelInteractionChanged` | `level_interaction_state`, `level_interaction_events` |
| Context Engine | `MarketStateChanged`, Market Clock, event calendar | `ContextChanged` | derived, not stored independently |
| Strategy Engine | `MarketStateChanged`, `ContextChanged`, `FeaturesUpdated` | `OpportunityCreated` | `ai_decisions`, `feature_snapshots` |
| Opportunity Engine | `OpportunityCreated` (all strategies, per symbol) | ranked opportunity list | `ai_decisions` |
| Decision Engine | ranked opportunities + Portfolio State | `OpportunitySelected` | `ai_decisions` |
| Trade Planning Engine | `OpportunitySelected` (auto) or manual `TradeRequest` (hotkey/UI) | `TradePlanned` | `trades` (draft) |
| Governor | `TradePlanned` + Portfolio State + Context | `OrderApproved` / `PlanRejected` | `trades` (approved/rejected) |
| Position Monitor | `OrderFilled`, ongoing `MarketStateChanged` | `PositionAdjusted` / `PositionClosed` | `positions` |
| Performance Intelligence | `PositionClosed`, `feature_snapshots`, `market_events` | strategy reweighting signal | `strategy_performance` |

**Implementation status (confirmed decision #46):** Level Interaction Engine is the first module in this table actually built and running — `app/trading_intelligence/level_interaction_engine.py`. Generic by construction: it runs the same touch/holding/rejected/conquered state machine against every key `FeatureSet.features` carries (currently `sma_9`/`sma_20`/`sma_50` — decision #45), so it needs no changes when EMA/VWAP/pivot levels start publishing later — and, as of decision #51, needed no changes to start tracking `5m`/`15m`/`1h` SMA either, since it was already generic over `timeframe`, not hardcoded to `1m`. Market State Engine, Context Engine, and everything below them in this table are still the target shape only, not yet built.

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

**Mode-aware since manual trading (`trading-intelligence-architecture.md` §18):** a system-wide `ExecutionMode` (`auto` | `manual`, owned by Portfolio State, broadcast via `ExecutionModeChanged`) gates the one place this module talks to the broker. In `auto` mode, `OrderApproved` flows straight to `place_order` as above, unchanged. In `manual` mode, an approved `TradePlan` is written to an **Approval Queue** (`trades`, `status=pending_confirmation`) instead of firing immediately; a `PlanAwaitingConfirmation` event notifies the UI, and only an explicit `ManualConfirmOrder` (or `ManualDiscardOrder`) actually calls `place_order`. This is the only module whose behavior changes between modes — Decision Engine, Trade Planning Engine, and Governor are identical either way.

### 4.10 Visualization Engine / Chart Overlay Protocol
The backend is the only source of chart truth. It pushes typed **chart objects** over WebSocket; the frontend (TradingView Lightweight Charts as a renderer only) draws whatever it receives — it computes nothing.

```json
{ "type": "horizontal_line", "price": 225.50, "label": "PDH" }
{ "type": "marker", "position": "BUY", "price": 225.10, "confidence": 87 }
{ "type": "rectangle", "top": 230, "bottom": 225, "label": "Resistance Zone" }
```

A discriminated-union Pydantic schema (`ChartObject`) validates all overlay types server-side before they're ever sent.

### 4.11 Trading Workspace UI
Component-based, workspace is **data, not code** — a JSON layout describes which widgets are mounted where. Core widgets: Chart, Watchlist, AI Analysis Panel, Trade Management (hosts the manual-mode Approval Queue — accept/ignore a Governor-approved plan, or a hotkey-proposed one, per `trading-intelligence-architecture.md` §18), Positions, Strategy Monitor, Market Scanner, Trade Journal. A `WidgetRegistry` maps widget type → React component, so new widget types don't require touching the layout engine.

**Chart-reading overlays (moving averages, session levels, volume averages) are being migrated onto Feature Engine as their single source — no longer a permanently separate, client-side-only category (confirmed decision #50, superseding the framing this paragraph carried under decisions #40/#41; full stage-by-stage plan: `feature-engine-chart-migration.md`).** The Chart widget draws two kinds of lines that look similar but come from different places: backend-pushed `ChartObject`s (§4.10 — server is the source of truth, e.g. an AI-placed annotation) and indicator overlays (`frontend/src/indicators/` — one calculation file per indicator: `sma.ts`, `ema.ts`, `vwap.ts`, `previousDayLevels.ts`, `premarketLevels.ts`, `camarillaPivots.ts`, `vpoc.ts`, plus a shared `sessions.ts` for US-Eastern trading-day/session classification; `frontend/src/utils/indicators.ts` is purely the dispatcher, no math of its own). Until decision #40, there was no backend Feature Engine to point these at, so they computed independently — a reasonable stopgap at the time, but one that quietly duplicated the same math Feature Engine now also computes (§4.5), which is exactly the failure mode §2's "compute once, consume everywhere" principle exists to rule out. **Target end-state:** every one of these files becomes, at most, a thin renderer of values Feature Engine already published — never an independent calculator — the same relationship Level Interaction Engine already has with Feature Engine (§4.8). **Current state, genuinely running as of decision #54, not just target-state description anymore:** `computePriceIndicator` (`utils/indicators.ts`) now sources SMA/EMA/VWAP from `GET /intelligence/series` (backfill) plus live `features.updated` pushes (`useFeatureEngineSeries.ts`) whenever Feature Engine has a value for the exact `(type, period, timeframe)` an instance is configured for, falling back to the original local computation — unchanged — with `" (local)"` appended to the label when it doesn't (a non-standard period, or a timeframe outside `1m`/`5m`/`15m`/`1h`). The frontend files above are NOT retired by this — they're the real, load-bearing fallback path, still called whenever the backend can't serve a given instance, not dead code. `previousDayLevels.ts`, `premarketLevels.ts`, `camarillaPivots.ts`, and `vpoc.ts` remain entirely local — Stage 3's remaining rows (`feature-engine-chart-migration.md`), not yet built server-side at all.

Two config shapes cover this, split by what they draw: `SubWindowConfig.priceIndicators` (`PriceIndicatorInstance[]`) is a continuous {time,value} line — SMA, EMA, VWAP, each instance with its own period/color/thickness/price-label-visibility — and `SubWindowConfig.horizontalLevels` (`HorizontalLevelInstance[]`) is a single fixed price level drawn via `createPriceLine` — Previous Day Close/High/Low, Pre-Market High/Low, all nine Camarilla pivots, VPOC — each instance with its own color/thickness/line-style (solid/dashed/dotted)/price-label-visibility. A future overlay kind (Bollinger Bands, ...) extends the first; a future single-level kind extends the second — neither should invent a third shape. Note the naming coincidence: a `HorizontalLevelInstance` of type `PDH` (client-computed, from `previousDayLevels.ts`) is unrelated to a hypothetical backend-pushed `ChartObject` that happened to also represent a prior-day-high annotation — same visual result, opposite data source, and nothing currently prevents both existing on the same chart at once (harmless — they'd just overlap at the same price if it happened to agree). See `../decisions/confirmed-decisions.md` #40–41.

**Render-rate is a widget concern, not a transport concern.** The WebSocket Gateway and Event Bus push at full frequency — throttling never happens upstream, and the backend stays simple: push everything, always. Widgets that don't need sub-second fidelity (Watchlist rows, Positions summary, P&L) batch incoming updates and flush on a fixed render cadence (e.g. once per animation frame or ~250ms) inside the widget/store layer. Chart needs no such throttling — Lightweight Charts already coalesces to the browser's paint cycle. Execution/risk-relevant UI (order status, Governor rejections) never batches — full latency, always. This gives each widget control over its own responsiveness-vs-render-cost trade-off without adding any backend complexity.

**Multi-monitor: a Main Window can pop out into its own real browser tab, live-synced with the main one — not a second, independent session.** `MainWindowTabs.tsx`'s pop-out button opens `/window/:id` via `window.open()`; `App.tsx`'s minimal hand-rolled router (two shapes only — `/` and `/window/:id`, no routing library) renders a chrome-stripped, single-window view locked to that id via `WorkspaceProvider`'s `lockedMainWindowId`. "Live-synced" is the load-bearing part: every browser tab of this app already persists its full session to `localStorage` on every change (pre-existing, since Phase 1); `frontend/src/state/crossTabSync.ts` adds a `BroadcastChannel`-based ping so every OTHER open tab re-reads `localStorage` and applies the update immediately, instead of only picking it up on next page load. A tab never overrides its own just-applied state with its own subsequent write (tracked via a last-synced-JSON ref, checked before every persist+broadcast) — without that guard, two open tabs would echo each other's updates back and forth indefinitely. A popped-out tab's own `activeMainWindowId` is pinned and exempted from the incoming sync's `activeMainWindowId`, specifically so it keeps showing the one window it was opened for regardless of which tab the main workspace has active at any given moment. Gracefully degrades to today's single-tab-only behavior if `BroadcastChannel` isn't available in the environment — no error, no crash, just no cross-tab live sync. See `../decisions/confirmed-decisions.md` #42.

**Volume bars (the histogram pane itself) are now fully customizable per sub-window, distinct from the volume-average lines drawn on top of them.** `SubWindowConfig.volumeBars` (`VolumeBarsConfig`) controls whether the pane shows at all, and whether bars are colored two-color (up/down, each its own hex) or one flat hex color — same `ColorField` swatch-plus-hex-input control already used for every other customizable color in this UI (background, grid, timer, volume-avg lines, indicators, levels), so this isn't a new interaction pattern, just a new place it's applied. Disabling collapses the volume price scale's margins to zero height (`ChartWidget.tsx`), not just hiding the bars, so the candle pane actually reclaims the vertical space. See `../decisions/confirmed-decisions.md` #42.

### 4.12 WebSocket Gateway
Single multiplexed connection, topic-tagged envelopes — a thin re-publisher sitting on top of the Event Bus (§4.4), not a separate source of truth:

```json
{ "channel": "market.tick", "symbol": "NVDA", "payload": {...} }
{ "channel": "opportunity.new", "symbol": "NVDA", "payload": {...} }
{ "channel": "orders.status", "order_id": "...", "payload": {...} }
{ "channel": "input.command", "payload": {...} }               // InputCommand from any device — §18.6, companion doc
{ "channel": "orders.approval_queue", "payload": {...} }       // manual-mode pending TradePlan, accept/ignore
{ "channel": "chart.overlay", "symbol": "NVDA", "payload": {...} }
```
Frontend subscribes/unsubscribes to channels per symbol as widgets mount/unmount. This avoids one-socket-per-widget sprawl.

### 4.13 Database (PostgreSQL)
Core tables: `symbols`, `candles` (natively partitioned by month, sub-partitioned by timeframe), `trades` (gains an `origin: auto|manual` column and a `pending_confirmation` status once manual trading lands — `trading-intelligence-architecture.md` §18), `orders`, `positions`, `ai_decisions`, `strategy_performance`, `workspace_layouts`, `watchlists`. Alembic manages migrations from day one so schema drift never becomes a manual-SQL problem.

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
│   │   │   ├── execution_router.py
│   │   │   ├── mode.py                    # ExecutionMode flag + ExecutionModeChanged event — §18
│   │   │   └── approval_queue.py          # manual-mode holding queue for approved TradePlans — §18
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
│   │   ├── input/                        # Input Layer — self-contained module, see trading-intelligence-architecture.md §18.10
│   │   │   ├── deviceAdapters/
│   │   │   │   ├── gamepadAdapter.ts      # Gamepad API, rAF poll loop, edge-triggered RawInputEvent
│   │   │   │   └── types.ts               # RawInputEvent — device-agnostic; keyboard/Stream Deck/voice adapters land here later
│   │   │   ├── bindingMap.ts              # Binding[] (action -> device -> input), persisted with workspace layout — §18.10
│   │   │   ├── safetyLevels.ts            # Action -> SafetyLevel (0-3) table — §18.10
│   │   │   ├── hotkeyContext.ts           # chart/modal/text_input/settings — gates all dispatch — §18.10
│   │   │   ├── commandDispatcher.ts       # RawInputEvent + bindingMap + HotkeyContext + TradeTarget/QueueCursor -> routes by Action Category
│   │   │   └── useInputLayer.ts           # module's only public export — mounted once at the app shell
│   │   ├── hooks/
│   │   ├── services/
│   │   │   ├── websocket-client.ts
│   │   │   └── api-client.ts
│   │   ├── store/                        # Zustand slices — workspace layout, TradeTarget (per-tab, written by tile clicks only — §18.6), QueueCursor
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
| `FeaturesUpdated` | `timeframe`, `candle_ts`, `close` (confirmed decision #46 — makes this self-contained for a consumer needing both a level value and the raw price), `features: {...}` — **as built (decisions #45, #51, #52, #53):** `{sma_9, sma_20, sma_50, ema_9, ema_20, vwap}` (SMA/EMA periods configurable) on `1m`, `5m`, `15m`, and `1h` — `vwap` is the SAME value across all four, per decision #53. Everything else below is still the target shape, not yet computed: `atr_14, pdh, pdl, rel_volume, gap_pct, opening_range_high, opening_range_low, avg_volume_20d, trend_slope` |
| `LevelInteractionChanged` | `timeframe`, `level_key` (e.g. `"sma_9"` — whatever key `FeaturesUpdated.features` used, not a hardcoded enum), `trading_day`, `status` (holding\|rejected\|conquered\|unclassified), `zone` (below\|inside_aura\|above), `touch_count_today`, `seconds_in_zone`, `distance_pct`, `anchor_price`, `observed_via` (dwell\|gap\|cold_start_unknown_origin, only set at resolution) — confirmed decision #46, `app/trading_intelligence/level_interaction_engine.py` |
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
