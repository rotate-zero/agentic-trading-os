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

### 170. Execution Engine design amended — the simulated-venue automatic path (Slice A) approved in principle; `execution_mode` and `execution_venue` split; narrow `OrderVenue` port; ledger-authoritative recovery; first limits set — amends #168, which stays as merged; nothing built

**Status: design approved in principle and amended; no application code, schema,
migration, or event model written or changed.** This entry **amends decision
#168** (`docs/architecture/execution-engine-design.md` landed there with every
fork open). Per `AGENTS.md` §6, existing decision content is immutable and is
corrected by a new entry that references the original, so #168 is untouched;
the living design doc is revised in place and is now the implementation
specification for the slice. Number #170 assigned after the three-source
re-check of latest `main` (`INDEX.md` last row #169, `confirmed-decisions.md`
tail #169, archive list unchanged).

**Saqib's resolutions (2026-09-22).**
- **EX-1 — Execution first** using the stub authorizer and `SimulatedVenue`. The
  stub is **technically restricted to simulated execution and fails closed for
  `paper`/`live`**: startup refusal, per-decision refusal, mode stamp plus a
  venue `supported_modes` check, and no non-simulated venue in existence
  (doc §6.2). #168's "dry-run is the default" invariant is replaced by this.
- **EX-2 — venue identity and capital mode are different concepts.** Add
  **`execution_mode`** (`backtest | simulated | paper | live`) and
  **`execution_venue`** (`simulated | ibkr | …`); keep `is_backtest`
  temporarily for compatibility only, derived from the mode, not the long-term
  classifier. Supersedes #168's recommendation of `execution_venue` alone.
- **EX-3 — a new narrow `OrderVenue` interface and an `execution` registry
  role; `BrokerAdapter` is not enlarged** (its job is market-data connectivity).
  Supersedes #168's recommended option in which `BrokerAdapter` would inherit
  the port. A future IBKR order venue is a separate class with its own
  connection.
- **EX-4 — one stub; initial values, all configurable rather than hardcoded:**
  maximum concurrent positions **1**, fixed size **$1,000 notional** per trade,
  daily loss cap **$100**. Conservative first-slice defaults for validating the
  lifecycle, not final trading-risk settings.
- **EX-6 — Portfolio State owns position accounting, in-flight orders, and
  daily P&L.** `PositionClosed` may use the critical lane **only after the
  closure is committed to the database.**
- **EX-7 — the snapshot requirement is a pre-trade gate; once any venue reports
  a fill it is always persisted and processed; an unexpectedly missing snapshot
  is recorded as nullable snapshot fields plus a missing-data reason — never a
  discarded fill.** Decision #128's discard stays for backtest rows only.

**Requirements added before implementation** (doc §3, I10–I15; §6.3, §6.5,
§6.9): every order has a stable, deterministic client-order ID; order and fill
updates are deduplicated by database constraint; restart recovery scans
non-terminal ledger orders and reconciles them with the venue before any new
authorization; the database ledger is authoritative and in-memory Portfolio
State is reconstructable from it; persist-before-publish applies to order
fills and position closures; the daily-loss gate counts realized loss plus
current unrealized loss and open risk, not realized P&L alone.

**Two precisions made while writing this in (stated, not silent).**
1. *The critical lane's guarantees.* Verified against `bus.py`, the lane gives
   FIFO ordering, isolation from normal-lane backlog, and isolation of one
   handler's failure from the others — it does **not** give persistence,
   delivery guarantees, crash recovery, **or propagation of a handler's failure
   to the publisher** (`_safe_call` swallows and logs; `publish()` only
   enqueues). The doc says so explicitly (F6, I7, §6.5) and builds "failure
   propagation" where it can actually exist: persist-before-publish,
   idempotent consumers, rebuild-from-ledger.
2. *A naming collision.* `trading-intelligence-architecture.md` §18.5 already
   defines `ExecutionMode` as `auto | manual`. The design calls that *placement
   mode* so `execution_mode` means the capital mode only (reported as a
   follow-up, not edited).

```
 Authorizer stub ── fails CLOSED unless execution_mode == simulated (4 layers) ── COMMIT decision ─► OrderApproved
        │  (limits: 1 position · $1,000 notional · $100 daily-loss cap, from Settings; gate counts realized + unrealized + open risk)
        ▼
 Execution Engine ── idempotent insert (client_order_id UNIQUE) ── COMMIT ─► OrderVenue port  ◄── `execution` registry role
        │                                                                        │            (BrokerAdapter untouched)
        │                                                                 SimulatedVenue  (IBKROrderVenue: deferred, separate)
        ◄── dedupe (execution_venue, venue_fill_id) ── COMMIT fill ─► publish OrderFilled  (critical: ordering + isolation only)
        ▼
 Portfolio State ── replays the fills ledger; COMMIT position / closure ─► publish PositionClosed (critical, after commit)
        ▼
 OutcomeRecorder ── entry/exit snapshots best-effort (NULL + reason if missing, fill never discarded) ─► StrategyOutcome
        ▼                                                     (execution_mode, execution_venue)
 record_strategy_outcome() ─► strategy_outcomes          RESTART: rebuild from ledger ─► reconcile non-terminal orders with the
                                                          venue ─► recover closed-without-outcome trades; bus is never the source
```
The full data-flow diagram and the internal flows of the authorizer stub,
Execution Engine, Portfolio State, `OutcomeRecorder`, and restart recovery are
in the design doc §6.1–§6.9.

**Still open.** **EX-5** (protective exits need no fresh authorization, only a
reduce-only guard) and **EX-12** (`strategy_outcomes` holds strategy-attributed
trades only; `OutcomeRecorder` writes it) need Saqib's confirmation before a
build task. EX-10 is treated as settled by the ledger requirement. EX-8, the
rest of EX-9, EX-11, EX-13, EX-14 proceed on their recommendations unless
Saqib objects. **Judgment calls in this revision to confirm or overrule
(doc §7.1):** J1 the placement-mode rename; J2 the daily-loss gate also counts
the candidate trade's own stop-out loss (a $1,000 trade with a stop more than
10% away is refused at the initial values); J3 `StrategyOutcome.schema_version`
1 → 2, backtest rows labelled `backtest`/`simulated`, and a migration that
aborts if any `is_backtest = false` rows exist; J4 unsent approved entry
orders are cancelled, not re-sent, at recovery; J5 EX-10 settled.

**Not done.** No code, schema, migration, event model, configuration key, or
test was written; the World View `portfolio` slot stays `null`; no real
broker session was reached. Related follow-ups reported, not fixed (doc §10):
R7 the `ExecutionMode` naming, R8 `BrokerAdapter`'s dormant order stubs, R9
`system-design.md` §2 principle 1's wording, alongside #168's R1–R6.

**Documentation updated in this delivery.**
`docs/architecture/execution-engine-design.md` (revised in place: status,
invariants I4/I6–I8 amended and I10–I15 added, §6 rewritten with revised
diagrams and new recovery/configuration sections, §7 fork statuses, §8–§9),
`docs/architecture/system-design.md` (the two pointer paragraphs and the
companion-doc entry only), this entry and its `INDEX.md` row, `CHANGES.md`,
`TESTING.md`.

### 171. Authorizer stub + entry-order Execution Engine built (`execution-authorizer-and-engine`) — amends nothing, builds the first real slice of #170's Slice A design

**What was built.** Two new packages, `backend/app/governor/` (the authorizer stub, §6.2) and `backend/app/execution_engine/` (entry-order placement only, §6.3), plus additive-only edits to three files already on `main`. This is the first code (not just design) for the Execution Engine — everything above it (Strategy Scheduler, `OpportunityCreated`) was already built; everything below the boundary this task drew (the real `orders`/`trades` ledger, `SimulatedVenue`, `broker_registry`'s `execution` role, Portfolio State, fill processing, Position Monitor-lite, `OutcomeRecorder`) is deliberately not.

**Governor (`backend/app/governor/`).** `rules.py` — a pure, DB-free function, `evaluate_authorization()`, implementing §6.2's seven rules (0–6) in order, short-circuiting at the first failure: execution-mode gate → regular session → actionable → pre-trade snapshot gate → slots/duplicates → reference price + stop geometry + fixed-notional sizing → daily-loss gate (I15, implemented exactly as documented — unrealized loss, open risk including in-flight entries, and the candidate's own stop-out loss, all against the same cap; any missing mark or stop makes the whole open-exposure term `UNKNOWN`, rejected, never estimated). `engine.py` — `AuthorizerStub`, the same subscribe → own-queue → worker pattern every prior engine in this codebase uses (`LevelInteractionEngine`, decision #84): `OpportunityCreated` (normal lane) is re-validated via `Opportunity.model_validate()` (a deliberate departure from `OpportunityCache`'s raw-dict trust boundary — this is the first consumer that acts on an Opportunity with financial consequences), evaluated, and committed via `TradeLedgerPort.commit_decision()` **before** anything is published (mirrors I8's "commit before any venue call" at the decision layer). A rejected decision commits and publishes `PlanRejected` alone. An approved decision mints `opportunity_id` (`uuid4()`, matching `strategy_outcomes.opportunity_id`'s UUID column type — decision #120) and `client_order_id = "<opportunity_id>:entry"` **only at acceptance** (EX-9), then publishes `TradePlanned` → `GovernorDecision(action="approved")` → `OrderApproved` in that order — `TradePlanned`/`GovernorDecision` are NOT published on the rejected path (a judgment call: `TradePlanned`'s `entry`/`stop`/`size` fields have no defaults and are genuinely unavailable for an early rejection, e.g. the mode gate, before rule 5 ever computes them; §6.1's own data-flow diagram shows only `{PlanRejected, OrderApproved}` branching off "publish," which corroborates this reading of this task's own scope-item-1 text). `ports.py` defines `TradeLedgerPort`/`PortfolioStateReader` as narrow `Protocol`s plus their `PortfolioSnapshot`/`OpenExposure`/`TradeDecisionRecord` dataclasses — no concrete implementation ships here (see "Fork 1," below). `reference_price.py` is a small, self-contained last-trade-price cache subscribing to `PriceUpdated`, the same "each engine owns its own read state" pattern `MarketStateEngine`/`ContextEngine` already use.

**Execution Engine (`backend/app/execution_engine/`), entry-order placement only.** `engine.py` — `ExecutionEngine`, same queue+worker shape, subscribed to `OrderApproved` (critical lane; the subscriber callback only ever does `put_nowait`, so a slow venue call delays this engine's own backlog, never an unrelated critical event elsewhere — I7, AC #20, tested directly). Per order: re-validates the payload; drops `position_effect == "close"` (reduce-only/exit path — needs EX-5, still open, left as a marked extension point, not built); derives `trade_id` from `client_order_id` (`"<trade_id>:entry"`, I10 — no re-minting, since governor already mints it); checks `DecisionAuthorizationPort.has_committed_decision(trade_id)` **before writing anything** (I2's authorization gate, AC #19 entry-gate half — a spoofed/stale `OrderApproved` never reaches the ledger); performs an idempotent `OrderLedgerPort.insert_order()` (a duplicate `client_order_id` returns the stored row and sends nothing further — AC #7 client-order-id-mint half); checks the configured venue via `ExecutionVenueProvider.get_execution_venue()` supports the order's `execution_mode` (AC #5 venue-refusal half — no venue configured, or a venue whose `supported_modes` doesn't include the mode, both reject, never routed); and calls `OrderVenue.place_order()`. **Fill processing (§6.3 steps 6–7 — `on_order_update`, dedup, `OrderFilled`) is explicitly NOT built** — a judgment call, flagged: it isn't in this task's owned AC list (no #8/#9/#13/#14) and couples directly to Portfolio State, which the file boundary forbids touching. `place_order()` is still called, so a venue can begin processing an order, but nothing here consumes the resulting update; that consumer is a clearly separate, later increment (Position Monitor-lite's own task), same footing as the reduce-only guard.

**New event: `OrderStatusChanged` (EX-9).** A venue-level order-status/rejection event, distinct from the plan-level `PlanRejected` (fired before any order exists). This delivery only ever publishes `status="rejected"` — for an order that never reaches a venue (the mode/venue check) or one a venue itself rejects; the `status` `Literal` is typed narrowly to what's actually produced rather than widened speculatively for the not-yet-built fill path. Name and shape are a judgment call, overridable, per this task's own instruction.

**`TradePlanned` (R2), reconciled.** `system-design.md` §10.3's prose and `trading-intelligence-architecture.md` §18.3's `TradePlan` prose disagreed. Per this task's own instruction, `TradePlan`'s field set (the more recent document) is the base, adapted two ways, both flagged as judgment calls: `symbol` is dropped from the payload (kept on the envelope only, matching every other payload's convention — `TradePlan`'s own sketch includes it, `OpportunityCreated`/`FeaturesUpdated`/`MarketStateChanged` do not); `direction` stays the planning-layer `long`/`short` vocabulary (matching `TradePlan` exactly), distinct from `OrderApproved.side`'s order-layer `BUY`/`SELL` — the authorizer translates one to the other when it mints `OrderApproved` (EX-14 option (a) in the design doc explicitly allows the two vocabularies to differ by layer). This delivery always publishes `origin="auto"`, `corroboration=[]` (D4/§18's manual path both out of scope), and leaves `max_hold_seconds`/`scaling_plan`/`trailing_stop_rule` at `None` (not built this slice).

**`OrderApproved.position_effect` (EX-14).** Added, required, no default — every instance this codebase currently constructs has `"open"` (entry-only scope); `"close"` is reserved for the unbuilt exit path. §6.3 also lists `execution_mode`/`opportunity_id`/`origin` as eventual additions to `OrderApproved` — deliberately **not** added here (outside this task's exhaustive scope item 3); the Execution Engine derives `trade_id` from `order_id` instead, and reads `execution_mode` fresh from `Settings` rather than trusting a stamped field that doesn't exist yet.

**`opportunity_id` minting (EX-9).** The design doc cites "decision #128's 'mint when the signal is accepted'" — as currently numbered, #128 is "Backtest Runner v1 closed out" and contains no such text (most likely stale due to this log's own documented renumbering drift, e.g. #98/#99, #111/#112, #114/#115, #120/#121, #122/#123, #127/#128). Flagged rather than silently followed or silently ignored: the underlying technical requirement is well-corroborated independently (`strategy_outcomes.opportunity_id`'s UUID column, decision #120; `strategy-engine-design.md`'s own `opportunity_id: UUID` sketch), so `opportunity_id = str(uuid4())`, minted once, only on acceptance, proceeds on that evidence rather than blocking on the citation.

**Fork 1 (Saqib, 2026-09-22) — ledger/Portfolio-State ownership.** This task's file boundary forbids `backend/app/models/**` and any Alembic migration, yet §6.3/§6.2 assign the authorizer a `trades`-row commit and the Execution Engine an idempotent `orders`-row insert. Resolved: the sibling `execution-ledger-and-venue` task owns the real ledger tables, migration, and `SimulatedVenue`; this delivery is built against narrow local `Protocol`s instead (`TradeLedgerPort`, `PortfolioStateReader`, `OrderLedgerPort`, `DecisionAuthorizationPort`, `OrderVenue`, `ExecutionVenueProvider`) — no generic CRUD, each shaped around one domain operation and its failure contract (a required commit failing publishes/routes nothing further). This delivery's own tests exercise these against in-memory fakes — an explicit, scoped departure from "real Postgres 16, never mocks," confirmed with Saqib rather than assumed. `default_execution_venue_provider()` (`execution_engine/ports.py`) duck-types onto `broker_registry.get_execution_venue()` so no code here needs to change once that registry role merges. AC #7/#8/#9/#13/#14/#17(portfolio-computation-only)'s full real-Postgres verification, and AC #5's `set_execution_venue()` refusal half, are explicitly **not** claimed by this delivery — only the consumer-side contract this task owns is.

**Fork 2 (Saqib, 2026-09-22) — the new `EventType`.** `OrderStatusChanged` needs a real `EventType` member (and critical-lane membership) in `backend/app/schemas/events/envelope.py`, outside this task's literal "may edit" list (`execution.py`/`config.py` only). Resolved: one minimal additive line plus one `CRITICAL_EVENT_TYPES` entry, re-confirmed against a fresh `main` pull immediately before editing (no collision — no sibling addition existed). Recorded here as the one necessary exception to the file boundary, alongside a second, smaller one: `backend/tests/conftest.py` gained two lines resetting `governor.engine._authorizer_stub`/`execution_engine.engine._execution_engine` between tests, the same singleton-reset convention every prior engine (`LevelInteractionEngine`, `MarketStateEngine`, `OpportunityCache`, ...) already required there — without it, this task's own new tests would leak state across each other. Both exceptions are necessary registration, not license to broaden scope elsewhere in either file.

**Config (`core/config.py`), append-only.** Exactly the three-setting block specified: `execution_max_concurrent_positions` (default 1), `execution_fixed_notional_usd` (default 1000.0), `execution_daily_loss_cap_usd` (default 100.0) — each validated positive at startup via a new `field_validator` (this file's first; no prior precedent existed to follow). `execution_mode` is deliberately **not** added here — the sibling task's own config.py block, per this task's own instructions.

**AC ownership, exactly as assigned.** #3, #4, #6, #16, #17 (authorizer, fully); #5 venue-refusal half, #7 client-order-id-mint half, #19 entry-gate half, #20, and the authorizer/engine half of #22 (Execution Engine). Not claimed: anything requiring the real ledger/venue (#8, #9, #13, #14, the rest of #5/#7/#19/#22). **EX-5 and EX-12 remain open** — untouched, as this task's own §2 established neither is needed for an entry-only slice.

**Diagrams.**

Data flow, `OpportunityCreated` → authorizer stub → `OrderApproved`/`PlanRejected` → Execution Engine → `OrderVenue.place_order()`:

```
OpportunityCreated (normal lane)
        │
        ▼
AuthorizerStub._on_opportunity_created()        fast: put_nowait onto own queue (I7)
        │
        ▼
AuthorizerStub._worker_loop() ──► _process_one()
        │  Opportunity.model_validate()            malformed ──► dropped, no commit
        │  execution_mode_provider()                defensive getattr — sibling's config field
        │  MarketClock.is_regular_session()/.trading_day()
        │  capture_strategy_outcome_snapshots()      real, already-built (decision #98/#128)
        │  PortfolioStateReader.get_snapshot()        fork 1: Protocol seam, faked in this delivery
        │  ReferencePriceTracker.get(symbol)           own PriceUpdated subscription
        ▼
rules.evaluate_authorization()    pure, §6.2 rules 0-6 (see second diagram)
        │
        ├─ rejected ──► TradeLedgerPort.commit_decision() ──► PlanRejected (critical)
        │
        └─ approved ──► mint opportunity_id (uuid4) + client_order_id "<id>:entry"  (EX-9: only here)
                         │
                         ▼
                   TradeLedgerPort.commit_decision()     commit fails ──► LedgerCommitError ──► nothing published
                         │
                         ▼
           TradePlanned (normal) ──► GovernorDecision(approved) (critical) ──► OrderApproved (critical)
                                                                                      │
                                                                                      ▼
                                                                ExecutionEngine._on_order_approved()   fast enqueue (I7)
                                                                                      │
                                                                                      ▼
                                                                ExecutionEngine._worker_loop() ──► _process_one()
                                                                                      │
                                                      position_effect=="close" ──► dropped (EX-5 not built, extension point)
                                                                                      │
                                                      malformed client_order_id ──► dropped
                                                                                      │
                                                      DecisionAuthorizationPort.has_committed_decision(trade_id)
                                                          no ──► OrderStatusChanged(rejected, "no_committed_decision")
                                                                                      │ yes
                                                                                      ▼
                                                      OrderLedgerPort.insert_order()      idempotent — fork 1: Protocol seam
                                                          duplicate ──► log, send nothing (AC #7)
                                                                                      │ inserted
                                                                                      ▼
                                                      ExecutionVenueProvider.get_execution_venue()
                                                          duck-types broker_registry.get_execution_venue()
                                                          once the sibling's registry role merges
                                                          venue is None, or mode ∉ venue.supported_modes
                                                              ──► update_order_status(rejected) + OrderStatusChanged
                                                                  (AC #5 venue-refusal half)
                                                                                      │ mode OK
                                                                                      ▼
                                                      OrderVenue.place_order(instruction)
                                                          ack.status=="rejected" ──► update_order_status(rejected)
                                                                                      + OrderStatusChanged
                                                          ack.status=="submitted" ──► update_order_status(submitted)
                                                                                      │
                                                                  [fill processing / Position Monitor-lite —
                                                                   NOT built here, extension point]
```

The authorizer's internal rule pipeline:

```
OpportunityCreated
        │
        ▼
 rule 0  execution_mode == "simulated" ?           no ──► rejected: execution_mode_not_permitted   (AC #3, #4)
        │ yes
        ▼
 rule 1  MarketClock.is_regular_session() ?         no ──► rejected: outside_regular_session
        │ yes
        ▼
 rule 2  opportunity.status == "actionable" ?       no ──► rejected: not_actionable
        │ yes
        ▼
 rule 3  market_state snapshot present              no ──► rejected: snapshot_unavailable:market_state
         AND context snapshot present               no ──► rejected: snapshot_unavailable:context
        │ yes
        ▼
 rule 4  symbol has no open position/in-flight       no ──► rejected: symbol_busy
         AND open_count + in_flight_count
             < max_concurrent_positions               no ──► rejected: max_concurrent_positions
        │ yes
        ▼
 rule 5  reference_price exists                     no ──► rejected: no_reference_price
         AND stop on the correct side
             (BUY: stop < ref; SELL: stop > ref)      no ──► rejected: invalid_stop_geometry
         AND qty = floor(fixed_notional_usd
             / reference_price) >= 1                  no ──► rejected: notional_below_one_share
        │ yes
        ▼
 rule 6  daily-loss gate (I15)
         realized_loss_today = max(0, -realized_pnl_today)
         open_exposure_loss  = Σ max(qty·|avg_entry−stop|, −unrealized_pnl)   over open + in-flight
                                any missing mark/stop ──► UNKNOWN
         candidate_loss      = qty · |reference_price − stop|
        │
        ├─ open_exposure_loss == UNKNOWN                              ──► rejected: loss_exposure_unknown
        ├─ realized_loss_today + open_exposure_loss >= cap            ──► rejected: daily_loss_cap_reached
        ├─ realized_loss_today + open_exposure_loss
              + candidate_loss > cap                                  ──► rejected: projected_loss_exceeds_daily_cap
        └─ else                                                       ──► approved (qty, reference_price)
```

**Footprint, confirmed by `diff -rq` against a freshly re-pulled `main`.** New: `backend/app/governor/` (`__init__.py`, `ports.py`, `reference_price.py`, `rules.py`, `engine.py`), `backend/app/execution_engine/` (`__init__.py`, `ports.py`, `engine.py`), `backend/tests/test_governor_rules.py`, `test_governor_config.py`, `test_governor_engine.py`, `test_execution_engine.py`, `test_execution_event_schemas.py`. Edited, additive only: `backend/app/schemas/events/envelope.py` (one `EventType` member, one `CRITICAL_EVENT_TYPES` entry), `backend/app/schemas/events/execution.py` (`OrderApproved.position_effect`, new `TradePlanned`/`OrderStatusChanged` models), `backend/app/core/config.py` (the three-setting block + validator), `backend/tests/conftest.py` (two singleton-reset lines). Nothing else touched — confirmed against every path this task's own file boundary named "must not touch."

**Verified.** 75 new tests (30 pure rule-pipeline cases covering AC #17's full table including the raised-`max_concurrent_positions` and gap-through-the-stop cases; 11 config-validator cases; 9 `AuthorizerStub` orchestration cases against a real `EventBus` and fake ports; 10 `ExecutionEngine` orchestration cases including the AC #20 critical-lane-isolation timing test; 15 schema/envelope cases), all passing repeatedly and deterministically on their own. Full suite (real local Postgres 16, migrated through 0011): 885 collected; a first run passed 885/885, a second surfaced one intermittent, wall-clock-time-sensitive failure in `test_backtest_routes.py` (this project's own long-documented #119 cluster, e.g. decisions #128/#129/#131) — confirmed pre-existing and unrelated to this delivery by reproducing the identical failure on a freshly re-pulled, completely untouched `main` (809 passed/1 failed there; 809 + this delivery's 75 = 884, matching this delivery's own second-run count exactly). Zero regressions attributable to this delivery.

**Not done, stated precisely rather than left implicit.** No exit path, no reduce-only guard, no `StrategyOutcome` writing (EX-5/EX-12 still open, as this task's own §2 established). No fill processing (`on_order_update`, dedup, `OrderFilled` for entries) — a judgment call, not an oversight, see above. No real Postgres-backed ledger, no `SimulatedVenue`, no `broker_registry` `execution` role — the sibling `execution-ledger-and-venue` task's own scope; this delivery's `TESTING.md` names the exact reconciliation points. `main.py` is not wired to start either engine — outside this task's file boundary (`main.py` isn't in "may edit"); `get_authorizer_stub()`/`get_execution_engine()` both exist and are ready for that wiring once the sibling's concrete ports land.

**Documentation updated in this delivery.** This entry and its `INDEX.md` row, `CHANGES.md`, `TESTING.md`. `docs/architecture/execution-engine-design.md` deliberately **not** touched (outside the file boundary — "must not touch any `docs/architecture/*.md` other than the decision entry").

### 172. Execution ledger + `OrderVenue`/`SimulatedVenue` + Portfolio State built (`execution-ledger-and-venue`) — the ledger/venue half of #170's Slice A design, sibling to #171

Second (and, together with #171, completing) real-code delivery for decision #170's Slice A design: the ledger tables, the `OrderVenue` port and its only implementation, the `execution` registry role, and Portfolio State — everything #171's own `execution_engine`/`governor` packages were built against narrow local `Protocol`s in anticipation of, per that task's own explicit ownership fork.

**Built, per this task's own numbered scope (§4):**
- **`OrderVenue` port** (`backend/app/broker_adapters/order_venue.py`) — the interface from design doc §6.4's table verbatim (`ExecutionMode`, `OrderInstruction`, `OrderAck`, `OrderStatusReport`, `OrderUpdate`, `VenuePosition`, the `OrderVenue` ABC). `BrokerAdapter` (`base.py`) untouched, exactly as required.
- **`SimulatedVenue`** (`backend/app/broker_adapters/simulated_venue.py`) — the only `OrderVenue` implementation in this slice (EX-1). Market orders fill on the first qualifying tick at/after acceptance; limit orders fill on cross; fill timestamps are the tick's own `exchange_ts` (I9), never wall-clock; `venue_fill_id = "<client_order_id>:f<n>"` (deterministic); zero/`None` slippage/commission by default (I3, EX-8); session-guarded via an injectable `MarketClock`; not durable by design (a fresh instance has no memory of a prior instance's orders — this IS the intended restart behavior §6.9 depends on); injectable tick source, clock, and partial-fill planner for tests. 10 unit tests, no database needed.
- **`execution` registry role** (`backend/app/services/broker_registry.py`) — a third, separately-typed global (`_execution_venue`) beside the existing `streaming`/`historical` `MarketDataProvider` slots, following that file's own module-level-functions-plus-globals pattern exactly (the "one finding" this task's own prompt pre-verified). `set_execution_venue()` fails closed (`UnsupportedExecutionModeError`) when the venue's `supported_modes` excludes the configured `execution_mode` (I6, AC #5). `clear_all()` now resets all three roles — already wired into `conftest.py`'s existing global `_reset_app_singletons` fixture (#171 added the call site expecting this; no `conftest.py` edit was needed from this task).
- **Execution ledger** (`backend/app/models/execution_ledger.py`, migration `0012`) — `trades`, `orders`, `fills`, `positions`, `portfolio_state_cursor`, exactly per design doc §6.8's persistence sketch, fleshed out to concrete columns/types/constraints (see that file's own module docstring for the column-type conventions and the mode/venue pairing CHECK repeated on every table that carries both columns, not just `strategy_outcomes`). `orders.client_order_id` and `fills.(execution_venue, venue_fill_id)` are both DB-level `UNIQUE` (AC #7 ledger half, AC #8) — verified by direct `IntegrityError` tests, not merely asserted.
- **`strategy_outcomes` EX-2/EX-7 changes**, in the same migration: `execution_mode`/`execution_venue` added `NOT NULL` (backfilled `backtest`/`simulated` for every existing `is_backtest = true` row); the four snapshot columns relaxed to nullable; new `snapshot_missing_reasons` JSONB; four new CHECK constraints (`is_backtest` ⟺ `execution_mode = 'backtest'`; the mode/venue pairing; a `backtest` row still requires all four snapshots — decision #128 preserved; **every** row's NULL snapshot must have a recorded reason). **The migration counts and aborts on any pre-existing `is_backtest = false` row** rather than guessing a label (verified: zero such rows existed, so the abort path itself is exercised only by a dedicated up/down/up round-trip during development, not by a real abort in this repository's actual data). Mirrored additively into `StrategyOutcomeRecord` (ORM) and `StrategyOutcome` (Pydantic) — `execution_mode`/`execution_venue` default from `is_backtest` when a caller doesn't set them (see J4 below), and four new `model_validator`s mirror the DB CHECKs for a friendly `ValidationError` before ever reaching Postgres.
- **Portfolio State** (`backend/app/portfolio_state/`) — `engine.py`'s `PortfolioState`: `apply_fill()` (idempotent on `ledger_seq`; opens/adds-to/closes a `positions` row; realized P&L on close, bucketed to the exchange trading day via `MarketClock`; advances `portfolio_state_cursor` in the same flush), `rebuild_from_ledger()` (replays every fill past the cursor; ledger wins on any disagreement with what was already in memory, logged not silent — I12, AC #11), `get_snapshot()` (sync, no I/O; `unrealized_pnl`/`open_risk` are `None` — not `0.0` — when a held position lacks a mark/stop, per I15's "unknown treated as unbounded"). `reconciliation.py`'s `reconcile_with_venue()` — this task's own §4.6 explicitly places §6.9 step 3 here rather than in `execution_engine/` (sibling territory): approved-never-sent entries → cancelled (`stale_opportunity_not_resubmitted`); approved-never-sent exits → re-submitted (idempotent, for real, against a live `OrderVenue`); submitted/partially-filled orders the venue has lost → `expired` (`venue_lost_state_on_restart`); orders the venue DOES know → missing fills pulled in (deduped on `venue_fill_id`) and status advanced; open-order and position-quantity mismatches against the venue → reported as discrepancies, never silently adopted or discarded (I13).
- **Config** — `execution_mode: str = "simulated"` appended to `core/config.py`, validated to fail closed on anything else (AC #3, #4).

**Two real bugs found by testing against real Postgres, not just written and trusted:**
1. Postgres CHECK constraints treat a NULL boolean expression as *passing*, not failing — `snapshot_missing_reasons ? 'key'` evaluates to NULL (not `false`) when `snapshot_missing_reasons` itself is NULL, silently letting an unexplained NULL snapshot through. Fixed with `COALESCE(snapshot_missing_reasons, '{}'::jsonb) ? 'key'` in the migration.
2. SQLAlchemy's `JSONB` type binds a Python `None` as the JSON scalar `null` by default (`none_as_null=False`), **not** SQL `NULL` — which would have silently defeated EX-7's entire "nullable snapshot" mechanism and its CHECK constraints (a `None` snapshot would read back as `null`, and `IS NOT NULL` is `true` for a JSON `null`). Fixed by setting `none_as_null=True` on every nullable JSONB column in `execution_ledger.py` and the four relaxed `strategy_outcomes` columns.

**Judgment calls (stated, not hidden):**
- **J1 — `db/base.py`.** Added the one-line `from app.models import execution_ledger` import this file's own docstring requires ("every model module must be imported somewhere reachable from here … or Alembic won't see it"). Not on this task's "may edit" list; done anyway as the narrowest possible additive exception, on the same footing as #171's own two approved exceptions (`envelope.py`/`execution.py`, `conftest.py`) — flagged here for confirmation rather than silently included.
- **J2 — `PositionClosed` publish left as a seam.** Design doc §6.5 has Portfolio State publish `PositionClosed` on the critical lane immediately after a closing commit. That event's Pydantic payload model and its `CRITICAL_EVENT_TYPES` entry live in `schemas/events/execution.py`/`envelope.py` — both outside this task's file boundary, and #171's own entry above confirms fork-1 assigns "ledger/Portfolio-State/venue-registry" to this task while payload/event work stays with `execution_engine/`. `PortfolioState.apply_fill()`'s return value signals a closure so a future caller can publish once those pieces exist; nothing here invents a payload shape unilaterally or drops the requirement silently.
- **J3 — mode/venue pairing CHECK repeated on `trades`/`orders`/`positions`.** The design doc states this CHECK explicitly only for `strategy_outcomes`. The same population-safety reasoning (AC #18) applies anywhere `execution_mode`+`execution_venue` co-occur; repeated rather than left as a gap only `strategy_outcomes` happens to close.
- **J4 — `execution_mode`/`execution_venue` defaults.** `record_strategy_outcome()` (`app/trading_intelligence/performance.py`) is outside this task's file boundary and constructs `StrategyOutcomeRecord` by explicit kwargs, so it does not forward `outcome.execution_mode`. A context-sensitive SQLAlchemy default (`get_current_parameters()`) derives `execution_mode` from the row's own `is_backtest` at insert time instead, so both of today's real populations (Backtest Runner's `is_backtest=True` rows, and the pre-existing population-isolation tests' synthetic `is_backtest=False` rows) land on the correct, CHECK-satisfying value with no edit to that file. Stated plainly: this is a compatibility fallback for callers that don't set the column, not a license for a future live caller to omit it.
- **J5 — `orders.status` maintenance added to `apply_fill()`.** Nothing else in either task updates an order's own status as fills arrive; `reconcile_with_venue()`'s "unmatched_order" anomaly detection (AC #13) depends on that status being accurate, so `apply_fill()` now also advances `orders.status` (submitted → partially_filled → filled) from cumulative fill quantity — the minimal addition needed for this task's own owned ACs to be meaningfully testable, not a claim that the full order state machine (§6.3, `rejected`/`cancelled`/`expired` transitions the Execution Engine itself drives) is built here.

**Compatibility with #171, verified directly, not assumed.** A re-pull of `main` partway through this task showed #171 had already merged (`backend/app/execution_engine/`, `backend/app/governor/`, additive `envelope.py`/`execution.py`/`config.py`/`conftest.py` edits — none overlapping this task's four editable files except `config.py`'s shared append point, resolved as a two-block merge exactly as both tasks' own prompts anticipated). Read `execution_engine/ports.py` directly: its local `OrderVenue` `Protocol`, `VenueOrderInstruction`/`VenueAck` dataclasses, and `default_execution_venue_provider()`'s `getattr`-based duck-typing onto `broker_registry.get_execution_venue` all match this delivery's real `SimulatedVenue`/`broker_registry` structurally — Python's duck typing means #171's already-merged code works against this delivery's concrete types with no further change on either side. Full combined suite re-run against the merged tree: **922 passed** (885 from #171 + this delivery's 37), one run surfacing the same pre-existing #119-cluster flake #171 also documented, reproduced independently by this task.

**Acceptance criteria owned (§9) — verified, not asserted:** #5 (fail-closed venue/mode registration), #7 ledger half (DB-level `client_order_id` dedup), #8 (DB-level `venue_fill_id` dedup), #9 (fault-injection-then-restart recovery from the ledger alone, nothing ever published), #10 (the full restart-reconciliation ladder — cancelled/resubmitted/expired/advanced, each exercised against a real `SimulatedVenue`), #11 (ledger wins over corrupted in-memory state, logged), #12 (a closure never published in this delivery — by construction, since J2 leaves publishing unbuilt — is still fully recovered from the ledger), #13 (overfill and unmatched-order fills persisted and flagged, never dropped), #18 (population CHECKs, DB-level and Pydantic-level), venue/config half of #22 (regression baseline unchanged: 885 → 922, the only addition being this delivery's own 37 tests).

**Not done, stated precisely.** No wiring of `PositionClosed`'s real event (J2). No `main.py` startup sequence calling `rebuild_from_ledger()`/`reconcile_with_venue()` (outside this task's file boundary; both are ready to be called). EX-5/EX-12 remain open, untouched, as this task's own §2 established from the start. `confirmed-decisions.md` is now over the ~100KB rollover trigger `docs/decisions/README.md` documents (both #170 and #171 already flagged approaching it without acting) — flagged again, still not acted on, per the same established precedent.

**Footprint**, confirmed by `diff -rq` against a freshly re-pulled `main` (post-#171): new — `backend/app/broker_adapters/{order_venue,simulated_venue}.py`, `backend/app/models/execution_ledger.py`, `backend/alembic/versions/0012_execution_ledger_and_strategy_outcomes_mode.py`, `backend/app/portfolio_state/{__init__,engine,reconciliation}.py`, 5 new test files. Edited: `backend/app/services/broker_registry.py`, `backend/app/models/trading_intelligence.py` (additive), `backend/app/schemas/performance.py` (additive), `backend/app/core/config.py` (append, two-block merge with #171's own block), `backend/app/db/base.py` (J1, one import line). Nothing under `backend/app/execution_engine/**`, `backend/app/governor/**`, `backend/app/broker_adapters/base.py`, `backend/app/broker_adapters/ibkr_adapter.py`, `backend/app/schemas/events/**`, or any `docs/architecture/*.md` touched.

**Data flow — a fill reaching Portfolio State (this delivery's own components only; the Execution Engine's fill-processing loop that would normally sit between `on_order_update` and the ledger insert is #171's still-open EX-5/EX-12 gap, stood in for here by `reconciliation.py` and this delivery's own tests):**

```
PriceUpdated (event bus, normal lane)
        │
        ▼
SimulatedVenue.ingest_tick(symbol, price, exchange_ts)
        │   matches pending orders on `symbol`
        │   market: fills on any tick · limit: fills only once price crosses
        ▼
SimulatedVenue._apply_fill()
        │   venue_fill_id = "<client_order_id>:f<n>"   (deterministic ⇒ dedupe by construction)
        ▼
venue.on_order_update(OrderUpdate)  ───►  registered callback(s)
        │                                  (Execution Engine's fill loop — NOT built;
        │                                   this delivery's reconciliation.py and tests
        │                                   drive the next step directly instead)
        ▼
INSERT fills row
   UNIQUE(execution_venue, venue_fill_id)  ◄── AC #8: a replay inserts nothing
        │
        ▼  COMMIT                          ◄── ledger authoritative (I12); AC #9 persist-before-publish
        │
        ▼
PortfolioState.apply_fill(session, fill)
        │   SELECT orders WHERE client_order_id = fill.client_order_id
        │   SELECT trades WHERE trade_id = order.trade_id   (thesis.final_stop/final_target)
        │
        ├─ position_effect == "open"  ─► new positions row, or weighted-avg add to an existing one
        │
        └─ position_effect == "close" ─► realized_pnl delta; qty -= fill.qty
                                          qty <= 0 ⇒ status="closed", closed_at=fill.venue_ts
                                          overfill (qty<0) ⇒ clamp to 0, fill.anomaly="overfill" (AC #13)
        │
        ├─ orders.status advanced (submitted → partially_filled → filled)         [J5]
        ├─ portfolio_state_cursor.last_applied_ledger_seq = fill.ledger_seq   (same flush)
        │
        ▼
in-memory PortfolioSnapshot updated (positions / marks / realized_pnl_today / open_risk)
        │
        ▼
[SEAM, not built — J2] PositionClosed (critical lane) — once execution.py's payload model
   and envelope.py's CRITICAL_EVENT_TYPES entry land (execution_engine/'s own future scope)
```

**Internal flow — the ledger migration's table relationships:**

```
                    trades  (trade_id UUID PK = opportunity_id)
                    ├─ execution_mode / execution_venue        CHECK pairing (EX-2, J3)
                    ├─ thesis JSONB  {final_stop, final_target, structural_*, confidence, evidence}
                    ├─ decision, reasons, limits_snapshot, status
                    ├─ entry_market_state / entry_context / entry_snapshot_missing_reasons
                    └─ outcome_id ────────────────────────────────► strategy_outcomes.outcome_id
                                                                     (existing table, #120 —
                                                                      EX-2/EX-7 columns added
                                                                      by this same migration:
                                                                      execution_mode/_venue,
                                                                      nullable snapshots,
                                                                      snapshot_missing_reasons)
                         │
              ┌──────────┴───────────┐
              ▼                      ▼
          orders                 positions
    trade_id FK ──────┐    trade_id FK ──────┐
    UNIQUE(client_      │    CHECK pairing     │
      order_id)         │    (EX-2, J3)        │
    CHECK(status IN     │    CHECK(status IN   │
      8 values, §6.3)   │    open|closing|     │
    CHECK pairing       │    closed)           │
      (EX-2, J3)        │                      │
              │         │                      │
              ▼         │                      │
           fills        │                      │
    client_order_id FK ─┘                      │
      → orders.client_order_id                 │
    ledger_seq BIGINT Identity PK               │
      (also the monotonic unit                  │
       portfolio_state_cursor tracks)            │
    UNIQUE(execution_venue, venue_fill_id)        │
    anomaly IN (overfill, unmatched_order)         │
                                                    │
                                       portfolio_state_cursor
                                       execution_mode PK
                                       last_applied_ledger_seq  ── advanced by
                                         PortfolioState.apply_fill(), same
                                         transaction as the positions upsert
```

### 173. Portfolio State accounting and event worker (`portfolio-state-engine`) — commit before publication, fill-day profit/loss/fees, and holding-period-independent positions

**Scope and authorization.** Fresh GitHub `main` was `c341a2c2f3e2e38901fe2ce10a430b826201be11` (decision #172 already merged); the initial `main` checkout was clean. Contrary to the task brief's older inventory, #172 already supplied `portfolio_state/engine.py`, reconciliation, and the execution tables. Work stopped before editing to report that ownership overlap. Saqib subsequently approved revising the existing package around shared pure arithmetic, a local persistence Protocol, and an event worker while preserving reconciliation's callable API. He approved nullable R when no trustworthy immutable risk basis exists, requested **profit, loss, and fees separately**, and clarified that this automation focuses on day trading **but also supports medium- and long-term stock holdings**. This entry corrects the affected #172 behavior without rewriting that entry. The temporary identifier was `portfolio-state-engine`; #173 was assigned only after re-fetching GitHub main and checking index #172, log tail #172, and archive filenames through `134-160` together.

**One accounting implementation, two integration surfaces.** `portfolio_state/accounting.py` is pure and database-free: immutable values, Decimal arithmetic, long/short opens and adds, weighted average cost, partial reductions, full closes, separate realized profit and loss, and separately reported fees. Invalid/nonfinite prices, invalid quantities, mismatched trade/mode/symbol, same-side closes, opposite-side opens, and over-closes are rejected before arithmetic changes any state. `PortfolioState` in `engine.py` supplies the subscribe → own queue → worker path and synchronous read cache. `legacy.py` retains #172's `apply_fill(session, fill)` and `rebuild_from_ledger(session, full_rebuild=False)` for existing reconciliation; it calls the **same** pure arithmetic. A single instance cannot use both the Session API and the Protocol. There is no second independent accounting implementation, no startup wiring, venue placement, exit decision, or `StrategyOutcome` writer.

**Contracts verified against code, not the older design inventory.** `OrderApproved.order_id` is the client-order ID and its payload carries `position_effect`, symbol, side, and quantity, but no execution mode. `OrderFilled` still contains only order ID, side, quantity, fill price, and timestamp: no durable fill ID/sequence, mode, or position effect. No application path publishes it yet. Therefore approval/fill/status events are **wake-ups for authoritative ledger reads**, never a source of fill arithmetic or a reason to label a fill with process configuration. The persisted `fills` row supplies `ledger_seq` and unique `(execution_venue, venue_fill_id)`; its order supplies trade ID, mode, side, symbol, and effect. Repeated notifications are harmless. A notification with unresolved order metadata makes the snapshot unavailable until read-back resolves it; this also covers an approval arriving before Execution inserts its order. No invented in-flight zero is exposed during that race. The eventual adapter must cover durable approved reservations, not only already-inserted orders.

**PositionLedgerPort.** `ports.py` declares four synchronous domain operations, offloaded by the worker with `asyncio.to_thread`: `load_state(mode)`, `pending_fills(mode, after_cursor)`, `get_order(client_order_id)`, and `commit_fill(application)`. The checkpoint includes complete open positions with their durable IDs and lifetime totals, non-terminal orders/reservations, cursor, checkpoint timestamp, and per-symbol/per-day profit/loss/fee aggregates reconstructed from individual realized-fill attributions. Unknown history is explicit, and unresolved anomalies block usable snapshots. A fill application contains its immutable identity, expected cursor, resulting position, and `RealizedFill` attribution (including fill timestamp, MarketClock day, gross realized delta, and nullable commission). An implementation must atomically persist the position/closure, fill attribution or immutable inputs sufficient to reproduce it, and cursor, with durable per-fill idempotency and a compare-and-set against the expected cursor. Exact duplicate returns `applied=False` and committed state; conflicting key reuse or cursor conflict raises. Return success only after durable commit. **No production implementation of this Protocol ships here.**

**Cursor safety is stronger than an Identity column.** `pending_fills` must return a complete, strictly ordered **safe committed prefix** for its mode. Sequence gaps for other modes or aborted transactions are legal. PostgreSQL Identity allocation does not establish commit order: a future adapter must serialize ingestion or establish a safe watermark so a lower sequence cannot commit later behind an advanced cursor. It must not silently skip anomalous/unresolvable fills. The retained Session path refuses to skip an earlier visible fill but still requires serialized ledger writers; this task does not prove arbitrary concurrent ingestion safe. Fake-ledger tests prove consumer behavior, not these database guarantees.

**State and honest reads.** `get_snapshot(symbol=None, *, trading_day=None)` performs no I/O. Each instance is explicitly scoped to one of `backtest`, `simulated`, `paper`, or `live`; this is accounting isolation, not permission to place orders in any mode. Omitted symbol returns the mode-wide view; a supplied symbol filters positions, orders, marks, exposures, and daily totals. An unknown symbol returns `None`; a closed symbol with known history returns a known flat view with its history. Before restoration, during queued accounting, after failure, or while unresolved metadata/anomalies remain, the result is `None`. A successfully restored empty ledger is known flat. Incomplete realized history produces `None` daily amounts, never zeros. Snapshots have detached dictionaries and immutable position/order values. Exposure fields deliberately match the concepts in governor's `PortfolioSnapshot`/`OpenExposure` without importing governor or execution-engine packages; a later adapter must handle unavailable reads explicitly and convert Decimal values as needed. Buying power stays `None`.

**Order lifecycle and marks.** Positions plus only the unfilled remainder of entry orders contribute exposure; exit orders stay visible as in-flight orders without duplicating exposure. Rejection uses the existing `OrderStatusChanged` notification and authoritative read-back. Terminal cancellations/expirations and filled orders are excluded when read back. **Live cancellation/expiration tracking remains incomplete**: #171's event schema permits only `rejected`, and this task does not widen sibling-owned contracts or add their publishers. `refresh()` and restart read-back discover those persisted transitions; production needs a broader status notification or another explicit refresh trigger. Duplicate/stale approvals cannot resurrect a terminal persisted order. EventBus has no symbol-filtered subscriptions, so unheld price events are dropped before queueing and checked again at processing. Marks retain exchange timestamps, reject older/pre-opening observations, and are discarded on closure/reopening or restart; missing marks/stops remain unknown. Mark freshness policy remains a consumer/integration concern.

**Position lifetime and money reporting.** A UUID is minted on opening and persisted in the first successful position-and-cursor transaction. It is retained through adds, partial closes, restarts, and arbitrary holding durations. Full closure retires it; a later opening in the same symbol gets a new UUID, even for the same trade ID. No session-close liquidation, daily position reset, maximum holding duration, or day-trading-only rule is introduced. Every reducing fill's signed gross P&L is attributed via `MarketClock.trading_day(fill.venue_ts)` (ET date); positive deltas sum into profit and negative deltas into a positive loss magnitude. Gross P&L = profit − loss. Fees are independently attributed on each fill's own day, including entry fills; they are never subtracted from gross P&L. `reported_fees` sums supplied charges/rebates; `fees` remains `None` if any included fill's fee is unknown, with an unknown-fee count. A known empty bucket can be zero; absent commission is never asserted to be zero cost. This supports multi-day holdings without moving earlier partial realizations to a later closing day. Stock splits, dividends, financing/borrow costs, and late fee corrections have no input contracts in this slice and are not modelled.

**PositionClosed and R.** The additive payload implements the documented five fields (`position_id`, `exit_price`, `realized_pnl`, `r_multiple_achieved`, `closed_ts`) plus optional trade/mode/venue, separate profit/loss/fee fields, and an R-missing reason. `exit_price` is the quantity-weighted average of **all** reducing fills; P&L is the lifetime gross result, distinct from daily totals. The current ledger has no immutable planned-risk contract adequate for adds/stop changes. Accordingly the worker emits `r_multiple_achieved=None`, `r_multiple_missing_reason="immutable_risk_basis_unavailable"`; it never divides by the current stop distance. Numeric R awaits a persisted, explicitly defined risk basis. Missing R never suppresses a valid closure.

**Commit, then publish; no exactly-once claim.** `PositionClosed` joins the critical lane only after `commit_fill` returns a successful committed application. A required commit failure publishes nothing and makes reads unavailable; retry reloads the committed checkpoint before arithmetic. Exact duplicate application publishes nothing. There is no outbox: a successful commit followed by a crash, lost acknowledgement, publish failure, or unhandled bus event can leave **no delivered PositionClosed**. Normal processing attempts one publication per newly committed closure; there is neither an at-least-once nor an exactly-once delivery guarantee. Consumers must recover committed closed positions independently from the ledger; the future OutcomeRecorder's recovery remains outside this task. Startup does not re-publish already-applied closures.

**Corrections to the retained Session path.** #172 updated its memory after flush but before the caller's commit, removed in-flight orders on the first partial fill, counted only the last reduction on closure day, and restored no daily realized totals. Session callbacks now stage a snapshot before commit and install it only after commit; rollback installs nothing. Complete immutable fill history reconstructs lifetime and daily profit/loss/fees and remaining in-flight quantities. `full_rebuild` audits/reconstructs without resetting the cursor or replacing durable IDs. Already-terminal order status does not by itself invalidate a legitimate unapplied fill (Execution can commit status before notification). Invalid overfills stay persisted and flagged, leave the position quantity/P&L unchanged, and block usable snapshots; the old clamp calculated P&L on excess shares. Reconciliation now reports unavailable accounting as a discrepancy instead of dereferencing an absent snapshot. The Session API does not publish closures and is not claimed as the new Protocol's production adapter. Legacy repeated openings with identical `(trade, symbol, opened_at)` cannot be unambiguously linked by the existing columns and fail explicitly instead of guessing an ID.

**Component flow:**

```text
OrderApproved / OrderFilled / OrderStatusChanged       PriceUpdated
                   |                              (held symbols only)
                   v                                      |
          fast enqueue, mark read cache stale             v
                   +------------> portfolio queue <--------+
                                      |
                                      v
                               single worker
                         /                         \
           PositionLedgerPort reads                 marks (memory only)
        persisted fill + order metadata                       |
                         |                                    |
                         v                                    |
                 pure accounting                              |
                         |                                    |
     atomic commit: position + realized-fill facts + cursor    |
                         |                                    |
                         +------> committed read cache <-------+
                         |          get_snapshot(): no I/O
                         v
                PositionClosed (critical)
                  notification, no outbox

Existing reconciliation -> Session compatibility -> same pure accounting
                            caller COMMIT -> cache installation
Production PositionLedgerPort adapter / startup / outcome recovery: not wired
```

**Internal fill transition:**

```text
notification -> load committed checkpoint -> read safe ordered fill prefix
                                                |
                          resolve identity / mode / effect from ledger
                                                |
                   +----------------------------+---------------------+
                   | open/add                   | close/reduce        |
                   v                            v                     |
             new UUID or same ID          opposite side, qty <= held  |
             weighted average             gross realized delta       |
                   +----------------------------+                     |
                                                v                     |
                              attribute delta and fee to fill day     |
                                                |                     |
                   atomic idempotent commit(position, attribution, cursor)
                         | failed                 | success           |
                         v                        v                   |
                  publish nothing        install committed state      |
                  read unavailable       qty == 0 and newly applied?  |
                         |                   no -> next fill           |
                         v                  yes -> PositionClosed      |
                  reload on retry                                     |
                                                                      |
invalid arithmetic / unresolved data ----------------------------------+
  -> no guessed position, no closure event; retain source fill, flag/block
```

**Validation.** 161 focused checks passed, with no skips: 45 pure-arithmetic/fake-ledger worker cases, 15 real-PostgreSQL Session accounting cases, 7 real-PostgreSQL reconciliation cases, plus related execution/governor/ledger/venue/bus/clock regressions. PostgreSQL **18.6** was available here, not 16; a fresh isolated database was migrated through the unchanged `0012`. Tests cover multi-month long/short reductions, per-mode/ET-day attribution, partial entry remainders, fees unknown versus known, duplicate fills/notifications, invalid reversals, stable/reopened IDs, detached/unknown/restored snapshots, rollback, cursor ordering, commit-before-publication, lost commit acknowledgement, post-commit publication failure, price filtering, and worker drain/isolation. Existing close fixtures were corrected from BUY to SELL; overfill tests now verify the persisted position remains unchanged instead of accepting an invented clamp. No full-suite or real-Protocol-adapter claim. Exact command and remaining integration gaps are in `TESTING.md`.

**Documentation and boundary.** Updated §6.5 of the canonical execution design (and its historical-status note and partial-realization formula), this appended entry, `INDEX.md`, `CHANGES.md`, and `TESTING.md`. No model/migration, broker-adapter/registry, governor/execution-engine, websocket channel, main startup, or frontend edits. The decision log was already above the rollover threshold on main; archival maintenance remains outside this delivery's append-only decision-entry boundary, as #172 also recorded.

### 174. First frontend consumer of the order-lifecycle events wired (`execution-lifecycle-frontend`) — closes the `execution-engine-design.md` §8 "Frontend" deferred prerequisite; no backend logic changed

**Collision note, stated up front — second collision on this same delivery.** This delivery was built, tested, and packaged as **#172**, then renumbered to **#173** after a re-check found `execution-ledger-and-venue` had merged first and taken #172. A second re-check (again prompted directly — "one git update happen in between. check if documentation or code modification is required and rezip") found that **#173 had since been taken too**, by `portfolio-state-engine` (Portfolio State accounting and event worker, revising #172). Renumbered a second time, to **#174**. Zero file overlap confirmed both times: `portfolio-state-engine`'s own stated boundary ("No model/migration, broker-adapter/registry, governor/execution-engine, websocket channel, main startup, or frontend edits") independently confirms it, and this task's own two editable files (`backend/app/api/websocket/channels.py`, `frontend/src/App.tsx`) are byte-identical, by hash, to this task's own original start-of-session baseline even after both intervening merges.

**Unlike the first collision, this one required a real code change, not just a renumber — `PositionClosed` is no longer purely hypothetical.** `portfolio-state-engine` added a real `PositionClosed` Pydantic model to `execution.py` and added `EventType.POSITION_CLOSED` to `envelope.py`'s `CRITICAL_EVENT_TYPES` — both confirmed absent as of this task's own second pull, confirmed present by direct diff on the third. The five fields this hook originally guessed from `system-design.md` §10.3's own sketch (`position_id`/`exit_price`/`realized_pnl`/`r_multiple_achieved`/`closed_ts`, chosen defensively, before any real model existed) matched the real model exactly on arrival — confirmed by direct diff, not assumed. The real model adds further optional fields this hook did not originally carry: `r_multiple_missing_reason`, `trade_id`, `execution_mode`, `execution_venue`, and a separate `realized_profit`/`realized_loss`/`fees`/`reported_fees`/`unknown_fee_count` breakdown. `PositionClosedWire` in `useOrderLifecycle.ts` was updated to mirror the real model in full (matching this codebase's own established convention of exact wire-shape mirroring); the display type and `describeEvent()` were extended minimally — `fees` is now shown on the row (genuinely new information), `realizedProfit`/`realizedLoss` are read but not separately displayed (they're `portfolio-state-engine`'s own decomposition of the same `realizedPnl` already shown, not new information), and `r_multiple_missing_reason` is read but deliberately not surfaced per row: that decision's own text states no immutable risk basis exists yet, so R is expected to be absent on *every* closure for now — a note repeated on every single row would be constant noise, not information.

**Still cannot arrive in a running system today, for a different and more precise reason than when this hook was first written.** `portfolio-state-engine`'s own entry states plainly, twice, that nothing wires it up: "No production implementation of this Protocol ships here" and "Production PositionLedgerPort adapter / startup / outcome recovery: not wired." The worker that would call `commit_fill` and publish `PositionClosed` exists and is unit-tested (161 cases, per that entry), but nothing in a running system instantiates it. `OrderFilled` is unaffected by either merge — re-confirmed by grep against the current tree that nothing publishes it, exactly as before. Both remain exercised here only against fabricated WS messages; TESTING.md's per-event-type table is updated to state the `PositionClosed` reason precisely rather than repeat the now-superseded "no publisher and no payload model" claim.

**`channels.py`'s `POSITION_CLOSED` routing line itself needed no change** — it was already forward-declared to `"orders.status"` before either sibling merged, and remains correct now that the payload model and `CRITICAL_EVENT_TYPES` entry both exist. Only the explanatory comment above it was updated, since its prior claim ("no `CRITICAL_EVENT_TYPES` entry or payload model does yet") is now false.

**Everything else is unchanged from the #173 packaging: the channel-split judgment call** (`TradePlanned` shares `"orders.status"` rather than getting its own channel — reasoning unaffected by either merge, recorded in `channels.py` and here), **the hook's overall design** (one app-wide `"orders.status"` subscription, no REST, bounded in-memory reverse-chronological list), **the panel** (`ScannerPanel.tsx`'s row convention, `BacktestResultsPanel.tsx`'s collapsible-sidebar scaffolding), and **the `App.tsx` wiring** (one import, two identical `<main>` inclusions).

**Diagrams.** Unchanged in shape from the #173 packaging — `EventType → EVENT_TO_CHANNEL → workspaceSocket → hook → panel` and the hook's own internal `normalizeOne()` switch — with one label updated: `PositionClosed` in the cross-component diagram now reads "payload model + CRITICAL_EVENT_TYPES entry exist (decision #173) but no production adapter/startup wiring — still no publisher in a running system" in place of the earlier "reserved EventType only... left as an explicit seam" wording, which decision #173 superseded.

```
 GovernorDecision / OrderApproved / PlanRejected / OrderFilled   (events already existed — decisions #6/#9)
 TradePlanned / OrderStatusChanged                               (decision #171 — governor/ and execution_engine/
                                                                    already publish these; not yet routed to the
                                                                    frontend before this delivery)
 PositionClosed                                                  (payload model + CRITICAL_EVENT_TYPES entry now
                                                                    exist — decision #173 — but no production
                                                                    adapter/startup wiring; still no publisher in
                                                                    a running system)
        │
        │  publish(...)   ← already existed for five of the seven event types; PositionClosed's
        │                    own worker exists (decision #173) but nothing instantiates it yet
        ▼
 EventBus (§4.4)
        │
        │  same envelope, every WebSocket Gateway subscriber path
        ▼
 WebSocket Gateway (§4.12) — app/api/websocket/channels.py
        │
        │  EVENT_TO_CHANNEL[TRADE_PLANNED]        = "orders.status"   ← new, this delivery
        │  EVENT_TO_CHANNEL[ORDER_STATUS_CHANGED]  = "orders.status"   ← new, this delivery
        │  EVENT_TO_CHANNEL[POSITION_CLOSED]       = "orders.status"   ← new, forward-declared, this delivery
        │  (OrderApproved/PlanRejected/OrderFilled/GovernorDecision's routing entries already existed)
        ▼
 "orders.status" — one shared, mixed-type channel (judgment call, unchanged since #172/#173 packaging)
        │
        ▼
 workspaceSocket (frontend/src/services/websocket-client.ts)     ← already existed, unchanged
        │
        ▼
 useOrderLifecycle.ts's onMessage handler                        ← new subscriber, this delivery
        │
        ▼
 normalizeOne() → LifecycleEvent (discriminated union, camelCase) ← new, this delivery — PositionClosedWire
        │                                                            updated to mirror decision #173's real model
        ▼
 bounded, reverse-chronological events[] (React state, in-memory only — no REST, no persistence)
        │
        ▼
 ExecutionLifecyclePanel.tsx  ← new, mounted in App.tsx's <main>, both the full shell and the popped-out shell
```

**Verified.** `npx tsc -b && npm run build` clean, re-run a third time on the tree post-both-mergers. `backend/app/api/websocket/channels.py` re-verified with a real Python import against the current tree, including a new assertion (`EventType.POSITION_CLOSED in CRITICAL_EVENT_TYPES`) that wasn't meaningful to check before decision #173 added that entry. The updated `PositionClosedWire` normalization was re-exercised against two fabricated messages: the full real shape (all of decision #173's new optional fields populated) and a minimal shape (only the original five fields, matching how this event might arrive from an earlier, partial implementation) — both normalize and render correctly, confirming the new optional fields don't break handling of a message that omits them.

**Footprint, confirmed by `diff -rq` against a freshly re-pulled `main`, done a third time.** Unchanged from the #173 packaging: `frontend/src/hooks/useOrderLifecycle.ts`, `frontend/src/components/execution/ExecutionLifecyclePanel.tsx` (new); `backend/app/api/websocket/channels.py` (comment-only change this round), `frontend/src/App.tsx` (unchanged this round) — confirmed against both #172's and #173's own stated boundaries that neither touches either file.

**Not done, stated precisely.** Unchanged from the #173 packaging, restated here for completeness: no mutation or action from the frontend; no Positions/Trade Management widget of *current* holdings (`portfolio_state.get_snapshot()` is real, per #172/#173, but still unwired to any frontend consumer — a different, out-of-scope task regardless); no new REST endpoint, no new backend persistence; no end-to-end browser-in-the-loop verification against a live backend.

**Documentation updated in this delivery.** This entry and its `INDEX.md` row, `CHANGES.md`, `TESTING.md`. `docs/architecture/execution-engine-design.md` and `system-design.md` deliberately **not** touched — `portfolio-state-engine` already updated §6.5 for its own changes; nothing about the *frontend's* shape needed correcting there.

**Found and fixed, not just flagged — again.** As of this delivery's own second pull, `TESTING.md`'s dropped #171-and-earlier history (found and restored once already, in the #173 packaging) was still missing from `main` — that fix was never merged, since this delivery had only been handed to Saqib as a zip, not applied. `portfolio-state-engine`'s own `TESTING.md` section correctly preserved #172's section above it (unlike #172's own section, which had dropped everything below it), so the gap has not grown, but it also hasn't shrunk. Restored again here, the same way: #171-and-earlier's history, byte-for-byte from this session's own original pre-#172 pull, preserved beneath #173's and #172's own sections, both left unchanged.

**Heads-up, not acted on.** `confirmed-decisions.md` is now well past the ~100KB rollover trigger (flagged at #171, #172, and #173, deliberately deferred every time) — still not performed as part of this delivery; flagged a fourth time for Saqib.

### 175. Position Monitor-lite built (`position-monitor-lite`) — the in-process exit-intent decision layer EX-11's recommendation calls for; decision #171's own marked extension point, named directly there

**What was built.** One new package, `backend/app/position_monitor/` (`__init__.py`, `ports.py`, `engine.py`), plus one new test file, `backend/tests/test_position_monitor_engine.py` — nothing else. This is Position Monitor-lite's own increment, named directly by decision #171 ("that consumer is a clearly separate, later increment (Position Monitor-lite's own task)") and specified by §6.6 of the canonical execution design.

**The EX-5/EX-11 scoping call, restated plainly, not buried.** EX-11 (exit enforcement) carries a recommendation — (a) in-process monitoring — and §7.1's own closing line puts it on the "proceeds on its recommendation unless Saqib objects" list; this task proceeds on it. EX-5 (does a protective exit need a fresh Governor-class decision, or just a reduce-only guard?) is explicitly NOT on that list — §7.1 states plainly it "still needs Saqib" before a full build. This task does not wait for EX-5 and does not resolve it either: it stays inside the half of the problem EX-5 doesn't touch — deciding WHEN and WHY a held position should exit — and deliberately stops at a typed, in-process `ExitIntent`. No event is published, no order is placed, `schemas/events/execution.py` is untouched, `execution_engine`/`governor` are never called. Placing the exit order — minting `"<trade_id>:exit:<n>"` (§6.6's own output spec), choosing reduce-only-guard vs. a fresh Governor round-trip — is exactly where EX-5's answer matters, so it is left for a later task, once EX-5 is actually confirmed by Saqib.

**Precedence and idempotency, exactly matching `fill_simulator`'s convention (EX-8).** Stop checked before target — a single bar (tick or candle) crossing both resolves to `"stop"` without a separate tie branch, same as `fill_simulator.simulate_exit()`'s own ordering. EOD-flatten is keyed to the position's own entry day (`clock.trading_day(position.opened_at)`, mirroring `entry_fill.entry_ts` there), via a local `_regular_session_close_utc()` that duplicates — deliberately, not by omission — `fill_simulator.regular_session_close_utc()`'s exact ET/half-day formula rather than importing it (EX-8 recommendation (a): "reuse conventions only… write a new incremental model"; extracting a shared helper is EX-8's un-taken option (b), which the design doc itself flags as expanding the build's footprint into `backtest_runner/` — not this task's call to make). Once a position has produced an `ExitIntent`, its `position_id` is latched in an in-memory dict; every subsequent tick/candle for that position is a no-op, checked directly with prices that would have independently re-triggered stop AND target after the first intent. This is an in-process-only latch — §6.9 step 5's ledger-backed "re-arm after restart" is explicitly not built here (read per this task's own reading list, informing this shape, not its scope); a restart today loses every position's latch along with everything else the in-memory worker held, the same honest gap the rest of this slice already lives with (Execution Engine's own restart recovery is also unbuilt — decision #171).

**Own `PositionReader` Protocol, not governor's.** `governor.ports.PortfolioStateReader`/`OpenExposure` were read as a pattern reference only, not reused: that Protocol has no `target` field (the daily-loss gate never needed one) and deliberately mixes already-open positions with in-flight ENTRY orders (both count as "exposure" for governor's own purpose) — the wrong shape for a module that must only ever see genuinely filled, open positions. `position_monitor.ports.PositionReader.get_open_positions() -> tuple[PositionView, ...]` is this module's own narrow Protocol; `PositionView` carries exactly the seven fields `_evaluate()` reads (`position_id`, `symbol`, `side`, `qty`, `stop`, `target`, `opened_at` — no `avg_price`, no P&L), satisfiable by adapting `portfolio_state.snapshot.PortfolioSnapshot`/`accounting.PositionState` (read-only reference) without this module importing `portfolio_state` directly. `stop`/`target` are typed `float | None` here, not `Decimal | None` as `accounting.PositionState` stores them — a deliberate, stated choice (`ports.py`'s own docstring): this module only ever compares against tick/candle prices, which arrive as `float` on `PriceUpdated`/`CandleClosed`, matching `fill_simulator`'s own float-based convention; the `Decimal → float` conversion is the future adapter's own visible job, not hidden inside a comparison here. No concrete implementation of `PositionReader` ships in this delivery — same "ports, no adapter" precedent `governor/ports.py`/`execution_engine/ports.py` both already set (decision #171's fork 1).

**Held-symbol filtering.** Mirrors decision #173's own Portfolio State worker precisely ("EventBus has no symbol-filtered subscriptions, so unheld price events are dropped before queueing and checked again at processing"): `_on_market_event()` (the EventBus subscriber, which must stay cheap — I7) reads `get_open_positions()` once to drop an event for a symbol nobody's holding before it's even enqueued; `_process_event()` (the worker) reads it again, fresh, before evaluating — so a position closed (or newly opened) between enqueue and processing is never acted on with stale membership.

**Component flow, this task's own internal engine:**

```text
PriceUpdated / CandleClosed (ALL symbols, normal lane -- already published, no new event)
              |
              v
   _on_market_event()  [EventBus subscriber -- must stay cheap, no awaiting -- I7]
   reads get_open_positions() once -- envelope.symbol not held? -> drop, never enqueued
              | held
              v
      position_monitor queue  -- asyncio.Queue, this module's own, decision #84's pattern
              |
              v
      single worker: _process_event()
              |  re-reads get_open_positions() fresh, filters to this symbol
              v
   for each open position on this symbol:
       position_id already in self._exit_intents?  --yes-->  skip -- latched, no second intent ever
              | no
              v
       _evaluate(position, bar, clock):
           stop_touched(bar)?      --yes-->  ExitIntent(exit_reason="stop",   trigger_price=position.stop)
              | no
           target_touched(bar)?    --yes-->  ExitIntent(exit_reason="target", trigger_price=position.target)
              | no
           bar.ts >= regular_session_close_utc(clock.trading_day(position.opened_at))?
              | yes                --yes-->  ExitIntent(exit_reason="eod_flatten", trigger_price=bar.close)
              | no
              v
           None -- nothing this tick; position stays eligible for re-evaluation on the next one
              |
              v (if an intent was produced)
       self._exit_intents[position.position_id] = intent   [the idempotency latch itself]
              |
              v
   get_exit_intents(symbol=None) -> tuple[ExitIntent, ...]   [sync, point-in-time read]

   === STOPS HERE (this task's own boundary) ===  no event published, no order placed,
   no execution_engine/governor call, no schemas/events/execution.py touched.
```

**Where this sits in §6.1's own bigger data-flow diagram** (trimmed to the fan-out after `OrderFilled`; `***` marks this task's own new work — everything else on this fan-out is exactly as `main` already has it, untouched):

```text
                                                                   OrderFilled (critical lane)
                              +-----------------------------------------+----------------------------------------+
                              v                                         v                                        v
                    Portfolio State [#172/#173]              *** Position Monitor-lite ***            OutcomeRecorder [not built]
                    applies fills, COMMIT position            (this task -- #175)                      (unaffected, either way)
                    exposes get_snapshot() -- unmodified       reads open positions via its OWN
                              |                                ports.PositionReader -- no import
                              |                                of portfolio_state/*.py at all
                              |                                          |
                              |                                subscribes INDEPENDENTLY to
                              |                                PriceUpdated/CandleClosed (normal
                              |                                lane, §6.6's own Inputs list) --
                              |                                NOT the OrderFilled edge drawn
                              |                                above, which is a DEPENDENCY
                              |                                (a position must exist to monitor),
                              |                                not an EventBus subscription
                              |                                          |
                              |                                stop/target/EOD precedence + the
                              |                                idempotency latch -> ExitIntent
                              |                                          |
                              |                                          X  in-process only (see above)
                              v
                    PositionClosed (critical) -> OutcomeRecorder -> strategy_outcomes -> World View
                    [none of this row touched by this task -- unchanged from #171-#174]
```

**Test coverage.** 10 new tests in `test_position_monitor_engine.py`: a long-position stop touched by a tick; a short-position target touched by a tick; a single candle touching both stop and target in one bar (stop wins — EX-8); EOD-flatten firing exactly at the real `regular_session_close_utc` instant and not one minute before; idempotency (two further ticks after the first intent, deliberately priced to independently re-trigger stop and target, produce no second intent); an unheld symbol's tick producing nothing; `get_exit_intents(symbol=...)` filtering; two positions on the same symbol with different stops evaluated independently (only the one actually crossed produces an intent); plus two pure `_evaluate()`-level tests (no asyncio/EventBus) covering the stop case directly and the "no stop/target configured" honest-absence case (a position with neither set can never produce a `"stop"`/`"target"` intent — only `eod_flatten` stays reachable for it).

**Validation.** Fresh tarball pull, `git status` recorded as absent (a tarball, not a clone). `pip install -r requirements.txt --break-system-packages` (Python 3.12.3). Baseline established on the untouched pull first, per `ways-of-working.md`'s "confirm pre-existing flakiness before attributing any failure to new work": **48 failed, 606 passed, 319 skipped, 93 errors.** Every single failure/error is `psycopg2.OperationalError: connection to server at "localhost"... Connection refused` (`test_daily_levels.py`, `test_position_ledger_postgres.py`, `test_authorization_ledger_postgres.py`, and others needing a live Postgres this sandbox doesn't have — the `entry-lifecycle-wiring` sibling's own new Postgres-backed adapters/tests, confirmed by direct inspection, not assumed), zero relation to this task, which needs no live Postgres (confirmed correct in practice: nothing in `position_monitor/` touches the DB). After adding this delivery: **48 failed, 616 passed, 319 skipped, 93 errors** — exactly +10, the new tests, zero regressions. `diff -rq` against a second, independently freshly-pulled, untouched clone confirms the only non-cache differences are `backend/app/position_monitor/` (new directory) and `backend/tests/test_position_monitor_engine.py` (new file) — nothing else in the tree was touched.

**Decision-number reconciliation, three-source, immediately before packaging.** `INDEX.md`'s last row: #174. `confirmed-decisions.md`'s tail: `### 174.`. Archive file list unchanged, ends at `134-160.md`. No `PENDING` marker or forward reference to #175 exists in either canonical log. Assigned **#175** on that basis. **Flagged, not silently followed:** `docs/architecture/execution-engine-design.md` §6.8's persistence-sketch table (read, not edited — outside this task's file boundary) already cites `"trade_reservations (#175)"` and `"position_fill_receipts (#174)"` for two tables that match the still-undocumented `entry-lifecycle-wiring` sibling's own migrations (`0013_position_ledger_receipts.py`, `0014_authorization_reservations.py`, `backend/app/{portfolio_state,execution_engine,governor}/postgres.py`, `backend/app/db/ledger_transaction.py` — all present on this pull, all still absent from any confirmed decision entry). Those inline citations are forward-guesses written into the design doc ahead of that sibling's own actual packaging, not entries in either canonical log — the same kind of citation drift decisions #161/#163 each found and corrected elsewhere in this repository — and this task's own number is assigned strictly from the two canonical sources (`INDEX.md`/`confirmed-decisions.md`), not from prose inside an architecture doc. This makes a **collision with `entry-lifecycle-wiring`'s own eventual packaging likely** (its author may also expect #174/#175) — the same "expect at least one collision" pattern this task's own brief anticipated. If that sibling lands first, this delivery renumbers to whatever is next, exactly as #173/#174 each already did in this session.

**Not built, restated for visibility, not left to be rediscovered later.** Publishing any event; calling `execution_engine`/`governor` or placing any order; any `schemas/events/execution.py` change (no `exit_reason` field added there); any edit to `backend/app/{portfolio_state,execution_engine,governor}/*.py`, `db/**`, `alembic/**`, `main.py` (no wiring — no module-level singleton getter ships here either, since there is no concrete `PositionReader` yet to default-construct one against); manual-position handling and emergency actions (§6.6 excludes both explicitly); broker-side protective orders (EX-11's own option (b), a hard prerequisite for any real venue, not this one, per §8); §6.9 step 5's ledger-backed re-arm-after-restart (informed this task's in-memory-latch shape but is not itself built).

**Boundary.** Created only `backend/app/position_monitor/**` (new) and `backend/tests/test_position_monitor_engine.py` (new). Nothing else in the tree touched — confirmed by `diff -rq` above. `docs/architecture/execution-engine-design.md` and every other architecture doc were read, not edited — this task's own file boundary permits only this decision entry, `INDEX.md`, `CHANGES.md`, and `TESTING.md`.

### 176. Entry-order lifecycle wired to real Postgres (`entry-lifecycle-wiring`) — fill ingestion, `main.py` restart-recovery startup sequence, and governor's `PortfolioStateReader`; two of the three concrete adapters this task was scoped to build were already on `main`, undocumented

**Renumbered once, collision anticipated by both sides.** This task's own three-source re-check (`INDEX.md`/`confirmed-decisions.md` tail/archive list) reserved **#175** before packaging. A re-pull immediately before committing found a parallel, entirely file-disjoint sibling (`position-monitor-lite`, `backend/app/position_monitor/**` + one new test file — confirmed zero overlap with this task's own footprint by `diff -rq` in both directions) had landed first and correctly taken #175; that task's own decision entry explicitly named this sibling by slug and pre-committed to deferring on exactly this collision (its own words: "If that sibling lands first, this delivery renumbers to whatever is next"). Since it landed first, this delivery is **#176**, reconciled against a second, fresh three-source re-check.

Closes the gap #171 and #172 both left explicitly open: as of #172, the full entry pipeline (authorizer, Execution Engine, ledger, `SimulatedVenue`, Portfolio State) was fully built and fully tested — **against fakes**. Nothing in it persisted to a real database in a running process, and `main.py` started none of it. This task wires the real thing together for the entry side of the lifecycle only (exits, Position Monitor, `StrategyOutcome` writing — EX-5/EX-12 — remain untouched, exactly as #171/#172 both scoped from the start; Position Monitor now has its own in-process lite build, decision #175, still stopping short of placing any exit order for the same EX-5 reason).

**Found already on `main`, undocumented, when this task began — inspected, tested, and adopted rather than rebuilt (Saqib's explicit direction).** A fresh pull at task start showed `backend/app/execution_engine/postgres.py` (`PostgresOrderLedger`, implementing both `OrderLedgerPort` and `DecisionAuthorizationPort`) and `backend/app/governor/postgres.py` (`PostgresTradeLedger`, implementing `TradeLedgerPort`) — real, tested code (`test_authorization_ledger_postgres.py`, 453 lines) satisfying two-and-a-half of this task's own three originally-scoped concrete-adapter items. `docs/architecture/execution-engine-design.md`'s own banner and §§6.2/6.3 had been edited to describe this as "**As built (#175)**" — but no decision #175 existed anywhere in `INDEX.md`, `confirmed-decisions.md`, `CHANGES.md`, or `TESTING.md` (confirmed twice, by two independent fresh pulls, days apart). A genuine, confirmed process gap — code merged to `main` with no decision-log entry at all — not a design fork. Reported to Saqib before writing any code; directed to treat it as baseline after verification. Verified: migrations `0013`/`0014` (already on `main`) apply cleanly; `test_authorization_ledger_postgres.py` + `test_position_ledger_postgres.py` + `test_execution_engine.py` + `test_governor_engine.py` + `test_governor_rules.py` + `test_governor_config.py` — **153/153 passing** against a real, freshly migrated Postgres 16, before this task changed a single line. Not rebuilt: neither file is edited by this delivery. This decision entry is their first canonical documentation.

A third undocumented-but-real file was found the same way, not named in Saqib's two-adapter list but adopted on the same basis: `backend/app/portfolio_state/postgres.py` (`PostgresPositionLedger`, implementing decision #173's own `PositionLedgerPort` — #173's own words: "No production Protocol adapter is included," "not tested because it is not built," now contradicted by its presence). Also inspected, also reused unmodified — `test_position_ledger_postgres.py`'s existing coverage coincidentally already exercises it fully.

**Built, per this task's own remaining scope:**
- **`FillLedgerPort` + `PostgresFillLedger`** (new file, `execution_engine/fill_ledger.py`) — the fill-ingestion persistence seam design doc §6.3 step 6 calls for. Deliberately a **new, separate Protocol**, not a widened `OrderLedgerPort`/`PostgresOrderLedger` — that port's own docstring scopes itself to steps 2 and 5 only, and Saqib's direction was reuse, not rebuild. Idempotent on `(execution_venue, venue_fill_id)` (lock-then-check under `LOCK TABLE orders, fills IN SHARE ROW EXCLUSIVE MODE`, the same idiom `PostgresOrderLedger`/`PostgresPositionLedger` already use, not an `IntegrityError`-driven retry); `execution_venue` is read from the fill's own `orders` row, never caller-supplied (`VenueOrderUpdate`/`OrderUpdate` carries no venue identity at all); status advances monotonically from the venue's own reported status, matching `reconcile_with_venue()`'s own convention; overfill persists and is flagged (I14), a genuinely unmatched-order fill raises instead (**J1** below) rather than attempting a schema-impossible write.
- **Fill processing wired into `ExecutionEngine`** (`execution_engine/engine.py`, additive) — `on_order_update()` registered once at `start()` when a `FillLedgerPort` is supplied (optional constructor kwarg, `None` by default — every pre-existing caller/test is unaffected); the venue callback `put_nowait`s onto the **same** queue `OrderApproved` payloads already use, tagged so `_worker_loop` can tell them apart, which is what guarantees a fill can never be processed before the very `_process_one()` call that placed its order has already committed (single worker task, FIFO queue — no new locking needed for this ordering guarantee). A duplicate delivery publishes nothing further (I11); a genuine new fill publishes `OrderFilled` (critical lane, already built by #171 — no schema edit needed here).
- **`PortfolioStateAdapter`** (new file, `governor/portfolio_state_reader.py`) — the third concrete adapter, governor's own `PortfolioStateReader`. A thin translation over the **same** live `portfolio_state.engine.PortfolioState` event-worker instance `main.py` wires for real accounting (decision #173) — reshapes its `get_snapshot()` into governor's own narrower `PortfolioSnapshot`/`OpenExposure` shape. Mode-checked (refuses any `execution_mode` other than the one live instance it wraps) and not-ready-checked (raises rather than inventing a default) — `AuthorizerStub._process_one()` (unmodified) already treats any exception from this call as fatal-for-this-Opportunity-only: logged, no decision committed, worker continues. I14's "halt NEW entries" for an unresolved fill anomaly is implemented **here** (**J2** below), not as a new table/flag.
- **`main.py` restart-recovery startup wiring** (additive, entirely new block in `lifespan()`) — the §6.9 sequence, for real, for the first time: construct a Session-mode `PortfolioState`, `rebuild_from_ledger()`, connect `SimulatedVenue`, `reconcile_with_venue()` (both functions already built by #172, never previously called from anywhere but tests); on any discrepancy, log `CRITICAL` and leave the execution pipeline entirely unwired (I13 — no separate halt flag needed, the pipeline just never starts); otherwise register the venue, construct the real event-worker `PortfolioState` + `PostgresPositionLedger`, `start()` it, construct `PostgresOrderLedger`/`PostgresTradeLedger`/`PostgresFillLedger`/`PortfolioStateAdapter`, and start `AuthorizerStub`/`ExecutionEngine` via their existing `get_*()` singleton factories (both extended additively — `get_execution_engine()` gained an optional `fill_ledger` kwarg; `get_authorizer_stub()`'s signature was already correct, only its stale docstring needed fixing). Whole block wrapped in one `try/except`, soft-failing (logged, `CRITICAL` or `exception`) exactly like the pre-existing Finnhub/Polygon auto-connects just above it — a DB outage must not crash market data / Feature Engine / everything else that doesn't need it. Symmetric shutdown: `authorizer_stub` → `execution_engine` → `portfolio_state` → venue disconnect, each `None`-guarded (the block above is best-effort; any of them may never have started).
- **`get_execution_engine()`/`get_authorizer_stub()` docstrings corrected** — both previously said "main.py is NOT wired to call this" (true when #171 wrote them, false as of this delivery).
- **Design doc corrected** (`execution-engine-design.md`, Saqib's own explicit direction, the one approved exception to this task's original "don't touch other architecture docs" boundary) — every phantom "#175" attribution found (banner, §6.2/§6.3's two "As built" callouts, and four smaller inline citations at §6.3/§6.7/§6.8 this task's first editing pass missed and a second full-file grep caught) rewritten to the `entry-lifecycle-wiring` slug instead of a decision number that never existed, with the discovery stated plainly; §6.3's stale "fill ingestion and reconciliation are still unimplemented" tail corrected (both are implemented, the first by this delivery, the second by #172 and now actually called). A second, differently-shaped citation error found the same way: §6.8's `position_fill_receipts` row was attributed to **#174** — but #174 is frontend-only ("no backend logic changed" per its own entry) and never built any table; corrected to name this delivery instead, with the mistake stated inline rather than silently swapped.

**Judgment calls (stated, not hidden):**
- **J1 — a fill for a `client_order_id` with no `orders` row raises, rather than persisting anomaly-flagged per I14's literal text.** `fills.client_order_id` has a database `FOREIGN KEY` onto `orders.client_order_id` (`models/execution_ledger.py`, outside this task's file boundary) — there is no `anomaly` value that makes an FK violation insertable; I14's "still persist" is schema-impossible for this specific sub-case. Not reachable via `SimulatedVenue` in this slice: it only ever calls a registered callback for a `client_order_id` it was itself asked to `place_order()`, and `ExecutionEngine` always completes its own idempotent `insert_order()` (step 2) before ever calling `place_order()` (step 4) — so by the time any fill can exist for an id, that id's `orders` row is already committed. `FillLedgerError`, logged, no publish — the overfill sub-case (order row exists, cumulative quantity exceeds it) has no such conflict and is handled exactly per I14.
- **J2 — I14's "halt NEW entries" implemented as `PortfolioStateAdapter.get_snapshot()` raising on an unresolved anomalous fill, not a new halt flag/table.** Adding one would touch `models/execution_ledger.py` and a migration, both outside this task's file boundary. `AuthorizerStub`'s existing fail-closed handling of any `get_snapshot()` exception (unmodified — already built by #171) already gives exactly "no decision committed, logged, worker continues." No new machinery; this adapter's own raise **is** the halt.
- **J3 — `FillLedgerPort` is a new, separate Protocol/file, not an extension of `OrderLedgerPort`/`PostgresOrderLedger`.** Saqib's direction was explicit: do not rebuild `PostgresOrderLedger`. `update_order_status()`'s own implementation already hard-rejects any status other than `submitted`/`rejected` — fill-driven status advancement is a distinct concern the original Protocol's own docstring never claimed to cover (it scopes itself to steps 2 and 5; fill ingestion is step 6). Zero edits to either pre-existing, reviewed file.
- **J4 — no `conftest.py` change.** This task deliberately introduces no new module-level singleton — `PortfolioState`, `PostgresPositionLedger`, `PostgresFillLedger`, `PortfolioStateAdapter`, and the reconciliation-mode `PortfolioState` are all local variables inside `main.py`'s own `lifespan()`, never cached at module scope. `get_execution_engine()`/`get_authorizer_stub()` are the only singletons involved, and both already have working `conftest.py` reset lines from #171. Item 7 of this task's original scope ("add whatever reset lines the new singletons require") resolves to zero lines needed, not skipped.
- **J5 — the reconciliation-mode `PortfolioState` and the event-worker `PortfolioState` are two separate objects, deliberately.** `PortfolioState` itself refuses to let one instance own both the Session-based reconciliation API and the ledger/bus-based event-worker API (`_require_session_mode()`, #173's own code, unmodified) — `main.py` constructs a throwaway Session-mode instance for `rebuild_from_ledger()`/`reconcile_with_venue()` at startup, then a separate, real event-worker instance for everything after. Both read/write the same underlying tables; state is DB-driven, not held in either Python object, so running the first to completion (committed) before starting the second is sufficient — verified directly (`test_main_execution_pipeline.py`), not assumed.

**Verified, not assumed.** `execution_engine/ports.py`'s local `VenueOrderInstruction`/`VenueOrderUpdate` dataclasses were checked for real structural compatibility with `broker_adapters/order_venue.py`'s actual `OrderInstruction`/`OrderUpdate` pydantic models by direct interactive use against a real `SimulatedVenue` (not merely re-asserted from #172's own claim) — confirmed working, including the session-hours guard, before writing any pipeline code against it.

**Testing.** Real Postgres 16 throughout, no mocks. **Full regression suite: 1066 → 1081 passing, zero regressions** (15 new tests: 7 for `PostgresFillLedger` — dedup, overfill, unknown-order, monotonic status advance, venue-identity-from-order-row; 4 for `PortfolioStateAdapter` — mode mismatch, not-ready, I14 anomaly halt, correct snapshot translation; 3 end-to-end `OpportunityCreated → real Position` integration tests wiring the actual production classes directly, including a rejection path and a read-side `symbol_busy` proof; 1 restart-recovery test driven through `main.py`'s **real** `lifespan()` via `TestClient` — not `reconcile_with_venue()` called directly, which #172's own `test_reconciliation.py` already covers, but the wiring sequence itself: submit an order, exit the process, re-enter with a fresh `SimulatedVenue` — never durable across a restart by construction, so any leftover non-terminal order already **is** the "process died mid-flight" case — confirm it's marked `expired`/`venue_lost_state_on_restart` and that the pipeline resumes normally afterward). Repeated 3x for timing flakiness (async queue-hop chain across four engines); stable every time.

**Not done, stated precisely.** EX-5/EX-12 (exits, Position Monitor, `StrategyOutcome` writing) remain untouched, as both #171 and #172 scoped from the start and this task's own prompt restated. No cancel/expire path beyond what restart reconciliation already provides. The "a real, durable venue reports a fill the dead process never got to persist" branch of restart recovery (`_reconcile_known_to_venue`'s missing-fills-pulled-in path) is exercised by #172's own `test_reconciliation.py` at the function level; it is not, and cannot be, reproduced against `SimulatedVenue` at the process level (not durable across a restart by design — see J5's reasoning), so this delivery's own process-level restart test only exercises the `venue_lost_state_on_restart` branch.

**Heads-up, not acted on.** `confirmed-decisions.md` is now well past the ~100KB rollover trigger (flagged at #171, #172, #173, #174, all deferred) — flagged a fifth time.

**Footprint**, confirmed by `diff -rq` against a freshly re-pulled `main` immediately before packaging: new — `backend/app/execution_engine/fill_ledger.py`, `backend/app/governor/portfolio_state_reader.py`, `backend/tests/{test_fill_ledger_postgres,test_governor_portfolio_state_reader,test_entry_lifecycle_wiring,test_main_execution_pipeline}.py`. Edited, additive only — `backend/app/execution_engine/engine.py` (fill processing + docstrings), `backend/app/governor/engine.py` (docstring only), `backend/app/main.py` (new `lifespan()` block, both halves), `docs/architecture/execution-engine-design.md` (the phantom-#175 correction, Saqib's explicit exception). Untouched, exactly as scoped: `backend/app/broker_adapters/**`, `backend/app/models/execution_ledger.py`, `backend/app/portfolio_state/**`, `backend/app/services/broker_registry.py` (called, not edited), any Alembic migration, `backend/tests/conftest.py` (J4), `backend/app/execution_engine/postgres.py`, `backend/app/governor/postgres.py` (both found pre-built, reused unmodified).

**Data flow — `OrderApproved` through a fill to `PositionClosed` (this delivery's own new pieces marked; everything else is #171/#172/#173, called, not rebuilt):**

```
OrderApproved (critical lane, published by AuthorizerStub — #171, unmodified)
        │
        ▼
ExecutionEngine._process_one()                                    #171, unmodified
        │   PostgresOrderLedger.insert_order()  ──► INSERT orders  (found pre-built, this delivery's own
        │   (idempotent, client_order_id UNIQUE)                    first canonical documentation — J-none)
        │
        ▼
venue.place_order(instruction)  ──►  SimulatedVenue                #172, unmodified
        │   PostgresOrderLedger.update_order_status("submitted")
        │
        ▼
   [ ... later: a PriceUpdated tick reaches SimulatedVenue.ingest_tick() ... ]
        │
        ▼
SimulatedVenue._apply_fill()  ──►  venue.on_order_update(OrderUpdate)      #172, unmodified
        │
        ▼
ExecutionEngine._on_venue_update()  ── put_nowait(("venue_update", update))  ◄── NEW, this delivery
        │   same queue as OrderApproved — strict FIFO, single worker task,
        │   so this can never run before ITS OWN order's insert_order()/
        │   update_order_status() above has already committed
        ▼
ExecutionEngine._process_venue_update()                                     ◄── NEW, this delivery
        │   PostgresFillLedger.record_fill()  ──► INSERT fills               ◄── NEW file, this delivery
        │        UNIQUE(execution_venue, venue_fill_id)  — duplicate ⇒ no-op (I11)
        │        unknown order ⇒ FillLedgerError, no insert (J1)
        │        overfill ⇒ persisted, anomaly="overfill" (I14)
        │        order.status advanced monotonically from update.status
        │   COMMIT fill + order.status together (one transaction)
        │
        ▼  (only after COMMIT)
publish OrderFilled (critical lane — already built by #171; here, a wake-up signal only)
        │
        ▼
PortfolioState._on_event() → _synchronize()                        #173, unmodified — the actual
        │   PostgresPositionLedger.pending_fills() / commit_fill()  accounting worker, found
        │   position opened/added-to/closed; realized P&L on close  pre-built this delivery too
        │
        └─ closure ⇒ COMMIT position first, THEN publish PositionClosed (critical lane, #173's own
                                                                          commit-before-publish rule)
        │
        ▼
governor.PortfolioStateAdapter.get_snapshot()                                ◄── NEW file, this delivery
        │   reads the SAME live PortfolioState instance above, translated
        │   into governor's own PortfolioSnapshot/OpenExposure shape;
        │   raises first on any unresolved fill anomaly (J2 — I14's halt)
        ▼
the NEXT OpportunityCreated's rule 4 (symbol_busy / max_concurrent_positions)  #171 rules.py, unmodified
   now sees this position — proven directly, not assumed
   (test_second_opportunity_for_a_busy_symbol_is_rejected_by_the_read_side)
```

**Internal flow — `main.py`'s new startup sequence (§6.9, called for the first time from anywhere but a test):**

```
lifespan() startup, after market-data auto-connect
        │
        ▼
SimulatedVenue(event_bus=bus)  ──►  await venue.connect()                    fresh instance every boot —
        │                                                                    NOT durable across a restart
        ▼
recon_portfolio_state = PortfolioState(execution_mode)      Session-mode (no ledger/bus) — §6.9 step 2
        │
        ▼
with SessionLocal() as recon_session:
    recon_portfolio_state.rebuild_from_ledger(recon_session)         #172's own Session API, unmodified
    report = await reconcile_with_venue(recon_session, venue,        #172's own function, unmodified —
                                         recon_portfolio_state)       called from main.py for the first time
        │
        ├─ report.has_discrepancy ──► logger.critical(...)                          I13
        │                              execution pipeline NEVER wired below —
        │                              rest of the app (market data, Feature
        │                              Engine, ...) still boots normally
        │
        └─ clean ──►
              broker_registry.set_execution_venue(venue)
                     │
                     ▼
              portfolio_state = PortfolioState(mode, ledger=PostgresPositionLedger, bus)   event-worker
                     │                                                                     instance —
              await portfolio_state.start()          ◄── own internal _synchronize()       SEPARATE object
                     │                                     (§6.9 step 2, again, for the     from the one
                     │                                     event-worker's own cache)        above (J5)
                     ▼
              order_ledger    = PostgresOrderLedger(SessionLocal)        found pre-built, adopted
              trade_ledger    = PostgresTradeLedger(SessionLocal)        found pre-built, adopted
              fill_ledger     = PostgresFillLedger(SessionLocal)         NEW, this delivery
              portfolio_reader = PortfolioStateAdapter(portfolio_state)  NEW, this delivery
                     │
                     ▼
              get_authorizer_stub(bus, trade_ledger, portfolio_reader).start()    #171 singleton,
              get_execution_engine(bus, order_ledger, order_ledger,               unmodified factory
                                    fill_ledger=fill_ledger).start()              signature (+kwarg)
                     │
                     ▼
              execution pipeline live — accepts OrderApproved / fills from here on
```

### 177. World View reads the restored running Portfolio State (`world-view-portfolio-read`)

This delivery fills the read-only portfolio slot reserved by #7/#150 now that #176 wires a running Portfolio State. `main.py` supplies that same event-worker instance to World View through a lifecycle-owned read dependency after successful reconciliation, restoration, and entry-pipeline startup; shutdown clears the dependency before the worker stops. A blocked pipeline, missing reader, or unavailable snapshot returns JSON `null`. A restored flat account returns a non-null portfolio with an empty positions list. No World View writer or second Portfolio State instance is added.

The narrow typed response contains execution mode, snapshot time, open positions (ID, symbol, side, remaining quantity, average entry, stop, target), and in-flight order count. Decimal prices serialize as strings without inferred precision. World View's optional `symbol` continues to filter only Market State and Context; Portfolio and Performance remain system-wide. The existing frontend performance columns remain, while the Portfolio section shows the open-position count and rows and offers manual refresh. No marks, buying power, P&L, order controls, Position Monitor wiring, exits, or EX-5/EX-12 resolution are introduced. Cross-component and internal read-flow diagrams are in `docs/architecture/trading-intelligence-architecture.md` §15.
