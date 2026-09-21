# Backtest Runner — Design & As-Built Record
**Owner:** Saqib
**Split from `strategy-engine-design.md`** (`split-strategy-engine-design-doc`, decision #142) — this file holds what was that document's §7; the section number is unchanged, and its body is unedited by the split.
**Companion documents:** [`strategy-engine-design.md`](./strategy-engine-design.md) (§0–§6, §8–§9, §11–§13 — Strategy Engine's own design, including the complete §5 `StrategyOutcome`/Performance Intelligence schema this Runner writes into), [`strategy-engine-open-decisions.md`](./strategy-engine-open-decisions.md) (D18/D19 — the two open items this Runner's own build history produced), [`strategy-engine-build-history.md`](./strategy-engine-build-history.md) (how the 7 strategies this Runner replays were built), [`system-design.md`](./system-design.md), [`../decisions/future-ideas.md`](../decisions/future-ideas.md) (#5 Replay Engine — the interface this file's own harness reuses), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md).

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

**As-built note (decision #128) — v1 is the vertical slice above the dashed line only, nothing below it.** Everything above described the eventual full system prospectively, before any of `backend/app/backtest_runner/` existed. What actually got built (Units 1-5) is the first vertical slice — proving the plumbing works end-to-end — not the outer grid-search/walk-forward/promotion loop described above, which remains real, deferred, future work exactly as originally scoped:

```
             IMPLEMENTED FIXTURE PATH (v1, decision #128)
   ┌──────────────────────────────────────────────────────────┐
   │  Fixture Candle Data                                       │
   │  (FixtureCandleProvider — plumbing proof, NOT real          │
   │   historical-market validation)                             │
   │            │                                                │
   │            ▼                                                │
   │  ReplayStateProducer                                        │
   │  (real EventBus/FeatureEngine/LevelInteractionEngine/       │
   │   MarketStateEngine — real engines; narrow replay-only       │
   │   immediate settlement control on Market State)             │
   │            │                                                │
   │            ▼                                                │
   │  Feature / Market State  ──────┐                            │
   │            │                    │                           │
   │            ▼                    ▼                           │
   │      Context Engine      (BacktestContextProvider boundary  │
   │  (FixtureBacktestContextProvider — fixture/calendar only,   │
   │   NOT point-in-time historical context fidelity)            │
   │            │                                                │
   │            ▼                                                │
   │  Strategy.evaluate()  (real, unmodified — every candle)     │
   │            │                                                │
   │            ▼                                                │
   │  Fill Simulator (pure, MarketClock-derived)                  │
   │            │                                                │
   │            ▼                                                │
   │  StrategyOutcome rows (is_backtest=True, backtest_run_id)   │
   │            │                                                │
   │            ▼                                                │
   │  Performance Intelligence (decisions #120/#122 — read-only) │
   └──────────────────────────────────────────────────────────┘
                             │
   ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌│╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
                             ▼
                    FUTURE (remaining prerequisites)
   Point-in-time historical context (HistoricalContextProvider, a
   documented but unbuilt extension point) + multi-symbol replay +
   outer grid-search/walk-forward loop
   + robustness/parameter-sensitivity report + human review + promotion
   — the full flow diagrammed earlier in this section. Real historical
   IBKR OHLCV later became a sibling input path; see the current as-built
   note at the end of this section. It does not make fundamentals/news
   historical.
```

`HistoricalContextProvider` (`backend/app/backtest_runner/context_provider.py`) already exists as a named, documented extension point for the Context half of the future boundary above — deliberately unbuilt (`NotImplementedError`), not a stub pretending to work. `BacktestContextProvider` is the seam a real implementation plugs into without `BacktestRunner` itself changing. See decision #128 for the full as-built record, including why entry/exit snapshots are captured at `entry_filled_at`/`exit_filled_at` rather than the signal candle — a correction to this section's own original, less precise framing.

**As-built note (decision #130) — the read side of the diagram above had a real gap.** `StrategyOutcome rows (is_backtest=True, backtest_run_id)` above feeds into the *same* `strategy_outcomes` table live rows will eventually land in — one table, one schema, distinguished only by `is_backtest`. `GET /intelligence/strategy-outcomes` (decision #123, feeding "Recent Closed Trades" in `InfoTab.tsx`) shipped before this table had any writer at all, so it never filtered on `is_backtest` — harmless until decision #128 gave it a real writer. Decision #130 closed that gap:

```
                         strategy_outcomes  (one table, one schema)
                                   │
                  ┌────────────────┴────────────────┐
                  │                                   │
         is_backtest = False                 is_backtest = True
     (real, live-executed trades —          (Backtest Runner v1 output —
      no writer yet, table is honestly       decision #128, real rows
      empty of these today)                  today, not fabricated)
                  │                                   │
   GET /strategy-outcomes                  GET /strategy-outcomes
   (default — is_backtest omitted           ?is_backtest=true
    or explicit `false`)                    [&backtest_run_id=<run>]
                  │
                  ▼
   "Recent Closed Trades" (InfoTab.tsx)
   — can now never silently render a
     backtest row as a real closed trade
```

`performance_queries.py`'s `_common_filters()` (decision #122) already enforced this exact discipline for the two `GROUP BY` queries next to this route; decision #130 brings `/strategy-outcomes` in line with it via the equivalent single predicate, since this route's raw-row shape doesn't share `_common_filters()`'s `GROUP BY`-oriented signature. `backtest_run_id` is additive-only — it always requires `is_backtest=true` alongside it (a live row never carries one), enforced as a 400, not a silently-empty result.

**As-built note (frontend, this delivery) — `POST /backtest/run` (decision #131) gets a real caller for the first time.** Every note above this one describes the route itself; before this delivery the only way to invoke it was constructing a raw HTTP request by hand and reading raw JSON back — the route's own module docstring says as much explicitly ("this route does not add any Performance Analytics UI for inspecting results ... a caller wanting the raw persisted rows can already query the existing route separately"), a deliberate scope boundary at the time, not an oversight. This delivery closes that one specific, narrow gap and nothing else: a new `BacktestPanel.tsx`, mounted as a fourth collapsible sibling panel in `App.tsx` alongside `InfoTab`/`FeatureEnginePanel`/`ScannerPanel` (same collapsible-width convention `ScannerPanel.tsx` already established — see that component's own `MIN_WIDTH`/`MAX_WIDTH`/`COLLAPSED_WIDTH` constants, reused verbatim), lets a person pick one of the 7 real strategy names and one of the 4 real fixture scenarios, submit, and see the real response. *(Correction, decision #152 — this panel is no longer fixture-scenario-only; see this section's own as-built note at the end for the added "Real IBKR data" mode.)* *(Further correction, decision #163 — nor is it two-mode-only anymore; see this section's own as-built note near the end for the added "Sweep" mode.)*

```
BacktestPanel.tsx (form: strategy_name / scenario / symbol)
            │
            ▼
   POST /backtest/run                 ◄── fully synchronous; exact queue
   (useBacktestRun.ts)                     settlement, no live debounce wait
            │                              (elapsed state remains useful)
            ▼
   BacktestRunResult, rendered              spinner, while this is in
   verbatim: run_id / sweep_id /            flight
   outcomes_recorded / discarded_signals[]
            │
            ▼
   outcomes_recorded=0 renders as a plain, neutral fact — four of the
   seven strategies are structurally unreachable in any BacktestRunner
   replay today (this section's own note above), so a zero here is
   expected for many (strategy, scenario) pairs, not an error state.
   No link into "Recent Closed Trades" / Performance Analytics UI —
   explicitly out of scope, matching this route's own stated boundary.
```

Strategy names (`ORB`/`Gap`/`Volume Spike`/`FirstPullback`/`Reversal`/`Momentum`/`VWAP`) and scenario names/descriptions are hardcoded in `api-client.ts` rather than fetched at runtime — `default_registry()`/`available_scenarios()` are Python-only, not reachable over HTTP anywhere in this codebase, and exposing either would mean adding a new backend route or editing `scheduler.py`/`scenarios.py` directly, both outside this delivery's own frontend-only file boundary; the route's own 400 error body remains the live source of truth if either list ever drifts. The panel's own collapsed/width state is local component state, not threaded through `WorkspaceContext.tsx` the way Scanner/FeatureEngine panels' persisted, cross-tab-synced state is — a run's in-flight/finished state belongs to the one browser tab that started it and has no server-side push to sync from, so extending `WorkspaceContextValue`/`MainWindowState`'s localStorage schema for it would add persistence with no real use — flagged as a deliberate, reconsiderable choice in the component's own comment, not a silent deviation from the established pattern. No decision-log entry accompanies this note (see this delivery's own `CHANGES.md` for why); Saqib may fold it into a numbered decision at merge time if he wants one.

**As-built note (decision #133) — the read path finally gets a viewer.** Every note above this one describes either the write side (Backtest Runner v1 itself) or a trigger for it (`BacktestPanel.tsx`); nothing before this note rendered a single `StrategyOutcome` row. Decision #130's own diagram above ends at "`GET /strategy-outcomes` `?is_backtest=true[&backtest_run_id=<run>]`" with no consumer drawn past it — this delivery is that consumer, closing the read side the same narrow way `BacktestPanel.tsx` closed the trigger side: frontend-only, zero new backend surface, reusing what already exists rather than adding to it.

```
BacktestResultsPanel.tsx (free-text run_id filter, Apply/Clear)
            │
            ▼
   useBacktestOutcomes.ts             ◄── isBacktest FIXED true here, never
   (limit=500, backtestRunId?)             caller-toggleable — this hook's
            │                              whole reason to exist is showing
            ▼                              the one currently-nonempty half
   GET /intelligence/strategy-outcomes     of `strategy_outcomes`
   ?is_backtest=true[&backtest_run_id=…]
            │
            ├── 200, outcomes: []  ──────► honest "no backtest outcomes
            │                              recorded yet" / "no outcomes
            │                              for that run_id" — never an
            │                              error state
            │
            ├── 200, outcomes: [...]  ───► one row per StrategyOutcome,
            │                              summary fields always visible;
            │                              ▸ toggle expands the full
            │                              record in place (evidence /
            │                              market_state_at_*/context_at_*
            │                              blobs a summary row can't show)
            │
            └── 400 (malformed run_id —    surfaced as a distinct error
                the is_backtest=false +     state (useBacktestOutcomes.ts's
                backtest_run_id            own `error`), never rendered as
                combination this route     a silently-empty result — this
                itself 400s on is          panel's own is_backtest=true
                unreachable from this      fix means only a malformed UUID
                panel's own state          can reach this branch at all
                machine)
```

No new route, no change to `fetchStrategyOutcomes()`/`StrategyOutcomeWireShape` (both already covered every field this panel needed), no change to `useStrategyOutcomes.ts` (that hook's own comment already named this exact panel as its deferred, separate scope — this delivery is that deferral being picked up, not a reason to touch the hook it was deferred from).

**As-built note (decision #134) — the two Backtest panels stop needing a copy-paste between them.** Both panels above already worked independently; `useBacktestOutcomes.ts`'s own comment named the gap explicitly (see that file directly) — this delivery closes exactly that connection and nothing else. Frontend-only, no backend/API change. New shared state on `WorkspaceContext.tsx`/`MainWindowState` — `lastBacktestRunId` / `setLastBacktestRunId` — modeled directly on `featureEnginePanelSymbol`'s own established "one panel writes, a sibling reads" pattern (decision #48), not a new mechanism. Scoped per-Main-Window, same as `featureEnginePanelSymbol`, since both panels are mounted once per active-window shell (`App.tsx`), not globally or per-sub-window.

```
BacktestPanel.tsx (BacktestForm)              BacktestResultsPanel.tsx (BacktestResultsBody)
        │                                                    │
   run() → POST /backtest/run resolves                       │
   status === "done", result.run_id                          │
        │                                                    │
        ▼                                                    │
setLastBacktestRunId(result.run_id)                           │
        │                                                    │
        ▼                                                    │
  WorkspaceContext.tsx                                        │
  MainWindowState.lastBacktestRunId  ─── useWorkspace() ──────►
  (persisted via the existing                    │
   session-autosave path, same                   ▼
   as every other MainWindowState        mode === "auto"?
   field — normalizeMainWindow()            │           │
   back-fills it for pre-#134             yes           no
   sessions that predate it)                │           │
                                             ▼           ▼
                              runIdInput/appliedRunId   left exactly as the
                              synced to the new value   person set it — a
                                             │           run finishing
                                             ▼           elsewhere never
                          useBacktestOutcomes({ backtestRunId })  clobbers an
                          — its own [limit, backtestRunId]        in-progress
                          dependency array (unchanged by this     manual lookup
                          task) already refetches reactively,
                          so the just-finished run's rows
                          appear with no new fetch logic
```

**Internal flow within the changed module (`BacktestResultsBody`, inside `BacktestResultsPanel.tsx`)** — the "don't silently overwrite a manual lookup" requirement this task's own prompt called out explicitly, resolved as a small two-state mode machine local to this component:

```
                 mount (mode = "auto", seeded from
                 whatever lastBacktestRunId already is)
                              │
                              ▼
             ┌───────────►  AUTO  ◄──────────────────┐
             │      (runIdInput/appliedRunId follow    │
             │       lastBacktestRunId on every          │
             │       change, incl. new runs finishing     │
             │       while this panel is already open)     │
             │                    │                      │
             │   person clicks Apply OR Clear      person clicks
             │   (typing a run_id, or explicitly   "↺ Follow latest run"
             │    clearing to "show everything" —   (the only way back
             │    both are real, deliberate          to auto — collapsing/
             │    manual choices, both freeze)        re-expanding the panel
             │                    ▼                   also resets to auto,
             └────────────  MANUAL  ────────────────► since this component
                    (frozen: lastBacktestRunId          unmounts on collapse,
                     keeps changing in shared state       but that's not a
                     as new runs finish, but this          discoverable path,
                     panel's own filter no longer           so this control
                     follows it — exactly the                is explicit)
                     "don't clobber" requirement)
```

---

*(Correction, decision #135 — documentation only, no file under `frontend/` touched by this correction.)* The diagram in decision #128's own as-built note earlier in this section stated "four of the seven strategies are structurally unreachable in any BacktestRunner replay today" — decision #135 closed that specific gap; see the as-built note directly below for what changed and what didn't. `outcomes_recorded=0` remains an expected, non-error rendering for many (strategy, scenario) pairs regardless — see this section's own decision #135 note for why.

**As-built note (decision #135) — the historical-provider gap decision #128's diagram (above) described as a hard ceiling is closed.** `FeatureEngine`'s Daily Levels/ATR/RVOL refresh had never had a real `broker_registry.get_historical_provider()` to ask during a replay, so `volume_regime_score`/`volatility_regime_score` were always `0.0`, structurally, for any symbol. `historical_provider_guard.py` (new, mirroring `engine_singleton_guard.py`'s own save/install/restore shape exactly) now installs the run's own `MarketDataProvider` as that role for the replay's duration; `fixture_provider.py`'s `FixtureCandleProvider` — already the source of the replay's `(symbol, "1m")` feed — now also carries a `(symbol, "1d")` entry (`fixture_daily_history.py`, new: a synthetic, real-NYSE-calendar-aware trailing daily history) and serves both roles from one instance.

```
   Fixture Candle Data (FixtureCandleProvider, one instance, two roles)
   (symbol, "1m") — replay feed          (symbol, "1d") — NEW, decision #135
   (unchanged since decision #128)        (fixture_daily_history.py)
            │                                       │
            ▼                                       ▼
   BacktestRunner.run()                  broker_registry historical role
   candle-by-candle replay               install_replay_historical_provider()
   (unchanged)                           — new, nested INSIDE the existing
            │                            install_replay_engines() block,
            │                            same save/install/restore shape,
            │                            own asyncio.Lock
            │                                       │
            ▼                                       │
   FeatureEngine._maybe_refresh_daily_levels() ◄─────┘
   (real, unmodified — reads broker_registry.get_historical_provider()
    exactly as it always has; gets a real answer instead of None)
            │
            ▼
   self._daily_candle_cache[symbol] populated → rvol / atr_14_pct real
            │
            ▼
   MarketState.volume_regime_score / volatility_regime_score
   — no longer structurally 0.0 (decision #128's diagram above, corrected)
```

**Two real, checked-not-assumed safety properties of the seam — see `historical_provider_guard.py`'s own module docstring for the full reasoning.** (1) The installed provider is never `.connect()`ed, so `GET /market/candles` keeps returning its existing honest 400 throughout a backtest run — no silent replay-data leak into that live-facing route. (2) Decision #132 originally guarded Finnhub/Polygon only; the current implementation also checks registry-owned IBKR connections, closing the later-documented future-idea #25 gap. The IBKR historical acquisition connection is not registry-owned, disconnects before replay, and therefore does not weaken or bypass this guarantee.

**A genuinely separate finding, not fixed by this decision, documented loudly rather than silently patched or silently accepted — see `backtest.py`'s own route docstring for the full disclosure given to callers.** Closing this gap means `FeatureEngine`'s real, unmodified Daily Levels reconciliation now actually runs during a backtest for the first time — and it writes into the SAME shared `symbols`/`daily_levels_state` tables live trading reads, under whatever `symbol` label the caller supplies, with no `is_backtest` flag on either table to distinguish origin. Confirmed by direct execution against a real Postgres: a second run against the identical `(symbol, scenario)` pair silently reverts `volume_regime_score`/`volatility_regime_score` to `0.0` for that run — `_maybe_refresh_daily_levels()`'s own pre-existing "restart-survival" short-circuit finds the `daily_levels_state` row the first run just persisted and skips the raw-candle-cache population before ever asking the provider again. Flagged as a new open item below (D18), not folded into this decision — a genuinely separate change from "wire a historical provider."

---

**As-built note (decision #136) — `backtests`' own metadata finally gets a reader.** Every real backtest run since decision #128 has written a real row to `backtests` (run-level metadata: `sweep_id`, `strategy_name`, `strategy_version`, `config_hash`, `symbol_universe`, `date_range_start/end`, `data_version`, `feature_version`, `walk_forward_fold`, `is_holdout`, `created_at`) — but before this delivery, nothing had ever read one back. `GET /strategy-outcomes?backtest_run_id=X` (decision #130) shows what a run *produced*; nothing showed the run's *own* metadata without already having kept its `run_id` from a prior `POST /backtest/run` response. This closes that gap: a new `GET /intelligence/backtest-runs` route, backend-only (matching this project's own established backend-then-frontend sequencing — `performance_queries.py` at #122 before #127 exposed it; Context Engine's split at #98/#125 is the same shape). Both `BacktestRunRecord`'s (`app/models/trading_intelligence.py`) and `BacktestRun`'s (`app/schemas/performance.py`) own docstrings — and two further copies of the identical stale claim, one in each of those same files' module-level docstrings — still said "no Backtest Runner writes to this table yet (§7: not built now)" as of decision #128 landing; all four corrected in this delivery, along with one more copy of the same claim in `system-design.md` §4.13 ("backtests ... shape locked, not yet created").

**Not a collision with the historical-provider gap decision directly above** — that delivery's own footprint statement confirms `intelligence.py`/`performance_queries.py` untouched, and this delivery never touches anything under `backend/app/backtest_runner/` or `backend/app/api/routes/backtest.py`, confirmed by `diff -rq`. This entry was originally drafted as decision #135, citing that number throughout; the standing immediate-before-packaging re-check (this log's own established practice — #98/#99, #111/#112, #114/#115, #120/#121, #122/#123, #127/#128, #131, and the historical-provider delivery immediately above all needed exactly this) caught that the historical-provider session had already claimed and merged #135 first. Renumbered to #136 throughout — code docstrings, test file, this note — before packaging.

**Cross-component data flow — `BacktestRunner` → `backtests` → the new route → a caller:**

```
BacktestRunner.run()  (decision #128, backend/app/backtest_runner/runner.py)
        │
        ▼
_write_backtest_run_record(BacktestRunRecord(...))   ◄── written BEFORE any
        │                                                  StrategyOutcome row
        ▼                                                  (backtest_run_id FK
   backtests  (Postgres table, migration 0008)              ordering requirement)
        │
        │   real rows exist here since #128 — this delivery adds the
        │   FIRST reader, nothing before it ever queried this table
        ▼
GET /intelligence/backtest-runs                       ◄── decision #136, THIS
  ?run_id=<uuid>                                            delivery
  &strategy_name=<name>
  &sweep_id=<uuid>
  &limit=<n>
        │
        ▼
{"backtest_runs": [BacktestRun, ...]}                  ◄── existing Pydantic
                                                             contract (decision
                                                             #89), reused as-is
        │
        ▼
A caller with only a run_id (e.g. a future frontend        ◄── no frontend
reading WorkspaceContext.tsx's lastBacktestRunId,               caller wired
decision #134 — NOT built here, deliberately) can now            this round —
resolve that run's own metadata without having kept            see the note
the original POST /backtest/run response around.                 above
```

**Internal flow within the changed module (the route itself, `app/api/routes/intelligence.py`):**

```
GET /intelligence/backtest-runs?run_id=...&strategy_name=...&sweep_id=...&limit=...
        │
        ▼
Parse run_id / sweep_id as UUID, if given
        │
        ├── malformed ──► 400 ("... is not a valid UUID")   ◄── same posture
        │                                                        GET /strategy-
        ▼                                                        outcomes takes
Build filters list (AND, never blended):                         for a malformed
  run_id       → BacktestRunRecord.run_id == run_uuid             backtest_run_id
  strategy_name → BacktestRunRecord.strategy_name == strategy_name
  sweep_id     → BacktestRunRecord.sweep_id == sweep_uuid
        │
        ▼
SELECT * FROM backtests WHERE <filters> ORDER BY created_at DESC LIMIT :limit
        │
        ▼
For each row: BacktestRun.model_validate(row, from_attributes=True)
        │            ◄── existing Pydantic contract (decision #89),
        │                reused as-is, not reshaped
        ▼
{"backtest_runs": [...]}         ◄── [] on a genuinely empty/no-match
                                       result, 200, never an error —
                                       same convention every route in
                                       this file already follows
```

No `performance_queries.py` change — that module's own docstring scopes it strictly to `GROUP BY` aggregations over `strategy_outcomes` ("two real queries, exactly two"); this is a raw recent-rows read over a different table, with no aggregation and no grouping key, exactly GET /strategy-outcomes' own shape, not that module's. Built inline in `intelligence.py`, same file and pattern as its closest sibling, rather than introducing a second read-side module for one un-aggregated query.

---

**As-built note (decision #139) — `backtests`' own metadata reaches the frontend for the first time.** Decision #136 (directly above) built the reader but shipped backend-only, deliberately, naming this exact next step in its own docstring and diagram: "a caller with only a run_id (e.g. ... `WorkspaceContext.tsx`'s `lastBacktestRunId`, decision #134) can now resolve that run's own metadata" and "showing a selected run's own metadata alongside `BacktestResultsPanel.tsx`'s outcome rows (decision #133/#134) is the natural next step this leaves open." This closes it. Frontend-only — `GET /intelligence/backtest-runs` itself, `intelligence.py`, and every backend file are unchanged.

New `frontend/src/hooks/useBacktestRuns.ts` — deliberately a NEW, separate hook from `useBacktestOutcomes.ts`, not an extension of it: that hook reads `strategy_outcomes` (one row per closed trade a run produced); this one reads `backtests` (one row per run's own settings) — a different table, a different granularity, related only via `run_id`/`backtest_run_id`. Naming mirrors that hook's own, the same pairing precedent `useStrategyOutcomes`/`useBacktestOutcomes` already set. Exposes only `runId`, even though the underlying `fetchBacktestRuns()` also supports `strategyName`/`sweepId`/`limit` (matching the route's full filter set) — this hook's one real caller (`BacktestResultsPanel.tsx`) only ever has a run_id to resolve against, so exposing the rest would be building ahead of an actual need. Deliberately skips the fetch entirely when `runId` is undefined — unlike `useBacktestOutcomes.ts` (which still fetches "everything" with no `backtestRunId` filter), there is no single run's metadata to show in that state. `run_id` is `BacktestRunRecord`'s own primary key (confirmed against `backend/app/models/trading_intelligence.py`), so resolving `backtest_runs[0] ?? null` is safe — never an arbitrary pick from a genuine multi-row list. Same caller-visible `error`-distinct-from-empty posture `useBacktestOutcomes.ts`/`usePerformanceAnalytics.ts` already established: a malformed (non-UUID) run_id — reachable here since this panel's filter is free-text — surfaces as `error`, never conflated with a well-formed, genuinely-unmatched run_id.

New `BacktestRunWireShape` / `BacktestRunsWireShape` / `fetchBacktestRuns()` in `api-client.ts`, matching `StrategyOutcomeWireShape`/`fetchStrategyOutcomes`'s own established shape and pattern exactly — same conditional-append query construction, same `ApiError`-on-non-2xx posture, field names/types copied directly from `schemas/performance.py`'s `BacktestRun` (re-verified against that file's current contents, not guessed).

New `RunMetadataCard` inside `BacktestResultsPanel.tsx` — reuses `BacktestResultsBody`'s existing `appliedRunId` (decision #134's own auto/manual filter-mode state, entirely unchanged) with zero new shared state: `WorkspaceContext.tsx` is untouched, since `appliedRunId` already carries the exact run_id linkage both data sources need. Rendered only when `appliedRunId` is set — the panel's "everything" default view (no run_id filter applied) shows no metadata card, since there is no single well-defined run to describe in that state. Independent loading/error/empty rendering from the `StrategyOutcome` rows list beside it — a metadata-fetch failure never renders identically to "this run has no outcomes," and vice versa, the same never-conflate-two-data-sources'-honest-state discipline this panel's own outcomes-list error handling already established.

```
BacktestResultsBody (BacktestResultsPanel.tsx)
        │
        │  appliedRunId  (decision #134's own auto/manual state —
        │                 unchanged, no new WorkspaceContext field)
        ▼
useBacktestRuns({ runId: appliedRunId })       ◄── NEW, this delivery
        │
        │  skips the fetch entirely when appliedRunId is undefined
        ▼
fetchBacktestRuns(undefined, runId)            ◄── NEW, api-client.ts
        │
        ▼
GET /intelligence/backtest-runs?run_id=<uuid>   ◄── decision #136,
        │                                            unchanged
        ▼
{"backtest_runs": [BacktestRun] | []}
        │
        ▼
backtestRun = backtest_runs[0] ?? null         ◄── safe: run_id is
        │                                           BacktestRunRecord's
        ▼                                           own primary key —
RunMetadataCard renders strategy_name/version,       0 or 1 row, never
config_hash, symbol_universe, date_range,            an arbitrary pick
data_version/feature_version, walk_forward_fold,
is_holdout, created_at — alongside, never merged
with, the existing StrategyOutcome rows list below,
both keyed to the same appliedRunId
```

**Internal flow within the changed module (`BacktestResultsBody`, inside `BacktestResultsPanel.tsx`):**

```
appliedRunId changes (Apply / Clear / auto-follow —
all pre-existing, decision #134, unchanged by this delivery)
        │
        ▼
   appliedRunId === undefined?
        │                     │
       yes                    no
        │                     │
        ▼                     ▼
 no RunMetadataCard    RunMetadataCard mounts/refetches
 rendered — nothing    (useBacktestRuns's own [runId]
 specific to describe  dependency array, mirroring
 in this state         useBacktestOutcomes.ts's shape)
                              │
                              ▼
                    loading?  ──yes──► "Loading run metadata…"
                              │ no
                              ▼
                    error?    ──yes──► "Failed to load run metadata: …"
                              │ no
                              ▼
                    backtestRun === null?
                              │            │
                             yes           no
                              │            │
                              ▼            ▼
                   "No run metadata    render fields grid (strategy_name,
                   found for run_id     strategy_version, config_hash,
                   <id>."               sweep_id, symbol_universe,
                                        date_range, data_version,
                                        feature_version, walk_forward_fold,
                                        is_holdout, created_at)
```

**As-built note (decision #141) — D19 isolates Daily Levels state per backtest run without reopening D18.** Decision #140 correctly separated live and backtest symbol/Daily Levels rows, but direct execution found that two distinct runs of the same ticker still shared the one `is_backtest=True` active-row pool: run 2 reused all ten of run 1's database IDs and `level_id` values and advanced every row's `updated_at`. Migration `0010` adds nullable `daily_levels_state.backtest_run_id`; a database CHECK requires exactly `(live, NULL)` or `(backtest, non-NULL)`, and a real FK targets `backtests.run_id`. `BacktestRunner` already creates the UUID before constructing `EngineBackedReplayStateProducer`, so the identity now flows into `FeatureEngine` before any Daily Levels write. Reconciliation selects only the current run's active rows. The existing `(symbol_id, is_backtest, level_id)` unique constraint remains unchanged; the old `(symbol_id, status)` index is replaced by `(symbol_id, is_backtest, backtest_run_id, status)`, matching the actual predicate.

```
BacktestRunner.run()
  run_id = uuid4()
        │
        ▼
EngineBackedReplayStateProducer(backtest_run_id=run_id)
        │
        ▼
FeatureEngine(is_backtest=True, backtest_run_id=run_id)
        │
        ├── provider fetch still runs every replay
        │   (#140 restart-survival skip unchanged)
        ▼
daily_levels_state
  symbol_id + is_backtest + backtest_run_id + status
        │
        ├── run A sees only run A active rows
        └── run B sees only run B active rows
```

```
DailyLevelState write
        │
        ▼
CHECK origin/run pairing
  live     ──► is_backtest=false AND backtest_run_id=NULL
  backtest ──► is_backtest=true  AND backtest_run_id=<UUID>
        │
        ▼
FK backtest_run_id ──► backtests.run_id ON DELETE CASCADE
        │
        └── deleting a run removes only its derived Daily Levels rows
```

The cascade is deliberate and narrower than `strategy_outcomes.backtest_run_id`'s migration-`0008` FK, which has the default `NO ACTION`: outcomes are durable analytical records, while these Daily Levels rows are derived run checkpoints. Current `backend/app` has no code path that deletes a `backtests` row, and no table has an FK to `daily_levels_state.id`, both confirmed directly before choosing the cascade. Migration `0010` deletes only pre-migration `is_backtest=True` Daily Levels rows because no honest run UUID can be reconstructed for them; it does not touch Market State, Level Interaction, candles, scanner-universe, fundamentals, or symbols. `_load_confirmed_daily_levels_for_today()` remains unreachable for backtest engines under #140's gate, but its query includes `backtest_run_id` anyway so a future restart-survival re-enable cannot silently restore another run's checkpoint.

**As-built note (decision #145) — real IBKR historical OHLCV is now a sibling replay path, not a replacement for fixtures.** `POST /backtest/run` and its frontend caller remain unchanged and continue to use named `FixtureCandleProvider` scenarios for deterministic regression. `POST /backtest/run/ibkr` accepts one symbol and timezone-aware `start`/`end`, fixes the replay timeframe at `1m`, enforces exact `[start,end)` semantics, and rejects a user interval over 24 elapsed hours rather than clamping it. The cap does not apply to Feature Engine's additional history and can be reconsidered when replay performance improves or a background-job model exists.

The route reads the configured lookbacks rather than copying constants: the current five-session premarket baseline requests 15 calendar days of auxiliary `1m` bars, and Daily Levels/ATR/RVOL request 180 calendar days of `1d` bars. Minute acquisition uses `TRADES`, `useRTH=False`; daily acquisition uses `TRADES`, `useRTH=True`. One-minute requests are serial one-day chunks with no automatic retry, normalized to timezone-aware UTC, sorted, deduplicated at overlaps, conflict-checked, and finally filtered to the requested interval. A normal IBKR historical completion proves protocol completion, not that every wall-clock minute traded; closed periods and halts are not fabricated into a gap-free grid. Zero primary bars is an error.

```
POST /backtest/run/ibkr
        │
        ▼
validate symbol / aware datetimes / exact [start,end) / 24h user cap
        │
        ▼
live-provider safety check ── Finnhub / Polygon / registry-owned IBKR
        │
        ▼
isolated IBKRAdapter(client_id=IBKR_BACKTEST_CLIENT_ID, readonly=True)
  no broker_registry role · no TickIngestBridge · no subscriptions/orders
        │
        ├── serial 1m TRADES, useRTH=False
        │     primary range + configured prior-session premarket span
        └── 1d TRADES, useRTH=True
              configured Daily Levels / ATR / RVOL span
        │
        ▼
normalize UTC → exact-filter → sort → deduplicate/conflict-check
        │
        ▼
disconnect IBKR in finally
        │
        ▼
PreloadedHistoricalCandleProvider (real data, permanently disconnected)
        │
        ▼
repeat live-provider safety check → BacktestRunner replay
        │
        ▼
BacktestRunRecord / StrategyOutcomeRecord persistence
```

Acquisition errors are stable and explicit: unresolved contract and zero primary data are `400`; live-provider conflict is `409`; malformed request/range is `422`; configuration, permission, pacing, connection and disconnect failures are `503`; timeout is `504`; malformed/conflicting/incomplete upstream data is `502`. `ib_async` request errors and error/disconnect events are both observed because not every IBKR condition becomes a normal exception. All acquisition and validation precede `BacktestRunner.run()`, so a failed download cannot leave a `BacktestRunRecord`.

`data_version="ibkr:TRADES:1m-ext:1d-rth"` records the meaningful vendor/request semantics without pretending IBKR publishes an immutable dataset version. `FixtureBacktestContextProvider` remains in use: market-calendar context is replay-safe, while historical point-in-time fundamentals and news remain honestly absent. As of decision #157, replay no longer waits approximately one second per primary candle; the route remains synchronous and IBKR acquisition time varies independently.

**As-built note — 2026-09-18, first attempted real-market-data execution (no code changed).** Saqib asked for a real, complete `BacktestRunner` run against real market data (not fixtures, not the test suite), specifically to give D4's readiness check (`strategy-engine-open-decisions.md`) something to find. This note is the honest record of that attempt, which did not succeed — no `strategy_outcomes`/`backtests` rows were produced, and D4 remains open.

*Environment, provisioned fresh this session:* PostgreSQL 16 installed natively (`apt-get install postgresql`; the repo's own `docker-compose.yml` Postgres image was not reachable — no container registry in this sandbox's network allowlist), `trading` superuser + `trading_workspace` DB created per this doc's own convention, `alembic upgrade head` applied cleanly to `0010` (matching decision #141's current head) with **zero errors**, backend started via `uvicorn`. Confirmed empty before any attempt: `strategy_outcomes` 0 rows, `backtests` 0 rows, `candles` 0 rows (`is_backtest=false` — no live session has ever run here, so there is no self-recorded real history to fall back on either, see `candle_store.py`). `symbols` had 6 rows from the scanner-universe seed migration only.

*Attempt:* with Finnhub/Polygon/IBKR all confirmed `connected: false` (so decision #132's live-data guard would not block the call), `POST /backtest/run/ibkr` was called directly — `strategy_name=ORB`, `symbol=AAPL`, a real 2-hour `[start,end)` window inside the 24-hour cap. Result:

```
POST /backtest/run/ibkr?strategy_name=ORB&symbol=AAPL&start=...&end=...
        │
        ▼
_reject_if_live_data_connected()  ── passed (all three providers disconnected)
        │
        ▼
acquire_ibkr_replay_data()
        │
        ▼
IBKRAdapter.connect(127.0.0.1:4002)  ── Connection refused
        │
        ▼
503 {"code": "ibkr_connection_unavailable",
     "message": "Could not connect to IB Gateway/TWS at 127.0.0.1:4002:
                  [Errno 111] Connection refused"}
```

This is exactly the gap `ibkr_adapter.py`'s own module docstring and decision #131's own verification notes already named ("this sandbox has no path to a running IB Gateway," "No live Gateway, TWS, account, entitlement, or IBKR response was verified in this sandbox") — not a new finding, but the first time it's been confirmed empirically through this specific route rather than stated as a standing caveat. A locally-run IB Gateway/TWS is a stateful desktop application requiring real IBKR account credentials; nothing in this sandbox's network egress configuration would change that, since the failure is a refused loopback TCP connection to a process that doesn't exist here, not a blocked outbound domain.

*Checked and ruled out as alternatives, not just assumed unavailable:* direct `curl` to `https://api.polygon.io` and `https://finnhub.io` both returned `403` with `x-deny-reason: host_not_allowed` — this sandbox's network allowlist has no market-data-provider domains in it (confirmed against the actual configured allowlist, not inferred). Moot regardless: neither `FINNHUB_API_KEY` nor `POLYGON_API_KEY` is set, and — more fundamentally — no existing code path replays a `BacktestRunner` run through either provider; `POST /backtest/run/ibkr` is the only real-market-data replay route this codebase has. Building one would be new scope, not something this task's own "use what already exists" instruction covers.

*Conclusion, stated plainly:* this environment cannot currently produce a real-market-data `BacktestRunner` execution. Nothing was worked around to manufacture rows — `strategy_outcomes`/`backtests` are exactly as empty now as before this attempt, and D4's readiness check still finds zero. Real resolution needs one of: (a) this sandbox given network access to a market-data provider's domain plus a real API key, which by itself still wouldn't produce IBKR data specifically and would need a new non-IBKR replay route built first; (b) `POST /backtest/run/ibkr` run from an environment with a real, reachable IB Gateway/TWS session (Saqib's own machine, per `ibkr_adapter.py`'s own standing caveat) — the fastest path to literally what was asked for, using code that already exists and already works, just never against a reachable Gateway; or (c) accepting a fixture-based real-`trading_workspace`-DB run instead, which is real persistence but not real market data, and wasn't what was asked for here. No code changed as part of this note — decision purely deferred to Saqib.

**As-built note (decision #152) — `BacktestPanel.tsx` stops being fixture-scenario-only.** The as-built note above this section's earlier `POST /backtest/run` diagram (frontend delivery, decision #131) described the only trigger UI that existed at the time — see the correction inline at that note. `POST /backtest/run/ibkr` (previous as-built note, this section) has been reachable from the backend since its own delivery but, until now, only by hand-constructing an HTTP request, the same gap decision #130's own delivery closed for the fixture route. This delivery adds a second mode to the same panel rather than a second panel, confirming directly against `runner.py` (not assumed) that both routes return `dataclasses.asdict()` of the identical `BacktestRunResult` — so the existing `ResultsView` renders either mode's result unchanged. *(Correction, decision #163 — a third "Sweep" mode joins these two below; see this section's own as-built note near the end.)*

```
BacktestPanel.tsx (BacktestForm) — mode: "Fixture scenario" | "Real IBKR data"
strategy_name / symbol shared across both modes; scenario picker shown
only in fixture mode, Eastern-time date-range shown only in ibkr mode
            │
            ├── Fixture scenario ──► useBacktestRun.ts (unchanged) ──►
            │                        POST /backtest/run — plain-string
            │                        error message; synchronous fast replay
            │
            └── Real IBKR data ───► two ET datetime-local inputs (+
                                     "Regular session"/"Extended session"
                                     presets, anchored to the date already
                                     entered or today's ET calendar date —
                                     never asserted as a real trading day)
                                             │
                                             ▼
                                     easternTime.ts: DST-aware
                                     America/New_York → UTC conversion
                                     (Intl.DateTimeFormat, no new
                                     dependency), round-trip verified —
                                     see its own header comment for a
                                     real false-negative bug this
                                     round-trip check caught and fixed
                                     during implementation, not just a
                                     defensive check that never fires
                                             │
                                             ▼
                                     client-side start<end / 24h-cap
                                     check — a doomed request is never
                                     sent, but backend's own
                                     _validate_ibkr_range stays the real
                                     authority (no market-calendar/
                                     holiday logic added here)
                                             │
                                             ▼
                                     useIbkrBacktestRun.ts — new sibling
                                     hook, not a mode branch inside
                                     useBacktestRun.ts (materially
                                     different request shape, error
                                     taxonomy, and acquisition behavior
                                     timing profile; small duplicated
                                     timer/status-machine mechanics kept
                                     deliberately obvious rather than
                                     factored into a shared abstraction)
                                             │
                                             ▼
                                     triggerIbkrBacktest() — new,
                                     purely additive wrapper +
                                     IbkrBacktestError in api-client.ts;
                                     parseErrorDetail()/ApiError and
                                     every existing caller untouched
            │
            ▼ (either path)
   BacktestRunResult, rendered verbatim by the same ResultsView:
   run_id / sweep_id / outcomes_recorded / discarded_signals[]
```

`IbkrBacktestError` is the first place in this codebase where a route's `detail` is an object (`{code, message}`) rather than a plain string — confirmed directly against `backtest.py`'s `_validate_ibkr_range`/`_ibkr_backtest_client_id` and `ibkr_historical.py`'s full `IBKRHistoricalAcquisitionError` hierarchy, not assumed from the route's docstring alone. Rather than widen the shared `parseErrorDetail()` (which would touch every existing caller's contract for a shape only this one route produces), a dedicated parser and error class carry `code` alongside `message`, so the panel can classify every real failure this route returns with a specific heading instead of a blended or generic one:

```
Start/End (ET) inputs, presets                 submit
        │                                          │
        ▼                                          ▼
etWallClockToUtc() each side          triggerIbkrBacktest() →
        │                              (success | IbkrBacktestError)
        ├── parse/round-trip fail                   │
        │   → inline message,                       ▼
        │     Run disabled                classifyIbkrBacktestError(status, code)
        │                                  409                        → live-data guard
        ▼ both sides convert              invalid_backtest_request    → invalid date/symbol/range
start < end AND window ≤ 24h ?            ibkr_backtest_not_configured→ not configured
        │                                 ibkr_contract_unresolved    → symbol unresolved
        ├── no → inline message,          ibkr_no_data                → no historical data
        │        Run disabled             ibkr_historical_permission_denied → permission denied
        │                                 ibkr_historical_pacing_rejected   → pacing/rate-limit
        ▼ yes                             ibkr_historical_timeout     → timed out
   Run enabled                            ibkr_malformed_response /
                                           ibkr_incomplete_response    → malformed/incomplete data
                                           anything else (incl. no code) → generic fallback heading
                                                          │
                                                          ▼
                                           backend's own message shown verbatim underneath
                                           every heading — never a stack trace or raw object
```

While a run is in flight, the panel shows live elapsed time and wording that the request can legitimately take up to ~16 minutes with no progress percentage available (the backend gives this synchronous route no progress signal to show one). Mode switching and the Run button are disabled whenever either mode's own hook reports `"running"` — both routes share the backend's single `_RUN_LOCK`, so this only prevents a wasted duplicate long-running request from this tab, not a real backend race. Every render block below `BacktestForm`'s own submit button is gated on `mode === "fixture" | "ibkr"` together with that mode's own hook state specifically (never a merged "whichever finished last" view), so a result or error from one mode can never render while the other mode is selected. Fixture mode's own copy, timing framing, and `useBacktestRun.ts` itself are byte-for-byte unchanged.

Frontend-only: `BacktestPanel.tsx`, new `easternTime.ts` and `useIbkrBacktestRun.ts`, and an additive-only block appended to `api-client.ts`. `useBacktestRun.ts`, `useMarketState.ts` (the parallel decision #146 delivery's own boundary, confirmed untouched by both sides), `BacktestResultsPanel.tsx`, `useBacktestOutcomes.ts`, and everything under `backend/` are unchanged.

---

**Deterministic fast replay foundation (decision #157).** Decision #155 measured two coupled defects in the original replay path: each candle after the first waited for Market State's live one-second debounce floor, and persisted Acceleration divided by the wall-clock processing gap. Faster replay therefore changed strategy input. The corrected path keeps the real engines and their authoritative worker/persistence/event flow while making market-data time authoritative and giving the run-scoped backtest engine an exact immediate-settlement control.

Cross-component data flow:

```
BacktestRunner
      │ each historical candle
      ▼
EngineBackedReplayStateProducer
      │ CandleClosed + exact bus/worker drains
      ▼
FeatureEngine ── FeaturesUpdated(1m, source candle_ts)
      │
      ▼
MarketStateEngine.settle_replay(symbol)
      │ replay-only flush + worker queue drain
      │ compute → persist → cache → MarketStateChanged
      ▼
exact Feature / Market State / Context timestamp verification
      │
      ▼
Strategy.evaluate()  (unchanged live/replay implementation)
```

Internal synchronization flow:

```
NORMAL LIVE PATH                              REPLAY FORCE/FLUSH PATH
FeaturesUpdated(1m)                          FeaturesUpdated(1m)
       │                                            │
       ▼                                            ▼
DebounceScheduler.trigger()                  DebounceScheduler.trigger()
       │                                            │
       ├─ floor clear → callback now                ├─ first → callback already queued
       └─ inside 1s → one delayed task              └─ rapid → pending delayed task
       │                                            │
       │                                      settle_replay(symbol)
       │                                            │
       │                                      scheduler.flush()
       │                                      ├─ pending check under same lock
       │                                      ├─ callback exactly once if pending
       │                                      └─ cancel + await delayed task
       │                                            │
       └──────────────────────┬─────────────────────┘
                              ▼
                    MarketState worker queue
                              │ queue.join(): compute complete
                              ▼
        score using Δ source candle_ts (positive only)
                              │
                              ▼
                  persistence → cache → publication
                              │
                              ▼
             bus queues drain / subscriber cache updated
                              │
                              ▼
 Feature.candle_ts == MarketState.candle_ts == requested candle_ts
 Context provider cursor == requested candle_ts; mismatch/missing = hard failure

Live ceiling loop: unchanged (~10s ordinary, ~4s SPY/QQQ/IWM).
Replay ceiling loop: not started; explicit candle advances are authoritative,
so a periodic callback cannot duplicate the latest replayed candle.
Shutdown/exception: scheduler.stop() cancels and awaits any delayed task before
the Market State poison-pill worker drain returns.
```

The replay producer no longer polls every 50ms and no longer exposes settle-timeout/poll constructor arguments. A delayed debounce task cannot later duplicate the flushed candle because delayed and forced paths consume the same `_pending` flag under one scheduler lock. The real Market State worker remains authoritative: no scoring or persistence logic was copied into Backtest Runner, and cache-before-publish plus `MarketStateChanged` ordering is unchanged.

**Measured replay duration, local evidence only.** Decision #155's prior `volume_gated_baseline` measurement replayed 119 of 120 fixture candles in 118.16s (1 vCPU/4GB sandbox, Python 3.12.3, PostgreSQL 16.15, `fsync=off`). The same existing route test after this change replayed the same 119 candles in 1.97s process wall time (pytest body 1.47s) on WSL2 with 12 logical CPUs, 7.7GiB RAM, Python 3.14.4, PostgreSQL 18.6, `fsync=on`, `synchronous_commit=on`, 128MB shared buffers. The environments differ, so this is evidence that the per-candle sleep disappeared, not a universal speedup claim.

**Intentional strategy effect.** Momentum is the only strategy that consumes `acceleration_score`. On `volume_gated_baseline`, decision #155's wall-clock semantics produced one BUY; source candle time now yields acceleration 50.00–54.22, below Momentum's 65 threshold, so the same replay honestly produces zero outcomes. First Pullback and the existing route/regression scenarios retain their asserted outcomes. The cap was not retuned: it already has points-per-second units, and consecutive 1m candle timestamps make its documented full-swing-in-60-seconds calibration internally consistent.

This delivery enables later batch/sweep work but does not implement an orchestrator, parameter generation, ranking, folds, promotion, background jobs, progress/cancellation, multi-symbol replay, or parallel execution. Remaining scaling constraints are explicit: process-wide replay locks still serialize runs, both HTTP routes remain synchronous, real IBKR acquisition remains external I/O, and historical fundamentals/news remain unavailable.

Decision #155's final-candle observation is confirmed and deliberately not changed: fixture providers implement `[start,end)`, while the route and the named regression runs pass `end=candles[-1].candle_ts`; therefore a 120-candle fixture replays exactly 119 candles. Exact settlement now proves every candle *included by the provider* persists once. Changing the caller boundary would add a separate candle and can change signals/fills, so it remains a separate behavioral task.

---

**As-built note (decision #159) — `POST /backtest/sweep`: the real batch caller `sweep_id` was minted for since v1 (decision #128; #155's own "a sweep of one" note).** Decision #157 (directly above) closed the one thing that made batch runs impractical (each run taking one to several minutes) without touching the one thing that makes concurrent runs unsafe (`engine_singleton_guard._RUN_LOCK`, process-wide by design) — so the right v1 shape is a synchronous, sequential loop, confirmed practical here: 3 symbols × 2 scenarios (6 real runs, real Postgres) completed in **4.36s wall clock**, matching the ~2s/run single-run figure decision #157 reported.

**Scope, confirmed with Saqib before implementation.** Fixture scenarios only — deliberately excludes `POST /backtest/run/ibkr`'s real-data path (a single IBKR acquisition already costs real minutes, per decision #145; looping that synchronously would be impractical). Explicit cross-product of an explicit `symbols` list × explicit `scenarios` list, both required — no implicit "all known symbols"/"all scenarios" expansion. Batch bound: `len(symbols) * len(scenarios) <= 20`, checked before any run starts. 20 was sized off the measured ~2s/run figure (20 runs ≈ 40s, comfortably under common default gateway timeouts) — a bound on *requested* pairs, not successful ones, and a measured fact about this environment, not a guaranteed production runtime.

Cross-component data flow:

```
POST /backtest/sweep(strategy_name, symbols[], scenarios[])
              │
              ▼
   pre-execution validation (all before any run starts)
   ├─ _validate_strategy_name()        ◄── reused from /backtest/run, unchanged
   ├─ _validate_scenario() per unique scenario  ◄── reused, unchanged
   ├─ symbols normalized (.strip().upper()), reject empty
   │    (no real symbol registry exists to validate against — confirmed
   │     directly: /backtest/run's own `symbol` is documented as "an
   │     arbitrary label ... not a real ticker lookup," same here)
   ├─ len(symbols) * len(scenarios) <= 20 (_MAX_SWEEP_PAIRS)
   └─ _reject_if_live_data_connected()  ◄── decision #132's guard, reused,
                                             checked once — see reasoning below
              │
              ▼
   sweep_id = uuid4()          ◄── minted once, shared by every run below
              │
              ▼
   for (symbol, scenario) in itertools.product(symbols, scenarios):
   ordering: symbols outer / scenarios inner — preserved exactly in the
   response, never database/collection order
              │
              ├─► fresh Strategy instance (fresh default_registry() call,
              │    same as /backtest/run does per-request — see internal
              │    flow below for why "fresh" matters)
              ├─► fresh FixtureCandleProvider (load_scenario_candles(scenario)
              │    reads its fixture CSV fresh each call — no shared
              │    mutable state across pairs)
              ├─► BacktestRunner(..., sweep_id=sweep_id)  ◄── the one new
              │    constructor param; every other kwarg matches /run exactly
              └─► await runner.run()   ◄── same call, same _RUN_LOCK,
                   try/except per pair — see partial-failure note below
              │
              ▼
   BacktestSweepResult: sweep_id, strategy_name, pairs_requested,
   pairs_succeeded, pairs_failed, runs[] (one SweepPairResult per pair,
   in request order — symbol, scenario, run_id | None, outcomes_recorded
   | None, discarded_signals, error | None)
```

**Execution model — reuses `/backtest/run`'s own path exactly, one pair at a time.** No second locking mechanism: each pair acquires and releases the identical process-wide `_RUN_LOCK` `/backtest/run` already uses, via the identical `install_replay_engines()` call inside `BacktestRunner.run()`. True parallelism was never on the table — that lock's own docstring explains why concurrent runs in one process are unsafe by design (singleton engine installation would let one run's in-flight capture calls see another run's state). Proved empirically, not just asserted: a test wraps the real `install_replay_engines` context manager with a timing spy (still delegating to the real implementation — the real lock, the real engines) and confirms the recorded `[enter, exit]` intervals across a multi-pair sweep never overlap.

Internal loop flow, and the one correctness finding that shaped it:

```
for (symbol, scenario) in pairs:
        │
        ▼
  default_registry(now) ──► pick strategy_name match ──► FRESH instance
        │                                                     │
        │            all 7 real strategies confirmed to hold
        │            self._state: dict[str, ...] KEYED PER SYMBOL
        │            (ORB, Gap, Volume Spike, FirstPullback, Reversal,
        │             Momentum, VWAP — checked directly, not assumed)
        │                                                     │
        │      reusing ONE Strategy instance across pairs would let
        │      one pair's state (e.g. ORB's opening-range candle
        │      count) leak into a later pair reusing the same symbol
        │      against a different scenario — a fresh instance per
        │      pair closes that off entirely
        ▼
  BacktestRunner(..., sweep_id=shared) ──► await run()
        │
        ├─ success ──► SweepPairResult(run_id, outcomes_recorded,
        │               discarded_signals, error=None)
        │
        └─ Exception ──► logged, SweepPairResult(run_id=None,
                          outcomes_recorded=None, error=str) — loop
                          CONTINUES to the next pair; already-collected
                          results are preserved. Matches an existing,
                          real convention in this codebase for
                          independent-item batches (FeatureEngine's
                          worker loop: "one bad symbol/candle must not
                          stall the other ~100"; websocket/manager.py's
                          broadcast: "a dead socket must not break the
                          broadcast") — applied here, not invented fresh.
```

**A second, deeper finding surfaced during verification — not a sweep defect, but real and worth recording here.** Running the *same symbol* through two separate real runs — whether two pairs in one sweep, or two separate calls to the existing, unmodified `POST /backtest/run` — can legitimately produce different `outcomes_recorded` between them. Confirmed directly: calling `/backtest/run` twice in a row against one fresh symbol, no sweep code involved at all, reproduced the identical discrepancy (1, then 0). Root cause, confirmed by reading the models rather than assumed: `daily_levels_state` got per-run isolation via `backtest_run_id` (decision #141/D19), but `LevelInteractionState`/`LevelInteractionEvent` (`app/models/trading_intelligence.py`) never did — `LevelInteractionState`'s own unique constraint is `(symbol_id, timeframe, level_key)`, with no `backtest_run_id` column at all, so a second run of the same symbol inherits real leftover touch/resolution state from the first. This is a pre-existing `BacktestRunner` characteristic that predates this delivery and applies equally to single runs; the sweep endpoint is simply the first caller likely to request the same symbol twice in quick succession. **Not fixed here** — out of this task's scope, and a fix would mean giving `level_interaction_state` the same per-run-isolation treatment D19 gave `daily_levels_state`, a change of that same shape and size. `docs/architecture/strategy-engine-open-decisions.md` is owned by a parallel session for this delivery and wasn't touched; flagged to Saqib directly to route as he judges best (a new D-item, most likely).

**As-built note (decision #163) — `POST /backtest/sweep` gets a real caller, and its own results become browsable, for the first time.** Every note above this one describes the sweep route itself, built and tested backend-only; before this delivery the only way to invoke it was constructing a raw HTTP request by hand, and the only way to see a sweep's own results as a group afterward was copy-pasting each individual `run_id` from that raw response — `sweep_id` was already visible per-row in `BacktestResultsPanel.tsx` (decision #133/#136/#139) but not usable as a filter, the exact shape of gap this project has repeatedly closed for `run_id` itself and for several other backend-built, UI-invisible capabilities before it. This delivery closes both ends: a third trigger mode in `BacktestPanel.tsx`, and a new filter type in `BacktestResultsPanel.tsx`. Frontend-only — `backtest.py`'s sweep route, `intelligence.py`, and every other backend file are unchanged.

**Trigger side — `BacktestPanel.tsx` stops being two-mode-only.** A third "Sweep" mode joins the existing tab toggle, following #152's own precedent for adding a mode to the existing panel rather than a new one. Confirmed directly against `runner.py`: `BacktestRunResult` (single-run) and `BacktestSweepResult` (many pairs sharing one `sweep_id`) are different shapes — `SweepResultsView` is a new component, not a reuse of the existing `ResultsView`, but each pair row inside it (`SweepPairRow`) reuses `ResultsView`'s own field layout/labels and the existing `DiscardedSignalRow`, per this task's own "don't reinvent it" scope note.

```
BacktestPanel.tsx (BacktestForm) — mode: "Fixture scenario" | "Real IBKR data" | "Sweep"
strategy_name shared across all three modes; symbol (single) shown in
fixture/ibkr modes only — sweep mode shows its own symbols/scenarios
inputs instead, described below
            │
            └── Sweep ──────► symbols: free-text, comma/space-separated,
                               parsed+deduped client-side (no multi-select
                               component exists anywhere in this codebase
                               today — confirmed directly, not assumed)
                                       │
                               scenarios: checkboxes over the existing
                               BACKTEST_SCENARIOS (all 4 known scenarios,
                               a small fixed set — no free-text parsing
                               needed, every option's state visible at
                               once, unlike a native <select multiple>)
                                       │
                                       ▼
                               pairCount = symbols.length × scenarios.length
                               checked against BACKTEST_SWEEP_MAX_PAIRS (20,
                               mirrors backend's own _MAX_SWEEP_PAIRS) —
                               client-side convenience only; the backend's
                               own pre-execution check stays the real
                               authority, same posture BacktestPanel.tsx's
                               existing IBKR range validation already takes
                               toward _validate_ibkr_range
                                       │
                                       ▼
                               useBacktestSweepRun.ts — new sibling hook,
                               mirrors useBacktestRun.ts/useIbkrBacktestRun.ts's
                               own status-machine/live-elapsed-timer shape
                               almost exactly; NOT folded into either
                               (materially different request/result shape,
                               same "keep each hook small and legible"
                               reasoning useIbkrBacktestRun.ts's own header
                               comment already gives for staying separate)
                                       │
                                       ▼
                               triggerBacktestSweep() — new, purely
                               additive wrapper in api-client.ts; symbols/
                               scenarios sent as repeated query keys
                               (confirmed directly against the route's
                               list[str] = Query(...) params, not a JSON
                               body); every error is a plain string detail
                               (confirmed directly), so this reuses the
                               existing shared ApiError/parseErrorDetail —
                               no new error class needed, unlike IBKR mode
                                       │
                                       ▼
                               BacktestSweepResult: sweep_id, strategy_name,
                               pairs_requested/succeeded/failed, runs[]
                                       │
                                       ▼
                               SweepResultsView — new component (the
                               response shape genuinely differs from
                               single-run BacktestRunResult); each pair
                               row (SweepPairRow) reuses ResultsView's own
                               field layout + the existing
                               DiscardedSignalRow, not reinvented
                                       │
                                       ▼
                               setLastBacktestSweepId(sweep_id) — NEW
                               WorkspaceContext field, mirrors
                               lastBacktestRunId end to end (type, default,
                               localStorage backfill, setter). Deliberately
                               does NOT call setLastBacktestRunId — a sweep
                               has many pairs' own run_ids and no single
                               one is "the" run this Main Window produced
```

**Wait-state design, stated explicitly.** Confirmed directly against the backend route: a sweep is one fully synchronous HTTP call running every requested pair sequentially, with no per-pair progress signal of any kind — not even the "still active, no percentage" framing IBKR mode's own copy already uses for its one external acquisition step, since nothing distinguishes pair 1 from pair 20 while a sweep is in flight. `useBacktestSweepRun.ts`'s live-elapsed timer (same real-`Date.now()`-delta mechanism the other two hooks already use, not a re-derived one) is the only honest signal available — no fabricated percentage, no "pair N of M," matching this task's own "set real expectations, don't reuse a shorter mode's timing copy" instruction and decision #152's own precedent for the same call.

**Results side — `BacktestResultsPanel.tsx` gets a second, independent filter type.** Confirmed directly: `GET /intelligence/backtest-runs` already supports a real `sweep_id` filter (decision #136); `GET /intelligence/strategy-outcomes` does not (only `is_backtest`/`backtest_run_id`, decision #123/#130) — adding one there would be the smaller backend change, but this delivery's own file boundary excludes `backend/` entirely, so the frontend resolves the same practical result in two steps instead (flagged as a real, worth-doing backend follow-up, not built here).

Design fork, decided explicitly rather than left ambiguous (per this task's own prompt): **two separate, explicit filter types** (`run_id` | `sweep_id`, a small tab toggle mirroring `BacktestPanel.tsx`'s own mode-toggle visual language) — not one generalized "run_id or sweep_id" field. Both are real UUID strings with no way to tell them apart without a round-trip query; a merged input would have to guess which endpoint to call or call both and pick whichever resolves, adding real, invisible complexity a person who already knows which kind of ID they're pasting shouldn't have to pay for. `sweep_id`'s own auto/manual state (`SweepIdFilterMode`) mirrors `RunIdFilterMode`'s exactly, driven by the new `lastBacktestSweepId` instead of `lastBacktestRunId` — **the auto-link follow-through is scoped IN, not deferred**: `BacktestPanel.tsx`'s sweep mode publishes `lastBacktestSweepId` the moment a sweep finishes (see the trigger-side diagram above), the exact mechanism decision #134 already built once for `run_id`, extended by one mirrored field rather than left as a future task.

```
BacktestResultsBody (BacktestResultsPanel.tsx)
        │
        │  filterType: "run_id" | "sweep_id"  ◄── NEW, this delivery
        │
        ├── run_id ──────► appliedRunId (decision #134's own auto/manual
        │                  state, entirely UNCHANGED) ──► useBacktestOutcomes
        │                  ({ backtestRunId, enabled: filterType==="run_id" })
        │                       │
        │                       ▼
        │                  GET /strategy-outcomes?is_backtest=true
        │                  [&backtest_run_id=<run>]   ◄── unchanged route
        │                       │
        │                       ▼
        │                  RunMetadataCard + outcomes list (unchanged)
        │
        └── sweep_id ────► appliedSweepId (NEW auto/manual state, mirrors
                           run_id's exactly, driven by lastBacktestSweepId)
                                │
                                ▼
                           useBacktestSweepOutcomes.ts — NEW hook
                           ({ sweepId: filterType==="sweep_id" ? appliedSweepId
                              : undefined })
                                │
                                ├─► fetchBacktestRuns(limit, undefined,
                                │    undefined, sweepId)   ◄── GET
                                │    /backtest-runs?sweep_id=<uuid>,
                                │    decision #136, unchanged — resolves
                                │    every BacktestRunRecord in the sweep
                                │
                                ▼
                           Promise.all: fetchStrategyOutcomes(limit, true,
                           run.run_id) for EVERY resolved run, in parallel
                           (no bulk/sweep_id-filtered route exists — see
                           the flagged backend follow-up above)
                                │
                                ▼
                           merge + re-sort by exit_filled_at desc (restores
                           the ordering guarantee lost by concatenating
                           already-sorted per-run arrays)
                                │
                                ▼
                           { runs: BacktestRunRecord[], outcomes:
                           StrategyOutcome[] } — BOTH returned, not
                           collapsed into just the merged outcomes: a pair
                           that ran cleanly but recorded outcomes_recorded=0
                           (an honest, expected sweep-route result) would
                           otherwise be invisible — present in `runs`,
                           absent from `outcomes`, never silently dropped
                                │
                                ▼
                           RunsInSweepStrip (runs, per-run outcome counts
                           computed client-side from the merged outcomes —
                           no second fetch) + the same OutcomeRow-based
                           list the run_id path already uses, via a new
                           shared OutcomesListSection wrapping both paths
```

**Internal flow within the changed module (`BacktestResultsBody`, inside `BacktestResultsPanel.tsx`):**

```
filterType toggle clicked
        │
        ▼
Both useBacktestOutcomes AND useBacktestSweepOutcomes are ALWAYS called
(React's rules of hooks forbid calling either conditionally) — each is
given its own explicit "not the active view" signal instead:
        │
        ├── useBacktestOutcomes({ enabled: filterType==="run_id" })
        │   NEW `enabled` param (default true, backward compatible) —
        │   skips its real fetch entirely when false rather than issuing
        │   its own "everything" call in the background for a result
        │   that will never render
        │
        └── useBacktestSweepOutcomes({ sweepId: filterType==="sweep_id"
            ? appliedSweepId : undefined })
            already no-ops on an undefined sweepId (same "nothing to show
            without a real filter value" posture useBacktestRuns.ts's own
            unset-runId branch already established) — reused as-is, no
            separate enabled flag needed for this one
        │
        ▼
OutcomesListSection renders whichever pair (outcomes/loading/error/refetch)
belongs to the active filterType — one shared component, not two
duplicated list+footer blocks, with RunMetadataCard or RunsInSweepStrip
slotted in above the list via its own `extraContent` prop
```

### D20 as built — Level Interaction persistence is isolated per run

The finding above remains the historical record of what decision #159
discovered. D20 (decision #160) closes it without changing strategy logic.
The runner already minted the run UUID and persisted its `backtests` parent
before replaying the first candle; the missing edge was producer → Level
Interaction Engine threading and run-aware persistence.

Cross-component flow:

```
BacktestRunner.run()
  │  mint run_id; persist backtests parent
  ▼
EngineBackedReplayStateProducer(backtest_run_id=run_id)
  │
  ├─► FeatureEngine(is_backtest=True, backtest_run_id=run_id)
  │      └─► run-scoped daily_levels_state          (D19)
  │
  └─► LevelInteractionEngine(is_backtest=True,
                             backtest_run_id=run_id)
         ├─► level_interaction_state
         │      one checkpoint per symbol/timeframe/key/run
         └─► level_interaction_events
                append-only; every replay event carries run_id

Live singleton construction
  └─► LevelInteractionEngine(is_backtest=False, backtest_run_id=None)
         ├─► one live checkpoint per symbol/timeframe/key
         └─► live events carry no run_id
```

Internal persistence flow:

```
FeaturesUpdated(symbol, timeframe, candle_ts)
  │
  ▼
in-memory key: (symbol, timeframe, level_key)
  │  one engine instance belongs to one live process or replay run
  ├─ cache miss ─► LOAD persisted checkpoint
  │                 WHERE symbol namespace = engine origin
  │                   AND backtest_run_id = engine run (NULL for live)
  │
  ├─ first observation / transition
  │       └─► UPSERT state in the identical origin + run scope
  │
  └─ concluded touch
          └─► APPEND event with identical origin + run attribution

Database boundary for BOTH tables:
  (symbol_id, is_backtest) → symbols(id, is_backtest)
  live     ⇔ backtest_run_id IS NULL
  backtest ⇔ backtest_run_id IS NOT NULL → backtests.run_id CASCADE

State uniqueness:
  live     UNIQUE (symbol_id, timeframe, level_key)
  backtest UNIQUE (symbol_id, timeframe, level_key, backtest_run_id)

Events: no state-style uniqueness; append-only by design.
```

Migration `0011` derives legacy origin from the related namespaced Symbol,
keeps live rows, and removes only legacy backtest state/events because their
run provenance cannot be recovered honestly. Downgrade likewise removes
replay-derived rows before restoring the legacy state unique constraint;
otherwise two isolated runs for one symbol could not fit the old schema.

**As-built note (decision #165) — sweep outcome reads no
longer fan out in the frontend.** Decision #163's sweep results view kept
the necessary `/backtest-runs?sweep_id=...` metadata read, but resolved each
returned `run_id` into its own `/strategy-outcomes?is_backtest=true` request.
That made the results path N+1 and required client-side merging and sorting.
This additive correction gives `/strategy-outcomes` a first-class
`sweep_id` filter. The route joins `strategy_outcomes.backtest_run_id` to
`backtests.run_id` and filters the existing `backtests.sweep_id` column in
SQL; no duplicate column, migration, or schema change is needed. `sweep_id`
and `backtest_run_id` remain independent AND-combined filters, both gated by
`is_backtest=true`, with one global newest-first `limit`.

The runs request remains necessary because a run can legitimately record zero
outcomes. The hook therefore performs exactly two requests per refresh — one
for all run metadata and one for all globally ordered outcomes — and returns
both collections so the panel continues to show every pair:

```
BacktestResultsPanel
        │ sweep_id
        ▼
useBacktestSweepOutcomes ── Promise.all ──┬─► GET /backtest-runs?sweep_id=...
                                         │      every run, including zero outcomes
                                         └─► GET /strategy-outcomes?
                                                is_backtest=true&sweep_id=...
                                                SQL join + global order/limit
                                                        │
                                                        ▼
                                           { runs, outcomes } → panel
```

The prior per-run fetch map, client-side merged-list construction, and
client-side re-sort are removed; sweep execution, persistence, and rendering
remain unchanged.

**`sweep_id` threading — confirmed a small, additive constructor change, not a larger one.** `BacktestRunner.run()` already minted its own `sweep_id = uuid4()` as a local — "a sweep of one," per decision #155's own note on the schema. The only change: `__init__` now accepts `sweep_id: UUID | None = None`, resolving to a fresh `uuid4()` when omitted (`self._sweep_id = sweep_id if sweep_id is not None else uuid4()`) and to the caller's value otherwise; `run()` reads `self._sweep_id` instead of minting its own. Every existing caller (`/backtest/run`, `/backtest/run/ibkr`, every direct-construction test) passes nothing and gets byte-for-byte the same behavior as before — confirmed via the full existing backtest test suite, unchanged pass count.

**Live-trading guard (decision #132), checked once before the sweep starts, not per-pair.** Same reasoning `/backtest/run` itself already rests on: a single run's own several-second replay never re-checks mid-run either, so treating a sequence of those same runs identically is consistent with existing precedent, not a new weaker standard invented for sweeps specifically.

**Response ordering is deterministic by construction**, not by database query: `runs[]` is built by appending to a plain list inside the `itertools.product(symbols, scenarios)` loop, in that exact order — never re-sorted, never read back from `backtests` in query order.

Route-only, matching this project's established "build the backend capability first, surface it later" sequencing (Context Engine #98→#125, Performance Analytics #122→#127, World View #150→#154) — no frontend change.

---
