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
                    IMPLEMENTED (v1, decision #127)
   ┌──────────────────────────────────────────────────────────┐
   │  Fixture Candle Data                                       │
   │  (FixtureCandleProvider — plumbing proof, NOT real          │
   │   historical-market validation)                             │
   │            │                                                │
   │            ▼                                                │
   │  ReplayStateProducer                                        │
   │  (real EventBus/FeatureEngine/LevelInteractionEngine/       │
   │   MarketStateEngine — zero modification to any of them)     │
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
                    FUTURE (not built, real prerequisites remain)
   Real minute-level historical data (paid Polygon tier or another
   vendor — future-ideas.md #17) + point-in-time historical context
   (HistoricalContextProvider, a documented but unbuilt extension
   point) + multi-symbol replay + outer grid-search/walk-forward loop
   + robustness/parameter-sensitivity report + human review + promotion
   — the full flow diagrammed earlier in this section, unchanged.
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

**As-built note (frontend, this delivery) — `POST /backtest/run` (decision #130) gets a real caller for the first time.** Every note above this one describes the route itself; before this delivery the only way to invoke it was constructing a raw HTTP request by hand and reading raw JSON back — the route's own module docstring says as much explicitly ("this route does not add any Performance Analytics UI for inspecting results ... a caller wanting the raw persisted rows can already query the existing route separately"), a deliberate scope boundary at the time, not an oversight. This delivery closes that one specific, narrow gap and nothing else: a new `BacktestPanel.tsx`, mounted as a fourth collapsible sibling panel in `App.tsx` alongside `InfoTab`/`FeatureEnginePanel`/`ScannerPanel` (same collapsible-width convention `ScannerPanel.tsx` already established — see that component's own `MIN_WIDTH`/`MAX_WIDTH`/`COLLAPSED_WIDTH` constants, reused verbatim), lets a person pick one of the 7 real strategy names and one of the 4 real fixture scenarios, submit, and see the real response.

```
BacktestPanel.tsx (form: strategy_name / scenario / symbol)
            │
            ▼
   POST /backtest/run                 ◄── fully synchronous, ~1s/candle,
   (useBacktestRun.ts)                     ~120-140s per scenario — the
            │                              panel shows a live "Nm Ns
            ▼                              elapsed" state, not a bare
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

**Two real, checked-not-assumed safety properties of the new seam — see `historical_provider_guard.py`'s own module docstring for the full reasoning.** (1) The installed provider is never `.connect()`ed, so `GET /market/candles` keeps returning its existing honest 400 throughout a backtest run — no silent fixture-data leak into that live-facing route. (2) Decision #132's Finnhub/Polygon 409 already means `broker_registry`'s historical role is unclaimed going into every valid run; one pre-existing, unrelated gap flagged rather than fixed here — decision #132's guard has no IBKR accessor, so an IBKR-connected session isn't blocked by it (`docs/decisions/future-ideas.md`, new entry).

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

---

