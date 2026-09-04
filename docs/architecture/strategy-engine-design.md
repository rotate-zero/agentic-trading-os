# Strategy Engine — Design & Lifecycle
**Status:** Stage 0 confirmed (`confirmed-decisions.md` #87, refined by #88). Concept locked across a two-round review (Saqib + Claude, with a consulted ChatGPT review of that same write-up incorporated directly — same reviewed-external-opinion pattern Daily Levels used with Grok, decision #59); §8's timing model went through a further two-round refinement (ChatGPT's "opportunity lifecycle" critique → Claude's schema-gap findings → ChatGPT's "ACT/WAIT/ABANDON, not bar-close" correction, adopted). **Stage 1 has begun, split across two concurrent sessions with no visibility between them:** `base_strategy.py`/`orb_strategy.py` in one; `momentum_strategy.py`/`vwap_strategy.py` (decision #98 — §1a below) in the other. §1's ORB snippet stays "illustrative, not final code" until that session's actual file is reconciled against it; §1a's Momentum/VWAP code is real and tested, but unverified against the real `base_strategy.py` — see #98's integration note for the specific open points.
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

---

## 1a. Momentum and VWAP — worked examples (decision #98)

ORB above was always illustrative — a shape to write every strategy against, not a locked pattern for any *other* strategy's own MATCH stage. Momentum and VWAP's actual conditions were never decided anywhere until this section. Built and tested (24 tests, pure GATE/MATCH/SCORE functions plus full `evaluate()` orchestration) against a local, spec-conformant stand-in for `base_strategy.py`, since that file didn't exist in the repo yet at the time of writing — see decision #98 for exactly what's verified vs. still open.

**Momentum — "is a trending move accelerating with volume behind it, worth joining now?"** A setup-detector consuming Market State's Trend/Acceleration/Volume-regime dimensions, never recomputing them:

```python
class MomentumStrategy(Strategy):
    name = "Momentum"
    trigger = every_candle(timeframe="5m")

    async def evaluate(self, market_state, features, context) -> Opportunity | None:
        fast_ma = features.features.get("sma_9")                        # GATE
        slow_ma = features.features.get("sma_20")                       # (ma_type/periods are
        if fast_ma is None or slow_ma is None:                          #  StrategyConfig.params —
            return None                                                 #  a different indicator
        if market_state.acceleration_score is None:                     #  choice is a new config
            return None                                                 #  version, not a new class)

        direction = match_direction(                                    # MATCH — crossover direction,
            fast_ma, slow_ma,                                           # confirmed by trend_score
            market_state.trend_score, market_state.acceleration_score,  # (>=60/<=40), acceleration_score
            market_state.volume_regime_score,                           # strengthening not fading
            trend_score_threshold=60.0,                                 # (>=55/<=45), and a volume
            acceleration_score_threshold=55.0,                          # floor (>=45, direction-
            volume_regime_threshold=45.0,                               # agnostic)
        )
        if direction is None:
            return None

        confidence = score_confidence(                                  # SCORE — weighted blend;
            market_state.trend_score, market_state.acceleration_score,  # regression_9_slope_norm
            market_state.volume_regime_score,                           # contributes a neutral 50,
            features.features.get("regression_9_slope_norm"),           # not a penalty, when absent
        )

        slow_ma_is_invalidation = slow_ma                                # PROPOSE — the crossover
        target = (                                                       # holding above/below the
            features.close + 2 * (features.close - slow_ma)             # slow MA IS the thesis; target
            if direction == "BUY" else                                  # is an R-multiple projection
            features.close - 2 * (slow_ma - features.close)             # off that same level (same
        )                                                                 # shape as ORB's own target)
        return Opportunity(
            strategy="Momentum", version=self.config.version, direction=direction,
            confidence=confidence,
            structural_invalidation=slow_ma_is_invalidation,
            structural_target=target,
            evidence={"conditions": {...}, "reason": "...", "basis": "closed"},
            setup_detected_at=features.candle_ts,
        )
```

**VWAP — "is price holding the session VWAP level right now?"** A different question from Momentum on purpose — both can fire together on the same symbol, and that's fine (confluence note below):

```python
class VWAPStrategy(Strategy):
    name = "VWAP"
    trigger = every_candle(timeframe="1m")           # fastest reaction to a level test;
                                                       # VWAP itself is timeframe-agnostic (§4.5),
                                                       # so this is a responsiveness choice
    async def evaluate(self, market_state, features, context) -> Opportunity | None:
        vwap = features.features.get("vwap")                             # GATE
        atr = features.features.get("atr_14")
        if vwap is None or vwap == 0.0 or atr is None:
            return None

        direction = match_direction(                                     # MATCH — a BAND read
            market_state.vwap_relationship_score,                        # (52-65 for BUY, mirrored
            market_state.trend_score, market_state.volume_regime_score,  # for SELL), not just >50/<50:
            vwap_score_low=52.0, vwap_score_high=65.0,                   # excludes both "sitting right
            trend_score_threshold=55.0, volume_regime_threshold=40.0,    # at the line" (noise) and
        )                                                                 # "already extended" (a
        if direction is None:                                            # different strategy's job)
            return None

        confidence = score_confidence(...)                               # SCORE

        target = (                                                       # PROPOSE — VWAP itself IS
            features.close + 2 * atr if direction == "BUY" else          # the invalidation level;
            features.close - 2 * atr                                     # target is an ATR-multiple
        )                                                                 # projection
        return Opportunity(
            strategy="VWAP", version=self.config.version, direction=direction,
            confidence=confidence,
            structural_invalidation=vwap,
            structural_target=target,
            evidence={"conditions": {...}, "reason": "...", "basis": "closed"},
            setup_detected_at=features.candle_ts,
        )
```

**Explicitly scoped, stated plainly:** VWAP v1 is a *position* read (has price established itself just above/below the level right now), not a *reclaim event* (just-crossed as of this candle vs. been on this side for an hour). A true event-based reclaim needs per-symbol memory across `evaluate()` calls — real, legitimate state per §8 below (the same shape the pending-Opportunity case already uses), but a bigger step than v1 needs. Flagged here rather than silently built or silently skipped; revisit if the position-based read proves too noisy once real data exists.

**Note — multi-strategy confluence (raised while building this section).** If VWAP and Momentum both fire BUY on the same symbol at the same time, does that make the signal stronger, and where does that get decided? **Not Governor's job** — by the time a trade reaches Governor (§12), Decision Engine has already collapsed the field to one selected opportunity; Governor only ever sees that one already-planned trade, never the raw multi-strategy set, and giving it that visibility would blur the exact "which one wins" (Decision Engine, §10) vs. "should we act on this one" (Governor, §12) split §6 already draws a hard line around. The right home is **Opportunity Engine's ranking (§9) feeding Decision Engine's arbitration (§10)** — but Decision Engine's documented logic today only covers resolving *conflict* (Momentum says BUY, Reversal says SELL); whether/how it extends to *rewarding agreement* (Momentum and VWAP both say BUY) is a real, currently open question, deliberately left for whoever builds Decision Engine rather than decided here. Doesn't block either strategy file: both just need to emit honest, independently-evidenced `Opportunity` objects — confluence assembly is entirely downstream of that, same "Strategy doesn't decide which is best" boundary §0 above already establishes.

---

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
   SCHEMA GAP                                WIRING GAP, not a data gap
   FeatureSet (schemas/events/               PriceSnapshot already carries
   features.py) carries only `close` —       open/high/low/close/volume for
   no open/high/low/volume, so a             the forming bar, field-for-field
   strategy can't compute a wick             identical to CandleClosed
   ratio or body size today                  (LiveTickRelay, decision #72) —
                                              but nothing on the backend
   Fix: add open/high/low/volume             subscribes to it except the
   to FeatureSet — additive,                 frontend chart. No Strategy or
   zero new engine logic                     Feature Engine reads it.

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
- Reusable candle-shape helper functions (wick ratio, body ratio, position-in-range) — trivial once OHLC exists on `FeatureSet`, not written yet.
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
- [ ] **Stage 1 — Not yet started.** Blocked on §10's D1/D2 open items being resolved enough to implement against, and on Saqib's own priority call relative to other in-flight work (IBKR live access, ORB as first live strategy per `trading-intelligence-architecture.md`'s existing plan).

---

## 13. How to resume this in a new session

1. Read this file in full, then `confirmed-decisions.md`'s most recent entries — check whether §10's open items have moved before re-deciding them.
2. Do not start any `strategy_engine/` code before §7's "identical live/backtest `evaluate()`" constraint is understood by whoever writes the first strategy — retrofitting it later is real, avoidable cost.
3. §8's ACT/WAIT/ABANDON *model* is locked (decision #88) — don't re-litigate whether `confirmation_timeframe` should come back. What's still genuinely open there is D5 (the waiting-value model itself) and D6 (wiring a consumer to `PriceSnapshot`) — build either only when a real strategy needs it, not speculatively.
