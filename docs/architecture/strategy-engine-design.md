# Strategy Engine — Design & Lifecycle
**Status:** Stage 0 confirmed (`confirmed-decisions.md` #87). Concept locked across a two-round review (Saqib + Claude, with a consulted ChatGPT review of that same write-up incorporated directly — same reviewed-external-opinion pattern Daily Levels used with Grok, decision #59). **No application code has been written yet** — `strategy_engine/` doesn't exist anywhere in the repo; this document and decision #87 are the direction lock, matching decisions #50/#59/#67's own precedent.
**Owner:** Saqib
**Companion documents:** [`trading-intelligence-architecture.md`](./trading-intelligence-architecture.md) (§8 Strategy Engine, §9 Opportunity Engine, §10 Decision Engine, §11 Trade Planning Engine, §12 Governor, §14 Performance Intelligence — every section this plan extends, not replaces), [`system-design.md`](./system-design.md) (§4.5 Feature Engine — the sole data source every strategy reads; §4.8's `Strategy`/`Opportunity` interfaces, extended in §4 below), [`../decisions/future-ideas.md`](../decisions/future-ideas.md) (#5 Replay Engine — the interface §7's Backtest Runner reuses; #7 TimescaleDB trigger — checked, not yet hit; #11 `governor/position_sizing.py` — the eventual home for §6's Governor extension), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (#87 — this plan's own direction lock).

**Why this doc exists:** same reason `daily-levels-design.md` and `feature-engine-indicator-expansion.md` exist — too large for one sitting, and genuinely new ground for this codebase (the first real design pass at Strategy Engine internals, not an extension of an already-built module). If a session ends mid-build, the next session should read this doc plus `confirmed-decisions.md`'s most recent entries before touching anything, rather than re-deriving the concept from a diff.

---

## 0. Two framing corrections, made before anything else was designed

**First:** the original question was "how do we know this is a Fallen Angel" — implying one upstream classifier picks a single label per symbol. Rejected immediately: it contradicts trading-intelligence-architecture.md §8's own worked example (`NVDA: Momentum 92, ORB 80, Pullback 10` — three strategies firing simultaneously, arbitrated downstream by Opportunity Engine + Decision Engine, which already exist for exactly that). Nothing decides "this is a Momentum setup, not ORB." Every eligible strategy's detector runs independently; whichever fire compete.

**Second, surfaced during review:** don't think of Strategy Engine as maintaining a *ranked list* of strategies. A rank is one stored number. Suitability is conditional on context — the same strategy can be the strongest candidate in a low-VIX trending morning and the weakest in a choppy afternoon. The system maintains a **population of versioned strategy candidates**, queried per-context, never looked up as a stored fact. This is load-bearing for §5 below, not just better vocabulary.

---

## 1. Anatomy of a single strategy's `evaluate()`

Every strategy — ORB, Reversal, whatever comes later — goes through the same four stages. One template to write every strategy against, instead of each one inventing its own shape:

```
   Market State + Features + Context
                  │
                  ▼
   ┌───────────────────────────┐
   │ 1. GATE                   │  cheap, binary preconditions —
   │    is it worth checking?  │  is there even an opening range yet?
   └─────────────┬─────────────┘
                  │ pass
                  ▼
   ┌───────────────────────────┐
   │ 2. MATCH                  │  the structural pattern test —
   │    does the pattern hold? │  breakout + volume + trend, etc.
   └─────────────┬─────────────┘
                  │ true
                  ▼
   ┌───────────────────────────┐
   │ 3. SCORE                  │  how strongly does it match?
   │    → confidence            │  → Opportunity.confidence
   └─────────────┬─────────────┘
                  ▼
   ┌───────────────────────────┐
   │ 4. PROPOSE                │  structural entry/invalidation/target
   │    → Opportunity          │  implied by the pattern itself (§4)
   └───────────────────────────┘
```

Illustrative, not final code:

```python
class ORBMomentum(Strategy):
    name = "ORB"
    trigger = after_time("09:30", until="09:45")

    async def evaluate(self, market_state, features, context) -> Opportunity | None:
        if features.opening_range_high is None:                       # GATE
            return None
        broke_out = features.close > features.opening_range_high       # MATCH
        vol_confirmed = features.relative_volume > 1.3
        trend_ok = market_state.trend.direction == "bullish"
        if not (broke_out and vol_confirmed and trend_ok):
            return None
        confidence = weighted_score(...)                                # SCORE
        return Opportunity(                                             # PROPOSE
            strategy="ORB", version=self.active_version, direction="BUY",
            confidence=confidence,
            structural_invalidation=features.opening_range_low,         # §4
            structural_target=features.close + 2 * (features.close - features.opening_range_low),
            evidence={...},                                             # §4
        )
```

Planned initial strategy set unchanged from trading-intelligence-architecture.md §8: ORB, Momentum, First Pullback, VWAP, Gap, Reversal, Volume Spike.

**Playbook as data vs. code — resolved: code, not a generic rule engine, for v1.** A declarative "playbook" format for MATCH's condition tree was considered and rejected. Multi-signal conditions (comparing current vs. prior slope, level proximity, participation flips) get awkward fast as pure data, and this codebase has a consistent pattern of deferring generality until a concrete gap appears (Redis, Replay, uncertainty propagation — all deferred in `future-ideas.md` with "build it when the need shows up"). `Strategy(ABC)` subclasses, per the existing interface (system-design.md §4.8). If per-strategy thresholds need constant hand-tuning, that's the trigger to pull just the thresholds into config (§3) — not the whole condition tree into a rule engine.

---

## 2. Gate

**Two layers, kept explicitly separate:**

**a) Per-strategy scheduling trigger — already the existing design, unchanged.** `Strategy.trigger: ScheduleTrigger` (`after_time`, `on_event`, `every_candle`) answers "when does `evaluate()` even get invoked" — timing, not market condition. The Strategy Scheduler computing which strategies are currently eligible is exactly this mechanism already at work, not a new module:

```
        Market State / Scheduler clock
                     │
                     ▼
          ┌────────────────────┐
          │   Strategy Gate     │   "which strategies are
          │  (per-strategy      │    allowed to run RIGHT NOW?"
          │   trigger + b)      │
          └──────────┬──────────┘
                     ▼
           Eligible Strategy Pool
                     │
                     ▼
           Strategy Evaluation (§1)
                     │
                     ▼
             Opportunity + Evidence (§4)
                     │
                     ▼
              Decision / Ranking (§6)
                     │
                     ▼
                Trade Planning (§4)
                     │
                     ▼
                  Execution
```

**b) Declarative environmental gate conditions on `StrategyConfig` — new, resolves a real duplication risk.** A gate can also be a market-condition precondition — VIX above a band, RVOL floor, SPY trend bullish — genuinely different from scheduling, and if left to each strategy's own internal GATE step, duplicated across every strategy sharing a similar precondition. Resolved: these live as a small declarative block on the same versioned `StrategyConfig` row §3 defines for thresholds, evaluated centrally by the Scheduler *before* `evaluate()` is even called — the same "tunable numbers as data, pattern logic as code" split already applied to thresholds, extended to cover gates too. `evaluate()`'s own internal GATE step (§1) stays for cheap, strategy-specific preconditions that don't need sharing.

**Gate ≠ ranking** — worth stating as a standing principle: a gate answers "is this strategy allowed to participate right now," never "which strategy is best right now." That second question belongs to §5/§6.

---

## 3. Strategy Family → Configuration, immutable and versioned

**A `Strategy` subclass is a family** — "what market behavior are we trying to exploit," e.g. `ORB`. Its tunable behavior (thresholds, gate conditions) lives in a separate, versioned `StrategyConfig` row, not hardcoded into the class:

```
   ORB  (Strategy Family)
    │
    ├── v1   (retired,  active_from → active_to)
    ├── v2   (retired,  active_from → active_to)
    ├── v3   (retired,  active_from → active_to)
    └── v4   (live now, active_from → present)
```

```python
class StrategyConfig(BaseModel):
    strategy_name: str          # family, e.g. "ORB"
    version: str                # "orb_v4" — immutable once minted
    params: dict                # {"rvol_threshold": 1.3, "trend_strength_min": "increasing"}
    gate_conditions: dict       # {"vix_min": 20, "session": "regular"} — §2b
    active_from: datetime
    active_to: datetime | None
    rationale: str              # same discipline as confirmed-decisions.md's own entries
```

**Immutability is the load-bearing rule, not a style preference.** If `ORB`'s RVOL threshold changes from 1.3 to 1.5, that's `ORB v5`, not an edit to `v4`. Editing in place would silently blend pre- and post-change outcome history under one identity, corrupting exactly the comparison §5 exists to make.

Every `Opportunity` and every `StrategyOutcome` (§5) carries `strategy_version`, never just `strategy_name` — the foreign key that makes version-scoped performance queries possible at all.

---

## 4. Opportunity schema — Evidence, and a renamed contract with Trade Planning

**`reason: str` (system-design.md §4.8, already shipped in the schema) upgrades to a structured `evidence` object.** A free-text reason is fine for a human reading the UI; it's inert as data. Evidence captures the literal MATCH-stage values, not just a sentence generated from them:

```python
class Opportunity(BaseModel):
    strategy: str
    version: str                          # §3
    direction: Literal["BUY", "SELL"]
    confidence: float
    structural_invalidation: float        # was suggested_stop — see below
    structural_target: float              # was suggested_target
    evidence: dict                        # {"conditions": {...}, "reason": "..."}
```

`reason` stays a human-readable string, generated from `conditions` for display — but `conditions` (the actual values MATCH checked: `relative_volume: 2.14`, `trend: "bullish"`, `vwap_position: "above"`) is what's stored and later queried by Performance Intelligence (§5) to answer "did ORB actually perform better when RVOL was above 2?" A sentence can't answer that; a structured snapshot can.

**`suggested_stop`/`suggested_target` renamed `structural_invalidation`/`structural_target` — not cosmetic.** "Suggested" implied Trade Planning Engine could freely override the number. "Invalidation" states what it is: the price at which the strategy's own thesis is falsified, not a starting guess. The explicit contract — worth writing down, since §11/§12 never previously stated Trade Planning has to read this at all:

```
        Strategy                              Trade Planning Engine
   ┌─────────────────────┐             ┌──────────────────────────────┐
   │ Entry thesis          │           │ Position size (fractional     │
   │ (evidence.conditions) │           │ Kelly)                        │
   │ Strategy confidence    │  ─────►  │ Risk budget / R:R             │
   │ Structural invalidation│  REQUIRED│ Final stop  (refines, not      │
   │ Structural target      │  STARTING│    recomputes, invalidation)   │
   └─────────────────────┘   POINT     │ Final target                  │
                                        │ Scaling plan / trailing stop  │
                                        └──────────────────────────────┘
```

If Trade Planning's risk-adjusted final stop would sit *inside* the strategy's own invalidation point, that's a real conflict between risk management and the trade's own thesis — worth surfacing, not silently overwritten. Trade Planning Engine's actual sizing/scaling/trailing logic (§11 of `trading-intelligence-architecture.md`) is unchanged; only its required input contract is now explicit.

---

## 5. Performance Intelligence — atomic outcomes, not a stored rank

**Central discipline (§0's second correction, made concrete):** never persist `"ORB rank = 3"` as a fact. Persist atomic outcome records instead; compute rank/vectors at query time, sliced by whatever context the asker cares about.

```python
class StrategyOutcome(BaseModel):
    opportunity_id: UUID
    strategy_name: str
    strategy_version: str                 # §3's immutable version — never blended across versions
    symbol: str
    evidence: dict                        # §4's structured conditions, at signal time
    confidence_at_signal: float
    market_state_at_signal: dict          # trend, participation, volatility regime
    context_at_signal: dict               # gap day?, session type, VIX regime
    structural_invalidation: float
    structural_target: float
    final_stop: float                     # Trade Planning's actual number, post-refinement
    final_target: float
    exit_reason: Literal["target", "stop", "time", "manual", "reversal"]
    realized_r: float
    realized_pnl: float
    is_backtest: bool                     # §7 — never blended with live in a live query
    backtest_run_id: UUID | None
    closed_at: datetime
```

Persists to the already-planned `strategy_performance` table (system-design.md §6). "Rank," "expectancy by regime," "win rate by time-of-day" — every one of these is a `GROUP BY` over this table, computed on demand, never a value stored on the strategy itself:

```
                       StrategyOutcome (one row per closed trade)
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
   expectancy by regime     win rate by time-of-day   parameter sensitivity
      (query, not a field)     (query, not a field)     (query, not a field)
```

**Two governing principles, agreed and worth stating as load-bearing, not implicit:**

> The Strategy Engine does not determine which strategy is "best." It determines which strategies are applicable and what opportunities they identify. Performance Intelligence determines the empirical suitability of strategy configurations under specific contexts. Decision Engine determines which eligible opportunity receives capital.

> Backtesting/tuning may automatically search and evaluate strategy configurations. Promotion, retirement, or modification of a live `StrategyConfig` requires human approval — no exception, regardless of how strong the automated evidence looks.

**v1 feedback loop is human-reviewed, not automatic — a direct decision, not a default assumed.** Performance Intelligence surfaces evidence; Saqib reviews and decides whether to promote a new `StrategyConfig` version. Automatic reweighting is a real future direction (trading-intelligence-architecture.md §14 already names "reweight or retire" as Performance Intelligence's eventual feedback into Strategy Engine) but isn't built now — same "empirical before architectural commitment" discipline already applied to Polygon depth, IBKR access, and Finnhub concurrency. Trigger to revisit: enough closed trades per `StrategyConfig` version that a reweight isn't noise.

---

## 6. Decision Engine and Governor — two different questions over the same evidence

**Not a single "decision and adjustment" module — two distinct questions, each already owned by an existing stage:**

```
                    Performance Intelligence evidence
                    (context-sliced, from §5's queries)
                              │
              ┌───────────────┴────────────────┐
              ▼                                 ▼
   "Which of several COMPETING      "Should we act on THIS ONE
    opportunities wins?"             already-selected, already-
                                      planned trade, and at what size?"
              │                                 │
              ▼                                 ▼
      Decision Engine (§10)              Governor (§12)
      — unchanged role,                  — unchanged role,
        evidence is a new                  evidence is a new
        arbitration input                  derate/veto input
        (tie-breaker)                      (approved_reduced /
                                            watch_only / delayed)
```

1. **"Given several competing opportunities right now, which one wins?"** — arbitration. Stays Decision Engine's job (§10 of `trading-intelligence-architecture.md`, unchanged) — it compares across candidates; nothing downstream of it does. Performance evidence becomes a new *input* to that arbitration (ORB: +0.31R vs. Momentum: +0.18R, in this exact context), not a new stage.

2. **"Given this one already-selected, already-planned trade, should we act on it, and at what size?"** — derating/veto. This is Governor's existing job (§12). Its output schema already has the vocabulary — `approved_reduced` (`size_multiplier`), `watch_only`, `delayed` — real branches in the type since v1, unimplemented until a concrete rule needed them (§12's own stated reasoning). Performance evidence is exactly that concrete rule: a `StrategyConfig` version with weak evidence in the current context is the same category of "no" as "daily loss limit reached." `future-ideas.md` #11 already earmarks `governor/position_sizing.py` as where evidence-informed sizing plugs in — this is that plug, arriving.

**The boundary, stated precisely so it can't drift:** Governor may derate or delay an individual trade using performance evidence. Governor may **not** retire, disable, or modify a `StrategyConfig`. One is a real-time risk judgment on one trade (Governor's actual job); the other is changing what's live (§5's human-approval principle). If live derating on a config looks bad enough that retirement seems warranted, that's a signal for Saqib to review, not a threshold Governor crosses on its own.

**Open, not resolved here — see §10's table:** Saqib has raised the possibility of merging Decision Engine and Governor into one component outright. Not decided either way in this document; both responsibilities above hold regardless of whether they end up as one component or two — a structure question, not a logic question, and it doesn't need resolving before Strategy Engine work can proceed.

---

## 7. Backtest Runner — extends the already-deferred Replay Engine, doesn't duplicate it

**Not a new capability from scratch.** `future-ideas.md` #5 already defers a Replay Engine (`broker_adapters/replay_provider.py` implementing `MarketDataProvider`) for interactive historical review. A Backtest Runner is the same interface, consumed headlessly: no real-time throttle, wrapped in an outer parameter-search loop over `StrategyConfig` candidates.

**Non-negotiable design constraint, worth baking in now since `strategy_engine/` doesn't exist yet:** `Strategy.evaluate()` must run byte-identical in backtest and live — no `if backtesting:` branches. Every strategy must derive "now" from the event/candle timestamp via `MarketClock`, never `datetime.now()` directly. Retrofitting this after several strategies already exist would mean auditing each one for live-only assumptions; building it in from the first strategy costs nothing.

**Search is automated; promotion is not (§5's principle, applied to this specific mechanism):**

```
   Historical Data (5yr, N symbols)
              │
              ▼
      Backtest Runner            ◄── outer loop over StrategyConfig
   (replay_provider.py, no          candidates (grid search)
    real-time throttle)
              │
              ▼
   StrategyOutcome rows
   (is_backtest=True, backtest_run_id set)
              │
              ▼
   Walk-forward / holdout validation     ◄── never a single in-sample pass
              │
              ▼
   Performance Intelligence report        ◄── expectancy + robustness (below),
              │                               never expectancy alone
              ▼
   ┌─────────────────────┐
   │   HUMAN REVIEW        │   ◄── Saqib, per §5's principle
   │   (Saqib)              │
   └──────────┬───────────┘
              ▼
      Promote (new StrategyConfig,
       active_from = today)  /  Reject
```

**Required output, not optional — robustness over raw expectancy.** A single in-sample optimization pass will always find a config that looks best on the exact data it was tuned against; that's not the same as a config that's actually good. The comparison report must include, alongside expectancy: trade count, consistency across years/symbols/regimes/time-of-day, and a parameter-sensitivity curve (expectancy vs. the threshold being tuned, across a range) — a smooth curve suggests a robust setting, a spiky one flags overfitting risk. "Best backtested expectancy" alone is not sufficient evidence for promotion.

**Scale check, done rather than assumed:** 5 years of 1m candles, regular session only, ≈490K rows/symbol; even 30–40 symbols stays well within current plain-Postgres monthly partitioning. Doesn't trigger the Timescale-migration question (`future-ideas.md` #7) at the symbol counts discussed — revisit only if the backtest universe grows substantially past that.

Not built now. Constrains how the first strategy gets written (pure `evaluate()`, `MarketClock`-only timing); the harness itself is real, deferred work.

---

## 8. Entry timing & bar-close confirmation — open, continuing

**Deliberately not locked in this pass — Saqib has asked to continue this discussion with more structure before it's settled.** Recorded here as the current state of the reasoning, not a final design.

**What's already true, confirmed against the real code, not assumed:**
- Feature Engine never publishes a still-forming higher-timeframe bar as final — 5m/15m/1h `FeaturesUpdated` only fires once `candle_aggregator.completes_bucket()` closes that bucket (system-design.md §4.5). No risk of reading an in-progress bar's OHLC as settled.
- Nothing computed today is sub-1-minute. Market State Engine (where Participation — buyer/seller control — would live) isn't built. Every Feature Engine indicator, RVOL included, recomputes on 1m close. The only sub-minute feed is `LiveTickRelay`'s `PriceSnapshot` — raw, uninterpreted OHLCV for the forming bar, max 8 actively-relayed symbols, no derived signal on top of it.

**The reframed question:** not "act now vs. wait a minute for fresh data" — the fastest computed signal available today is already the just-closed 1m bar. The real choice is "act on the 1m signal already in hand, or wait for a slower timeframe to also close before trusting it."

**A distinction proposed, not yet fully worked through:**

```
   Level / participation facts              Candle-shape facts
   (price crossed a level, RVOL       vs.   (this bar closed as a rejection
    rising, buyer/seller flip)                wick, closed holding above a level)
              │                                        │
              ▼                                        ▼
   true the instant they're true —         NOT a fact until the bar closes —
   safe to act on as soon as the           acting early isn't faster, it's
   fastest available bar confirms it       evaluating something not yet real
```

Which category a given MATCH condition falls into determines whether waiting is a genuine requirement or a tunable trade-off.

**A mechanism sketched, not committed:** a `confirmation_timeframe: Optional[str]` field on `StrategyConfig` (§3), checked by a small, reusable confirmation gate before an `Opportunity` reaches Decision Engine — centralizing "wait for this timeframe to close" once, rather than duplicating it per strategy, and making "does this config require confirmation, and at what timeframe" a versioned, backtestable question (§7) rather than a hardcoded guess.

**Deliberately flagged as a real, currently-missing capability rather than assumed to exist:** genuinely intrabar (sub-1m) pattern detection — deriving a live decay/imbalance signal directly off `PriceSnapshot`'s raw feed — doesn't exist today and isn't a side effect of anything already built. If wanted, needs its own design pass and its own `future-ideas.md` entry, not an assumption folded into this one.

**To be continued** — timing/structure possibilities under the current architecture, and how far to take this now vs. defer, per Saqib's own stated plan to keep discussing before locking further.

---

## 9. The full feedback loop, assembled

Everything above, connected — the learning loop this design is actually building, not just "a collection of trading strategies":

```
                          Strategy (§1, §2)
                                │
                          produces (§4)
                                ▼
                          Opportunity + Evidence
                                │
                                ▼
                    Decision Engine  (§6 — arbitration)
                                │
                                ▼
                       Trade Planning Engine
                                │
                                ▼
                    Governor  (§6 — derate/veto)
                                │
                                ▼
                            Execution
                                │
                                ▼
                        Position Closed
                                │
                                ▼
                   Performance Intelligence (§5)
                       StrategyOutcome rows
                                │
                                ▼
                          HUMAN REVIEW  (Saqib)
                                │
                                ▼
                    New StrategyConfig version (§3)
                                │
                                ▼
                   Backtest Runner (§7) — search + validate
                                │
                                ▼
                          HUMAN REVIEW  (Saqib)
                                │
                                ▼
                              LIVE
```

---

## 10. Open decisions — still genuinely open

| # | Decision needed | Status |
|---|---|---|
| D1 | Merge Decision Engine and Governor into one component, or keep as two | **Open.** Saqib has raised this as a real possibility. §6 states both responsibilities regardless of eventual component boundary; this is a structure question, not a logic question, and doesn't block Strategy Engine work. |
| D2 | Bar-close confirmation mechanism (§8) — exact scope, which strategies need it, whether `confirmation_timeframe` is the right shape | **Open, by design.** Saqib has asked to continue this discussion with more structure before it's settled — not resolved in this document on purpose. |
| D3 | When automatic (vs. human-reviewed) reweighting/retirement graduates from future work to real (§5) | **Open, deferred.** Trigger: enough closed trades per `StrategyConfig` version for a reweight to not be noise — no specific count set yet. |
| D4 | Exact "Candidate Selection Score" formula (Performance × Context Fit × Confidence × Robustness) | **Deliberately not decided.** Agreed directly (Saqib + Claude + the consulted ChatGPT review) not to lock a scoring formula before real outcome data exists to check it against. |

---

## 11. Guiding constraints carried into this design (standing project principles, not new rules)

- **Honest state over fabricated state** — an outcome record with no data for a field stays `None`/absent; Performance Intelligence never estimates a plausible-looking number for something not yet measured.
- **Compute once, consume everywhere** — one `StrategyOutcome` schema serves live performance queries and backtest reports alike, distinguished only by `is_backtest`, never duplicated per consumer.
- **Real Postgres, not mocks**, once `strategy_performance` has real rows to query against — same standard as every other module in this codebase.
- **Docs updated in the same change as code** — once Strategy Engine code exists, this document and `confirmed-decisions.md` update alongside it, not after.
- **Architecture questions surfaced before code** — §10's open items get resolved (or explicitly deferred with a trigger condition) before the corresponding code is written, not silently decided mid-implementation.

---

## 12. Staged plan

- [x] **Stage 0 — Lock the direction in writing (no application code).** This document + `confirmed-decisions.md` #87.
- [ ] **Stage 1 — Not yet started.** Blocked on §10's D1/D2 open items being resolved enough to implement against, and on Saqib's own priority call relative to other in-flight work (IBKR live access, ORB as first live strategy per `trading-intelligence-architecture.md`'s existing plan).

---

## 13. How to resume this in a new session

1. Read this file in full, then `confirmed-decisions.md`'s most recent entries — check whether §10's open items have moved before re-deciding them.
2. Do not start any `strategy_engine/` code before §7's "identical live/backtest `evaluate()`" constraint is understood by whoever writes the first strategy — retrofitting it later is real, avoidable cost.
3. §8 is explicitly unfinished by design — expect more discussion before it's locked, not a gap to quietly fill in.
