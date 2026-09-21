# Confirmed Decisions

**Extracted from:** `../architecture/system-design.md` §9 (original numbering preserved for continuity with any existing references).
**Companion documents:** [`../architecture/system-design.md`](../architecture/system-design.md) (how it's built), [`../architecture/trading-intelligence-architecture.md`](../architecture/trading-intelligence-architecture.md) (how it thinks), [`future-ideas.md`](./future-ideas.md) (deferred, not rejected), [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md) (when things ship).

This is the running log of settled architecture decisions — the "why did we choose X" record, kept in one place instead of scattered as inline asides across growing documents. **New confirmed decisions get appended here, numbered sequentially, as part of the same change that makes them** — not batched up and written after the fact.

---

**A note on this file's structure (updated at decision #160's rollover):** this file holds only the MOST RECENT range of decisions — currently #161 onward. Decisions #1–#60 live in `archive/001-060.md`; decisions #61–#79 live in `archive/061-079.md`; decisions #80–#90 live in `archive/080-090.md`; decisions #91–#106 live in `archive/091-106.md`; decisions #107–#121 live in `archive/107-121.md`; decisions #122–#133 live in `archive/122-133.md`; decisions #134–#160 live in `archive/134-160.md`. All seven moved verbatim and unedited at the point each rollover happened. See `README.md` in this directory for the full read/write/maintain protocol — every Claude session touching this project should read that file before writing a new decision here, not just this note.

---

### 161. Fixed the five stale "no real caller/writer yet" docstrings decision #158's own D-item audit inventoried, and reaffirmed D13 with the real cross-module consumer it named

Decision #158's audit of `strategy-engine-open-decisions.md` explicitly listed five stale claims and one architecture question, all "for a future task or for Saqib's call," and left them untouched by design. This entry closes that list.

**The five stale claims, each verified directly against the live file before editing, each sharing the same root cause (written before decision #128's Backtest Runner became a real caller/writer of the persistence layer decision #120 built):**

1. `strategy-engine-design.md` §5 — "a real (unwired) writer" → corrected to note decision #128 as the real, wired writer; the paragraph's "what still doesn't exist" framing narrowed to the LIVE-path caller specifically (Backtest Runner already exists); D17's status corrected inline to match #158's own resolution (resolved for the Backtest Runner path, open for live).
2. `backend/app/trading_intelligence/performance.py`'s module docstring — "no real caller wired into it... its only caller today is this task's own test suite" → corrected to distinguish LIVE (still absent) from Backtest Runner (real, since #128, via `BacktestRunner.run()`).
3. `backend/app/models/trading_intelligence.py`'s `strategy_outcomes` docstring — "no real caller wired yet" → corrected in the same style already used for the very next bullet's own `backtests`/#136 correction, citing #128 directly.
4. `backend/app/trading_intelligence/state_snapshot.py`'s docstring — two separate stale claims fixed: "no migration exists yet" (migration 0008 built at #120) and "a Backtest Runner" listed as a hypothetical future caller (confirmed via `runner.py`'s real, direct `capture_strategy_outcome_snapshots()` call at two sites — it's a real caller now, not hypothetical).
5. `backend/app/schemas/performance.py`'s D17 discussion — "deliberately UNRESOLVED" / "genuinely unresolved... no code in this module resolves it either way" → corrected to state decision #128 chose option (a) (only call `record_strategy_outcome()` once both snapshots are confirmed non-`None`, discarding the signal otherwise), resolving D17 for the Backtest Runner path; the live-path caller still doesn't exist, so D17 remains open for that path — matching #158's own precise, not-rounded-up correction style for D17 elsewhere.

**D13 — the one real open question #158 flagged, reaffirmed here, not silently resolved.** D13's original resolution ("import `Opportunity` directly, don't move it into `schemas/events/`") rested on "no such cross-module consumer today." Confirmed directly: `backend/app/backtest_runner/fill_simulator.py` and `runner.py` both `from app.strategy_engine.base_strategy import Opportunity` — a real cross-module consumer now exists, though it's Backtest Runner, not the Opportunity Engine (§9) D13's own text named as its anticipated trigger. Call made here rather than deferred again: reaffirmed as import-directly, not moved — D13's own text already said "no code change required by this resolution either way," the existing import already works correctly for this consumer, and moving the class now would be a pure consistency change with no functional necessity. Stated explicitly, not hidden: this is a low-stakes, trivially reversible call; if Saqib wants it moved for consistency with `MarketState`/`ContextChanged`/`FeatureSet` anyway, that's a small, easily-scheduled follow-up. The Opportunity Engine trigger D13 originally named has still not occurred, and remains the row's own stated revisit condition.

**Scope discipline.** No code logic, test, or persistence behavior changed — every edit is a docstring, a design-doc paragraph, or the D13 table row. Python compilation checked clean on all four edited `.py` files. `diff -rq` against a freshly-pulled `main` confirms exactly six files changed: `docs/architecture/strategy-engine-design.md`, `docs/architecture/strategy-engine-open-decisions.md`, `backend/app/trading_intelligence/performance.py`, `backend/app/models/trading_intelligence.py`, `backend/app/trading_intelligence/state_snapshot.py`, `backend/app/schemas/performance.py` — plus this entry, `INDEX.md`, and `TESTING.md`. Re-checked the decision log tail both before and immediately before writing this entry: #160 was latest both times, no collision.

---

### 162. Strategy Performance gains a strategy-name filter — closing #137/#138's deferral, whose stated reason was inaccurate when written

Assigned #162 at packaging, after the three-source re-check (temp id during parallel work: `strategy-performance-name-filter`; `main` was at #161 on every check — `INDEX.md`'s last row, `confirmed-decisions.md`'s tail, and the archive list all agreed).

`usePerformanceAnalytics.ts` and both analytics routes have supported `strategy_name` since #127. Decision #137 declined to expose it, stating that "there is no existing source of selectable strategy names anywhere in this codebase (checked directly, none found)"; #138 restated the deferral as "choosing a strategy is a separate UI decision." This is that decision. Frontend-only: `InfoTab.tsx`'s `StrategyPerformanceSummary` is the only code file changed.

**The fact this rests on, confirmed directly rather than taken from the task prompt.** `BACKTEST_STRATEGY_NAMES` (`api-client.ts`) already existed when #137 was written, so #137's claim was inaccurate at the time, not overtaken since. The constant has **no decision number of its own**: it arrived with `BacktestPanel.tsx`'s frontend delivery, which `backtest-runner-design.md` §7 records as unnumbered ("No decision-log entry accompanies this note"), whose files self-cited "decision #130" (the number #131 later reconciled for the trigger route), and which #134 calls "decision #131's frontend." Ordering: #130 → that delivery (#131-era) → #131/#132 → #133 (whose own as-built note already describes `BacktestPanel.tsx` as having closed the trigger side) → #134 → #137/#138. The names are also usable as-is, not merely plausible: all 7 strings equal each strategy class's own `.name` (the values `default_registry()` builds), and `BacktestRunner` writes `self._strategy.name` into `strategy_outcomes.strategy_name`, so the filter matches exactly what backtests recorded.

**Read directly before writing anything.** `usePerformanceAnalytics.ts` accepts `PerformanceAnalyticsFilters` and threads `strategyName`/`strategyVersion`/`isBacktest` into both fetches, with `[strategyName, strategyVersion, isBacktest]` as its `load()` dependency array — zero hook changes needed. `_performanceAnalyticsQuery` omits a param only when it is `undefined`, so "All strategies" must be `undefined`, never `""` (which would send `strategy_name=` and match nothing). Both routes take `strategy_name`, `strategy_version`, `is_backtest`; `strategy_version` without `strategy_name` is a 400 (`_validate_strategy_filters()`, #127). `BacktestPanel.tsx` renders its own Strategy control as a native `<select>` over `BACKTEST_STRATEGY_NAMES`. `api-client.ts` and `BacktestPanel.tsx` were read-only throughout.

**UI shape: a native `<select>` ("All strategies" + the 7 names), not a button row.** Eight options, some as long as "FirstPullback", do not fit one row in this narrow, resizable panel, and BacktestPanel already uses a `<select>` for the same list. The Live/Backtest toggle stays a two-button row (two states); the `<select>` reuses its `font-mono`/`text-[10px]`/`border-base-border` look, and every Tailwind class it uses was already in the bundle (built CSS is byte-identical, same hash, before and after). "All strategies" is the default and means `strategyName = undefined` — today's exact request, no `strategy_name` param — never a strategy chosen for the person. The selection is local state (same reasoning as `view`) and is independent of Live/Backtest: it survives switching between them. The header names the strategy when one is selected ("Strategy Performance — Backtest · ORB") so the label always matches the request, the same principle #137 applied to provenance.

**`strategyVersion`: out of scope, deferred.** Confirmed directly that no version selector or version list exists anywhere: `strategy_version` appears in the UI only as a read-only field on a single result (`BacktestResultsPanel.tsx`, `useBacktestRunMeta.ts`). Versions are per-strategy strings (`"orb_v1"`, minted in each strategy's `default_config()`); nothing enumerates them for the UI, and the backend requires a name alongside a version, so a version control would also have to depend on the name selection. That needs its own data-source/design decision, unlike names, which already had a source. Because the UI never sends a version, #127's 400 cannot be reached from this control.

**Empty states.** Backtest with a strategy selected now names it ("No backtest performance data for ORB is available yet — run a backtest with this strategy (a run can legitimately record none).") because the filter can itself be why nothing matches, "run a backtest" must say which strategy, and #131 established that a run can legitimately record zero outcomes. Backtest with All strategies and Live are unchanged, byte-for-byte: Live's stated reason (no Execution Engine exists) is strategy-independent, so naming a strategy there would imply a strategy-specific absence that is not the real cause.

**Loading state: same protection, confirmed by trace and by running it.** A strategy change goes through the identical path a Live/Backtest change does — new `strategyName` → new `load()` identity → effect re-runs → `loading = true` → #137's `loading`-only render gate. The hook's cancelled-flag also discards a superseded in-flight response. A throwaway vitest/jsdom harness (kept outside the repo, not shipped) rendered the real edited `InfoTab.tsx` with the real hook and real `api-client.ts`, stubbing only unrelated sibling hooks and `fetch`: 8/8 passed, covering default request URLs (no `strategy_name`), both routes carrying `strategy_name=ORB&is_backtest=true`, `Volume%20Spike` encoding, return to All dropping the param, selection surviving the Live toggle (`is_backtest=false`), the strategy-named vs. unchanged empty messages, the error state, and a rapid A→B change with a stale late response. Three mutations each failed exactly one test: reverting to the pre-#137 `loading && isEmpty` gate (stale-render test), removing the hook's cancelled guard (race test), and sending `""` instead of `undefined` (All-strategies test). One honest limit: the harness runs inside `act`, so it cannot itself observe a browser paint between commit and effect; that rests on React 18.3.1's `createRoot` flushing passive effects synchronously for discrete events, the same mechanism #137's toggle relies on (a `<select>` `change` is a discrete event).

**Docs.** `strategy-engine-design.md` §5's #137 note hard-codes `usePerformanceAnalytics({ isBacktest })` and a bare `?is_backtest=<bool>` query in its diagrams, and its closing paragraph states the claim this decision closes — so it did need an addendum, not "already generic enough." Added an inline correction pointer on that paragraph (also noting the default view has been Backtest since #138, not the `"live"` the #137 text records) plus a short as-built note with two delta diagrams (cross-component flow; internal flow on a selection change). The #137 diagrams themselves were not redrawn.

**Found, not fixed — citation drift outside this task's file boundary, for a future task.** The comment block above `BACKTEST_STRATEGY_NAMES` (`api-client.ts:830`) cites "decision #130" for `POST /backtest/run`; so does `triggerBacktestRun`'s docstring (`api-client.ts:914`), `useBacktestRun.ts:7`, and `backtest-runner-design.md` §7's frontend note (line 153, echoed at line 570). #131's own entry says the trigger route should cite #131 and lists `api-client.ts` as "correctly already attributed to #130" — true only for the `is_backtest` citation at `api-client.ts:360` (also correct: `useStrategyOutcomes.ts`, `intelligence.py:411`); #131's four-file correction list never mentions the two trigger-route citations in `api-client.ts`. `api-client.ts` and `useBacktestRun.ts` are outside this task's boundary (the latter belongs to the parallel sweep-UI session), so neither was touched.

**Parallel work.** The task named two parallel sessions: `backtest-sweep-frontend-ui` (`BacktestPanel.tsx`, `BacktestResultsPanel.tsx`, `api-client.ts`, `useBacktest*.ts`) and an overdue decision-log rollover. Zero file overlap with the first. The rollover was already visible on `main` at task start (`confirmed-decisions.md` holds only #161, `archive/134-160.md` exists, its header says "updated at decision #160's rollover"), so this entry was appended normally with no restructuring. `main` was re-pulled at task start, mid-task, and immediately before packaging; no change landed between pulls. `CHANGES.md` was deliberately left untouched — outside the stated footprint, and the sweep-UI session is the likelier owner of its next entry.

**Verification.** `npx tsc -b`: output identical to the untouched-clone baseline — only the four known #35 `GridPresetPicker` errors, zero new. `npx vite build`: clean. No frontend test file added: this codebase has no frontend test framework (no vitest/jest dependency, decision #123's test-free-hooks convention), adding one means a `package.json` change disproportionate to a single control, and no hook file changed, so there is no new hook logic to cover; the component behavior was verified with the throwaway harness above instead.

**Footprint, confirmed by `diff -rq` against a freshly re-pulled clone immediately before packaging.** `frontend/src/components/workspace/InfoTab.tsx` (import, the `strategyName` state/`<select>`, header suffix, Backtest empty-message branch, and the corrected comment block — all inside `StrategyPerformanceSummary`/its comment), `docs/architecture/strategy-engine-design.md` (§5 correction pointer + as-built note), this entry, its `INDEX.md` row, and `TESTING.md`. Confirmed untouched: everything under `backend/`, `api-client.ts`, `usePerformanceAnalytics.ts`, `BacktestPanel.tsx`, `BacktestResultsPanel.tsx`, `useBacktestRun.ts`, `useBacktestOutcomes.ts`, `CHANGES.md`, and the rest of `confirmed-decisions.md`.

---

### 163. `POST /backtest/sweep` (decision #159) gets a real caller and its own results become browsable, for the first time

Decision #159 built the sweep route, tested, backend-only. Before this delivery the only way to invoke it was a hand-constructed HTTP request, and the only way to see a sweep's own results as a group afterward was copy-pasting each individual `run_id` out of the raw response — `sweep_id` was already visible per-row in `BacktestResultsPanel.tsx` (decisions #133/#136/#139) but not usable as a filter. Same shape of gap this project has repeatedly closed for `run_id` itself and for several other backend-built, UI-invisible capabilities before it (#143–#147, #158/#161, #162). Frontend-only — `backtest.py`'s sweep route, `intelligence.py`, and every other file under `backend/` are unchanged.

**Trigger side — `BacktestPanel.tsx` stops being two-mode-only.** A third "Sweep" mode joins the existing tab toggle, following decision #152's own precedent for adding a mode to the same panel rather than a new one. `BacktestRunResult` (single-run) and `BacktestSweepResult` (many pairs sharing one `sweep_id`) are confirmed directly, not assumed, to be genuinely different shapes (`runner.py`), so a new `SweepResultsView` renders the top-level sweep summary — but each pair row inside it (`SweepPairRow`) reuses `ResultsView`'s own field layout/labels and the existing `DiscardedSignalRow`, per this task's own "don't reinvent it" scope note.

Sweep's own inputs, both shapes chosen and stated rather than defaulted to: **symbols** via a single free-text field, split on comma/whitespace and deduped client-side — there is no multi-select/tag-input component anywhere in this codebase (confirmed directly), and a comma-separated field needed no new UI primitive, only parsing. **Scenarios** via checkboxes over the existing `BACKTEST_SCENARIOS` — exactly 4 real scenarios (confirmed directly against `scenarios.py`'s own registry), a small fixed set where every option's current state should be visible at once, which a native `<select multiple>` doesn't give without scrolling. Pair count (`symbols.length × scenarios.length`) is checked client-side against a new `BACKTEST_SWEEP_MAX_PAIRS = 20`, mirroring the backend's own `_MAX_SWEEP_PAIRS` — a convenience so a doomed request is never sent, never a second source of truth; the backend's own pre-execution validation stays the real authority, same posture the existing IBKR-range client-side check already takes toward `_validate_ibkr_range`.

New `useBacktestSweepRun.ts` — sibling to `useBacktestRun.ts`/`useIbkrBacktestRun.ts`, mirroring their status-machine/live-elapsed-timer shape almost exactly, kept as its own file rather than folded into either (materially different request/result shape, same "keep each hook small and legible" reasoning `useIbkrBacktestRun.ts`'s own header already gives for staying separate). New, purely additive `triggerBacktestSweep()` in `api-client.ts`: `symbols`/`scenarios` sent as repeated query keys (`?symbols=A&symbols=B`), confirmed directly against the route's `list[str] = Query(...)` params, not assumed to be a JSON body; every error this route raises is confirmed directly to be a plain string `detail` (unknown strategy_name/scenario, empty symbols/scenarios, batch-size exceeded, the unchanged decision #132 live-data guard), so this reuses the existing shared `ApiError`/`parseErrorDetail` exactly like `triggerBacktest` — no new error class needed, unlike IBKR mode's `IbkrBacktestError`.

**Wait-state design, stated explicitly per this task's own instruction, not reused from either shorter mode.** Confirmed directly against the backend route: a sweep is one fully synchronous HTTP call running every requested pair sequentially, with genuinely no per-pair progress signal of any kind — not even the "still active, no percentage" framing IBKR mode's own copy already uses for its one external acquisition step, since nothing distinguishes pair 1 from pair 20 while a sweep is in flight. The hook's live-elapsed timer (the same real-`Date.now()`-delta mechanism the other two hooks already use) is the only honest signal available — no fabricated percentage, no "pair N of M," matching decision #152's own "set real expectations" precedent for adding IBKR mode alongside fixture mode.

On completion, the panel publishes a new `lastBacktestSweepId` WorkspaceContext field — mirroring `lastBacktestRunId` end to end (type, default, `normalizeMainWindow` localStorage backfill, setter, all four sites) — **deliberately not `lastBacktestRunId`**: a sweep response carries many pairs' own `run_id`s, some possibly `null` on a per-pair failure, and no single one of them is "the" run this Main Window produced; the shared `sweep_id` is the one real, unambiguous thing to publish instead.

**Results side — `BacktestResultsPanel.tsx` gains a second, independent filter type.** Confirmed directly: `GET /intelligence/backtest-runs` already supports a real `sweep_id` filter (decision #136); `GET /intelligence/strategy-outcomes` does not — only `is_backtest`/`backtest_run_id` (decision #123/#130). Adding a `sweep_id` filter there would be the smaller backend change, but this delivery's own file boundary excludes everything under `backend/`, so it's flagged here as a real, worth-doing follow-up rather than built.

Design fork, decided explicitly rather than left ambiguous, per this task's own prompt: **two separate, explicit filter types** (`run_id` | `sweep_id`, a small tab toggle mirroring `BacktestPanel.tsx`'s own mode-toggle visual language), not one generalized "run_id or sweep_id" field. Both are real UUID strings with no way to tell them apart without a round-trip query; a merged input would have to guess which endpoint to call, or call both and pick whichever resolves, adding real, invisible complexity for a person who already knows which kind of ID they're pasting. `sweep_id`'s own auto/manual state (`SweepIdFilterMode`) mirrors decision #134's `RunIdFilterMode` exactly, driven by the new `lastBacktestSweepId` instead of `lastBacktestRunId`.

**The auto-link follow-through is scoped IN, not deferred** — the exact mechanism decision #134 built once for `run_id`, extended by one mirrored field rather than left as a future task, since the mechanism already existed to extend.

New `useBacktestSweepOutcomes.ts` resolves the practical equivalent of a `sweep_id` filter in two steps, frontend-only: (1) `fetchBacktestRuns(limit, undefined, undefined, sweepId)` — already supports `sweepId`, confirmed directly against its own signature, no change needed — to resolve every `BacktestRunRecord` in the sweep; (2) `fetchStrategyOutcomes(limit, true, runId)` for every resolved run, in parallel (`Promise.all`; no bulk/sweep_id-filtered route exists, per the flagged follow-up above), merged and re-sorted by `exit_filled_at` descending (concatenating already-per-run-sorted arrays loses that ordering guarantee otherwise). Both the resolved `runs` and the merged `outcomes` are returned, deliberately not collapsed into just the merged list: a pair that ran cleanly but genuinely recorded `outcomes_recorded=0` (an honest, expected result per the sweep route's own docstring) would otherwise be invisible — present in `runs`, absent from `outcomes`, never silently dropped, matching this codebase's own "absent means not-yet, never silently worked around" principle. New `RunsInSweepStrip` renders that accounting above a new shared `OutcomesListSection`, which both filter types now render through (`RunMetadataCard` slotted in for `run_id` mode, `RunsInSweepStrip` for `sweep_id` mode) — one list+footer implementation, not two duplicated ones.

`useBacktestOutcomes.ts` gained one small, additive, backward-compatible `enabled` param (default `true`, existing callers unaffected): React's own rules of hooks forbid calling either hook only when its filter type is active, so both are always called from `BacktestResultsBody` — `enabled: filterType === "run_id"` is this hook's own explicit way to skip its real "everything" fetch while the sweep_id view is the one actually showing, rather than always firing an unused background request. `useBacktestSweepOutcomes.ts` needed no equivalent flag — it already no-ops on an `undefined` `sweepId`, the same "nothing to show without a real filter value" posture `useBacktestRuns.ts`'s own unset-`runId` branch already established, reused as-is by passing `sweepId: filterType === "sweep_id" ? appliedSweepId : undefined`.

**Parallel work.** Re-checking the decision log immediately before writing this entry — required by protocol regardless of what this task's own prompt assumed about concurrent sessions — found decision #162 (`InfoTab.tsx`'s strategy-name filter) had already landed on `main` mid-session. Zero file overlap confirmed directly: #162 touched `InfoTab.tsx`, `strategy-engine-design.md`, its own `INDEX.md` row/`confirmed-decisions.md` entry, and `TESTING.md`; this delivery never touches any of those. The 4 files #162 changed (`InfoTab.tsx`, `strategy-engine-design.md`, `INDEX.md`, `confirmed-decisions.md`) were synced from a fresh `main` pull into this working tree before this entry was assigned a real number — renumbered from the temporary slug `backtest-sweep-frontend-ui`, used everywhere during drafting per this project's own never-assign-a-real-number-during-parallel-work protocol, to `#163` across every code comment and doc reference.

**Found and fixed, not just flagged.** Decision #162's own entry independently surfaced the exact same stale `(decision #130)` self-citation on `POST /backtest/run`'s trigger-route delivery (confirmed directly against decision #131's own retroactive-reconciliation text: the real number is #131, #130 is a genuinely different, unrelated `/strategy-outcomes` `is_backtest`-filter delivery) in `api-client.ts:830`/`:914`, `useBacktestRun.ts:7`, and two spots in `backtest-runner-design.md` §7 — this session's own required reading surfaced it independently too. Corrected all five directly rather than re-flagging a third time, since this delivery was already touching both `api-client.ts` and that doc for unrelated reasons. Every other `(decision #130)` citation left in the repo (`api-client.ts:360`, `useStrategyOutcomes.ts:49/57`, `InfoTab.tsx:134` — already its own correction note — and two more spots in `backtest-runner-design.md`) was checked individually against decision #131's own text and confirmed to genuinely be about #130's real, unrelated delivery; none of those were touched.

**Docs.** `backtest-runner-design.md` gained a new as-built note with a trigger-side data-flow diagram, a results-side data-flow diagram, and an internal-flow diagram for the changed `BacktestResultsBody` module, matching this project's own "always add diagrams for architectural work" convention — plus forward-pointing correction markers added to the existing #130/#131 and #152 as-built notes, matching that section's own established correction-pointer convention (the same shape #152's own note already used correcting #130/#131's).

**Verification.** `npx tsc -b`: only the four known #35 `GridPresetPicker` errors, zero new, both before and after every edit in this delivery including the final citation-number pass. `npx vite build`: clean. No new frontend test file: this codebase has no frontend test framework (decision #123's/#162's own precedent), and no hook file's actual logic changed in a way a test would meaningfully cover beyond what `tsc`/`vite build` already verify structurally.

**Footprint, confirmed by `diff -rq` against a freshly re-pulled clone (post-#162) immediately before packaging.** `frontend/src/components/backtest/BacktestPanel.tsx`, `frontend/src/components/backtest-results/BacktestResultsPanel.tsx`, `frontend/src/services/api-client.ts`, `frontend/src/hooks/useBacktestOutcomes.ts`, `frontend/src/hooks/useBacktestRun.ts` (citation fix only), `frontend/src/state/WorkspaceContext.tsx`, `frontend/src/types/workspace.ts`, two new files (`frontend/src/hooks/useBacktestSweepRun.ts`, `frontend/src/hooks/useBacktestSweepOutcomes.ts`), `docs/architecture/backtest-runner-design.md`, this entry, its `INDEX.md` row, `TESTING.md`, and `CHANGES.md` (decision #162 deliberately left it untouched, anticipating this session as its likelier next owner — confirmed correct). Confirmed untouched: everything under `backend/`, `InfoTab.tsx`, `usePerformanceAnalytics.ts`, `strategy-engine-design.md`, and every other file decision #162 touched.

---

### 164. Synchronize living roadmap and Scanner status with the as-built repository

The living status surfaces had stopped tracking the implementation. `phase-roadmap.md` still described Phase 2's frontend swap as paused, treated Phase 4 as an SMA-only beginning with no Scanner, and called all of Phase 5–6 “not started” with `strategy_engine/` nonexistent. `scanner-design.md`'s header independently said no application code had been written even though its own later revision notes described the scorer, on-demand route, persisted universe and frontend. `INDEX.md` had one row per decision through #163 while its introduction still hardcoded “#1 through #98.” This delivery synchronizes those statements; it changes no architecture, contract, behavior, or roadmap deliverable/exit criterion.

**Evidence, checked against code and the full relevant decision history rather than the task prompt.** Feature Engine's indicator package and decisions #45/#51–#71/#83/#100–#102 establish the current indicator baseline; Level Interaction is built and later extended/corrected by #46/#64/#85–#86/#160; per-symbol and cross-symbol Market State are #93/#97; `ContextEngine` registers exactly `CalendarProvider`, `FundamentalsProvider`, and `NewsFlagProvider` per #92/#96. `default_registry()` constructs seven strategies and `StrategyScheduler` centrally enforces `gate_conditions` (#99/#104–#105/#109–#110/#113–#119). `OpportunityCache` and `get_opportunity_conflicts()` exist without ranking (#114–#115/#121/#123), so D4 remains open. Performance persistence/query/route/UI paths exist (#120/#122–#124/#127/#133/#137–#139/#162), with `BacktestRunner` as the real non-live writer (#128); fixture, isolated-IBKR and sweep paths exist (#131/#145/#159/#163). `WorldView` and its frontend surface exist (#150/#154) and honestly return `portfolio=None`. No Decision Engine, Trade Planning, Portfolio State, Execution Engine, or Position Monitor application module exists; `GovernorDecision` is only a schema (#6), and `IBKRAdapter.place_order()` still raises `NotImplementedError`.

**Exactly what changed.** Only `phase-roadmap.md`'s `Status (living)` section was edited: Phase 1 remains unchanged; Phase 2's completed swap and real debounce consumers are acknowledged; Phase 3 retains the honest lack of real IBKR/Finnhub validation while replacing the stale passport reason and recognizing the later real-Polygon check; Phase 4 now summarizes the verified Feature/Level Interaction/Market State/Context/on-demand-Scanner baseline and explicitly says the 100-symbol/no-dropped-ticks exit criterion has not been demonstrated in the repository record; Phase 5–6 is split in prose into built, partial and not-started capabilities. That section contains this delivery's one plain-text ASCII pipeline/status diagram. Only `scanner-design.md`'s header Status line changed, distinguishing the built scorer/runner/universe/routes/panel/hooks from the unbuilt continuous cadence, top-N Scheduler and `LiveTickRelay` promotion, Discovered tier and spread input. Its `DRAFT`/not-confirmed label remains. `INDEX.md`'s introduction no longer has a hardcoded maximum and this row was appended. `CHANGES.md` and a fresh task-specific `TESTING.md` record the delivery.

**Deliberately unchanged.** The roadmap phase table, deliverables, exit criteria and all non-status sections are byte-for-byte untouched. Scanner design §§0–§12 are untouched despite internal historical drift; whether the document remains `DRAFT` is Saqib's decision. No existing decision row or entry, archived decision, other architecture/design document, `docs/README.md`, `milestone-tracker.html`, backend file, frontend file, test, migration or Git history changed. This is documentation synchronization, not an architecture change; no architecture diagram was added beyond the single roadmap status diagram.

**Read-only drift sweep, classified under AGENTS.md §9 and not fixed.** No blocker was found. Related follow-ups: `docs/README.md` still calls `diagrams/` a placeholder although `trading-intelligence-overview.md` now exists (its `api/` placeholder claim remains accurate); `milestone-tracker.html` still names an Alpaca Phase 3 despite decision #1 and the implemented providers; `scanner-design.md`'s body still says Strategy Engine is absent and, in §11, that the panel lacks resize/collapse persistence and a universe editor; `future-ideas.md` #13 still bases its deferral on no `strategy_engine/` and no outcome evidence; `backtest-runner-design.md`'s opening design prose still says Strategy Engine and the runner do not exist despite later as-built notes; and `trading-intelligence-architecture.md`'s decision-#92 note still says Market State/its event do not exist even though #93/#97 landed—reconciling the still-unchanged Context signature is an architecture decision, not a wording guess. Unrelated/accurate candidates: `scanner/runner.py`'s “none of that exists yet” refers narrowly to the genuinely absent continuous scanner/cadence/promotion pieces, and `docs/api/README.md` accurately says that contract folder is empty. Precise summaries and supporting evidence are retained in `TESTING.md`.

**Collision, verification and footprint.** All six editable working files were clean and SHA-256-identical to an independent GitHub `main` snapshot at task start. A second independent `main` snapshot immediately before numbering had the same hashes; its archives covered contiguous #1–#160, and its live index/log ended at #163. Backend/frontend suites were not run because no application code changed. Focused source/decision greps, relative-link validation, decision-continuity and existing-entry immutability checks, `git diff --check`, full diff review, and archive inspection are recorded in `TESTING.md`. Final `diff -rq` against that fresh snapshot reports exactly: `CHANGES.md`, `TESTING.md`, `docs/architecture/scanner-design.md`, `docs/decisions/INDEX.md`, `docs/decisions/confirmed-decisions.md`, and `docs/roadmap/phase-roadmap.md`.

---

### 165. First-class `sweep_id` filtering removes sweep-results fan-out

Decision #163's sweep results hook necessarily retained one metadata request
for `/backtest-runs?sweep_id=...`, because every pair must remain visible even
when it recorded zero outcomes. It then made one
`/strategy-outcomes?is_backtest=true&backtest_run_id=...` request per resolved
run and merged/re-sorted those responses in the browser. This delivery
supersedes that read-path limitation without changing sweep execution or
persistence.

`GET /intelligence/strategy-outcomes` now accepts optional `sweep_id: str`.
Supplying it without `is_backtest=true` is HTTP 400; malformed UUIDs are HTTP
400; a valid unknown sweep is HTTP 200 with `{"outcomes": []}`. Membership is
resolved database-side by joining `strategy_outcomes.backtest_run_id` to
`backtests.run_id` and filtering `backtests.sweep_id`. `backtest_run_id` and
`sweep_id` are independent AND-combined filters, both require backtest mode,
and ordering (`exit_filled_at DESC`) plus `limit` apply once to the global
filtered result. No schema or migration change was needed.

The API client exposes the additive optional `sweepId` positional input. The
sweep hook now runs one `Promise.all` containing exactly the metadata request
and one global outcomes request, with no per-run map, client merge, or client
sort. It still returns both `runs` and `outcomes`, preserving zero-outcome-run
visibility in `BacktestResultsPanel.tsx`. Real-Postgres route tests cover
multi-run membership, cross-sweep/live exclusion, unknown and malformed IDs,
isolation, combined filters, global ordering, and global limit behavior.

The exact footprint is `backend/app/api/routes/intelligence.py`,
`backend/tests/test_strategy_outcomes_and_opportunity_conflicts_routes.py`,
`frontend/src/services/api-client.ts`,
`frontend/src/hooks/useBacktestSweepOutcomes.ts`,
`docs/architecture/backtest-runner-design.md`, this entry and its `INDEX.md`
row, `CHANGES.md`, and `TESTING.md`. Deliberately unchanged are models,
schemas, migrations, sweep execution, `BacktestResultsPanel.tsx`, unrelated
hooks/routes, and all other documentation.

---

### 166. Explicitly exclude the deferred `GridPresetPicker` sketch from the active TypeScript project

The clean-main baseline was not clean because the full frontend TypeScript
program included `frontend/src/components/workspace/GridPresetPicker.tsx`, an
unreachable abandoned first sketch for the deferred workspace-preset feature.
Before this delivery, `npx tsc -b` reported exactly four errors in that file:

- `GridPresetPicker.tsx(2,10): error TS2305: Module '"../../types/workspace"' has no exported member 'GRID_PRESETS'.`
- `GridPresetPicker.tsx(6,11): error TS2339: Property 'preset' does not exist on type 'WorkspaceContextValue'.`
- `GridPresetPicker.tsx(6,19): error TS2339: Property 'setPreset' does not exist on type 'WorkspaceContextValue'.`
- `GridPresetPicker.tsx(19,30): error TS7006: Parameter 'p' implicitly has an 'any' type.`

The component was not repaired because its `GRID_PRESETS`/`preset`/`setPreset`
shape is not the real workspace model, and it was not deleted because Future
Ideas #18 records workspace preset save/export as a wanted but explicitly
deferred feature. The exact narrow resolution is an `exclude` entry in
`frontend/tsconfig.json`:

```json
"exclude": ["src/components/workspace/GridPresetPicker.tsx"]
```

This does not weaken checks for live code: the exclusion names one file only,
does not change strictness or any other compiler behavior, and TypeScript would
still include the file if a live source file imported it. A source search before
and after the change found only the sketch's own declaration/path and no
application import. The live `GridPicker.tsx` uses the real `gridLayout` and
`setGridLayout` workspace contract and was not changed.

**Verification.** After the exclusion, `npx tsc -b` passed with zero errors and
`npm run build` passed its `tsc -b && vite build` stages, with Vite transforming
101 modules. `npx tsc --noEmit --listFiles -p tsconfig.json` produced no
`GridPresetPicker.tsx` entry. The component's SHA-256 remained
`cfae235139c8a7c1f270db32a54a80fb4e2c5ba13381e4adbad1813bb5e89599`. No backend
tests were run because no backend code changed.

**Exact footprint.** The seven changed files are `frontend/tsconfig.json`, the
scoped frontend-build note in `backend/README.md`, entry 18 of
`docs/decisions/future-ideas.md`, this `docs/decisions/confirmed-decisions.md`
entry, its `docs/decisions/INDEX.md` row, `CHANGES.md`, and `TESTING.md`.
`GridPresetPicker.tsx`, all other frontend source, backend code/tests,
dependencies, lockfiles, architecture documents, roadmap documents, and
decision archives remain unchanged. Workspace preset save/export was not
implemented.

---

### 167. Close the two low-risk documentation status-drift follow-ups from #164

Decision #164 recorded two low-risk, read-only documentation findings for a
later focused pass. This delivery closes exactly those two findings and does
not expand into the other drift identified there.

`docs/README.md`'s `diagrams/` row no longer calls the folder a placeholder.
It identifies the one current standalone diagram,
`diagrams/trading-intelligence-overview.md`, and accurately summarizes its
Mermaid flowcharts for live component links and compact internal views of the
trading-intelligence modules. The `api/` row is unchanged because that folder
still contains only its explanatory placeholder README.

`milestone-tracker.html`'s Phase 3 keeps `id: "p3"`, its existing exit
criterion, exactly three checklist items, and its phase-id-plus-item-index
checked-state keys. Only the stale Alpaca title and checklist wording changed:
the title now names Finnhub streaming plus Polygon history; the checklist
records the independent streaming/historical provider roles, Finnhub's genuine
real-time WebSocket stream and lack of free-tier historical stock candles,
Polygon's historical service and delayed polling fallback, and manually
connected IBKR's ability to take over both roles. These roles match the
provider implementations and registrations in `backend/app/main.py`,
`finnhub_data.py`, `market_data.py`, `broker.py`, and `broker_registry.py`, and
the early Alpaca rejection plus provider decisions #1 and #28–#33.

This is documentation synchronization only. No backend or frontend application
code, Phase 3 exit criterion, other phase, other drift finding, or architecture
document changed.

### 168. Execution Engine & Portfolio State design doc landed: as-built gaps verified, first slice analysed, fourteen forks (EX-1…EX-14) left open — nothing decided or built

**Status: awaiting Saqib's direction — nothing decided, built, migrated, or
changed in `backend/` or `frontend/`** (the decision #155 posture). Delivery
slug `execution-engine-design`; number #168 assigned after the three-source
re-check of latest `main` (INDEX last row, confirmed-decisions tail, archive
list all at #167). Provisional fork labels `EX-1…EX-14` are not D-numbers and
not decision numbers; each fork Saqib resolves becomes its own decision.

**Why.** Everything downstream of the Strategy Engine — Decision Engine,
Trade Planning, Governor, Portfolio State, Execution Engine, Position Monitor —
existed only as prose, while D17's live half (no live caller of
`record_strategy_outcome()`, decision #158) and D4's data starvation both wait
on a real trade lifecycle. The Execution Engine is the missing writer.

**What landed.** New `docs/architecture/execution-engine-design.md` (DRAFT):
a verified inventory of the downstream pipeline (built / partial / not built,
every claim cited `path:symbol`); thirteen findings where the code disagrees
with the prose; a field-by-field map of what one live `StrategyOutcome` needs;
three candidate first slices compared; component designs with diagrams for the
recommended slice; the fork list; deferred prerequisites; proposed acceptance
criteria for a build task. `system-design.md` gains a companion-doc entry and one
pointer paragraph each under §4.6 and §4.9 — no existing text rewritten.

**Findings that shape the design (details in the doc §2).**
- The execution-side events are declared, not built: four of eight have no
  payload model and none has a real publisher or subscriber; `PositionClosed`
  rides the normal lane although Portfolio State must feed the Governor.
- `BrokerAdapter` cannot report a fill (`OrderAck` is `submitted|rejected`); it
  has no client order id or bracket fields; the registry has no execution role.
- `IBKRAdapter` connects `readonly=True`, its order methods are stubs,
  `get_positions()` has no caller, and it is unverified live (#27) — so the
  first venue cannot be IBKR paper.
- "Only the Governor can place orders" reads consistently only as *Governor =
  the only authorizer; Execution = the only placer; manual mode adds a human
  confirmation after authorization* — so manual-first does not avoid needing an
  authorizer (the task brief's "human as Governor" shorthand was imprecise).
- The critical lane awaits handlers serially and persists nothing, so a venue
  call must never run inside a handler and the order ledger must be durable
  independently of the bus.
- `Opportunity` has no identity (the cache overwrites); `StrategyOutcome` cannot
  represent a strategy-less manual trade; `is_backtest` cannot label
  simulated-money outcomes — they would silently blend with real-money rows.
- `fill_simulator` is a look-ahead model and cannot serve as a live venue as-is.
- D17's live half differs from #128's backtest half: discarding a `None`
  snapshot would drop a real fill from the evidence table.

**Recommended first slice (a recommendation, not a decision).** Slice A:
simulated venue behind a narrow venue port, auto path, one thin clearly-labelled
authorizer stub, in-process protective exits, and an `OutcomeRecorder` — it
inverts the roadmap's stage order, which is EX-1. Manual-first is a good second
slice; IBKR paper stays deferred (#27).

```
 Strategy Scheduler ─► OpportunityCreated ─► Opportunity Cache                [built]
        │
        ▼
 Authorizer stub ─► TradePlanned ─► GovernorDecision ─► OrderApproved         [slice A — NEW, provisional]
        │                                            (critical lane)
        ▼
 Execution Engine ─► venue port ─┬─► SimulatedVenue                           [slice A — NEW]
        │                        └─► IBKRAdapter (stub, unverified)           [deferred, #27]
        │ OrderFilled (critical)
        ├─► Portfolio State ─► PositionClosed                                 [slice A — NEW]
        ├─► Position Monitor-lite (stop / target / EOD) ─► reduce-only exit   [slice A — NEW]
        └─► OutcomeRecorder ─► record_strategy_outcome() ─► strategy_outcomes [recorder NEW; writer + table built]
```
Internal flows of the Execution Engine, Portfolio State, and OutcomeRecorder,
and the full data-flow diagram, are in the design doc §6.1, §6.3, §6.5, §6.7.

**Open forks (all OPEN; each with options, evidence, and a recommendation in
doc §7).** EX-1 slice order · EX-2 population label for simulated outcomes ·
EX-3 venue port and `execution` registry role · EX-4 authorizer stub shape and
v0 rule numbers · EX-5 whether protective exits need authorization · EX-6
position-accounting owner, in-flight orders, `PositionClosed` lane · EX-7 D17
live policy for missing snapshots · EX-8 simulated fill model and
`fill_simulator` reuse · EX-9 identity and payloads · EX-10 durability · EX-11
exit enforcement (in-process vs broker-side) · EX-12 who writes
`StrategyOutcome` and what belongs in it · EX-13 manual mode in the first build
· EX-14 direction vocabulary and position effect.

**Not done.** No code, schema, migration, event model, or test was written; no
real IBKR session was reached; no fork was resolved; the World View `portfolio`
slot stays `null`. Related follow-ups found and reported, not fixed (doc §10):
Alpaca and `ApprovedOrder` naming drift in `system-design.md`, the two
disagreeing prose definitions of `TradePlanned`, and §18.8's reference to a
`trades` table that does not exist.

**Documentation updated in this delivery.** `docs/architecture/execution-engine-design.md`
(new), `docs/architecture/system-design.md` (pointers only), this entry and its
`INDEX.md` row, `CHANGES.md`, `TESTING.md`.

### 169. Phase 4 scale/load investigation — measured the exit criterion for the first time: FeatureEngine and LevelInteractionEngine keep up with a synthetic 100-symbol burst, 14–20x inside the 60s budget; MarketStateEngine's own debounce (decision #10/#155) coalesces by design; nothing decided or changed

**Status: investigation only — nothing decided, built, or migrated.** (slug `phase4-scale-load-measurement`; number assigned after re-checking `main` immediately before writing this entry — found the parallel `execution-engine-design` sibling had already landed #168, confirmed via three-source check: `INDEX.md` last row #168, `confirmed-decisions.md` tail #168, archive files `001-060`…`134-160` unchanged and non-overlapping. Assigned #169 per that sibling's own manual-merge notes in `TESTING.md`, which named this task by its exact slug and anticipated exactly this ordering.) Zero application code changed — `backend/app/**`, `frontend/**`, `backend/tests/**` untouched. Only `backend/scripts/measure_live_pipeline_scale.py` (new), this entry, its `INDEX.md` row, `CHANGES.md`, `TESTING.md`, and one sentence of `docs/roadmap/phase-roadmap.md`'s Phase 4 status paragraph changed.

**Why now.** Decision #164 recorded explicitly that "the Phase 4 exit criterion — 100-symbol streaming with a FeatureSet per symbol and no dropped ticks — has not been demonstrated in the repository record." Tracing the live wiring (`backend/app/main.py`'s `lifespan()`) found every stage from `CandleClosed` onward — `CandleRecorder`, `FeatureEngine`, `LevelInteractionEngine`, `MarketStateEngine` — is one asyncio worker draining an **unbounded** `asyncio.Queue` (`put_nowait`, no `maxsize`) with per-item `asyncio.to_thread` work; `MarketStateEngine` additionally runs one `DebounceScheduler` (1.0s floor, decision #10/#155) per symbol. So "dropped ticks" cannot come from queue overflow — nothing here has a ceiling. The real question is BACKLOG/LAG: does the pipeline drain a same-second 100-symbol burst before the next one arrives, 60 real seconds later in production. This had never been measured.

**Environment.** 1 vCPU / 3.9 GB sandbox, Ubuntu 24.04, Python 3.12.3, PostgreSQL 16.15 (apt), scratch database `trading_scale_scratch` (owner `trading`, migrated to head `0011`), **`fsync = off`** the only Postgres tuning (matching decision #155's own precedent) — never a dev/prod database, name-guarded in code (`_require_scratch_db()` refuses to run unless `POSTGRES_DB` contains `"scratch"`). `os.cpu_count() == 1`, so the default `asyncio.to_thread` executor is `min(32, cpu+4) == 5` threads (process-wide, shared by every stage). SQLAlchemy `create_engine()` (`app/db/session.py`) takes no explicit `pool_size`/`max_overflow`, so both are library defaults (`pool_size=5`, `max_overflow=10`, ceiling 15 connections) — confirmed by reading the call site, not assumed.

**The harness (`backend/scripts/measure_live_pipeline_scale.py`, not collected by pytest).** Constructs the real `EventBus`, `CandleRecorder`, `LiveTickRelay`, `FeatureEngine`, `LevelInteractionEngine`, `MarketStateEngine` (`is_backtest=False`, the live namespace), `ContextEngine`, `StrategyScheduler`, `OpportunityCache`, `FundamentalsRefreshJobs` — same classes and start order `main.py`'s `lifespan()` uses (FastAPI app, WebSocket Gateway, and provider auto-connect omitted — not part of the pipeline under test). Fresh instances per ramp step, not the `get_xxx()` singletons `main.py` itself uses — the singleton cache exists so route handlers share one cross-request instance; reusing it across ramp steps would leak in-memory state (rolling windows, per-symbol debounce schedulers, daily-levels cache) between different N values and bias the very timing this harness measures. A `SyntheticProvider(MarketDataProvider)` stands in for a real feed; `bus.subscribe_all()` (an existing public API — the WebSocket Gateway's own production caller) is the harness's only hook into the pipeline. No historical/streaming broker is registered, so Daily Levels correctly takes its designed no-provider no-op path (confirmed: `broker_registry.get_historical_provider()` returns `None`, asserted at harness start) — an honest gap, not a workaround.

```
SyntheticProvider.push(tick)                                   (harness-only, satisfies MarketDataProvider)
        │ registered via provider.on_tick(bridge._on_tick)
        ▼
TickIngestBridge._on_tick ──► asyncio.create_task(_handle_tick)     (tick_ingest.py — one task PER TICK, no queue)
        │ publish PriceUpdated (always)
        │ minute rollover keyed on tick.exchange_ts, NOT wall clock ──► publish CandleClosed
        ▼
EventBus._consume() — normal lane, ONE consumer task, unbounded asyncio.Queue        (event_bus/bus.py)
        │ dequeues one envelope, awaits asyncio.gather(*handlers) before the next     ◄═ SERIALIZATION POINT
        ├─► CandleRecorder: put_nowait ─► worker: to_thread(persist)      (1 worker, unbounded queue)
        ├─► LiveTickRelay: gated to ≤8 active symbols, 5s real-wall-clock flush ─► PriceSnapshot
        └─► FeatureEngine: put_nowait ─► worker: [maybe_refresh_daily_levels/premarket — to_thread,
             cold-start-only] → to_thread(_compute_one, PURE CPU, no DB) ─► up to 4 FeaturesUpdated
                    │  (1m + any of 5m/15m/1h this candle completes)     (1 worker, unbounded)  ◄═ HARNESS
                    ▼                                                                              OBSERVES:
             LevelInteractionEngine: put_nowait ─► worker: to_thread(_process_one — REAL DB WRITE      engine._queue
                    │  EVERY item: level_interaction_state upsert + level_interaction_events append)   .qsize() +
                    │  LevelInteractionChanged published ONLY on an actual zone TRANSITION,             .join();
                    │  verified against level_interaction_state's own updated_at during this            bus.sub-
                    │  harness's own smoke test — NOT once per item processed                           scribe_all
                    ▼
             MarketStateEngine._on_features_updated (1m only) ─► DebounceScheduler.trigger() (one/symbol)
                    │ elapsed ≥ 1.0s real monotonic ─► run now
                    │ otherwise ─► _pending=True; ONE _run_after_delay(1.0−elapsed) task   ◄═ REAL WAIT,
                    ▼                                                                        coalesces every
             put_nowait(symbol) ─► worker: to_thread(_compute+_persist) ─► MarketStateChanged  intervening
                    ▼                                                                            trigger
             StrategyScheduler._on_market_state_changed — NO queue, runs INLINE inside the       ◄═ SECOND
                    │ bus's own gather() above: 7 strategies' evaluate() execute synchronously      SERIALIZATION
                    │ in the bus's normal-lane consumer task before it dequeues the next envelope   POINT
                    ▼
             OpportunityCreated (if any) ─► OpportunityCache (no queue, direct dict write)
```

```
run_pipeline_for(N)                                              (measure_live_pipeline_scale.py)
        │
        ├─ _reset_scratch_db(): TRUNCATE … RESTART IDENTITY CASCADE      (name-guarded, never dev/prod)
        ├─ _seed_scanner_universe(N synthetic SYNnnnn symbols)            real Symbol/ScannerUniverseSymbol
        │                                                                  rows — ContextEngine's own
        │                                                                  bootstrap reads this table, so
        │                                                                  StrategyScheduler really evaluates
        ├─ construct + start all stages, same classes/order as main.py's lifespan()
        ├─ bus.subscribe_all(recorder.handler)      ◄══ harness's ONLY hook; no backend/app/** file touched
        │
        ├─ Stage A — tick ingestion (SCOPE 2a)
        │     SyntheticProvider.push(Tick, synthetic exchange_ts) × N×(minutes·ticks_per_minute+1)
        │     assert PriceUpdated observed==expected, CandleClosed observed==expected (both, every N)
        │     assert PriceSnapshot only for LiveTickRelay's ≤8 active symbols (zero violations, every N)
        │     bridge.stop()  ◄══ FOUND EMPIRICALLY, fixed: must stop here, before Stage B (see Finding 3)
        │
        └─ Stage B — candle burst (SCOPE 2b)
              for k in range(K): publish N CandleClosed at synthetic candle_ts = start + k·1m
                  record each engine's queue.qsize() BEFORE this burst (backlog check)
                  await asyncio.sleep(0)  ◄══ REQUIRED for the backlog check to mean anything — see Finding 2
              await feature_engine._queue.join() / level_engine._queue.join() / market_state_engine._queue.join()
                  ◄══ authoritative drain detection (same primitive settle_replay() itself already uses),
                       not a published-event-count poll — see Finding 1 for why that would be wrong here
              + bounded extra settle for MarketStateChanged only (catches a trailing DebounceScheduler
                catch-up scheduled up to 1.0s after its last trigger, outside any queue this harness can join)
              → per-symbol coverage tally, last-symbol timing, compare drain_s against the 60s deadline
```

**Finding 1 — `LevelInteractionChanged` is not a per-item heartbeat; queue-drain is the correct throughput proxy, not event count.** `LevelInteractionEngine._process_one` runs on every `FeaturesUpdated` (no debounce) and always writes `level_interaction_state`, but only *publishes* `LevelInteractionChanged` on an actual zone transition. Verified directly in the harness's own smoke test (N=1, 5 `FeaturesUpdated`): `level_interaction_state` held 6 rows (vwap/vwap_ext/regular_open × 1m/5m) with `updated_at` spanning the full run, while only 1 `LevelInteractionChanged` published — near-flat synthetic prices rarely cross a zone boundary. The harness's first drain-detection design (event-count polling) would have silently declared "done" while real backlog remained; fixed by switching to `queue.join()` (Finding 3 lists the fix).

**Finding 2 — an unbounded `asyncio.Queue.put()` never actually suspends the coroutine, so a bare `await bus.publish(...)` loop gives every downstream worker task zero chance to run.** First burst-loop version recorded queue depth 0 before every single burst at every N, including N=100 — impossible if any real processing were happening between bursts. Confirmed against `asyncio.queues.py`: `Queue.put()` only awaits when the queue is `full()`; an unbounded queue is never full, so `put_nowait()` runs and returns with no yield point. Fixed with one `await asyncio.sleep(0)` per burst — long enough for the bus's consumer task and each engine's worker task to run as far as they can before their own yield points, short enough that K bursts still complete in a small fraction of a real second.

**Finding 3 — synthetic historical `candle_ts` collides with `TickIngestBridge`'s real-wall-clock safety-net flush.** `_flush_loop`'s stale-bucket check compares `bucket.minute_ts` against real `datetime.now(timezone.utc)` (`tick_ingest.py`'s own module docstring) — by design, for production. This harness's Stage A deliberately leaves one bucket open per symbol at a synthetic 2026-01-05 timestamp; when the harness's own real wall-clock crossed a real minute boundary mid-run, that safety net correctly saw a "stale" bucket (Jan 2026 is always less than the real current minute) and force-published it, which `FeatureEngine`'s real duplicate/out-of-order guard then correctly rejected — harmless (logged, not counted, no data lost) but noisy, and never happens in production, where `candle_ts` always tracks real time. Fixed by calling `bridge.stop()` immediately after Stage A, before Stage B (which never needs the bridge — it publishes `CandleClosed` directly).

**Results — primary ramp** (K=16 synthetic 1m candles from session open 09:34–09:49 ET, spanning two 5m boundaries and one 15m boundary; Stage A precedes each row with 3 synthetic minutes / 3 ticks-per-minute, strictly before Stage B's candle_ts range).

| N | FeaturesUpdated (count / expected N×16) | FeatureEngine drain | LevelInteraction (queue-verified full drain) | LevelInteraction drain | MarketStateChanged | MarketState drain | 1m coverage | ≤60s, every stage |
|---|---|---|---|---|---|---|---|---|
| 1 | 21 / 16 (+5 for the 5m/15m boundary crossings) | 0.031s | 3 events; queue fully drained | 0.058s | 1 | ~0.000s | 1/1 | yes |
| 10 | 210 / 160 | 0.236s | 17 events; queue fully drained | 0.427s | 20 | ~0.000s | 10/10 | yes |
| 25 | 525 / 400 | 0.673s | 36 events; queue fully drained | 1.203s | 50 | ~0.000s | 25/25 | yes |
| 50 | 1050 / 800 | 1.157s | 57 events; queue fully drained | 2.205s | 124 | ~0.000s | 50/50 | yes |
| 100 | 2100 / 1600 | 2.744s | 125 events; queue fully drained | 4.194s | 400 | ~0.000s | **100/100** | **yes** |

1m coverage is exact (every symbol produced exactly 16 `FeaturesUpdated(1m)` — no drops, verified per-symbol, not just in aggregate). `FeaturesUpdated` count exceeds N×16 by the expected 5m/15m-boundary extras (module docstring: up to 4 FeatureSets per 1m close). Sustained throughput computed from these numbers: FeatureEngine ~700–900 `FeaturesUpdated`/s, LevelInteractionEngine ~350–500 items/s (the slower stage — its worker does a real DB write every item, unlike FeatureEngine's post-warmup pure-CPU `_compute_one`) — consistent across N, this sandbox's real ceiling, not a per-N artifact.

**MarketStateChanged coalescing is DebounceScheduler working as designed (decision #10/#155), not a drop — and, counterintuitively, faster upstream stages mean FEWER distinct recomputes, not more.** DebounceScheduler's 1.0s floor is real-wall-clock, not synthetic-`candle_ts`. Because the whole 16-candle burst publishes in milliseconds, most of a symbol's 16 triggers arrive well inside its own 1.0s floor and coalesce into one pending catch-up that reflects only the LATEST close by the time it fires — at N=100, mean coverage was 4.0 recomputes/symbol out of 16 triggers, because the OTHER stages' own real processing time (LevelInteractionEngine's ~4.2s drain) stretched the burst's real duration past several 1.0s floors, each one allowing one more genuine "immediate" run. A system with faster upstream stages would show fewer, not more, MarketStateChanged events per burst. This is the same mechanism decision #155 already measured for backtest replay and decision #157 already addressed there (`settle_replay()`); nothing here touches that path or reopens it.

**Results — Stage A (tick ingestion + `LiveTickRelay` gating), every N.** `PriceUpdated` observed == expected and `CandleClosed` observed == expected at N=1/10/25/50/100 (10/10, 100/100, 250/250, 500/500, 1000/1000 ticks; 3/3, 30/30, 75/75, 150/150, 300/300 candles) — zero drops on the ingestion side at any scale tested. `LiveTickRelay.set_active_symbols()` gating: zero symbols outside the active ≤8-symbol subset ever received a `PriceSnapshot`, at every N, confirming the gate holds under a 100-symbol backing set, not just a small one.

**Results — supplementary stress point, N=100 / K=60 (one simulated hour, beyond the requested ramp — cheap to run, directly answers "how much margin," so included).** 7,700 `FeaturesUpdated` fully drained in **9.11s**; LevelInteractionEngine's queue fully drained (`queue.join()`) in **14.03s** for 6,000+ items (1,038 `LevelInteractionChanged` published on transitions). Both still **1m coverage 100/100** — zero drops even at 4× the primary ramp's burst depth. At the slower stage's ~500 items/s ceiling, LevelInteractionEngine used **14.03 of the 60s per-candle-minute budget — about 4.3× headroom remains even here**; the primary ramp's N=100/K=16 row used only 4.19s, ~14× headroom. **Known limitation of this one supplementary row:** the harness's `MarketStateChanged` settle wait is a fixed window sized for the K=16 primary ramp; at K=60 a `wait_until_quiet` timeout fired (`"timed out after 2.0s at count=1051"`), so the reported MarketState count there is a documented lower bound, not the fully-settled figure — this affects only that one diagnostic number in the stress row, not the FeatureEngine/LevelInteractionEngine coverage or drain-time findings the exit criterion turns on.

**Does this bear on `scanner-design.md`'s §7 "100-symbol concurrency prerequisite" bullet? No — reported as a follow-up, not edited, per this task's own file boundary.** That bullet is about DATA-PROVIDER concurrency — how many symbols Finnhub's free WebSocket tier can stream simultaneously versus IBKR's 100-line default — a question about the provider connection, unverified for Finnhub either way before or after this task. This investigation used a synthetic in-process provider and never touched real Finnhub/IBKR streaming; it measured the BACKEND's own downstream processing throughput once ticks/candles already arrive, an orthogonal question. Conflating the two into one bullet would misrepresent what got measured. Left untouched; flagged here for whoever picks up real-feed validation next.

**Roadmap wording changed** (`docs/roadmap/phase-roadmap.md`, Phase 4 status paragraph) — from "has not been demonstrated in the repository record" to a measured-on-synthetic-input statement citing this decision, the N/coverage/drain numbers above, and that real-feed delivery remains open. Exact diff in `CHANGES.md`.

**What this does NOT prove (synthetic input; stated plainly, not left implicit).** No real Finnhub delivery was exercised — `TickIngestBridge` ran against a harness-only `SyntheticProvider`, not a real WebSocket connection. No provider symbol-count cap was tested (see the scanner-design.md paragraph above — that remains a genuinely separate, still-open question). Real tick burstiness (uneven arrival, reconnects, partial ticks) is not represented — this harness's Stage A pushes ticks at a fixed synthetic cadence. Real DB latency on production hardware may differ from this 1 vCPU / `fsync=off` sandbox in either direction — `fsync=off` is a real, stated advantage this measurement enjoys that a production database likely won't have; the sandbox's single vCPU is a real, stated disadvantage a production host likely won't have. The Core-100 symbol list itself remains unsettled (decision #164, unchanged by this task — not this task's call). `ContextEngine`, `StrategyScheduler`, and `OpportunityCache` were wired and running (so `StrategyScheduler` really evaluated all 7 strategies per `MarketStateChanged`, not skipped via a missing context) but their own throughput was not the object of measurement and is not separately reported here.

**Options for the found headroom margin, and the one already-flagged real gap — Saqib's call, nothing chosen here.**

| | What it would address | Trade-off | Saving (estimate unless noted) |
|---|---|---|---|
| **(a) Do nothing** | — | Matches what was measured: comfortable margin (14–20× at K=16, 4.3–6.5× even at K=60) at today's per-item cost on weaker-than-production hardware | — |
| **(b) Real-Finnhub validation pass** | Closes the one gap this task explicitly could not close (a real feed, real burstiness, real provider symbol cap) | New task, needs a funded/keyed Finnhub connection and, per scanner-design.md §7, eventually an answer on Finnhub's free-tier WS symbol ceiling | Unmeasured — the actual next question, not a savings trade |
| **(c) Bound the per-stage queues** | Turns silent backlog into an observable, alertable condition (`qsize()` growing) instead of relying on this task's one-off measurement staying true as the codebase changes | Additive; a `maxsize` choice and a policy for what happens when full (block vs. drop vs. alert) is a real design question, not free | Prevents an undetected future regression; does not speed anything up today |
| **(d) Batch LevelInteractionEngine's DB writes** | Addresses the one real per-item cost this task found (a DB write every candle, not just on cold start) — the slowest stage by a clear margin | Changes `level_interaction_state`/`level_interaction_events` write timing; needs its own correctness pass, not attempted here | Unmeasured — LevelInteractionEngine is not currently the constraint, so this is a future-headroom option, not an urgent one |

**Not measured (explicit follow-ups, not implemented).** Real Finnhub/IBKR delivery; Finnhub's free-tier WS symbol-count ceiling (scanner-design.md §7, still open); genuine tick burstiness; production hardware timing; `ContextEngine`/`StrategyScheduler`/`OpportunityCache` throughput in isolation; behavior beyond N=100 (K was pushed to 60 at fixed N=100; N itself was not pushed past 100); the MarketStateChanged trailing-catch-up undercounting noted above at K=60.

**Documentation updated in this delivery.** This entry, its `INDEX.md` row, `CHANGES.md`, `TESTING.md`, one sentence of `docs/roadmap/phase-roadmap.md`'s Phase 4 status paragraph, and the new `backend/scripts/measure_live_pipeline_scale.py`. `docs/architecture/scanner-design.md` deliberately left untouched (see above). No `backend/app/**`, `frontend/**`, or `backend/tests/**` file changed.
