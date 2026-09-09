# Strategy Engine — Design & Lifecycle
**Status:** Stage 0 confirmed (`confirmed-decisions.md` #87, refined by #88). Concept locked across a two-round review (Saqib + Claude, with a consulted ChatGPT review of that same write-up incorporated directly — same reviewed-external-opinion pattern Daily Levels used with Grok, decision #59); §8's timing model went through a further two-round refinement (ChatGPT's "opportunity lifecycle" critique → Claude's schema-gap findings → ChatGPT's "ACT/WAIT/ABANDON, not bar-close" correction, adopted). **No application code has been written yet** — `strategy_engine/` doesn't exist anywhere in the repo; this document and decisions #87/#88 are the direction lock, matching decisions #50/#59/#67's own precedent.
**Owner:** Saqib
**Companion documents:** [`trading-intelligence-architecture.md`](./trading-intelligence-architecture.md) (§8 Strategy Engine, §9 Opportunity Engine, §10 Decision Engine, §11 Trade Planning Engine, §12 Governor, §14 Performance Intelligence — every section this plan extends, not replaces), [`system-design.md`](./system-design.md) (§4.5 Feature Engine — the sole data source every strategy reads; §4.8's `Strategy`/`Opportunity` interfaces, extended in §4 below), [`../decisions/future-ideas.md`](../decisions/future-ideas.md) (#5 Replay Engine — the interface §7's Backtest Runner reuses; #7 TimescaleDB trigger — checked, not yet hit; #11 `governor/position_sizing.py` — the eventual home for §6's Governor extension; #20 Time-to-Target Estimator — the eventual source of a temporal expectation on `Opportunity`/`StrategyConfig` (§3/§4), deferred pending real `StrategyOutcome` data), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (#87 — this plan's own direction lock).

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

**`gate_conditions` is declarative; `StrategyScheduler` is its sole enforcement authority — decision #118, made explicit once real implementation (decision #117) existed to make the distinction concrete.** `StrategyConfig.gate_conditions` is configuration data, nothing more. `app/strategy_engine/gate_conditions.py` plus `StrategyScheduler` are the only code permitted to interpret it — a `Strategy` subclass may declare `gate_conditions` on its own config, but must never independently interpret the dict, invent a new key's meaning, or enforce a gate itself. The risk this closes: absent this rule, nothing stops a future strategy from reading its own `gate_conditions` inside `evaluate()` and re-implementing (or subtly redefining) a condition the Scheduler already enforces — silently forking one precondition into two implementations that can drift apart, exactly the duplication risk (b) above exists to prevent, just reintroduced one layer down. Matters most as more strategies are added, not less. See §10 D16 (resolved, decision #119) for the cleanup this made necessary — 3 of the 7 v1 strategies' own inline `is_regular_session()` calls (not 4; `orb_strategy.py`'s `minutes_since_open()` was always load-bearing MATCH logic, never a duplicate session gate) have been removed, so `gate_conditions.py`/`StrategyScheduler` are now this codebase's sole session-gate enforcement, with no remaining exception.

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
    allows_waiting: bool = False  # §8 — v1 default; every planned v1 strategy acts immediately.
                                   # A capability flag, not a timeframe-mode: the actual wait
                                   # reason/duration is decided dynamically by evaluate() itself
                                   # (§8), never a fixed "wait_for_5m" setting here.
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
    evidence: dict                        # {"conditions": {...}, "reason": "...", "basis": "live"|"closed"} — §8
    status: Literal["potential", "waiting", "actionable", "expired"] = "actionable"  # §8 —
                                           # default preserves today's stateless one-shot behavior;
                                           # "waiting"/"expired" only ever appear once a strategy
                                           # sets allows_waiting=True (§3)
    wait_reason: str | None = None        # set only when status == "waiting" — free-form, produced
                                           # by evaluate() itself, never a fixed enum (§8)
    wait_expires_at: datetime | None = None
    setup_detected_at: datetime
    confirmed_at: datetime | None = None
    decided_at: datetime | None = None    # set once Decision Engine acts on it
```

`reason` stays a human-readable string, generated from `conditions` for display — but `conditions` (the actual values MATCH checked: `relative_volume: 2.14`, `trend: "bullish"`, `vwap_position: "above"`) is what's stored and later queried by Performance Intelligence (§5) to answer "did ORB actually perform better when RVOL was above 2?" A sentence can't answer that; a structured snapshot can.

**Boundary, stated here since this is where `evidence` originates (decision #89, restated in §5/§11):** `conditions` holds the strategy's own reasoning — the specific values its MATCH stage actually checked — never arbitrary market data reached for because it happened to be convenient. Left unstated, this drifts into silently re-storing `FeatureSet` wholesale one key at a time. Anything not part of the strategy's own decision belongs in `feature_snapshots` (system-design.md §4.13), referenced by ID, not copied in here.

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

**`status`, `wait_reason`, `wait_expires_at`, and the three timestamps are new — direction-locked in decision #88, full reasoning in §8.** They exist so a strategy *can* deliberately defer a decision (ACT now vs. WAIT for better evidence vs. ABANDON as the setup decays) without forcing every strategy to. Today, every planned v1 strategy leaves `status` at its default (`"actionable"`) and the rest `None` — nothing changes in practice until a strategy actually sets `allows_waiting=True` (§3). `evidence.basis` (`"live"` or `"closed"`) records whether a condition was read from a still-forming bar or a settled one — see §8 for why that distinction matters and why it isn't a schema change so much as a documented convention on the already-open `evidence` dict.

If Trade Planning's risk-adjusted final stop would sit *inside* the strategy's own invalidation point, that's a real conflict between risk management and the trade's own thesis — worth surfacing, not silently overwritten. Trade Planning Engine's actual sizing/scaling/trailing logic (§11 of `trading-intelligence-architecture.md`) is unchanged; only its required input contract is now explicit.

---

## 5. Performance Intelligence — atomic outcomes, not a stored rank

**Central discipline (§0's second correction, made concrete):** never persist `"ORB rank = 3"` as a fact. Persist atomic outcome records instead; compute rank/vectors at query time, sliced by whatever context the asker cares about.

**Schema refined and locked (decision #89), organized around three conceptual pillars — Ledger (what happened), Evidence (what the system saw), Provenance (where/how the record was generated) — expressed as six field groups so identity and timing stay separate from those three:**

```python
class StrategyOutcome(BaseModel):
    # A. Identity & Versioning
    outcome_id: UUID
    opportunity_id: UUID
    schema_version: int                   # shape of THIS record — additive optional fields don't bump
                                           # it (system-design.md §10.2's rule, applied here); removing
                                           # a field or changing its meaning does. Distinct from
                                           # strategy_version below — never conflate the two.
    strategy_name: str
    strategy_version: str                 # §3's immutable version — never blended across versions
    symbol: str
    origin: Literal["auto", "manual"]     # mirrors trades.origin (trading-intelligence-architecture.md §18)
    is_backtest: bool                     # §7 — never blended with live in a live query
    backtest_run_id: UUID | None          # FK -> backtests, see below

    # B. Timing — all instants UTC; trading_day is the one ET-calendar concession
    trading_day: date                     # single value covers entry AND exit — day-trading only,
                                           # no overnight holds, so no entry/exit split is needed
    setup_detected_at: datetime
    signal_confirmed_at: datetime | None  # Opportunity.confirmed_at, renamed at the persistence
                                           # boundary only — Opportunity's own field name is unchanged
    decided_at: datetime | None           # Decision Engine acted
    entry_filled_at: datetime
    exit_filled_at: datetime
    holding_seconds: int                  # stored convenience, same precedent as realized_r below

    # C. Ledger
    direction: Literal["BUY", "SELL"]
    entry_price: float                    # avg/VWAP fill if more than one partial
    entry_qty: float
    exit_price: float                     # avg/VWAP fill
    exit_qty: float                       # INVARIANT: entry_qty == exit_qty for a fully closed row —
                                           # asserted at write time, not just documented (§11)
    commission_total: float | None        # None if the broker adapter doesn't surface it yet —
                                           # honest state, never estimated
    slippage_entry: float | None          # entry_price - TradePlanned.entry (trading-intelligence-
                                           # architecture.md §11's event) — separates execution quality
                                           # from strategy edge; nullable until Execution Engine exists
    realized_pnl: float                   # NET of commission_total. Stated explicitly because this is
                                           # exactly the kind of field where an undocumented meaning
                                           # change later would be a real schema_version bump, not a
                                           # footnote. gross_pnl may be added later if isolating
                                           # commission drag from strategy edge becomes useful — not v1.
    realized_r: float
    exit_reason: Literal["target", "stop", "time", "eod_flatten", "manual", "reversal"]
                                           # eod_flatten is new: the day-trading rule forces every
                                           # position closed by session end regardless of thesis —
                                           # a materially different signal from a thesis-driven "time"
                                           # exit, worth distinguishing when Performance Intelligence
                                           # later asks why a strategy underperforms

    # D. Thesis
    structural_invalidation: float
    structural_target: float
    final_stop: float                     # Trade Planning's actual number, post-refinement
    final_target: float
    confidence_at_signal: float

    # E. Evidence — interpretation, not measurement (§11); raw feature values live in
    #    feature_snapshots (system-design.md §4.13), referenced not duplicated
    evidence: dict                        # §4's structured conditions — the strategy's own reasoning,
                                           # never a dumping ground for arbitrary market data (§11)
    market_state_at_entry: dict           # trend_score, volatility_score, etc. — decision #91's
                                           # per-symbol scores, captured at entry_filled_at, not
                                           # setup_detected_at (see note below); a dict here, not a
                                           # typed model, since the dimension set is still growing
    context_at_entry: dict                # gap day?, session type, VIX regime
    market_state_at_exit: dict            # same shape as _at_entry, captured at exit_filled_at
    context_at_exit: dict
    feature_snapshot_id: UUID | None      # FK -> feature_snapshots, for full traceability back to the
                                           # exact FeatureSet without duplicating it into this row
```

**Why `_at_entry`, not `_at_signal`, and why only one snapshot per side.** Signal and entry are the same instant for every v1-planned strategy — `allows_waiting` defaults `False` everywhere (§3, §10 D2/D5). A genuine signal-vs-entry gap only exists once a real waiting-capable strategy ships. Capturing two full duplicate snapshot dicts for a distinction that doesn't bite yet would be exactly the generality §11 already argues against deferring. `evidence.conditions` (captured at signal time, inside `evidence` above) already preserves the thesis snapshot; `market_state_at_entry`/`context_at_entry` capture the moment money was actually on the line, which is the more decision-relevant instant regardless. Revisit — reintroducing a separate `_at_signal` pair — only when D5 (§10) stops being deferred. Tracked as D7 below.

**These four fields have a real capture contract now (decision #98, M4), still no table and no writer.** `app/trading_intelligence/state_snapshot.py`'s `capture_market_state_snapshot`/`capture_context_snapshot`/`capture_strategy_outcome_snapshots` read `MarketStateEngine`/`ContextEngine`'s new `get_snapshot()` accessors and shape the result to match `market_state_at_entry`/`_at_exit`/`context_at_entry`/`_at_exit` exactly. What still doesn't exist: the `strategy_outcomes` migration itself, and whatever future Execution/Position Monitor fill handler actually calls this contract at `entry_filled_at`/`exit_filled_at` — that's real, later work (§10/§12 below), not something M4 built just to exercise this function.

Persists to the `strategy_outcomes` table (renamed from `strategy_performance` — decision #89; system-design.md §4.13), while a record is still atomic, singular, and pre-migration is the cheapest possible time to fix a name that read as an aggregate. "Rank," "expectancy by regime," "win rate by time-of-day" — every one of these is a `GROUP BY` over this table, computed on demand, never a value stored on the strategy itself:

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

**Run-level metadata — `backtests` table, shape locked (decision #89).** `StrategyOutcome.backtest_run_id` needs somewhere to resolve to, or §7's own "consistency across years/symbols/regimes" and parameter-sensitivity requirements below can't actually be produced from the outcome rows alone. `system-design.md` §4.13 already reserves the table name; this is its shape. One row = one specific `(strategy_version, config_hash)` tested against one walk-forward fold — not one row per whole grid-search sweep, since `walk_forward_fold`/`is_holdout` only mean something at that granularity:

```python
class BacktestRun(BaseModel):
    run_id: UUID
    sweep_id: UUID                        # groups every run belonging to the same grid-search session —
                                           # a report pulls "all runs in this sweep," not an inline
                                           # candidate list on one bloated row
    strategy_name: str
    strategy_version: str                 # §3's immutable version being tested
    config_hash: str                      # sub-version identifier for tuning within strategy_version,
                                           # pre-promotion — never itself a promoted StrategyConfig
    symbol_universe: list[str]
    date_range_start: date
    date_range_end: date
    data_version: str                     # market-data snapshot/provider version this run read from
    feature_version: str                  # Feature Engine version this run computed indicators with —
                                           # without this and data_version, two backtests can look
                                           # identical and produce different results for reasons that
                                           # have nothing to do with the strategy being tested — a
                                           # reproducibility gap worth closing before Stage 1, not after
    walk_forward_fold: int | None
    is_holdout: bool                      # in-sample vs. out-of-sample — required, not inferred
    created_at: datetime
```

**Required output, not optional — robustness over raw expectancy.** A single in-sample optimization pass will always find a config that looks best on the exact data it was tuned against; that's not the same as a config that's actually good. The comparison report must include, alongside expectancy: trade count, consistency across years/symbols/regimes/time-of-day, and a parameter-sensitivity curve (expectancy vs. the threshold being tuned, across a range) — a smooth curve suggests a robust setting, a spiky one flags overfitting risk. "Best backtested expectancy" alone is not sufficient evidence for promotion.

**Scale check, done rather than assumed:** 5 years of 1m candles, regular session only, ≈490K rows/symbol; even 30–40 symbols stays well within current plain-Postgres monthly partitioning. Doesn't trigger the Timescale-migration question (`future-ideas.md` #7) at the symbol counts discussed — revisit only if the backtest universe grows substantially past that.

Not built now. Constrains how the first strategy gets written (pure `evaluate()`, `MarketClock`-only timing); the harness itself is real, deferred work.

---

## 8. Entry timing — ACT / WAIT / ABANDON, not bar-close confirmation

**Direction-locked (decision #88) — the *model*, not the intelligence.** What ships in v1 is "every strategy acts immediately." What's locked here is the *shape* so that a future strategy can deliberately wait, or abandon a decaying setup, without a schema rework. Two rounds of review corrected the framing before it got here — worth keeping both corrections visible, since each fixes a real mistake, not a style preference.

**Correction 1 — this was never "1m vs. 5m."** The first framing conflated waiting with bar-close specifically. The actual question a strategy needs to answer is:

```
              Evidence available right now
                        │
                        ▼
          Is it already sufficient to act?
                        │
        ┌───────────────┼───────────────────┐
        ▼                ▼                   ▼
   SUFFICIENT      NOT YET, BUT          NOT SUFFICIENT,
                   IMPROVING              AND DECAYING
        │                ▼                   │
        ▼              WAIT                  ▼
       ACT               │                ABANDON
                          ▼
                 re-evaluate on the
                 strategy's own next
                 trigger fire — §8's
                 pending-state note
                 below, not a new engine
```

A 5m candle closing is *one possible reason* evidence might improve — not the definition of waiting. Tomorrow, "wait" could mean watching participation strengthen, or a different market condition entirely, with no candle involved at all. Locking `confirmation_timeframe: Optional[str]` as the mechanism (an earlier draft of this section) would have quietly baked "waiting = bar close" into the architecture. Rejected for exactly that reason — see §3's `allows_waiting` flag instead, which asserts only *that* a strategy may wait, never *how*.

**Correction 2 — waiting has a value and a cost, and neither is free.** Waiting is an information-gathering decision, not a synonym for confirmation:

```
                        WAIT
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
    Evidence improves         Entry quality degrades
    (hypothesis strengthens,   (price drift, opportunity
     e.g. a bar closes          decay — the setup you were
     holding a level)           waiting to confirm moves
                                 away while you wait)
             │                       │
             └───────────┬───────────┘
                         ▼
              Is waiting still worth it?
              (not modeled yet — §10 D5)
```

Sometimes waiting strengthens the hypothesis. Sometimes it does nothing. Sometimes it costs entry quality faster than it adds confidence. Sometimes it outright invalidates the setup. An architecture that treats "wait for confirmation" as universally superior — waiting by default whenever the data exists — drifts toward what's worth naming and avoiding explicitly: a confirmation fetish, where every strategy waits simply because it can, not because waiting is actually worth it for that setup.

**Two principles, load-bearing, adopted close to verbatim from the review that produced them:**

> A strategy must not be required to wait for a candle close unless its hypothesis specifically depends on information only establishable at that close. The architecture must support both immediate and deliberately delayed entry, without assuming delayed confirmation is universally superior.

> Waiting is an information-gathering decision, not a synonym for confirmation. It has a value (better evidence) and a cost (entry-price drift, opportunity decay) — both eventually measurable by Performance Intelligence (§5), neither modeled today.

**What's already true, confirmed against the real code, not assumed:**
- Feature Engine never publishes a still-forming higher-timeframe bar as final — 5m/15m/1h `FeaturesUpdated` only fires once `candle_aggregator.completes_bucket()` closes that bucket (system-design.md §4.5). No risk of reading an in-progress bar's OHLC as settled.
- Nothing computed today is sub-1-minute. Market State Engine (where Participation — buyer/seller control — would live) isn't built. Every Feature Engine indicator, RVOL included, recomputes on 1m close.

**Two genuinely different structure gaps — found by reading the real schemas, not assumed to be one problem:**

```
   Closed-bar structure                    Live/forming-bar structure
   (a bar's final OHLC)                    (the currently-forming bar)
        │                                          │
        ▼                                          ▼
   SCHEMA GAP — RESOLVED, 1m only            WIRING GAP, not a data gap —
   (decision #99, ORB's opening range        still open
   was the first real consumer)
   FeatureSet (schemas/events/               PriceSnapshot already carries
   features.py) now carries                  open/high/low/close/volume for
   open/high/low/volume — but ONLY           the forming bar, field-for-field
   on the 1m FeatureSet. Aggregated          identical to CandleClosed
   5m/15m/1h FeatureSets still leave         (LiveTickRelay, decision #72) —
   these `None` — a true aggregated-         but nothing on the backend
   bucket OHLC needs the whole               subscribes to it except the
   bucket's constituent 1m candles,          frontend chart. No Strategy or
   not tracked anywhere yet.                 Feature Engine reads it.

                                              Fix (later): evaluate() gains
                                              an optional live-snapshot input,
                                              populated only when a strategy's
                                              trigger fires off a tick event —
                                              a real interface addition, not
                                              "just subscribe it"
```

Both gaps stay bounded by the existing `LiveTickRelay` cap — "observed live structure" can only ever exist for whichever ≤8 symbols are actively relayed, regardless of how far this design goes.

**The observed-live vs. confirmed-closed distinction is the same discipline this codebase already applies to data, extended to time.** §11's "honest state over fabricated state" rule already says an engine never emits a plausible-looking value for something not yet computed. A forming candle's shape is real information but not yet a settled fact — the same rule, applied to *when* a fact becomes true rather than *whether* it exists. `evidence.basis` (§4) is the field that carries this distinction once it's ever wired up: `"live"` for a condition read off `PriceSnapshot`, `"closed"` for one read off a settled `FeatureSet`. Nothing sets `"live"` today — there's no consumer of `PriceSnapshot` yet — but the field exists so a future strategy's evidence is honest about which kind of fact it acted on.

**The mechanism, reshaped from the earlier draft:** no `confirmation_timeframe` enum. Instead, `StrategyConfig.allows_waiting: bool` (§3) is a bare capability flag, default `False`. A strategy with it set to `True` may, inside its own `evaluate()`, return an `Opportunity` with `status="waiting"` and a free-form `wait_reason` instead of `None` or an actionable `Opportunity` — the *reason* for waiting is whatever that strategy's own evidence-sufficiency judgment produces at that moment, never a fixed per-version setting. **Where the pending state lives, since a stateless `evaluate()` has nowhere to keep "still waiting" between one trigger fire and the next:** each `Strategy` instance holds its own small pending set internally, re-checked on its own next trigger fire — not a new shared "Opportunity Tracker" engine. This keeps every strategy independently testable, costs nothing until a strategy actually sets `allows_waiting=True`, and composes cleanly with §7's identical-live/backtest constraint as long as transitions are driven by candle/event timestamps, never wall-clock.

**Deliberately not this document's job to decide whether Decision Engine and Governor should merge.** Waiting happens *before* an Opportunity is even actionable — it never reaches Decision Engine while `status="waiting"`. Decision Engine still only ever arbitrates finalized opportunities; Governor still only ever derates one already-planned trade. This section adds a stage upstream of both, not an argument for merging them — §10 D1 stays exactly as open as it already was.

**Explicitly deferred — real work, not built now:**
- The waiting-value model itself (is this specific wait worth its cost) — §10 D5.
- Wiring any consumer to `PriceSnapshot` at all — §10 D6.
- Reusable candle-shape helper functions (wick ratio, body ratio, position-in-range) — OHLC exists on the 1m `FeatureSet` now (decision #99), but no strategy has needed these specific helpers yet; still not written.
- A candle-pattern library, an automatic confirmation selector, or any dedicated "Timing Engine"/"Confirmation Engine" — premature architecture until a real strategy needs more than the flag above. Same "defer generality until a concrete gap appears" discipline this codebase already applies everywhere else (Redis, Replay, uncertainty propagation, the playbook-as-data rejection in §1).

**This becomes a backtestable question, same as every other tunable in this design.** "ORB v4, `allows_waiting=False`" vs. "ORB v5, `allows_waiting=True`" are two versions (§3); Performance Intelligence (§5) can eventually report not just expectancy per version but entry-quality degradation alongside it — waiting that improves expectancy by degrading median entry price enough to not be worth it is exactly the kind of trade-off §7's backtest report is required to surface, not hide behind a single expectancy number.

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
| D1 | Merge Decision Engine and Governor into one component, or keep as two | **Open.** Saqib has raised this as a real possibility. §6 states both responsibilities regardless of eventual component boundary; §8 confirms timing doesn't add a new argument either way. Doesn't block Strategy Engine work. |
| D2 | Entry timing mechanism (§8) | **Resolved (decision #88):** ACT/WAIT/ABANDON model locked, `confirmation_timeframe` rejected in favor of a bare `allows_waiting` capability flag (§3) plus dynamic, strategy-produced wait reasons (§4/§8). The *model* is locked; the *intelligence* (D5 below) is not. |
| D3 | When automatic (vs. human-reviewed) reweighting/retirement graduates from future work to real (§5) | **Open, deferred.** Trigger: enough closed trades per `StrategyConfig` version for a reweight to not be noise — no specific count set yet. |
| D4 | Exact "Candidate Selection Score" formula (Performance × Context Fit × Confidence × Robustness) | **Deliberately not decided.** Agreed directly (Saqib + Claude + the consulted ChatGPT review) not to lock a scoring formula before real outcome data exists to check it against. |
| D5 | The waiting-value model itself — how a strategy actually decides "is waiting worth it" (§8) | **Open, deferred.** No strategy needs this yet (`allows_waiting` defaults `False` everywhere in v1); build when a real strategy wants to wait, not speculatively. |
| D6 | Wiring any consumer to `PriceSnapshot` for live/forming-bar structure (§8) | **Open, deferred.** The data already exists (`LiveTickRelay`, decision #72); no Strategy or Feature Engine module reads it. Needs its own design pass — `evaluate()`'s signature would need to change — not assumed as a side effect of anything above. |
| D7 | Whether `StrategyOutcome` needs a separate `market_state_at_signal`/`context_at_signal` pair, distinct from `_at_entry` (§5) | **Open, deferred, tied to D5.** Signal and entry are the same instant while `allows_waiting` defaults `False` everywhere — no current strategy makes them diverge. Revisit only once D5 stops being deferred and a real waiting-capable strategy exists. |
| D8 | Whether `StrategyOutcome.trading_day` (§5, decision #89) stays a single `date` field once swing/overnight holding exists | **Open, flagged not resolved.** The single-value simplification was explicitly justified by "day-trading only, no overnight holds" — Saqib has since clarified the platform is day-trading-*focused* but not day-trading-*limited*. Nothing needs to change today; every real trade is still intraday. The trigger is concrete: the first time a position is intentionally held overnight, `trading_day` needs to split into `entry_trading_day`/`exit_trading_day`, and `exit_reason`'s `eod_flatten` value stops being universal (a forced-by-rule exit only for trades actually subject to the day-trading rule). Caught here so it isn't rediscovered as a bug later. |
| D9 | How a `Strategy` reads `LevelInteractionEngine` state (§16, decisions #107/#108) | **Resolved for v1, narrowly.** First Pullback/Reversal call `get_level_interaction_engine().get_snapshot(symbol)` directly inside `evaluate()` — same free-function-singleton precedent `orb_strategy.py` already uses for `get_market_clock()` — rather than changing `evaluate()`'s signature or wiring `on_event(LevelInteractionChanged)` through a Scheduler that doesn't exist yet (§13 item 4 / `base_strategy.py`'s own "NOT BUILT HERE" note). `seconds_in_zone` deliberately never read — it's wall-clock `datetime.now()`-derived (`level_interaction_engine.py`'s `get_snapshot()`, decision #47, built for the UI panel), which would violate §7's backtest-safety invariant. Staleness (a design review finding, decision #108): `get_snapshot()` is fed by an async queue/worker, with no built-in guarantee it reflects the candle a strategy is currently evaluating — resolved by exposing `last_applied_candle_ts` per entry, checked before either strategy calls into the resolution-reconstruction logic. **Still open:** a future strategy that genuinely needs the authoritative `status`/`observed_via` fields (gap-through vs. dwell) rather than a one-candle-late zone-transition inference would need real event wiring — not decided here, no strategy has hit that need yet. |
| D10 | Sequencing and scope for Stage 2 — the Strategy Scheduler/wiring milestone (decision #112, renumbered from a collision at #111 — see §13 item 9) | **Resolved and now COMPLETE, both tracks.** Two calls made directly by Saqib during a status review, prompted by "this thread's strategies are built — what's next": (1) **wait for Momentum/VWAP to land** before starting Stage 2, rather than building the Scheduler against 5 strategies and retrofitting the other 2 in later; (2) **Stage 2's scope is wiring only** — instantiate built strategies against their `StrategyConfig`s, read each one's `ScheduleTrigger` and subscribe it to the live event bus, call `evaluate()` with real `MarketStateEngine`/`ContextEngine` snapshots (M4, decision #98) plus the triggering `FeatureSet`, and publish the result as a new `OpportunityCreated` event with a `get_snapshot()` read-side — same shape as every other engine's read pattern (decision #47). Two adjacent pieces explicitly did NOT get pulled into this scope: **declarative `gate_conditions` enforcement (§2b)** — every v1 strategy still does its own inline GATE check, so `StrategyConfig.gate_conditions` (e.g. ORB's `{"session": "regular"}`) stays set-but-unenforced by any shared code for now — and **Opportunity Engine's cross-strategy ranking (§9)** — a separate, later consumer of whatever `OpportunityCreated` starts publishing, not part of getting strategies running live in the first place. **Track A (the write side — `scheduler.py`) landed as decision #114.** **Track B (the read side — `opportunity_cache.py`) built in a parallel session, ALSO logged as that session's own #114 — a genuine numbering collision, not caught until this entry** (decision #115): Track B's own decision-log entries were lost when Track A's delivery was applied over them, and the `main.py`/`conftest.py` wiring Track A's own #114 explicitly flagged as a manual-merge point was never actually completed — `OpportunityCache` existed, fully tested in isolation, but was never subscribed in the live app. Both fixed in decision #115: Track B's record restored (renumbered, collision noted, same pattern as #98/#99 and #111/#112), the wiring gap closed and verified by running (not just reading) — a real publish through the actual app lifespan confirmed empty on the unfixed push, populated after the fix. |
| D11 | `ESTABLISHED_TREND_SCORE_THRESHOLD` (`scoring_utils.py`) is one authoritative constant shared by `reversal_strategy.py` (fires when established) and `vwap_strategy.py` (fires when NOT) so the two partition every `trend_score` reading with no gap and no overlap (§18, decision #113) | **Open.** The shared constant only guarantees the two strategies' DEFAULTS match — `StrategyConfig.params` stays independently overridable per strategy, per its own versioning (§3), and nothing structurally stops Reversal's or VWAP's `trend_score_threshold` from being retuned to different values in a later config version, silently reopening either a gap or an overlap between them. No cross-strategy-config validation mechanism exists anywhere else in this codebase either — not decided here whether one is worth building, or whether "don't retune one without the other" stays a documentation-only discipline until a concrete incident makes the case for more. |
| D12 | Which live event should trigger `StrategyScheduler.evaluate()` calls — `FeaturesUpdated` (the original plan, per D10) or `MarketStateChanged` (decision #114) | **Resolved, found by testing rather than planned.** The first version of `scheduler.py` subscribed to `FeaturesUpdated` directly, on the reasoning that "every_candle" means "react to the candle-close event." A real-`EventBus`/real-`MarketStateEngine` integration test caught this failing every time: `MarketStateEngine._on_features_updated` only enqueues a debounced recompute (`core/debounce_scheduler.py`) — the actual compute + `asyncio.to_thread` persist + cache happens later, in a separate `_worker_loop` task, with no ordering guarantee relative to any OTHER subscriber of that same `FeaturesUpdated` event. Fixed by subscribing to `MarketStateChanged` instead — `_worker_loop`'s own inline comment already documents the guarantee this relies on ("Cache before publish (decision #98) — a subscriber reacting to the event that's about to go out can immediately call get_snapshot() and see this exact state"), just attached to the wrong event in the original design. Reading `market_state` straight off `MarketStateChanged`'s own payload (no `get_snapshot()` call needed for it at all) turned out simpler than the original plan, too. Second-order finding along the way: `FeatureEngine.get_snapshot()` (decision #47) can't be used to reconstruct the `FeatureSet` `evaluate()` needs, because its shape predates decision #99's `open`/`high`/`low`/`volume` fields and was never extended to carry them — doing so would have silently hidden real OHLC from every strategy exactly the way decision #99's own schema comment warns against. Resolved by caching the real `FeaturesUpdated` payload directly (keyed `(symbol, timeframe)`) rather than reconstructing it from anywhere. See `scheduler.py`'s module docstring for the full reasoning, and `test_strategy_scheduler.py`'s real-engine integration test, kept explicitly as a regression test for this exact failure mode. |
| D13 | Where `Opportunity` (currently defined on `strategy_engine/base_strategy.py`) should live once it's published as `OpportunityCreated`'s payload — import it directly, or mirror/move it into `schemas/events/opportunity.py` to match the convention `MarketState`/`ContextChanged`/`FeatureSet` already follow (§4). **Originally logged as D12 in a parallel session (Track B) — renumbered here after colliding with the D12 above, same decision #115 collision as D10's own note describes.** | **Resolved: import directly, do not move.** Decided by Saqib directly. `MarketState`/`ContextChanged`/`FeatureSet` earn their `schemas/events/` separation because they're genuinely imported across module boundaries — checked directly: all three are pulled in by their producing engine AND every strategy file, and `FeatureSet` additionally by `scanner/runner.py`/`scanner/scorer.py`. `Opportunity` has no such cross-module consumer today — only `base_strategy.py` (defines it) and the 7 strategy files (construct it), both inside `strategy_engine/`; `OpportunityCache` itself does not import the class (it trusts the raw `envelope.payload` dict — see D10's note on Track B). `scheduler.py`'s own delivery independently confirms this default in practice: it imports `Opportunity` only via `Strategy`/`base_strategy.py`, never from a mirrored location. No code change required by this resolution either way. Revisit if/when a real cross-module consumer (the Opportunity Engine, §9) actually needs to import the class directly rather than trust an envelope dict — not preemptively. |
| D14 | Whether `StrategyScheduler` enforces `StrategyConfig.active_from`/`active_to` before calling `evaluate()` | **Resolved — decision #116. Activation-window enforcement is OUT of scope for Stage 2.** `StrategyScheduler` MUST NOT compare `features.candle_ts` (or any other timestamp) against `active_from`/`active_to` in Stage 2 — every registered strategy is called on every matching trigger regardless of its config's activation window, same treatment `gate_conditions` already got in D10. Stage 2's job is scheduling/evaluation/publication, not enforcing a strategy's complete temporal eligibility model. **The two instructions that looked contradictory are reconciled, not in tension:** (1) "out of scope for now, same as gate_conditions" (confirmed directly, Track A's own thread) is the authoritative architectural decision for what Stage 2 builds. (2) "use event/market timestamps, never wall-clock `now`" (Track B's own thread) is a constraint on HOW enforcement must be implemented **if and when** it's ever brought into scope in a future stage — it was never permission to build it now, and `features.candle_ts` remaining available on every `evaluate()` call does not by itself imply the Scheduler must currently act on it for this purpose. A future session must not reopen this item merely because it encounters `active_from`, `active_to`, or `candle_ts` in the code — those fields existing is expected and intentional; using them for activation-window enforcement is what's deferred. If a future stage does bring enforcement into scope, the correct comparison is `active_from <= ts` and (`active_to is None or ts < active_to`) against `features.candle_ts` (or `market_state`'s own timestamp) — never `datetime.now()`, per §7's existing invariant — but that implementation doesn't exist yet and this entry is not authorization to add it. `scheduler.py` is unchanged by this resolution — it was already built consistent with "out of scope" (D10/decision #114), so no code follows from closing this item, only the documentation catching up to confirm that was the right call. |
| D15 | Whether `StrategyScheduler` enforces `StrategyConfig.gate_conditions` (§2b) before calling `evaluate()` — the other item D10 deferred alongside D14/`active_from`/`active_to` | **Resolved — decision #117. Enforced, closing this item on its own terms (`active_from`/`active_to` stays separately closed by D14 — not reopened here).** New `app/strategy_engine/gate_conditions.py` — a small key/value registry plus one pure check function — validated once per strategy at `StrategyScheduler.__init__` (raises `ValueError` for anything unrecognized, a deliberately harder failure than D12's own `after_time`/`on_event` "registered but unreachable" precedent) and evaluated once per strategy per candle, immediately before `evaluate()`, using `market_state.candle_ts` (never wall-clock, same §7 invariant D14 already enforces). v1 supports exactly `{"session": "regular"}` — confirmed by grep to be the only condition any of the 7 real `StrategyConfig`s declares; §3's own illustrative `vix_min` example confirmed, also by grep, to correspond to no real field anywhere in this codebase, so no support was built for it. Found, not assumed: 3 of the 7 strategies (First Pullback, Reversal, VWAP) had declared this exact precondition with **zero enforcement anywhere** before this change — the other 4 already self-gate on session inline and are now redundant with the central check, left untouched (removing an inline check is separate, later work). Full reasoning, including the timestamp-source and fail-loud calls: decision #117. |
| D16 | Remove the v1 strategies' now-redundant inline session GATE checks (`gap_strategy.py`, `momentum_strategy.py`, `volume_spike_strategy.py`'s explicit `MarketClock.is_regular_session()` calls), now that decision #118 makes `StrategyScheduler`/`gate_conditions.py` the sole enforcement authority | **Resolved — decision #119, a standalone cleanup pass per decision #118's own trigger condition (Saqib's direct call, not a deferral).** **Correction to this row's original framing:** only 3 strategies had a removable inline check, not 4 — `gap_strategy.py`, `momentum_strategy.py`, `volume_spike_strategy.py`. `orb_strategy.py` has no `is_regular_session()` call anywhere; its `minutes_since_open()` usage is load-bearing MATCH logic (opening-range formation timing), not a duplicate session gate, and `orb_strategy.py` was correctly left completely untouched, code-wise. The two-line inline check removed from each of the 3 files; `clock = get_market_clock()` stays live in all three (reused for `trading_day()`, and in Gap's case `minutes_since_open()` inside `match_direction()`). Each file's `default_config()` `gate_conditions` comment and module docstring's "Session scope" section updated to point to central `StrategyScheduler`/`gate_conditions.py` enforcement instead of claiming self-enforcement (Gap/Volume Spike had a stale `default_config()` comment claiming self-enforcement; Momentum did not have one to begin with — verified per-file, not assumed uniform). The 3 corresponding `test_outside_regular_session_never_fires` tests removed (not rewritten) — the behavior they asserted no longer exists at the strategy layer by design, and rewriting them to call `evaluate()` directly would re-legitimize the bypass path #118 closed; existing coverage in `test_gate_conditions.py` (10 tests) and `test_strategy_scheduler.py` (incl. a real-engine end-to-end case) already proves the real contract — an out-of-session candle never reaches a strategy's `evaluate()` at all. MATCH/SCORE/PROPOSE logic confirmed byte-for-byte unchanged in all three files via diff against an untouched clone. Full details: decision #119. |

---

## 11. Guiding constraints carried into this design (standing project principles, not new rules)

- **Honest state over fabricated state** — an outcome record with no data for a field stays `None`/absent; Performance Intelligence never estimates a plausible-looking number for something not yet measured. Extends to time, not just data (§8): a forming candle's shape is real but not yet settled, and `evidence.basis` exists so a strategy is never ambiguous about which kind of fact it acted on.
- **Compute once, consume everywhere** — one `StrategyOutcome` schema serves live performance queries and backtest reports alike, distinguished only by `is_backtest`, never duplicated per consumer.
- **Evidence stores interpretation, not measurement (decision #89)** — `evidence` holds the strategy's own reasoning (`conditions`, `reason`, `basis`), never raw indicator values wholesale. Feature Engine measures; Strategy interprets; `StrategyOutcome` records what was observed and what happened; Performance Intelligence aggregates — the same layering already applied to Feature Engine vs. Market State/Context Engine (system-design.md §4.5), carried one stage further downstream. `feature_snapshot_id` is the escape hatch for full traceability without violating it.
- **Invariants enforced at write time, not just documented (decision #89)** — `entry_qty == exit_qty` for a fully closed `StrategyOutcome` row is asserted before the row is written, same "assertion-guarded, not just narrated" discipline already used for multi-site structural edits elsewhere in this project.
- **Real Postgres, not mocks**, once `strategy_outcomes` (renamed from `strategy_performance` — decision #89) has real rows to query against — same standard as every other module in this codebase.
- **Docs updated in the same change as code** — once Strategy Engine code exists, this document and `confirmed-decisions.md` update alongside it, not after.
- **Architecture questions surfaced before code** — §10's open items get resolved (or explicitly deferred with a trigger condition) before the corresponding code is written, not silently decided mid-implementation.
- **Defer generality until a concrete gap appears** — no dedicated "Timing Engine" or "Confirmation Engine" (§8), no generic rule engine for MATCH (§1), until a real strategy's needs outgrow the flag/dict-based approach already in place. Same reasoning kept `_at_signal`/`_at_entry` as one snapshot pair, not two, in §5.

---

## 12. Staged plan

- [x] **Stage 0 — Lock the direction in writing (no application code).** This document + `confirmed-decisions.md` #87, refined by #88 (§8's ACT/WAIT/ABANDON model), refined again by #89 (§5's `StrategyOutcome`/`backtests` schema: field groups, `strategy_outcomes` rename, `eod_flatten`, `slippage_entry`, write-time invariants).
- [x] **Stage 1 — ORB built (decision #99).** `base_strategy.py` (`Strategy`/`StrategyConfig`/`Opportunity`/`ScheduleTrigger`) and `orb_strategy.py` — the first concrete strategy. An earlier, undocumented attempt at this stage (`momentum_strategy.py`/`vwap_strategy.py`, built by a concurrent session against a `base_strategy.py` that didn't exist yet, decision-log entry lost to a numbering collision with #98) was found orphaned and discarded rather than built on top of — see decision #99 for the full account. Momentum and VWAP are being rebuilt fresh, assigned to a separate session, against this now-real interface.
- [x] **Stage 1 (continued) — Gap and Volume Spike built (decisions #104, #105).** `gap_strategy.py` and `volume_spike_strategy.py`, the second and third concrete strategies against the real interface — see §15 below for both.
- [x] **Stage 1 (continued) — First Pullback and Reversal built (decisions #107-#110).** Design-reviewed before code (decision #108: gap-through/cold-start handling, `get_snapshot()`'s new `last_applied_candle_ts` staleness field, MATCH/SCORE boundary tightened). `first_pullback_strategy.py`, `reversal_strategy.py`, and shared `level_touch_tracking.py` — see §16 for the full walkthrough. All 7 v1 strategies from trading-intelligence-architecture.md §8 are now either built (ORB, Gap, Volume Spike, First Pullback, Reversal) or assigned (Momentum, VWAP — a separate session's thread).
- [x] **Stage 1 (continued) — Gap/Volume Spike design review's six changes made, `scoring_utils.py` extracted (decision #111).** `regular_open` moved to Feature Engine, Gap bounded to `max_minutes_since_open`, Volume Spike gained `min_absolute_volume`/`min_body_ratio`, shared `clamp`/`trend_magnitude`/`validate_mirror_threshold` adopted by all 5 built strategies, `Opportunity.expected_horizon_minutes` added.
- [x] **Stage 1 complete — Momentum and VWAP built (decision #113).** `momentum_strategy.py`, `vwap_strategy.py` — the sixth and seventh, and last, v1 strategies. Externally reviewed before code (same practice as #107/#108); `ESTABLISHED_TREND_SCORE_THRESHOLD` extracted to `scoring_utils.py`, `reversal_strategy.py` refactored onto it. See §18 for the full walkthrough. **All 7 v1 strategies from `trading-intelligence-architecture.md` §8 are now built.**
- [x] **Stage 2 — fully built and verified, both tracks (decisions #114/#115/#116).** **Track A — Strategy Scheduler:** `scheduler.py` instantiates all 7 built strategies, subscribes `FeaturesUpdated` (caches the real payload — see decision #114 for why) and `MarketStateChanged` (the actual `evaluate()` trigger — also decision #114, a real correction found by testing, not the original plan), publishes `OpportunityCreated`. **Track B — OpportunityCreated read-side** (cache + `GET /intelligence/opportunities`), built in a parallel session; collided with Track A on decision number #114 (lost from the log when Track A's delivery was applied) and its own `main.py`/`conftest.py` wiring was flagged but never finished — both restored/completed in decision #115, verified end-to-end by running the real app lifespan (a directly-published `OpportunityCreated` correctly reaches `GET /intelligence/opportunities`). Full suite with a real local Postgres: 577 passed, 1 failed (`test_vwap_publishes_even_while_sma_is_still_warming_up`, pre-existing, unrelated). Opportunity Engine's ranking (§9) remains deliberately OUT of Stage 2's scope. `active_from`/`active_to` enforcement — also OUT of scope, canonically closed, not merely deferred pending clarification — see decision #116/D14. Declarative `gate_conditions` enforcement (§2b), originally deferred alongside `active_from`/`active_to`, was closed SEPARATELY and is now built — see decision #117/D15.
- [x] **Stage 2 (continued) — `gate_conditions` enforcement built (decision #117/D15).** `app/strategy_engine/gate_conditions.py` (new) — registry/validation/check for §2b's declarative preconditions, wired into `scheduler.py` centrally, before `evaluate()`. v1 supports exactly `{"session": "regular"}`, the only condition any real `StrategyConfig` declares. Closed a real, live gap for 3 of 7 strategies (First Pullback/Reversal/VWAP had no session enforcement anywhere before this). 20 new tests, real local Postgres, zero regressions.

---

## 13. How to resume this in a new session

1. Read this file in full, then `confirmed-decisions.md`'s most recent entries — check whether §10's open items have moved before re-deciding them.
2. `strategy_engine/base_strategy.py` now exists (decision #99) — any new strategy should import the real `Strategy`/`StrategyConfig`/`Opportunity`/`ScheduleTrigger` from it, not re-guess the interface the way the discarded momentum/vwap attempt had to.
3. §8's ACT/WAIT/ABANDON *model* is locked (decision #88) — don't re-litigate whether `confirmation_timeframe` should come back. What's still genuinely open there is D5 (the waiting-value model itself) and D6 (wiring a consumer to `PriceSnapshot`) — build either only when a real strategy needs it, not speculatively.
4. Before building on Stage 1, know what M4 (decision #98) already prepared: `MarketStateEngine.get_snapshot()`, `ContextEngine.get_snapshot()`, and `app/trading_intelligence/state_snapshot.py`'s three capture functions all exist and are tested (`backend/tests/test_strategy_integration_contract.py`) — a strategy should call these, not re-derive its own read path against either engine. Also worth knowing: `ContextChanged` has no domain-safe timestamp (§4's providers are timer-triggered, not candle-triggered) — decision #98 left this open rather than inventing one; don't assume it got solved.
5. `FeatureSet` now carries `open`/`high`/`low`/`volume` (decision #99) — but ONLY on the 1m `FeatureSet`; a 5m/15m/1h `FeatureSet`'s open/high/low/volume are `None` (true aggregated-bucket OHLC isn't tracked anywhere yet — see `schemas/events/features.py`'s `FeatureSet` docstring for why passing through the last constituent 1m candle's OHLC would be dishonest, not just incomplete). A strategy reading these on anything other than a 1m `FeatureSet` needs to handle `None`, not assume they're populated.
6. `Strategy.evaluate()`'s real signature takes `symbol: str` as its first argument (decision #99) — the illustrative 3-arg sketch (system-design.md §4.8) had no way for a strategy to know which symbol it's being asked about, which only mattered once a strategy needed its own per-symbol memory (ORB's opening range does; the discarded Momentum draft's stateless MATCH logic never hit this gap). Any new strategy's `evaluate()` must match the real 4-arg signature.
7. First Pullback and Reversal (§16, decisions #107-#110) are now built — `first_pullback_strategy.py`/`reversal_strategy.py` call `get_level_interaction_engine().get_snapshot(symbol)` directly (D9), never read its `seconds_in_zone` field (wall-clock, not `candle_ts`-derived), and re-derive rejected-vs-conquered one candle late from each strategy's own private per-symbol state rather than assuming `LevelInteractionChanged`'s `status` field is reachable — no Scheduler wires `on_event(...)` triggers to anything yet. *(Corrected — this point previously said "design-locked but unbuilt," stale as of decision #109/#110; caught during a status review, decision #112.)*
8. **All 7 v1 strategies are now built** (ORB, Gap, Volume Spike, First Pullback, Reversal, Momentum, VWAP — decision #113 closed out the last two). **Stage 2 (the Scheduler/wiring milestone, decision #112/D10) is now unblocked** and is this thread's next work — nothing further needs to land in Strategy Engine itself first.
9. A numbering collision happened at #111: a docs-only decision drafted in an earlier session (Stage 2 sequencing/scope, D10) was queued but never actually applied to git before Saqib separately committed different, unrelated work (the Gap/Volume Spike design-review changes) as #111. Same category of collision decision #99 already documents happening once before with #98 — resolved the same way: the queued content wasn't discarded, just renumbered and re-applied as #112, with this note as the record of what happened. If a THIRD such collision is ever found, it's worth asking whether the decision-log's queue/apply handoff between sessions needs an actual fix rather than another one-off renumbering.
10. Momentum and VWAP (§18, decisions #113) are now built — `momentum_strategy.py`/`vwap_strategy.py`. `reversal_strategy.py` was touched too (refactored onto `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD`, no behavior change). A local Postgres was actually provisioned for this session's own verification (`apt-get install postgresql`, `alembic upgrade head`) rather than relying on DB-free tests alone — worth doing again for any future session touching `LevelInteractionEngine`-dependent strategies, since the two most logically intricate VWAP tests (the same-zone-repeat suppression, the day-rollover reset) would otherwise only be trace-verified by hand.

---

## 14. ORB — Stage 1's first strategy, and what actually happened getting there (decision #99)

**What was found before any of this was built:** `momentum_strategy.py` and `vwap_strategy.py` already existed in the repo, from an earlier concurrent session — undocumented. Both imported a `base_strategy.py` that didn't exist anywhere. Their own docstrings admitted this and flagged three unverified interface assumptions. A `TESTING.md` at the repo root claimed decision #98 for that work — but the real decision #98 in `confirmed-decisions.md` is the M4 integration milestone, a different piece of work entirely. Two concurrent sessions minted the same decision number; only the M4 session's doc updates actually landed. The momentum/vwap code was orphaned, silently, with no working `pytest` collection possible. Discarded rather than built on top of — see the session's own discussion for the full reasoning. *(Historical note, as of decision #99: Momentum was assigned to be rebuilt fresh, by a separate session, against the now-real interface below. Both it and VWAP are since built — decision #113, §18 — against this same interface, discarded draft not consulted.)*

**GATE/MATCH/SCORE/PROPOSE, applied to ORB's actual state machine** (not the discarded sketch's time-boxed trigger — see `orb_strategy.py`'s module docstring, correction #1):

```
 every 1m candle, all session long (trigger = every_candle("1m"))
      │
      ▼
 ┌─────────────────────────┐   minutes_since_open      ┌──────────────────────────┐
 │  FORMING                │ ─────< or_minutes ───────▶│  accumulate this         │
 │  (0 candles..or_minutes)│                            │  candle's high/low into  │
 └─────────────────────────┘                            │  the running OR;         │
      │                                                  │  return None             │
      │ minutes_since_open >= or_minutes                └──────────────────────────┘
      ▼
 ┌─────────────────────────┐   candles_seen < or_minutes
 │  freeze the range        │──────────────────────────▶ return None, permanently,
 │  (or_formed = True)      │   (gap / late start)        for the rest of this day
 └─────────────────────────┘   — honest absence,          (module docstring's
      │                          not a fabricated range    accepted limitation)
      │ candles_seen == or_minutes
      ▼
 ┌─────────────────────────┐
 │  GATE passed — MATCH:    │   close > or_high & trend/volume confirm  ──▶ BUY
 │  breakout test against   │
 │  the frozen or_high/low  │   close < or_low  & trend/volume confirm  ──▶ SELL
 └─────────────────────────┘
      │
      │ direction not in fired_directions (else: already proposed today, return None)
      ▼
 SCORE (trend + volume + breakout-strength blend) → PROPOSE an Opportunity,
 mark direction as fired for today. Reset entirely at the next trading_day
 (MarketClock-derived, never wall-clock).
```

**State is symbol-keyed inside the strategy instance, never published.** One `ORBStrategy` instance serves every symbol (same singleton-with-internal-keying shape `FeatureEngine`/`MarketStateEngine`/`LevelInteractionEngine` already use) — `evaluate()` takes `symbol: str` explicitly so that keying is possible (base_strategy.py's own docstring, assumption #4). The opening range itself is Saqib's explicit call to keep private rather than publish to `FeatureSet` — accepted trade-off: no persistence, no restart-survival mid-day (see `orb_strategy.py`'s module docstring for the full accounting of that limitation).

**Verification:** `backend/tests/test_base_strategy.py` (9 tests) + `backend/tests/test_orb_strategy.py` (18 tests) — pure GATE/MATCH/SCORE math plus a full multi-day, multi-symbol `evaluate()` simulation (formation → freeze → breakout → fire-once → reversal → day rollover → independent per-symbol state → honest absence on missing OHLC or a missed formation window). Full existing suite re-run against this change: identical pass/fail signature to the pre-change baseline (40 pre-existing DB-connectivity failures in a sandbox with no local Postgres, 91 skipped, both unchanged) plus these 27 new tests passing — zero regressions from the `FeatureSet`/`feature_engine/engine.py` OHLC threading.

**A second reconciliation, from an independent set of concurrent-session findings, folded in before this was finalized.** A separate session (assigned Momentum) reviewed the discarded `momentum_strategy.py`/`vwap_strategy.py` in place rather than rebuilding fresh, and found two real bugs plus one Feature Engine documentation gap — see decision #99's full account for all three, and `match_direction()`'s own docstring in `orb_strategy.py` for the one applied directly here (a threshold-mirroring guard). The `MarketStateEngine._latest_features` per-symbol timeframe race those reviews found remains open, flagged for Saqib, not fixed by this build.

---

## 15. Gap and Volume Spike — the second and third strategies after ORB (decisions #104, #105)

Both built against the real `base_strategy.py`, after decision #103 closed the `MarketStateEngine` per-symbol timeframe race §14 flagged — neither has the shared-slot exposure ORB/Momentum/VWAP each had to reason about. Full reasoning for every design choice below lives in each module's own docstring (`gap_strategy.py`, `volume_spike_strategy.py`) and decisions #104/#105 — this section is a summary and a diagram, not a duplicate of either.

**Gap — is today's opening gap holding (continuation), or already given back (reversal)?**

```
 every 1m candle, regular session only (trigger = every_candle("1m"))
      │
      ▼
 gap_pct / gap_dollars / pdc present in features.features?
      │ no ──▶ return None (no gap yet, or no prior trading day — honest absence)
      │ yes
      ▼
 already fired today for this symbol?
      │ yes ──▶ return None (gap_pct's sign is frozen for the day — only one
      │          possible direction, unlike ORB's two-sided range)
      │ no
      ▼
 regular_open = pdc + gap_dollars        (reconstructed, not separately tracked)
      │
      ▼
 MATCH: |gap_pct| >= min_gap_pct  &  volume_regime_score confirms  &
        gap_pct > 0 → close > regular_open & trend_score confirms   ──▶ BUY
        gap_pct < 0 → close < regular_open & trend_score confirms   ──▶ SELL
      │
      ▼
 SCORE (trend + volume + gap-size blend) → PROPOSE an Opportunity,
 invalidation = regular_open (the same level MATCH just tested against),
 mark fired for today. Reset entirely at the next trading_day.
```

**Volume Spike — did this candle print unusually heavy volume, and which way did the market move on it?**

```
 every 1m candle, regular session only (trigger = every_candle("1m"))
      │
      ▼
 open/high/low/volume all present? ── no ──▶ return None (pre-#99 shape,
      │ yes                                   or an aggregated FeatureSet)
      ▼
 rolling baseline has lookback_bars samples yet?
      │ no ──▶ push this candle's volume, return None (honest warm-up,
      │         same discipline as ORB's candles_seen < or_minutes)
      │ yes
      ▼
 baseline = mean(prior lookback_bars volumes)   (this candle NOT included)
 volume_ratio = this candle's volume / baseline
 push this candle into the window for the NEXT candle's baseline
      │
      ▼
 within cooldown_minutes of the last fire for this symbol?
      │ yes ──▶ return None (floor against re-firing the same still-elevated move)
      │ no
      ▼
 MATCH: volume_ratio >= spike_ratio_threshold  &  volume_regime_score confirms  &
        close > open → trend_score confirms   ──▶ BUY
        close < open → trend_score confirms   ──▶ SELL
      │
      ▼
 SCORE (trend + volume + spike-excess blend) → PROPOSE an Opportunity,
 invalidation = this candle's own low/high (the same bar MATCH just tested),
 record last_fired_ts. Reset the whole baseline + cooldown at the next trading_day.
```

**Three design choices worth naming, since each deliberately diverges from ORB's own precedent rather than copying it wholesale:**

1. **Gap's `_GapState.fired` is a plain `bool`, not ORB's `fired_directions: set`.** A gap's direction is fixed by `gap_pct`'s own sign for the whole day (`_update_gap`, decisions #67/#68) — there is never a second, opposite direction for a reversal to test against the way ORB's fixed range allows both sides.
2. **Volume Spike uses a per-symbol cooldown timer, not a fired-once-per-day flag.** Unlike Gap (one fixed daily event) or ORB (one fixed daily range), a volume spike is a recurring pattern — a busy session can produce several genuine, independent spikes hours apart, so a once-per-day cap would discard real signal. The cooldown only floors against re-firing the immediate next candle or two of the *same* still-elevated move.
3. **Volume Spike needed genuinely new per-symbol state (a rolling volume baseline) that no other engine provides — the same "strategy-private state" precedent ORB's own opening range already established, not new territory.** `rvol` (Feature Engine, decision #71) is a day-level, time-of-day-normalized proxy; Market State's `volume_regime_score` already interprets it. Neither answers "was THIS candle anomalous relative to this symbol's own last N bars" — a narrower, single-candle claim nothing else in the codebase computes.

**Verification:** `backend/tests/test_gap_strategy.py` (15 tests) + `backend/tests/test_volume_spike_strategy.py` (18 tests) — pure GATE/MATCH/SCORE math plus end-to-end multi-day, multi-symbol `evaluate()` simulations for each (session gating, day rollover, independent per-symbol state, honest absence on missing data). Full existing suite re-run against this change: identical pass/fail signature to the pre-change baseline (40 pre-existing DB-connectivity failures, 91 skipped, both unchanged) plus these 33 new tests passing — zero regressions.

---

## 16. First Pullback and Reversal — design, review, and build (decisions #107, #108, #109, #110)

The last two strategies from trading-intelligence-architecture.md §8's planned v1 set (ORB, Gap, Volume Spike built; Momentum still assigned elsewhere). **Built** — `first_pullback_strategy.py`/`reversal_strategy.py`, plus a small shared `level_touch_tracking.py` module both depend on. The design below (decision #107) went through an external design review before any code was written, per Saqib's own "short design note first, then code" instruction; the review's findings and this file's response to them are decision #108, folded into the sections below rather than kept as a separate narrative — see "What the design review changed" near the end of this section for the review itself and how each point was resolved. Both strategies read `LevelInteractionEngine`'s touch/holding/rejected/conquered vocabulary (decision #46) as a settled input — same "consume, don't rebuild" boundary discipline `orb_strategy.py` already applies to `trend_score`/`volume_regime_score`. Neither strategy re-derives zone classification, Aura width, or touch counting — that's `LevelInteractionEngine`'s own job.

**Shared mechanism both strategies build on: private per-symbol touch tracking, one candle late.**

`get_snapshot()` (decision #47) only exposes the CURRENT steady `zone` — by the time a touch resolves and `zone` moves on, the transient `status` ("rejected"/"conquered") that produced it is already gone from the snapshot; it only ever existed on the `LevelInteractionChanged` event itself, which neither strategy can subscribe to yet (see the D9 callout below). Both strategies work around this identically: while `zone == "inside_aura"`, remember `entered_from` in a small private dataclass keyed by symbol; on a later candle, once `zone` is no longer `"inside_aura"`, compare the resolved zone to the remembered `entered_from` — equal means REJECTED (bounced back out the side it came from), different means CONQUERED (broke through) — the exact same rule `level_interaction_engine.py`'s own module docstring defines, just re-derived one candle after the fact instead of read off the authoritative event.

```
 watching?  (private per-symbol state: level_key, entered_from, trading_day)
      │
      no ──▶ zone == "inside_aura" this candle?
      │            │ no  ──▶ nothing to watch yet, return None
      │            │ yes ──▶ remember entered_from + trading_day, start watching, return None
      │
      yes ──▶ zone still "inside_aura"?
                   │ yes ──▶ still resolving, return None (no allows_waiting yet — see below)
                   │ no  ──▶ resolved. compare resolved zone to remembered entered_from:
                                  equal      → REJECTED (bounced back)
                                  not equal  → CONQUERED (broke through)
                              stop watching this touch either way
```

**First Pullback — is this the trend's first pullback to a key reference level today, and did the level hold?**

```
 every 1m candle (trigger = every_candle("1m"))
      │
      ▼
 features.timeframe == "1m"?  ── no ──▶ return None
      │ yes
      ▼
 established trend? |trend_score - 50| past trend_score_threshold
 (mirror-around-50, threshold > 50 — same guard as match_direction(), decision #99)
      │ no ──▶ return None (no trend, nothing to pull back within)
      │ yes  →  direction = BUY if trend_score ≥ threshold, SELL if ≤ 100-threshold
      ▼
 get_level_interaction_engine().get_snapshot(symbol)
   .get(timeframe, {}).get(level_key)
      │ missing ──▶ return None (level not tracked yet — honest absence, not an error)
      ▼
 touch_count_today == 1?  (the FIRST pullback specifically — a later touch is a
      │                     different, not-yet-built strategy family)
      │ no ──▶ return None
      ▼
 [shared touch-tracking mechanism above] → resolved REJECTED, in trend's favor?
      │ CONQUERED, or not yet resolved ──▶ return None (see mechanism diagram)
      │ REJECTED
      ▼
 already fired today for this symbol?  ── yes ──▶ return None
      │ no
      ▼
 SCORE (trend + volume_regime_score + resolution distance_pct blend) → PROPOSE,
 invalidation = anchor_price (the level's own value when the touch began —
 a re-test failing below/above that same value falsifies the thesis),
 mark fired for today.
```

**Reversal — has an established trend's key level just been conquered against it?**

Same touch-tracking mechanism, opposite confirming outcome, and deliberately NOT restricted to the first touch — a reversal is often the second or third test that finally breaks, not the first, so `touch_count_today` is read for SCORE but never gates MATCH the way it does for First Pullback.

```
 every 1m candle (trigger = every_candle("1m"))
      │
      ▼
 features.timeframe == "1m"?  ── no ──▶ return None
      │ yes
      ▼
 trend_score still shows the OLD, about-to-be-tested direction past threshold?
      │ no ──▶ return None (no established direction left to reverse)
      │ yes  →  the trend being tested is BUY-side if trend_score ≥ threshold, SELL-side if ≤ 100-threshold
      ▼
 get_level_interaction_engine().get_snapshot(symbol).get(timeframe, {}).get(level_key)
      │ missing ──▶ return None
      ▼
 [shared touch-tracking mechanism above] → resolved this candle?
      │ not yet, or REJECTED (level held, trend intact) ──▶ return None, keep counting touches
      │ CONQUERED
      ▼
 already fired a reversal today for this symbol?  ── yes ──▶ return None
      │ no
      ▼
 MATCH confirmed — direction is the MIRROR of the trend just broken
 (an established uptrend conquered downward proposes SELL, not BUY)
      │
      ▼
 SCORE (broken-trend strength + volume_regime_score + touch_count_today blend —
 a break after several prior holds is stronger evidence than a break on the
 very first test) → PROPOSE,
 invalidation = anchor_price, mark fired for today.
```

**`level_key` is a `StrategyConfig.params` value (v1 default `"vwap"`), not a hardcoded constant — same "not its own strategy class" precedent §3 already sets for SMA 9/20.** A second `StrategyConfig` version pointed at `"sma_20"` gives a second First Pullback variant for free, no new code.

**D9 — why neither strategy reads `LevelInteractionChanged`'s `status` field directly, and why `base_strategy.py` doesn't change.** The clean, event-driven design would be `trigger = on_event("LevelInteractionChanged")` with the event payload passed into `evaluate()` — but `base_strategy.py`'s own docstring already flags that no Scheduler exists to wire `on_event(...)` triggers to anything live, and `evaluate()`'s fixed 4-argument signature (`symbol`, `market_state`, `features`, `context`) has no slot for an arbitrary triggering event's payload regardless. Building that wiring for two strategies, ahead of ORB/Gap/Volume Spike ever needing it, would be exactly the kind of speculative generality §11 already argues against. Both strategies instead call `get_level_interaction_engine().get_snapshot(symbol)` directly inside `evaluate()` — the same free-function-singleton pattern `orb_strategy.py` already uses for `get_market_clock()` — and accept the one-candle-late re-derivation described above as the cost of not touching the shared interface. Full open item logged as §10's D9.

**`allows_waiting` stays `False` for both, v1.** First Pullback's "touch just started, not yet resolved" moment (the `watching` state in the mechanism diagram above) is a natural fit for a `status="waiting"` Opportunity under §8's ACT/WAIT/ABANDON model, rather than silently returning `None` until resolution. But D5 (the waiting-value model itself) is explicitly deferred until a real strategy needs it, and `allows_waiting` defaults `False` everywhere in v1 — First Pullback would be the first real trigger for D5, not decided here. Flagged rather than built speculatively, same restraint §11 asks for.

**Not yet decided, still open after the build:** the exact SCORE blend weights and `trend_score_threshold` default (v1 guess, unvalidated against real score distributions, same caveat every other strategy's calibration constants already carry).

---

### What the design review changed (decision #108)

Before any code was written, this design went to an external review (Saqib's standing practice of consulting multiple AI systems before bringing consolidated direction back — see the project's own "Approach & patterns"). The review's five points and this file's response, in the order raised:

1. **"Make sure the strategy's reconstruction is exactly equivalent to `LevelInteractionEngine`'s own definition — inspect the real implementation, don't assume."** It wasn't equivalent as first drafted. Reading `level_interaction_engine.py`'s actual `_process_level`/`_process_one` against the original "watch while `inside_aura`, compare on resolution" sketch found two real gaps, both now fixed in `level_touch_tracking.py` (see its own module docstring for the full mechanism): **gap-through** (a zone jumping straight between `below`/`above` with `inside_aura` never observed — the engine still counts it as a touch and always calls it `conquered`; the original sketch would never have seen it at all, since it only started watching on an `inside_aura` observation) and **cold-start-unknown-origin** (`entered_from is None`, the engine's own "can't determine which side" case, decision #46 — the original sketch would have guessed a direction instead of declining to classify). Both are now handled identically to the engine's own rules, not approximated.

2. **A third issue, not raised by the review, surfaced by that same source-reading: `get_snapshot()` is fed by an async queue + `asyncio.to_thread` worker (`_worker_loop`/`_process_one`), not computed synchronously on the triggering `FeaturesUpdated`.** A strategy reading `get_snapshot()` right after publishing a candle has no built-in guarantee the engine has finished processing that exact candle yet — and unlike Context Engine's `get_snapshot()` (which exposes `evaluated_at`), `LevelInteractionEngine.get_snapshot()` exposed no recency signal at all. Resolved by exposing `last_applied_candle_ts` per entry (`level_interaction_engine.py`, this decision) — the same `_last_applied_ts` value `_process_one`'s own out-of-order guard already tracked internally, now surfaced rather than computed fresh. Both strategies check it before ever calling into `level_touch_tracking.observe_resolution()`, and skip the candle entirely (not just return early with a stale trust) on a lagging read — see either strategy's own GATE section and `level_touch_tracking.py`'s module docstring for why feeding a stale entry through the tracker would corrupt its own zone history.

3. **"Keep interaction detection separate from strategy recognition"** — confirmed clean, with one adjustment: rather than each strategy independently re-deriving `entered_from`/`anchor_price`, `level_touch_tracking.py` reads those verbatim from `get_snapshot()`'s own `holding` dict and never recomputes them; the ONE thing genuinely reconstructed is the final "same side or opposite side" comparison the engine's own `status` field would answer directly if a strategy could reach it. `LevelInteractionEngine` still owns `zone`/`touch_count_today`/`entered_from`/`anchor_price`/`trading_day` outright.

4. **"Do not build event infrastructure speculatively."** Agreed, unchanged — `base_strategy.py` was never modified; the `get_level_interaction_engine().get_snapshot(symbol)` polling pattern (D9) stands as designed, and `level_touch_tracking.py` is deliberately isolated so it can be deleted outright, no other file touched, the day real `on_event(LevelInteractionChanged)` wiring exists.

5. **"Separate MATCH from SCORE strictly."** Confirmed already the shape both strategies were designed with — `volume_regime_score`, resolution distance, and touch count were already SCORE-only in the original sketch. Flagged explicitly in both files' own module docstrings: this is a deliberate divergence from `orb_strategy.py`'s `match_direction()`, which DOES use `volume_regime_score` as a MATCH-stage participation floor. Not retrofitted onto ORB/Gap/Volume Spike — a live open question for whoever next touches any of the three, not resolved here.

6. **Invalidation semantics.** `structural_invalidation = anchor_price` kept, with the framing tightened rather than the logic changed: both files' docstrings now say explicitly that this is a THESIS boundary ("falsified if a later candle closes back through this value"), not an executable stop price — that translation is Trade Planning Engine/Position Monitor's job downstream (neither built yet), matching `Opportunity.structural_invalidation`'s own field comment in `base_strategy.py`.

7. **First Pullback vs. Reversal touch-count gating, and `level_key` configurability.** Both already matched the review's description exactly (First Pullback gates on `touch_count_today == 1`, Reversal never does; `level_key` a params default) — confirmed, no change.

### The build itself

`level_touch_tracking.py` — the shared resolution-reconstruction module described above, its own file so the two strategies never carry two subtly different copies of "rejected vs. conquered." `first_pullback_strategy.py` and `reversal_strategy.py` — both follow `orb_strategy.py`'s GATE → MATCH → SCORE → PROPOSE shape exactly, `_FirstPullbackState`/`_ReversalState` mirroring `_ORBState`'s per-symbol dict shape. One asymmetry worth flagging: Reversal's MATCH condition (CONQUERED) genuinely includes gap-throughs, which never have an `anchor_price` to invalidate against — falls back to `features.features.get(level_key)` (the level's live current value, read straight off the same `FeaturesUpdated` already in hand) rather than failing to propose. First Pullback never hits this path (it only ever fires on `rejected`, and gap-throughs are unconditionally `conquered`), so no equivalent fallback was needed there.

**Verification:** `backend/tests/test_level_touch_tracking.py` (13 tests, pure — every scenario in that module's own docstring: normal reject/conquer, both gap-through directions, both cold-start flavors, day rollover) + `backend/tests/test_first_pullback_strategy.py` (16 tests) + `backend/tests/test_reversal_strategy.py` (12 tests) — each strategy's suite covers pure GATE/MATCH/SCORE math, the staleness guard in isolation (a stubbed engine, no DB needed to prove that branch), and end-to-end `evaluate()` runs against a REAL `EventBus` + REAL `LevelInteractionEngine` + REAL Postgres (same posture `test_level_interaction_engine.py` already established — skipped as a whole, not failed, if Postgres isn't reachable), publishing genuine `FeaturesUpdated` sequences and reading genuine engine-computed zone transitions rather than a hand-rolled substitute. Full suite re-run against this change: 486 passed, 0 failed (up from the pre-change 445 passed baseline — the 41 new tests, zero regressions).

---

## 17. Gap/Volume Spike design review, and the six changes it produced (decision #111)

A structured design-level review of decisions #104/#105 (not a code/test review — an explicit audit of layer ownership, trading semantics, timing/entry model, `Opportunity` semantics, state lifecycle, and multi-symbol/multi-day isolation). Full review reasoning isn't reproduced here — it lives in this session's own record — this section covers what changed as a result and why, for future sessions that need the "what's different now and why" without the full review transcript.

**Six items, all six made:**

1. **`regular_open` published by Feature Engine, no longer reconstructed.** `gap_strategy.py`'s own `pdc + gap_dollars` algebra is gone — `_update_gap` (`feature_engine/engine.py`) now publishes `regular_open` as its own `features` key, the same generic-market-fact category as `pdc`/`pdh`/`pdl`, independent of whether `gap_pct`/`gap_dollars` are also present (known the moment today's regular session opens, even without a prior day to compare against).

2. **Gap bounded to `max_minutes_since_open` (v1 default 60, unvalidated).** MATCH now refuses past this window regardless of how confirming everything else is — a real gap the original build had no answer for (nothing stopped a 2pm reclaim of `regular_open` from reading identically to a clean 9:30 hold). Computed via `MarketClock.minutes_since_open()` in `evaluate()`'s own orchestration, passed into `match_direction()` as a plain value — the pure function itself stays clock-free. Does NOT resolve the deeper, still-open question of whether an instantaneous `close` vs. `regular_open` comparison actually proves "holding" rather than "currently on the right side" — deliberately deferred pending real outcome data.

3. **Volume Spike gained `min_absolute_volume` (v1 default 500 shares) and `min_body_ratio` (v1 default 0.3).** Two concrete MATCH-stage gaps closed: a ratio-only test can't tell a genuine spike from a small order against a thin/illiquid baseline; a huge-volume, razor-thin-body candle previously passed as directional on `close != open` alone. `body_ratio = abs(close-open)/(high-low)`, computed from OHLC already on the candle, safe by construction (`close != open` already guarantees `high > low`). Deliberately NOT addressed: whether high volume + directional close actually means continuation vs. exhaustion, and whether sizing the stop off the spike candle's own wick can produce an impractically wide risk/target — both flagged as needing real outcome data, not a guessed fix.

4. **`scoring_utils.py` — new shared module (`clamp`, `trend_magnitude`, `validate_mirror_threshold`).** `_clamp()` and the `trend_score_threshold > 50.0` mirror-guard had been copy-pasted, near-verbatim, across `orb_strategy.py`, `gap_strategy.py`, `volume_spike_strategy.py` — extracted, with each strategy's own MATCH conditions and SCORE weights staying exactly where they were. **Mid-change discovery:** a `git pull` immediately before this item found `first_pullback_strategy.py`/`reversal_strategy.py` (decisions #107–#110, merged concurrently) had independently duplicated the identical pattern a fourth and fifth time — folded into the same extraction rather than left half-finished, verified behavior-neutral against their own test suites.

5. **`Opportunity.expected_horizon_minutes: int | None = None` added to `base_strategy.py`.** Every strategy had an implicit, un-encoded expectation of how long its setup should take — lost the moment `evaluate()` returned, since nothing on the schema carried it. Optional, honest-absence default. Deliberately not an expiry mechanism (`wait_expires_at` already owns that, WAITING-path only) — descriptive metadata for a future Decision Engine to reason with. Populated by ORB (45 min), Gap (60 min), Volume Spike (15 min) — each a v1 guess. Deliberately left unpopulated on First Pullback/Reversal, that thread's own call to make.

6. Renumbering only — this was drafted as decision #107 before the concurrent First Pullback/Reversal work (which claimed #107–#110) was pulled; became #111 with no content change.

**Verification:** `backend/tests/test_scoring_utils.py` (new, 11 tests), plus 8 new tests across `test_gap_strategy.py` (18, was 15) and `test_volume_spike_strategy.py` (22, was 18) pinning the two new Volume Spike checks and the Gap window against the exact failure modes the review described, plus one new `test_base_strategy.py` test for the schema addition. Full suite: 346 passed, 40 pre-existing DB failures (unchanged), 119 skipped (unchanged) — 327-passed baseline + these 19 new tests, zero regressions, including confirmation that the `scoring_utils.py` fold-in left First Pullback/Reversal's own (skipped, DB-gated) suites unaffected.

---

## 18. Momentum and VWAP — design, external review, and build (decision #113)

The sixth and seventh, and last, v1 strategies from `trading-intelligence-architecture.md` §8's planned set. Went through an external (ChatGPT) design review before any code was written — same "design note first, then code" practice §16 already documents for First Pullback/Reversal. Full review transcript isn't reproduced here; this section covers what was decided and built.

### Momentum

**Question:** is an existing directional move accelerating, right now, regardless of time of day or any specific price level? Deliberately not time-boxed or level-based — that distinction is what keeps this genuinely different from ORB (§14), not a restatement of it. First real consumer of `MarketState.acceleration_score` — checked directly against `market_state_engine/scoring.py` rather than assumed from the field's name: it's `trend_score`'s own rate of change, no volume folded in.

```
 every 1m candle, all regular session
      │
      ▼
 ┌───────────────────────────┐
 │ 1. GATE                   │  regular session, high/low present,
 │    is it worth checking?  │  acceleration_score not null, swing
 │                           │  window warmed up, not in cooldown
 └─────────────┬─────────────┘
               │ pass
               ▼
 ┌───────────────────────────┐
 │ 2. MATCH                  │  volume floor (participation) →
 │    accelerating +         │  acceleration off-neutral in one
 │    directional + backed   │  direction (PRIMARY) → trend_score
 │                           │  leaning the same way (lighter
 │                           │  CONTEXT bar, not "established")
 └─────────────┬─────────────┘
               │ true
               ▼
 ┌───────────────────────────┐
 │ 3. SCORE                  │  0.45×acceleration + 0.25×trend
 │    → confidence           │  + 0.30×volume — same hierarchy
 │                           │  as MATCH, numerically
 └─────────────┬─────────────┘
               │
               ▼
 ┌───────────────────────────┐
 │ 4. PROPOSE                │  invalidation = prior N-bar swing
 │    → Opportunity          │  low/high (own private state);
 │                           │  target = mechanical R-multiple
 └───────────────────────────┘
```

Trend's MATCH-stage threshold (`DEFAULT_TREND_CONTEXT_THRESHOLD = 55.0`, own params key `trend_context_threshold`) is deliberately NOT `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD` — a lighter, distinct question ("at least leaning the right way") from Reversal/VWAP's "established" claim; reusing the shared 60/40 bar here would filter out exactly the earliest, most valuable part of a fresh move, where `trend_score` is still crossing the low-50s while `acceleration_score` is already extreme.

Invalidation is a new private rolling swing lookback (`DEFAULT_LOOKBACK_BARS = 10`), not ATR, not `LevelInteractionEngine` — the review's own preferred framing ("the structure supporting the move failed" vs. "price moved X against me"). `FeatureSet` publishes no swing-high/low, so this needed genuinely new per-symbol state, same category as ORB's opening range / Volume Spike's rolling baseline. During the first `lookback_bars` minutes of each trading day per symbol, this file simply doesn't fire — honest-absence, same precedent Volume Spike's own baseline warm-up already set, deliberately not the review's suggested ATR-during-warm-up fallback (one fewer code path, and the warm-up window is small enough that "don't fire yet" is a fully acceptable v1 answer).

Cadence is `volume_spike_strategy.py`'s exact cooldown precedent (`DEFAULT_COOLDOWN_MINUTES = 5`), not once-per-day (ORB/Gap) — Momentum's whole point is catching multiple genuinely independent acceleration phases across a session. The review's further refinement (recognizing "still the same episode" rather than treating every cooldown-cleared candle as brand new) was explicitly NOT built — acceptable for v1 per the review's own conclusion, deferred until real live behavior shows the plain cooldown is insufficient.

**Momentum vs. ORB — co-firing accepted directly, no arbitration added.** Confirmed by both Saqib and the review: the two ask genuinely different questions (a specific morning-range level breaking vs. the market already moving and gaining force, independent of any level), and nothing in either file or `base_strategy.py` should arbitrate between them — matching `base_strategy.py`'s own framing that strategy competition is downstream (Opportunity/Decision Engine's job), not this layer's.

### VWAP

**Question:** has intraday VWAP-side control transitioned, before an established trend exists? Two candidate designs were considered and rejected first:

- **Relationship persistence** (`vwap_relationship_score` confirming strength + trend agreeing, no `LevelInteractionEngine`) — rejected: risked being "Momentum with a different gating field," not a genuinely different trading question.
- **Naive two-candle cross** (`close` vs. `vwap`, no engine) — rejected as too primitive: would false-trigger on exactly the chop `LevelInteractionEngine`'s Aura band already exists to absorb, and would be a second, cruder, silently-drifting definition of "meaningful move" alongside the engine's own authoritative one.

What shipped instead — a genuine `conquered` resolution, reused verbatim from `level_touch_tracking.observe_resolution()` (decisions #107/#108, zero new touch/cross-detection code), gated to `trend_score`'s neutral band:

```
 every 1m candle
      │
      ▼
 ┌───────────────────────────┐
 │ 1. GATE                   │  snapshot fresh (last_applied_
 │    is it worth checking?  │  candle_ts staleness guard, D9),
 │                           │  resolution == "conquered"
 └─────────────┬─────────────┘
               │ pass
               ▼
 ┌───────────────────────────┐
 │ 2. MATCH                  │  trend_score in the NEUTRAL band
 │    control transitioned,  │  only (D11's shared constant,
 │    no established trend   │  Reversal's exact complement) →
 │                           │  direction = the RESOLVED zone
 │                           │  itself, never a mirror of trend
 └─────────────┬─────────────┘
               │ true
               ▼
 ┌───────────────────────────┐
 │ 3. SCORE                  │  0.55×distance_pct (transition
 │    → confidence           │  strength) + 0.45×volume;
 │                           │  touch_count logged, not scored
 └─────────────┬─────────────┘
               │
               ▼
 ┌───────────────────────────┐
 │ 4. PROPOSE                │  invalidation = anchor_price
 │    → Opportunity          │  (live level value if gap-through);
 │                           │  target = mechanical R-multiple
 └───────────────────────────┘
```

**Direction is the structural opposite of Reversal's own `match_direction()`.** Reversal bets against whichever trend is established — its direction comes from mirroring `trend_score`, never from which way the conquest itself moved. VWAP has no established trend to mirror against by construction (the neutral-band gate guarantees that), so direction comes from the conquest's own resolved `zone` instead. This is the strongest evidence the two strategies ask genuinely different questions rather than the same one with a relabeled gate.

**Disjoint from Reversal by construction, not left to downstream arbitration — Saqib's explicit call**, made directly rather than "co-firing is fine" (the accepted answer for Momentum/ORB above): VWAP and Reversal read the exact same underlying event and would otherwise near-duplicate each other on almost every candle with opposite direction conventions, which is a meaningfully higher correlation than the Momentum/ORB case and worth a real gate. `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD` (promoted out of Reversal's own former private constant) is the single number both strategies read, so the neutral-band/established-band partition has no gap and no overlap by construction — see D11 for what this does NOT guarantee (the two configs can still drift if retuned independently later).

**Cadence — fires on a genuine control transition, via `_VWAPState.last_fired_zone`.** Checked directly against `level_touch_tracking.py`'s own classification rule (`"rejected" if current_zone == entered_from else "conquered"`): two consecutive `conquered` resolutions in the engine's own unbroken stream always alternate zones by construction, so raw same-zone-twice-in-a-row can't happen there. The real, reachable repeat case is more specific: VWAP only sees the conquests that land in the neutral band, so it never observes the conquests that happen while trend is established (Reversal's window) — the zone can drift back to a value VWAP already fired for while VWAP wasn't watching. `last_fired_zone` catches exactly that; `test_vwap_strategy.py` proves both directions — the repeat is suppressed, and a genuinely new zone immediately afterward still fires.

**SCORE uses `distance_pct`, not `touch_count_today`.** `get_snapshot()`'s `distance_pct`, on a resolved zone, was confirmed (by reading `level_interaction_engine.py` directly) to be computed from `_latest_close`/`_latest_level_value` — candle-derived, not `seconds_in_zone`'s wall-clock `datetime.now()` — so it's backtest-safe per §7's invariant. `touch_count_today` is logged for future calibration but not weighted: unlike Reversal, where more prior touches before a break is straightforwardly stronger evidence, the sign of that relationship for a VWAP conquest isn't obvious (could mean a well-tested level finally giving way, or an already-choppy session) — flagged rather than guessed.

### `structural_target` — a schema constraint both strategies hit the same way

The review's recommendation for both strategies was "don't force a strategy-specific projection; Trade Planning decides target/risk/sizing downstream." `Opportunity.structural_target: float` is currently REQUIRED (not `Optional`) on `base_strategy.py` — changing that schema would be a bigger, cross-cutting change touching all 5 other built strategies, out of scope for this build. Reconciled by using the same mechanical R-multiple (`close ± target_r_multiple * risk`, default 2.0) every other v1 strategy already computes — a schema-completeness value, not a claim that the number is meaningful trading advice.

### The build itself

`momentum_strategy.py` and `vwap_strategy.py`, both following `orb_strategy.py`'s GATE → MATCH → SCORE → PROPOSE shape exactly. `scoring_utils.py` gained `ESTABLISHED_TREND_SCORE_THRESHOLD` and `trend_established_side()` (D11); `reversal_strategy.py` refactored onto both — behavior unchanged, its own `match_direction()` now delegates to the shared helper rather than repeating the same `>=`/`<=` classification a second file also needed verbatim. One stale comment in `orb_strategy.py` corrected in the same change — it cited the discarded draft's momentum threshold value (decision #99) as precedent for a number this rebuild deliberately doesn't reuse.

**Verification — against a real local Postgres, not DB-free-only.** Unlike most entries in this log, this session provisioned PostgreSQL 16 directly and ran `alembic upgrade head`, so every DB-gated test in this delivery actually ran rather than being skipped — including the two most logically intricate VWAP scenarios (the same-zone-repeat suppression across an established-trend window, the day-rollover reset) that would otherwise only be trace-verified by hand. `backend/tests/test_momentum_strategy.py` (new, 18 tests, no DB dependency), `backend/tests/test_vwap_strategy.py` (new, 19 tests, DB-gated, all run and passed), `backend/tests/test_scoring_utils.py` (+6, now 17), `backend/tests/test_reversal_strategy.py` (all 11 re-run unchanged, confirming the refactor is behavior-neutral). Full suite: 546 passed; 2 pre-existing failures (`test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`, `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`) confirmed identical against a completely untouched second clone of `main` — pre-existing, unrelated to this change, not investigated further here.

**Stage 2 (decision #112/D10) is now unblocked** — see §12.
