# Strategy Engine — Design & Lifecycle
**Status:** Built — all seven v1 strategies complete and wired end-to-end (decisions #99, #104, #105, #109, #110, #113 for the strategies themselves — #99 built `base_strategy.py` plus ORB, #113's "closing out the full planned set" is the completion point; decisions #114–#117 for the Scheduler + `gate_conditions` enforcement that dispatches them; see §12's Staged plan below and `strategy-engine-build-history.md`'s §14–§18 for the full build accounts). `strategy_engine/` is a real, substantial module — verified directly against the repository: `base_strategy.py`, seven strategy files (`orb_strategy.py`, `gap_strategy.py`, `volume_spike_strategy.py`, `first_pullback_strategy.py`, `reversal_strategy.py`, `momentum_strategy.py`, `vwap_strategy.py`), plus `scheduler.py`, `gate_conditions.py`, `scoring_utils.py`, `level_touch_tracking.py`. This plan's separately-scoped Backtest Runner extension is also built — see `backtest-runner-design.md`'s §7 for its own full account and citations, not repeated here. Decision Engine's and Governor's evidence-informed arbitration/derating (§6) remain the target shape only, not yet built — confirmed directly, no corresponding module exists under `backend/app/`. **Originally Stage 0 only** (`confirmed-decisions.md` #87, refined by #88 — concept locked across a two-round review, Saqib + Claude, with a consulted ChatGPT review of that same write-up incorporated directly, same reviewed-external-opinion pattern Daily Levels used with Grok, decision #59; §8's timing model went through a further two-round refinement, ChatGPT's "opportunity lifecycle" critique → Claude's schema-gap findings → ChatGPT's "ACT/WAIT/ABANDON, not bar-close" correction, adopted). This line previously read "No application code has been written yet — `strategy_engine/` doesn't exist anywhere in the repo," true when originally written, stale since decisions #99–#117 landed; corrected here.
**Owner:** Saqib
**Companion documents:** [`trading-intelligence-architecture.md`](./trading-intelligence-architecture.md) (§8 Strategy Engine, §9 Opportunity Engine, §10 Decision Engine, §11 Trade Planning Engine, §12 Governor, §14 Performance Intelligence — every section this plan extends, not replaces), [`system-design.md`](./system-design.md) (§4.5 Feature Engine — the sole data source every strategy reads; §4.8's `Strategy`/`Opportunity` interfaces, extended in §4 below), [`../decisions/future-ideas.md`](../decisions/future-ideas.md) (#5 Replay Engine — the interface `backtest-runner-design.md`'s §7 Backtest Runner reuses; #7 TimescaleDB trigger — checked, not yet hit; #11 `governor/position_sizing.py` — the eventual home for §6's Governor extension; #20 Time-to-Target Estimator — the eventual source of a temporal expectation on `Opportunity`/`StrategyConfig` (§3/§4), deferred pending real `StrategyOutcome` data), [`backtest-runner-design.md`](./backtest-runner-design.md) (this document's former §7), [`strategy-engine-open-decisions.md`](./strategy-engine-open-decisions.md) (former §10, D1–D19), [`strategy-engine-build-history.md`](./strategy-engine-build-history.md) (former §14–§18), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (#87 — this plan's own direction lock).

**Why this doc exists:** same reason `daily-levels-design.md` and `feature-engine-indicator-expansion.md` exist — too large for one sitting, and genuinely new ground for this codebase (the first real design pass at Strategy Engine internals, not an extension of an already-built module). If a session ends mid-build, the next session should read this doc plus `confirmed-decisions.md`'s most recent entries before touching anything, rather than re-deriving the concept from a diff.

**Where content moved (split `split-strategy-engine-design-doc`, decision #142):** this document used to run §0–§18 in one file (175KB). Three growing parts were pulled into their own files — section numbers and D-item numbers are unchanged, only the file changed, and each moved body is untouched by the split:

| Was | Now lives in |
|---|---|
| §7 Backtest Runner | [`backtest-runner-design.md`](./backtest-runner-design.md) |
| §10 Open decisions (D1–D19) | [`strategy-engine-open-decisions.md`](./strategy-engine-open-decisions.md) |
| §14–§18 (per-strategy build history) | [`strategy-engine-build-history.md`](./strategy-engine-build-history.md) |

Everything else — §0–§6 (including the complete §5), §8–§9, §11–§13 — stays in this file.

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

**`gate_conditions` is declarative; `StrategyScheduler` is its sole enforcement authority — decision #118, made explicit once real implementation (decision #117) existed to make the distinction concrete.** `StrategyConfig.gate_conditions` is configuration data, nothing more. `app/strategy_engine/gate_conditions.py` plus `StrategyScheduler` are the only code permitted to interpret it — a `Strategy` subclass may declare `gate_conditions` on its own config, but must never independently interpret the dict, invent a new key's meaning, or enforce a gate itself. The risk this closes: absent this rule, nothing stops a future strategy from reading its own `gate_conditions` inside `evaluate()` and re-implementing (or subtly redefining) a condition the Scheduler already enforces — silently forking one precondition into two implementations that can drift apart, exactly the duplication risk (b) above exists to prevent, just reintroduced one layer down. Matters most as more strategies are added, not less. See `strategy-engine-open-decisions.md` §10 D16 (resolved, decision #119) for the cleanup this made necessary — 3 of the 7 v1 strategies' own inline `is_regular_session()` calls (not 4; `orb_strategy.py`'s `minutes_since_open()` was always load-bearing MATCH logic, never a duplicate session gate) have been removed, so `gate_conditions.py`/`StrategyScheduler` are now this codebase's sole session-gate enforcement, with no remaining exception.

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
    is_backtest: bool                     # see `backtest-runner-design.md` §7 — never blended with live in a live query
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

**Why `_at_entry`, not `_at_signal`, and why only one snapshot per side.** Signal and entry are the same instant for every v1-planned strategy — `allows_waiting` defaults `False` everywhere (§3, `strategy-engine-open-decisions.md` §10 D2/D5). A genuine signal-vs-entry gap only exists once a real waiting-capable strategy ships. Capturing two full duplicate snapshot dicts for a distinction that doesn't bite yet would be exactly the generality §11 already argues against deferring. `evidence.conditions` (captured at signal time, inside `evidence` above) already preserves the thesis snapshot; `market_state_at_entry`/`context_at_entry` capture the moment money was actually on the line, which is the more decision-relevant instant regardless. Revisit — reintroducing a separate `_at_signal` pair — only when D5 (`strategy-engine-open-decisions.md` §10) stops being deferred. Tracked as D7 there.

**These four fields have a real capture contract (decision #98, M4) AND, as of decision #120, a real table and — as of decision #128 — a real, wired writer.** `app/trading_intelligence/state_snapshot.py`'s `capture_market_state_snapshot`/`capture_context_snapshot`/`capture_strategy_outcome_snapshots` read `MarketStateEngine`/`ContextEngine`'s new `get_snapshot()` accessors and shape the result to match `market_state_at_entry`/`_at_exit`/`context_at_entry`/`_at_exit` exactly. The `strategy_outcomes` migration (0008) and its paired ORM/Pydantic/write-path (`app/models/trading_intelligence.py`'s `StrategyOutcomeRecord`, `app/schemas/performance.py`'s `StrategyOutcome`, `app/trading_intelligence/performance.py`'s `record_strategy_outcome()`) now exist — decision #120. Decision #128's Backtest Runner is a real caller of `record_strategy_outcome()` today, via `capture_strategy_outcome_snapshots()` at its own `entry_filled_at`/`exit_filled_at` equivalents — what still doesn't exist is the LIVE-path caller: whatever future Execution/Position Monitor fill handler would call the same function for a real, non-backtest trade (`strategy-engine-open-decisions.md` §10 / §12 below), not something #120 built just to exercise this function. Decision #120 also surfaced a real gap between this section's REQUIRED typing for these four fields and `state_snapshot.py`'s honest-`None` capture behavior, tracked as open item **D17** in `strategy-engine-open-decisions.md` §10 — decision #128 resolved this for the Backtest Runner path (option (a): only call `record_strategy_outcome()` once both snapshots are confirmed non-`None`, discarding the signal otherwise); the live-path caller still doesn't exist, so D17 remains open for that path.

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

**Two of the three query types above are built — decision #122, `app/trading_intelligence/performance_queries.py`.** `get_win_rate_by_hour()` and `get_expectancy_by_session_type()` (one concrete regime dimension, not regime analytics generally). "Parameter sensitivity" stays a query, not a field, with no implementation yet — decision #122's own entry states why.

---

**As-built note (decision #137) — the read side finally has a live consumer that can show BOTH live and backtest evidence, not just whichever one the backend happened to default to.** Decision #127 routed `get_win_rate_by_hour()`/`get_expectancy_by_session_type()` to `GET /win-rate-by-hour`/`GET /expectancy-by-session-type`, both with a real `is_backtest` selector; `usePerformanceAnalytics.ts` accepted an `isBacktest` filter from the start. But `InfoTab.tsx`'s `StrategyPerformanceSummary` called the hook with no arguments at all, so `isBacktest` was always `undefined` and the backend's own default (`false`) was the only thing ever requested — structurally empty forever, since no Execution Engine exists to write a live row, even though real `is_backtest=True` rows have existed since decision #128. This decision adds a Live/Backtest toggle (local component state, default `"live"`) so the same section can show either. Frontend-only — `usePerformanceAnalytics.ts`, `api-client.ts`, and every route/query function above are unchanged.

**Cross-component data flow — toggle click → explicit filter → the same unchanged read path decision #127 already built:**

```
Click "Live" / "Backtest"  (InfoTab.tsx, StrategyPerformanceSummary)
              │
              ▼
   Local state: view: "live" | "backtest"        ◄── NOT WorkspaceContext —
              │                                        no other panel reads this
              ▼
   isBacktest = view === "backtest"               ◄── always an explicit true/false,
              │                                        never undefined (unlike before
              ▼                                        this decision)
   usePerformanceAnalytics({ isBacktest })         ◄── decision #127's hook, UNCHANGED
              │                                        (filters arg always existed,
              ▼                                        just never driven by anything)
   fetchWinRateByHour(filters)
   fetchExpectancyBySessionType(filters)            ◄── api-client.ts, UNCHANGED
              │
              ▼
   GET /win-rate-by-hour?is_backtest=<bool>
   GET /expectancy-by-session-type?is_backtest=<bool>   ◄── intelligence.py, UNCHANGED
              │
              ▼
   get_win_rate_by_hour() / get_expectancy_by_session_type()   ◄── performance_queries.py,
              │                                                     UNCHANGED (#122/#124)
              ▼
   strategy_outcomes  (is_backtest=True rows exist since #128;
                        is_backtest=False rows: none yet — no
                        Execution Engine — an honest empty result,
                        not an error)
```

**Internal state flow — inside `StrategyPerformanceSummary`, where the real gap this decision closes actually lived:**

```
view changes (Live <-> Backtest)
        │
        ▼
isBacktest = view === "backtest"  ──► usePerformanceAnalytics re-fetches
        │                              (hook's own [strategyName,
        │                               strategyVersion, isBacktest]
        │                               dependency array — #127 — already
        │                               reacts to this, no change needed)
        ▼
loading = true
        │
        │   ◄── render gate WIDENED from `loading && isEmpty` to
        │       `loading` alone (the actual fix this decision made) —
        │       the hook's own load() doesn't clear winRateByHour/
        │       sessionExpectancy until the fetch resolves, so without
        │       this widening a toggle switch would keep rendering the
        │       OTHER mode's numbers under the NEW mode's own header
        │       label for the duration of the in-flight request
        ▼
"Loading…" shown — never the previous mode's stale numbers
        │
        ▼
fetch resolves
        │
        ├── error  → distinct error state (unchanged from #127)
        ├── empty  → mode-specific message:
        │            Live: "No live data yet — no Execution Engine
        │                   exists to write it."
        │            Backtest: "No backtest data yet — run a backtest
        │                   to populate this."
        └── data   → existing win-rate/expectancy rows, always under
                      an explicit "Strategy Performance — Live" /
                      "— Backtest" header; Backtest mode also shows
                      "Derived from backtest StrategyOutcome data —
                      not live trading results."
```

`strategyName`/`strategyVersion` — also real filters on the same hook/routes — deliberately NOT exposed by this toggle: no source of selectable strategy names exists anywhere in this codebase today (checked directly), and building one is a separate UI/data-source design question, not a natural extension of a two-state provenance toggle. Stays real, deferred future work, same as `future-ideas.md`'s own pattern for named-but-not-yet-triggered ideas.

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

**Open, not resolved here — see `strategy-engine-open-decisions.md` §10's table:** Saqib has raised the possibility of merging Decision Engine and Governor into one component outright. Not decided either way in this document; both responsibilities above hold regardless of whether they end up as one component or two — a structure question, not a logic question, and it doesn't need resolving before Strategy Engine work can proceed.

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
              (not modeled yet — `strategy-engine-open-decisions.md` §10 D5)
```

Sometimes waiting strengthens the hypothesis. Sometimes it does nothing. Sometimes it costs entry quality faster than it adds confidence. Sometimes it outright invalidates the setup. An architecture that treats "wait for confirmation" as universally superior — waiting by default whenever the data exists — drifts toward what's worth naming and avoiding explicitly: a confirmation fetish, where every strategy waits simply because it can, not because waiting is actually worth it for that setup.

**Two principles, load-bearing, adopted close to verbatim from the review that produced them:**

> A strategy must not be required to wait for a candle close unless its hypothesis specifically depends on information only establishable at that close. The architecture must support both immediate and deliberately delayed entry, without assuming delayed confirmation is universally superior.

> Waiting is an information-gathering decision, not a synonym for confirmation. It has a value (better evidence) and a cost (entry-price drift, opportunity decay) — both eventually measurable by Performance Intelligence (§5), neither modeled today.

**What's already true, confirmed against the real code, not assumed:**
- Feature Engine never publishes a still-forming higher-timeframe bar as final — 5m/15m/1h `FeaturesUpdated` only fires once `candle_aggregator.completes_bucket()` closes that bucket (system-design.md §4.5). No risk of reading an in-progress bar's OHLC as settled.
- Nothing computed today is sub-1-minute. Market State Engine (where Participation — buyer/seller control — lives, decisions #92/#93/#96/#97) recomputes on the same 1m `FeaturesUpdated`/`MarketStateChanged` cadence Feature Engine publishes at, never faster (decision #103). This line previously read "Market State Engine... isn't built," true when originally written, stale since those decisions landed; corrected here. Every Feature Engine indicator, RVOL included, recomputes on 1m close.

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

**The mechanism, reshaped from the earlier draft:** no `confirmation_timeframe` enum. Instead, `StrategyConfig.allows_waiting: bool` (§3) is a bare capability flag, default `False`. A strategy with it set to `True` may, inside its own `evaluate()`, return an `Opportunity` with `status="waiting"` and a free-form `wait_reason` instead of `None` or an actionable `Opportunity` — the *reason* for waiting is whatever that strategy's own evidence-sufficiency judgment produces at that moment, never a fixed per-version setting. **Where the pending state lives, since a stateless `evaluate()` has nowhere to keep "still waiting" between one trigger fire and the next:** each `Strategy` instance holds its own small pending set internally, re-checked on its own next trigger fire — not a new shared "Opportunity Tracker" engine. This keeps every strategy independently testable, costs nothing until a strategy actually sets `allows_waiting=True`, and composes cleanly with `backtest-runner-design.md` §7's identical-live/backtest constraint as long as transitions are driven by candle/event timestamps, never wall-clock.

**Deliberately not this document's job to decide whether Decision Engine and Governor should merge.** Waiting happens *before* an Opportunity is even actionable — it never reaches Decision Engine while `status="waiting"`. Decision Engine still only ever arbitrates finalized opportunities; Governor still only ever derates one already-planned trade. This section adds a stage upstream of both, not an argument for merging them — `strategy-engine-open-decisions.md` §10 D1 stays exactly as open as it already was.

**Explicitly deferred — real work, not built now:**
- The waiting-value model itself (is this specific wait worth its cost) — `strategy-engine-open-decisions.md` §10 D5.
- Wiring any consumer to `PriceSnapshot` at all — `strategy-engine-open-decisions.md` §10 D6.
- Reusable candle-shape helper functions (wick ratio, body ratio, position-in-range) — OHLC exists on the 1m `FeatureSet` now (decision #99), but no strategy has needed these specific helpers yet; still not written.
- A candle-pattern library, an automatic confirmation selector, or any dedicated "Timing Engine"/"Confirmation Engine" — premature architecture until a real strategy needs more than the flag above. Same "defer generality until a concrete gap appears" discipline this codebase already applies everywhere else (Redis, Replay, uncertainty propagation, the playbook-as-data rejection in §1).

**This becomes a backtestable question, same as every other tunable in this design.** "ORB v4, `allows_waiting=False`" vs. "ORB v5, `allows_waiting=True`" are two versions (§3); Performance Intelligence (§5) can eventually report not just expectancy per version but entry-quality degradation alongside it — waiting that improves expectancy by degrading median entry price enough to not be worth it is exactly the kind of trade-off `backtest-runner-design.md` §7's backtest report is required to surface, not hide behind a single expectancy number.

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
                   Backtest Runner (`backtest-runner-design.md` §7) — search + validate
                                │
                                ▼
                          HUMAN REVIEW  (Saqib)
                                │
                                ▼
                              LIVE
```

---

## 11. Guiding constraints carried into this design (standing project principles, not new rules)

- **Honest state over fabricated state** — an outcome record with no data for a field stays `None`/absent; Performance Intelligence never estimates a plausible-looking number for something not yet measured. Extends to time, not just data (§8): a forming candle's shape is real but not yet settled, and `evidence.basis` exists so a strategy is never ambiguous about which kind of fact it acted on.
- **Compute once, consume everywhere** — one `StrategyOutcome` schema serves live performance queries and backtest reports alike, distinguished only by `is_backtest`, never duplicated per consumer.
- **Evidence stores interpretation, not measurement (decision #89)** — `evidence` holds the strategy's own reasoning (`conditions`, `reason`, `basis`), never raw indicator values wholesale. Feature Engine measures; Strategy interprets; `StrategyOutcome` records what was observed and what happened; Performance Intelligence aggregates — the same layering already applied to Feature Engine vs. Market State/Context Engine (system-design.md §4.5), carried one stage further downstream. `feature_snapshot_id` is the escape hatch for full traceability without violating it.
- **Invariants enforced at write time, not just documented (decision #89)** — `entry_qty == exit_qty` for a fully closed `StrategyOutcome` row is asserted before the row is written, same "assertion-guarded, not just narrated" discipline already used for multi-site structural edits elsewhere in this project.
- **Real Postgres, not mocks**, once `strategy_outcomes` (renamed from `strategy_performance` — decision #89) has real rows to query against — same standard as every other module in this codebase.
- **Docs updated in the same change as code** — once Strategy Engine code exists, this document and `confirmed-decisions.md` update alongside it, not after.
- **Architecture questions surfaced before code** — `strategy-engine-open-decisions.md` §10's open items get resolved (or explicitly deferred with a trigger condition) before the corresponding code is written, not silently decided mid-implementation.
- **Defer generality until a concrete gap appears** — no dedicated "Timing Engine" or "Confirmation Engine" (§8), no generic rule engine for MATCH (§1), until a real strategy's needs outgrow the flag/dict-based approach already in place. Same reasoning kept `_at_signal`/`_at_entry` as one snapshot pair, not two, in §5.

---

## 12. Staged plan

- [x] **Stage 0 — Lock the direction in writing (no application code).** This document + `confirmed-decisions.md` #87, refined by #88 (§8's ACT/WAIT/ABANDON model), refined again by #89 (§5's `StrategyOutcome`/`backtests` schema: field groups, `strategy_outcomes` rename, `eod_flatten`, `slippage_entry`, write-time invariants).
- [x] **Stage 1 — ORB built (decision #99).** `base_strategy.py` (`Strategy`/`StrategyConfig`/`Opportunity`/`ScheduleTrigger`) and `orb_strategy.py` — the first concrete strategy. An earlier, undocumented attempt at this stage (`momentum_strategy.py`/`vwap_strategy.py`, built by a concurrent session against a `base_strategy.py` that didn't exist yet, decision-log entry lost to a numbering collision with #98) was found orphaned and discarded rather than built on top of — see decision #99 for the full account. Momentum and VWAP are being rebuilt fresh, assigned to a separate session, against this now-real interface.
- [x] **Stage 1 (continued) — Gap and Volume Spike built (decisions #104, #105).** `gap_strategy.py` and `volume_spike_strategy.py`, the second and third concrete strategies against the real interface — see `strategy-engine-build-history.md` §15 for both.
- [x] **Stage 1 (continued) — First Pullback and Reversal built (decisions #107-#110).** Design-reviewed before code (decision #108: gap-through/cold-start handling, `get_snapshot()`'s new `last_applied_candle_ts` staleness field, MATCH/SCORE boundary tightened). `first_pullback_strategy.py`, `reversal_strategy.py`, and shared `level_touch_tracking.py` — see `strategy-engine-build-history.md` §16 for the full walkthrough. All 7 v1 strategies from trading-intelligence-architecture.md §8 are now either built (ORB, Gap, Volume Spike, First Pullback, Reversal) or assigned (Momentum, VWAP — a separate session's thread).
- [x] **Stage 1 (continued) — Gap/Volume Spike design review's six changes made, `scoring_utils.py` extracted (decision #111).** `regular_open` moved to Feature Engine, Gap bounded to `max_minutes_since_open`, Volume Spike gained `min_absolute_volume`/`min_body_ratio`, shared `clamp`/`trend_magnitude`/`validate_mirror_threshold` adopted by all 5 built strategies, `Opportunity.expected_horizon_minutes` added.
- [x] **Stage 1 complete — Momentum and VWAP built (decision #113).** `momentum_strategy.py`, `vwap_strategy.py` — the sixth and seventh, and last, v1 strategies. Externally reviewed before code (same practice as #107/#108); `ESTABLISHED_TREND_SCORE_THRESHOLD` extracted to `scoring_utils.py`, `reversal_strategy.py` refactored onto it. See `strategy-engine-build-history.md` §18 for the full walkthrough. **All 7 v1 strategies from `trading-intelligence-architecture.md` §8 are now built.**
- [x] **Stage 2 — fully built and verified, both tracks (decisions #114/#115/#116).** **Track A — Strategy Scheduler:** `scheduler.py` instantiates all 7 built strategies, subscribes `FeaturesUpdated` (caches the real payload — see decision #114 for why) and `MarketStateChanged` (the actual `evaluate()` trigger — also decision #114, a real correction found by testing, not the original plan), publishes `OpportunityCreated`. **Track B — OpportunityCreated read-side** (cache + `GET /intelligence/opportunities`), built in a parallel session; collided with Track A on decision number #114 (lost from the log when Track A's delivery was applied) and its own `main.py`/`conftest.py` wiring was flagged but never finished — both restored/completed in decision #115, verified end-to-end by running the real app lifespan (a directly-published `OpportunityCreated` correctly reaches `GET /intelligence/opportunities`). Full suite with a real local Postgres: 577 passed, 1 failed (`test_vwap_publishes_even_while_sma_is_still_warming_up`, pre-existing, unrelated). Opportunity Engine's ranking (§9) remains deliberately OUT of Stage 2's scope. `active_from`/`active_to` enforcement — also OUT of scope, canonically closed, not merely deferred pending clarification — see decision #116/D14. Declarative `gate_conditions` enforcement (§2b), originally deferred alongside `active_from`/`active_to`, was closed SEPARATELY and is now built — see decision #117/D15.
- [x] **Stage 2 (continued) — `gate_conditions` enforcement built (decision #117/D15).** `app/strategy_engine/gate_conditions.py` (new) — registry/validation/check for §2b's declarative preconditions, wired into `scheduler.py` centrally, before `evaluate()`. v1 supports exactly `{"session": "regular"}`, the only condition any real `StrategyConfig` declares. Closed a real, live gap for 3 of 7 strategies (First Pullback/Reversal/VWAP had no session enforcement anywhere before this). 20 new tests, real local Postgres, zero regressions.
- [x] **Performance Intelligence's persistence layer built — `strategy_outcomes` + `backtests` tables (decision #120).** §5's shape / `backtest-runner-design.md` §7's shape (decision #89), Stage 0 since #89, now has a real migration (0008), ORM (`app/models/trading_intelligence.py`'s `StrategyOutcomeRecord`/`BacktestRunRecord`), Pydantic contract (new `app/schemas/performance.py`), and a real but unwired write path (`app/trading_intelligence/performance.py`'s `record_strategy_outcome()`, asserting `entry_qty == exit_qty` before every write, raising rather than swallowing per §11). Establishes this codebase's first JSONB and native-UUID-PK/FK conventions. `feature_snapshot_id`/`opportunity_id` stay unenforced UUID references — their target tables don't exist yet; `backtest_run_id` is a real FK to `backtests`, built in the same migration. Surfaced a genuine, deliberately unresolved gap between §5's required-dict typing and `state_snapshot.py`'s (#98) honest-`None` capture behavior — tracked as new open item D17, not silently patched. No Execution Engine/Position Monitor exists yet to call `record_strategy_outcome()` for real — same "build the stable contract now, real callers plug in later" precedent decision #98 set for the read side. 8 new tests, real local Postgres, 100% stable across repeated runs.
- [x] **`strategy_outcomes` and the opportunity conflict view (#120/#121) exposed via routes — `GET /intelligence/strategy-outcomes`, `GET /intelligence/opportunity-conflicts` (decision #123).** Both #120 and #121 deliberately shipped without a route, each citing the same parallel-track collision risk on `app/api/routes/intelligence.py`; that risk is gone now, closing the gap the same way `GET /intelligence/opportunities` did for `OpportunityCache` back in Stage 2. `/strategy-outcomes` is a raw recent-rows read (`ORDER BY exit_filled_at DESC`, `limit`-capped) through #120's own `StrategyOutcome` Pydantic contract — not an aggregate, and not dependent on #122's `performance_queries.py` (that module's own `GROUP BY` queries remain unrouted, by design, per #122's own entry). `/opportunity-conflicts` is a genuinely thin wrapper over #121's `get_opportunity_conflicts()`. Minimal frontend surfacing: a per-symbol "conflict/agreement" section next to the existing opportunities list (`AIAnalysisPanel.tsx`), and a global "Recent Closed Trades" section in the market-wide view (`InfoTab.tsx`'s `GeneralContent`, since `strategy_outcomes` has no `symbol` filter) — no new page or panel type. `strategy_outcomes` still has zero real LIVE rows in production (no Execution Engine/Position Monitor exists to write one) — the empty state is rendered honestly, not hidden. *(As of decision #130: this route had no `is_backtest` filter at all until then — harmless at the time this entry was written since nothing wrote backtest rows either, but a real gap once Backtest Runner v1 (#128) started producing genuine `is_backtest=True` rows. See #130 for the fix and the corrected route docstring.)*

---

## 13. How to resume this in a new session

1. Read this file in full, then `confirmed-decisions.md`'s most recent entries — check whether `strategy-engine-open-decisions.md` §10's open items have moved before re-deciding them. This document split into four files at `split-strategy-engine-design-doc` (decision #142) — see the moved-sections map above; the other three are only needed when the work at hand actually touches Backtest Runner, an open D-item, or a specific strategy's build history.
2. `strategy_engine/base_strategy.py` now exists (decision #99) — any new strategy should import the real `Strategy`/`StrategyConfig`/`Opportunity`/`ScheduleTrigger` from it, not re-guess the interface the way the discarded momentum/vwap attempt had to.
3. §8's ACT/WAIT/ABANDON *model* is locked (decision #88) — don't re-litigate whether `confirmation_timeframe` should come back. What's still genuinely open there is D5 (the waiting-value model itself) and D6 (wiring a consumer to `PriceSnapshot`) — build either only when a real strategy needs it, not speculatively.
4. Before building on Stage 1, know what M4 (decision #98) already prepared: `MarketStateEngine.get_snapshot()`, `ContextEngine.get_snapshot()`, and `app/trading_intelligence/state_snapshot.py`'s three capture functions all exist and are tested (`backend/tests/test_strategy_integration_contract.py`) — a strategy should call these, not re-derive its own read path against either engine. Also worth knowing: `ContextChanged` has no domain-safe timestamp (§4's providers are timer-triggered, not candle-triggered) — decision #98 left this open rather than inventing one; don't assume it got solved.
5. `FeatureSet` now carries `open`/`high`/`low`/`volume` (decision #99) — but ONLY on the 1m `FeatureSet`; a 5m/15m/1h `FeatureSet`'s open/high/low/volume are `None` (true aggregated-bucket OHLC isn't tracked anywhere yet — see `schemas/events/features.py`'s `FeatureSet` docstring for why passing through the last constituent 1m candle's OHLC would be dishonest, not just incomplete). A strategy reading these on anything other than a 1m `FeatureSet` needs to handle `None`, not assume they're populated.
6. `Strategy.evaluate()`'s real signature takes `symbol: str` as its first argument (decision #99) — the illustrative 3-arg sketch (system-design.md §4.8) had no way for a strategy to know which symbol it's being asked about, which only mattered once a strategy needed its own per-symbol memory (ORB's opening range does; the discarded Momentum draft's stateless MATCH logic never hit this gap). Any new strategy's `evaluate()` must match the real 4-arg signature.
7. First Pullback and Reversal (`strategy-engine-build-history.md` §16, decisions #107-#110) are now built — `first_pullback_strategy.py`/`reversal_strategy.py` call `get_level_interaction_engine().get_snapshot(symbol)` directly (D9), never read its `seconds_in_zone` field (wall-clock, not `candle_ts`-derived), and re-derive rejected-vs-conquered one candle late from each strategy's own private per-symbol state rather than assuming `LevelInteractionChanged`'s `status` field is reachable — no Scheduler wires `on_event(...)` triggers to anything yet. *(Corrected — this point previously said "design-locked but unbuilt," stale as of decision #109/#110; caught during a status review, decision #112.)*
8. **All 7 v1 strategies are now built** (ORB, Gap, Volume Spike, First Pullback, Reversal, Momentum, VWAP — decision #113 closed out the last two). **Stage 2 (the Scheduler/wiring milestone, decision #112/D10) is now unblocked** and is this thread's next work — nothing further needs to land in Strategy Engine itself first.
9. A numbering collision happened at #111: a docs-only decision drafted in an earlier session (Stage 2 sequencing/scope, D10) was queued but never actually applied to git before Saqib separately committed different, unrelated work (the Gap/Volume Spike design-review changes) as #111. Same category of collision decision #99 already documents happening once before with #98 — resolved the same way: the queued content wasn't discarded, just renumbered and re-applied as #112, with this note as the record of what happened. If a THIRD such collision is ever found, it's worth asking whether the decision-log's queue/apply handoff between sessions needs an actual fix rather than another one-off renumbering.
10. Momentum and VWAP (`strategy-engine-build-history.md` §18, decisions #113) are now built — `momentum_strategy.py`/`vwap_strategy.py`. `reversal_strategy.py` was touched too (refactored onto `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD`, no behavior change). A local Postgres was actually provisioned for this session's own verification (`apt-get install postgresql`, `alembic upgrade head`) rather than relying on DB-free tests alone — worth doing again for any future session touching `LevelInteractionEngine`-dependent strategies, since the two most logically intricate VWAP tests (the same-zone-repeat suppression, the day-rollover reset) would otherwise only be trace-verified by hand.

---

