# Trading Intelligence Architecture
**Version:** 1.3
**Companion documents:** [`system-design.md`](./system-design.md) — that doc explains *how the system is built* (modules, interfaces, deployment, folder structure). This doc explains *how the system thinks* (market state, context, strategy, decision logic). [`../decisions/future-ideas.md`](../decisions/future-ideas.md) holds concepts raised and deliberately deferred, with the reasoning intact, so they don't need to be re-argued from scratch later. [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) is the running settled-decisions log. Keep these separate; a change to trading logic shouldn't require touching WebSocket plumbing, and an idea that isn't ready yet shouldn't clutter a document meant to describe what's actually built. See [`../README.md`](../README.md) for how the whole `docs/` tree is organized.

---

## 1. Central Idea

Every component in this system, whatever else it does, is ultimately answering one question: **"What is the current market state?"** — not "what's the latest tick." The chart, the AI, execution, and eventually replay are all *consumers* of state, not owners of their own private view of it. This is what makes the platform a **Trading Intelligence Operating System** rather than a chart with some scripts attached.

The pipeline below mirrors how a discretionary trader actually reasons:

```
What market am I in?          →  Market State
What is today's situation?    →  Context
What opportunities exist?     →  Strategy Engine → Opportunity Engine
Which one is best?            →  Decision Engine
How should I trade it?        →  Trade Planning Engine
Can I afford it?              →  Governor
Execute.                      →  Execution Engine
Manage.                       →  Position Monitor
Learn.                        →  Performance Intelligence
```

---

## 2. Two Kinds of Intelligence

Every module in this system falls into one of two categories. Naming the category a new module belongs to is the fastest way to keep the architecture disciplined as it grows — e.g. a future Order Flow Engine or News Sentiment Engine: which side does it belong on?

**State Intelligence** — describes reality. Answers *"what is happening?"* Has no opinion about what to do about it.
- Feature Engine, Market State Engine, Context Engine, Portfolio State Engine

**Decision Intelligence** — decides action. Answers *"what should we do?"*
- Strategy Engine, Opportunity Engine, Decision Engine, Trade Planning Engine, Governor

**Two hybrids, called out explicitly rather than forced onto one side:**
- **Position Monitor** reads state continuously but exists to decide (hold / partial / exit / reverse) — it's best understood as a Decision Engine that runs continuously against one open position instead of once against a new opportunity.
- **Performance Intelligence** describes the past (pure state, about what already happened) but exists solely to change future decisions — it's State Intelligence whose entire purpose is feeding Decision Intelligence.

---

## 3. The Full Pipeline

```
                    Market Data (Broker Adapter)
                                │
                                ▼
                        Feature Engine  ─────────────────┐
                                │                          │
                                ▼                          ▼
                     Market State Engine            Scanner (100 → N)
                                │                          │
                                ▼                          │
                        Context Engine                     │
                                │                          │
                                └────────────┬──────────────┘
                                             ▼
                                  Strategy Scheduler
                                             │
                                             ▼
                                    Strategy Engine
                       (ORB, Momentum, Pullback, VWAP, Gap,
                        Reversal, Volume Spike, News, ...)
                                             │
                                             ▼
                                  Opportunity Engine
                              (what exists — no decisions)
                                             │
                                             ▼
                     Decision Engine  ◄──── Portfolio State
                    (which opportunity wins, if any)
                                             │
                                             ▼
                             Trade Planning Engine
                    (entry, stop, target, size, R, max hold,
                     scaling, trailing stop)
                                             │
                                             ▼
                   Governor (Risk & Policy)  ◄──── Portfolio State
                                             │
                                             ▼
                                   Execution Engine
                                             │
                                             ▼
                                  Position Monitor
                    (still valid? weakening? partial? exit?)
                                             │
                                             ▼
                          Performance Intelligence
                                             │
                        ┌────────────────────┴─────────────────────┐
                        ▼                                          ▼
                Strategy Engine                          Trade Planning Engine
              (reweight / retire)                       (recalibrate sizing/stops)
```

**Portfolio State** and **Market Clock** are drawn as side inputs rather than pipeline stages because they're shared services, not steps — every stage that needs "what do we currently hold" or "what time/session is it" reads them directly rather than having that information passed down the chain. Decision Engine, Governor, Position Monitor, and Performance Intelligence all read Portfolio State independently.

---

## 4. Market State — Has Memory, Not a Snapshot

The mistake to avoid: treating market state as a single current value.

```
Trend:      Bullish
```

Instead, every state dimension carries its own trajectory:

```
Trend:      Bullish
Duration:   23 minutes
Strength:   Increasing
Confidence: 91%
Previous:   Neutral
Changed at: 09:52:14
```

This is what lets strategies reason about *change*, not just position — "momentum has been weakening for 12 minutes" is a fundamentally different (and more useful) signal than "momentum = 65." A snapshot can't tell you that; a state object with memory can.

**State dimensions tracked (each with the duration/strength/confidence/previous shape above):** Trend, Volatility regime, Volume regime, VWAP relationship, Session type, Market breadth, Participation.

**Participation is the observable half of market psychology, not the causal half.** §7's agent-design table already asks "who is in control — buyers or sellers?" as an example question; this is where it gets a real answer. Feature Engine computes a raw, tick-derived signed-volume / uptick-downtick imbalance — an observable, no different in kind from relative volume or gap % — and Market State Engine turns that into a Participation dimension with the same duration/strength/confidence/previous shape as everything else, so "buyers have been in control for 8 minutes and strengthening" is a first-class read. What Participation deliberately does *not* claim is *why*: the same volume imbalance can mean panic, excitement, short covering, or options-hedging flow, and telling those apart needs data (options gamma exposure, short interest) this system has no confirmed source for yet. That causal-inference layer is real, and it's kept visible rather than dropped — see [`../decisions/future-ideas.md`](../decisions/future-ideas.md) #13 — but faking it from data that can't support the distinction would produce a confidently wrong label, not a useful one.

**Implementation note, flagged deliberately because it's easy to get wrong:** this makes the Market State Engine *stateful* — it holds a rolling window per symbol, not just the latest computed value. That raises a real question for Phase 5: on a backend restart mid-session, does the engine rebuild "bullish for 23 minutes" from `market_state_history` / `market_events` (see `system-design.md` §4.13), or does it wake up with duration reset to zero? **Decision: rebuild from persisted history on startup.** The whole point of storing `market_state_history` and `market_events` is to make state survive a restart — an engine that forgets duration every time the process bounces defeats the feature. This should be a concrete Phase 5 task, not an afterthought.

**Recompute cadence, decided now for Phase 2 wiring:** Market State Engine doesn't recompute on every tick, and it doesn't run on a fixed timer either — both are wrong for different reasons (every tick is wasteful given how rarely trend/volatility/volume regime actually change; a fixed timer misses fast-moving regime shifts between ticks of the timer). It uses the shared `DebounceScheduler` (`system-design.md` §8, `core/debounce_scheduler.py`): recompute is triggered by relevant upstream events (`FeaturesUpdated`, `ContextChanged`, a volatility spike crossing threshold), floored to no more than once per ~1 second so a burst of ticks doesn't cause redundant recompute, and ceilinged to at least once every ~10 seconds so state can't go stale even in a quiet market. This is the same event-driven-with-bounds shape as Scanner's cadence schedule (`system-design.md` §4.7) and Strategy triggers (§8 below) — a pattern this system already uses twice, extended here rather than reinvented.

---

## 5. Context Engine — Composed Providers, Not One Engine

Market State describes the market. Context describes the *situation* — and the same market state means different things depending on it.

> Bullish trend, first 15 minutes, gap up, near PDH, Fed day, high relative volume, inside yesterday's range — is a completely different trade than the same "Bullish" reading at 1pm on a quiet Tuesday.

Context is going to keep growing — economic calendar, OPEX, breadth, macro regime, sector strength, news, sentiment, seasonality all plausibly belong here eventually. Treating it as one engine with a growing pile of `if` branches would turn it into the least maintainable module in the system within a few months. Instead, Context Engine is a thin **aggregator** over independent, individually-testable **context providers**, each responsible for one question:

```python
class ContextProvider(ABC):
    name: str
    async def evaluate(self, market_state: MarketState) -> dict: ...
```

v1 providers: `CalendarProvider` (session timing, Fed days, holidays — via Market Clock), `GapProvider` (gap status vs. prior close), `LevelsProvider` (proximity to PDH/PDL/VWAP/round numbers), `VolatilityRegimeProvider` (realized vol vs. recent history), `SectorCorrelationProvider` (is this symbol moving with or against its sector ETF right now — a breakout against a falling sector is a different trade than one moving with it), `NewsFlagProvider` (has a headline hit for this symbol in the last N minutes — a boolean/count, deliberately not sentiment scoring; see [`../decisions/future-ideas.md`](../decisions/future-ideas.md) #13 for why NLP sentiment stays deferred). Context Engine calls each registered provider and merges their output into one `ContextChanged` event (see `system-design.md` §10 for the payload contract). Adding a new context dimension later — OPEX, breadth, fundamentals — means writing one new provider, not touching the aggregator or anything downstream.

**Providers refresh at whatever cadence their underlying reality actually changes at — this was always true of the interface, now made explicit because it matters for what comes next.** `VolatilityRegimeProvider` re-evaluates on the same `DebounceScheduler` rhythm as Market State (§4, seconds-scale). `CalendarProvider` changes on session/day boundaries. Nothing about the `ContextProvider` interface assumes tick-speed refresh — a provider whose underlying reality only changes quarterly (earnings, balance-sheet data) is exactly as valid a provider as one that changes every 10 seconds; it just triggers on a different event (`EarningsReleased` instead of a volatility threshold crossing) and sits idle otherwise. This is what keeps a path open to slower-moving context — fundamentals, macro — without it costing anything today: same abstraction, sparser trigger, no new module. See [`../decisions/future-ideas.md`](../decisions/future-ideas.md) #9–#12 for the specific slow-tier providers this unlocks once their data sources are settled.

---

## 6. Portfolio State — Treated Like Market State

Portfolio State is the account-side mirror of Market State: continuously maintained, not computed ad hoc when someone needs it. It's a shared service, not something owned by whichever module asked for it first.

| Consumer | Needs |
|---|---|
| Decision Engine | exposure, correlation across open positions |
| Governor | capital, buying power, daily loss consumed |
| Position Monitor | unrealized P&L, average cost |
| Performance Intelligence | historical position data |

Updated on `OrderFilled` / `PositionClosed`. Never recomputed independently by a consumer — same principle as Feature Engine (§ below): compute once, read everywhere.

---

## 7. Agent Design Philosophy — Question-Based, Not Indicator-Based

Don't design a module around "an EMA strategy." Design it around a question it answers. This is a subtle shift with real leverage: it makes every module extensible, because a new module just needs a new question, not a rewrite of how modules relate to each other.

| Module | Question |
|---|---|
| Trend (part of Market State Engine) | What is the dominant trend? |
| Participation (part of Market State Engine, §4) | Who is in control — buyers or sellers? |
| Liquidity | Where is liquidity likely sitting? |
| Breakout (a Strategy) | Is this breakout likely to continue? |
| Risk (the Governor) | Can we afford this trade? |

**Important distinction this raises:** "Trend" and "Participation" are **state-builders** — they feed Market State/Context and belong to State Intelligence. "Breakout" and "Momentum" are **setup-detectors** — they consume that state to decide whether a trade exists, and belong to Strategy Engine, i.e. Decision Intelligence. Both are "agents" in the loose sense, but they don't sit in the same pipeline stage. Keep that boundary explicit once there are a dozen of these — otherwise it becomes unclear whether a Trend module is "competing" with an ORB module, when they're not even doing the same job.

---

## 8. Strategy Engine

Each strategy answers one question and produces an **Opportunity Object**, never a bare BUY/SELL:

```
NVDA
  Momentum:  92
  ORB:       80
  Pullback:  10
```
or, expanded:
```
AAPL — ORB
  Confidence:  82
  Entry:       220.10
  Stop:        219.30
```

No trade yet. Only opportunity. Strategies don't run on a shared clock — each declares its own trigger (every candle, only after 9:35, only after a volume spike, every tick) via the Strategy Scheduler, so timing logic isn't duplicated across strategies (see `system-design.md` §4.7's Scanner cadence for the same pattern applied one layer up).

Planned initial strategy set: ORB, Momentum, First Pullback, VWAP, Gap, Reversal, Volume Spike. News is listed as a future addition, not a v1 strategy.

---

## 9. Opportunity Engine

Reads every Opportunity Object produced for a symbol across all strategies and ranks them. **It does not decide anything.** Its entire job is answering "which opportunities currently exist, and how do they compare" — arbitration is explicitly not its responsibility, which is why Decision Engine exists as a separate stage.

---

## 10. Decision Engine

Arbitrates when opportunities compete — same symbol, conflicting directions (Momentum says BUY, Reversal says SELL), or multiple symbols competing for the same limited capital. Reads Portfolio State (current exposure, correlation to existing positions) to make that call. Outputs at most one `OpportunitySelected` per available capital slot — everything else is discarded at this stage, not silently overridden later.

This is the layer that resolves: *Opportunity: 95% confidence. Trade Planner: ready. Decision Engine still has to decide whether this opportunity gets acted on at all before planning even starts.*

---

## 11. Trade Planning Engine

Answers *"if we trade this, how?"* — a fundamentally different question from *"should we trade?"* (that's Decision Engine and Governor's job). Produces:

- Entry
- Stop
- Target
- Position size (fractional Kelly — capital preservation first)
- R multiple
- Maximum hold time
- Scaling plan
- Trailing stop rule

---

## 12. Governor (Risk & Policy)

The heart of the system — not because it calculates anything sophisticated, but because it's the layer allowed to say **no** after everything upstream said yes.

```
Opportunity:    95%
Trade Planner:  ready
Governor:       "No."

Reasons:
  Already long NVDA
  Daily loss limit reached
  Too correlated to existing positions
  Fed speech in 8 minutes
  Risk budget exhausted
```

Reads Portfolio State (capital, buying power, daily loss consumed) and Context (scheduled events, session type) to make that call. Every rejection is logged with its reason — a rejected plan is exactly as valuable a data point as an approved one when Performance Intelligence later asks "are we too conservative, or exactly conservative enough?"

**The Governor's output schema is wider than a binary approve/reject, even though v1 only implements two of the branches.** A real risk manager doesn't just say yes or no — they say "reduce size," "wait 3 minutes," or "watch only, don't act." Building the schema for that now costs nothing (it's a type definition), and avoids a breaking change to every downstream consumer later:

```python
class GovernorDecision(BaseModel):
    action: Literal["approved", "approved_reduced", "delayed", "watch_only", "rejected"]
    size_multiplier: float | None = None   # used only when action == "approved_reduced"
    delay_seconds: int | None = None       # used only when action == "delayed"
    reasons: list[str]
```

**v1 implements `approved` and `rejected` only.** `approved_reduced`, `delayed`, and `watch_only` are real branches in the type but return `NotImplementedError` (or simply never get triggered by v1 rule logic) until there's a concrete rule that needs them. This is a schema decision made now, not a feature built now — the distinction matters.

---

## 13. Position Monitor

Underweighted in early drafts of this system — deliberately elevated here. Not a passive "position is open" tracker. It continuously asks, against live Market State and Features:

```
Still valid?
Momentum weakening?
Move stop?
Take partial?
Exit?
Reverse?
Hold?
```

This is an active decision-making module, not a display widget — hence its classification as hybrid State/Decision Intelligence in §2.

**Cadence:** like Market State Engine (§4), Position Monitor uses the shared `DebounceScheduler` rather than a fixed poll timer — re-evaluate immediately on a relevant event (price crossing near stop/target, a `MarketStateChanged` on the held symbol), no more than once per ~1 second, at least once every ~10 seconds regardless of whether anything relevant fired. An open position shouldn't silently go un-evaluated for a long stretch, but it also doesn't need re-evaluation on every single tick when nothing has actually changed.

---

## 14. Performance Intelligence

Deliberately not called "Analytics" — analytics sounds passive, and this module's entire reason for existing is to change future behavior. It answers:

```
Why are we losing?
Which strategy underperforms?
Morning vs. afternoon?
Gap days?
High VIX regimes?
Low float names?
Fridays?
```

Feeds back into two places: **Strategy Engine** (reweight or retire underperforming strategies) and **Trade Planning Engine** (recalibrate sizing/stop logic based on realized outcomes, not assumptions). This is the seed of an eventual optimization engine, though building that optimization loop itself is out of scope for now.

---

## 15. World View — Read-Only Composite Snapshot

There's a real idea worth keeping from the "everything should revolve around a persistent World Model" suggestion — but not in the form it was proposed. "Everything reads from it, everything writes to it" is the exact anti-pattern Feature Engine and Portfolio State Engine exist to prevent (§7 of `system-design.md`, principle 8: compute once, consume everywhere). A shared object with many writers is how state gets inconsistent, not how it stays coherent.

What's actually valuable is a **read-only composite view**: a facade that assembles Market State + Portfolio State + Context + recent Performance Intelligence output into one coherent snapshot of "what does the system currently believe," for consumers that want the whole picture at once — a debug dashboard, or a future LLM-based reasoning layer that needs one prompt-sized summary instead of five separate queries.

```python
class WorldView:
    async def snapshot(self, symbol: str | None = None) -> WorldViewSnapshot: ...
    # assembles from existing single-owner sources — owns nothing itself
```

Single-writer-per-domain stays fully intact — `WorldView` has no state of its own and no write path. It's purely a read aggregator, the same relationship Context Engine has to its providers (§5). The full "everything writes to it" version is parked in [`../decisions/future-ideas.md`](../decisions/future-ideas.md) in case a genuine need for shared mutable world state emerges later — it hasn't yet.

---

## 16. Explicitly Deferred (Not Forgotten)

Deferred ideas — this round's and earlier rounds' — now live in one place: [**`../decisions/future-ideas.md`**](../decisions/future-ideas.md). That includes Replay Engine, Simulation Mode, Knowledge Engine, Attention Engine, uncertainty propagation, the full write-everywhere World Model, and — added this round — a quarterly-tier `FundamentalsProvider`, an Expectation/Surprise provider, fundamentals-informed sizing, a macro slow-tier provider, and causal psychology / market-participant inference (options flow, short interest). Centralizing them there (rather than re-explaining reasoning in whichever doc happened to be open when the idea came up) is what keeps this doc and `system-design.md` from drifting — the same reasoning that justified splitting into two documents in the first place applies to a third.

One piece of reasoning worth keeping visible here rather than only in the future-ideas doc, because it's load-bearing for Phase 3: `system-design.md`'s `BrokerAdapter` interface already means the Market Data Engine doesn't care whether its source is live or replayed. Replay, whenever it's built, becomes a new implementation of that interface — not a redesign of anything upstream. That's why deferring it now costs nothing later.

---

## 17. Bridge to Software Architecture

Every concept above has a concrete home in `system-design.md`. Use this table when a conversation starts drifting between "how it thinks" and "how it's built" — that's the signal to switch documents.

| Trading-intelligence concept | Code location (`system-design.md`) |
|---|---|
| Market State (with memory) | `trading_intelligence/market_state_engine.py` → `market_state_history` table |
| Participation (Market State dimension) | `feature_engine/indicators.py` (signed volume / tick imbalance) → `trading_intelligence/market_state_engine.py` |
| Context (composed providers) | `trading_intelligence/context_engine/` (`engine.py` + `providers/`) (derived, not persisted) |
| Sector/correlation context | `trading_intelligence/context_engine/providers/sector_correlation_provider.py` |
| News-flag context | `trading_intelligence/context_engine/providers/news_flag_provider.py` |
| Strategy Engine | `trading_intelligence/strategy_engine/` → `ai_decisions`, `feature_snapshots` |
| Opportunity Engine | `trading_intelligence/opportunity_engine.py` → `ai_decisions` |
| Decision Engine | `trading_intelligence/decision_engine.py` → `ai_decisions` |
| Trade Planning Engine | `trading_intelligence/trade_planning_engine.py` → `trades` (draft) |
| Governor (widened decision schema) | `governor/governor.py`, `risk_rules.py`, `position_sizing.py` → `trades` (approved/rejected) |
| Position Monitor | `position_monitor/monitor.py` → `positions` |
| Performance Intelligence | `performance_intelligence/analyzer.py` → `strategy_performance` |
| Portfolio State | `portfolio_state/engine.py` |
| Market Clock | `core/market_clock.py` |
| Event Bus + event contracts | `event_bus/bus.py`, `events.py` → see `system-design.md` §10; two dispatch lanes (critical vs. normal), see §4.4 |
| Feature Engine | `feature_engine/engine.py`, `indicators.py` → `feature_snapshots` |
| World View (read-only facade) | `world_view/composite.py` — reads only, owns nothing |
| Shared update-policy utility (DebounceScheduler) | `core/debounce_scheduler.py` — used by Market State Engine (§4) and Position Monitor (§13) |

If a trading-logic change doesn't map to a row in this table, it's a signal the code structure needs to catch up — not that the mapping should be skipped.
