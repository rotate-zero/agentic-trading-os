# Feature Engine → Chart Migration Plan
**Status:** Stage 0 confirmed (`confirmed-decisions.md` #50). Stage 2's backend piece confirmed and built (#51). Stage 3's first two indicators, EMA and VWAP, confirmed and built (#52, #53). **Executed out of the original sequence** — Stage 2 and 3.1/3.2 all landed before Stage 1 (the Chart's first actual consumption of Feature Engine) was started, since their design questions (D2, D3) were more time-sensitive to lock in than the order this doc originally listed. Practically: Feature Engine now computes `1m`/`5m`/`15m`/`1h` SMA, EMA, and VWAP, but **nothing on the Chart consumes any of it yet, including SMA** — Stage 1 is the single real dependency blocking Stages 2.3/2.4 and every remaining row of Stage 3 from being visible anywhere. VPOC (3.6) is expected to hit D5's schema question when reached — deliberately deferred rather than forced, per standing instruction.
**Owner:** Saqib
**Companion documents:** [`system-design.md`](./system-design.md) (current architecture, incl. §1 non-goal, §4.5 Feature Engine, §4.11 chart-reading overlays), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (decisions #40/#41 — the client-side split this plan reverses; #45–#49 — the backend Feature Engine/Level Interaction Engine work this plan builds on; #50 — this plan's own direction lock; #51 — Stage 2), [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md) (Phase 4 status).

**Why this doc exists:** this migration is too large for one sitting. This file is the resumable checkpoint — if a session ends mid-migration, the next session should read this doc plus the latest `confirmed-decisions.md` entries before touching anything, rather than re-deriving state from a diff. Each step below is scoped to be independently completable, independently testable, and safe to stop after — no step should ever leave the system half-migrated for an indicator that's still in active use.

---

## 0. The decision this plan implements

**Chart-drawn indicator values come from the backend Feature Engine. The Chart is a consumer, not a calculator, for every indicator Feature Engine is able to publish.** This reverses the reasoning (not the good parts of the mechanics) in decisions #40/#41, which scoped `frontend/src/indicators/*.ts` as permanently client-side/view-only. That reasoning held when it was written — there was no backend Feature Engine to point the chart at yet. Phase 4 (decisions #45–#49) removed that constraint. Continuing to compute the same indicator twice from here forward is exactly the failure mode principle 8 (`system-design.md` §2) already warns against: *"indicators... are each computed in exactly one module and shared, never duplicated across consumers."*

**What does NOT change:** Level Interaction Engine's relationship to Feature Engine (already correct — §4.8, decision #46), the Event Bus/WebSocket transport mechanism (already built — decision #47), and backend-pushed `ChartObject`s (§4.10 — AI-placed annotations, a genuinely separate concept from indicator overlays, out of scope here).

**What does change, eventually:** every file under `frontend/src/indicators/` stops being a source of computed values and becomes, at most, a thin renderer of values the backend already computed — or is retired outright once the chart no longer calls into it.

---

## 1. Open decisions — resolve before the stage that needs them, not upfront

Flagging these now so they don't get quietly decided by whoever happens to be writing the code at the time. Each is called out again at the stage where it actually blocks progress.

| # | Decision needed | Where it blocks | Recommendation (not yet confirmed) |
|---|---|---|---|
| D1 | `FeatureSet.features` stays a flat `dict[str, float]` (`"ema_20": 231.4`) vs. becomes nested by indicator family | Stage 0 (schema is written once, ideally not revisited) | **Resolved (decision #50): flat.** Level Interaction Engine already treats it generically by key string — nesting buys readability and costs a rewrite there for no functional gain. `"cam_r1"`, `"pdh"`, `"vwap"` etc. slot into the same shape `"sma_9"` already uses. |
| D2 | How Feature Engine gets 5m/15m/1h coverage: a new aggregated-candle-close event, vs. on-demand computation reusing `candle_aggregator.py` | Stage 2, hard blocker | **Resolved (decision #51): Option A2** — event-triggered off the existing 1m `CandleClosed`, via `candle_aggregator.completes_bucket()`, with cold-start history from `aggregate_from_recorded()`. Chosen over on-demand (Option B) because B would leave Level Interaction Engine — purely event-driven — never seeing these values at all. |
| D3 | EMA: true incremental recursion (needs to persist last EMA value, not just raw closes) vs. full recompute from a bounded window each time (same shape as SMA today) | Stage 3, EMA step | Full recompute is simpler and matches decision #45's stated reason for choosing full-recompute-over-incremental for SMA ("avoid floating-point drift"), but EMA's weighting means a *truncated* window recompute isn't mathematically identical to a running EMA — needs its own small write-up before coding, not an assumed copy-paste of the SMA approach. |
| D4 | Arbitrary/user-chosen periods (chart lets someone add `SMA(37)`) vs. a fixed, config-driven period list per indicator (today: `[9, 20, 50]`) | Stage 5 | Needed eventually if the chart's existing "add any period" UX is preserved. Options and cost sketched in Stage 5 — deliberately not resolved here since it may not be worth doing before the rest of the migration proves out. |
| D5 | VPOC's underlying data (a full session volume-at-price histogram) doesn't fit a scalar `dict[str, float]` the way every other feature does | Stage 3, VPOC step | Flagging structurally now so it isn't discovered mid-implementation. Likely needs its own field on `FeatureSet` or its own event, not a `"vpoc": <float>` entry pretending the histogram doesn't exist. |

---

## 2. Guiding constraints (carried from existing project principles — not new rules)

- **Full recompute over incremental, unless a specific indicator's math forces otherwise** (decision #45's precedent) — resolve per-indicator, not blanket-assumed.
- **Approximations surfaced, never hidden.** If a chart indicator can't yet be sourced from Feature Engine (wrong timeframe, non-standard period, not yet migrated), the UI should show that plainly — greyed out / a small "not yet backend-sourced" affordance — never silently fall back to the old client-side math without saying so. Silent fallback would recreate exactly the two-places-can-disagree problem this migration exists to remove.
- **Dead code flagged, not deleted unilaterally** (decisions #35's precedent, applied identically to `resample.ts`). Once an indicator file under `frontend/src/indicators/` has no remaining caller, it gets flagged in `confirmed-decisions.md`/`future-ideas.md` the same way, not silently rm'd in the same commit that migrates it.
- **Docs updated in the same change that changes architecture**, not after. Each stage below names exactly which doc(s) it touches.
- **Edit-safety pattern** (`assert content.count(old) == 1` guards) for any multi-point find/replace, same as always.
- **Real backend tests against real local Postgres**, no mocks standing in for the architectural claim being tested.
- **No live browser click-through in this environment** — every frontend step below still ends with the existing honesty caveat: `tsc -b`/`vite build` clean is verified; on-screen behavior is not, until you confirm it yourself.

---

## 3. Stage 0 — Lock the direction in writing (no application code)

Purely documentation. Small, self-contained, safe to do in a five-minute session on its own.

- [ ] **0.1** — Append a new numbered entry to `confirmed-decisions.md` stating the direction change, explicitly referencing #40/#41 as superseded-in-part (their mechanics — instance-based config, per-indicator files — stay; their "permanently client-side" scoping does not). Decisions are append-only, so this reads as a correction entry, same pattern already used elsewhere in the log.
- [ ] **0.2** — Edit `system-design.md` §1's non-goal line and §4.11's "distinct category... not a second one" paragraph to state the target end-state (chart consumes Feature Engine output) plus the honest interim state (migration in progress, tracked in this file) — not a claim that it's already done.
- [ ] **0.3** — Add this file to `system-design.md`'s companion-documents line at the top, and add a short pointer to it in `phase-roadmap.md`'s Phase 4 status paragraph.
- [ ] **0.4** — Resolve D1 (schema shape) explicitly as part of 0.1's decision text, since it's cheap to settle once and expensive to revisit after Stage 1 code exists.

**Exit criteria:** three doc files updated, one new decision entry, zero application code touched. Safe stopping point.

---

## 4. Stage 1 — Prove the pipe on the smallest possible slice (SMA, 1m only)

Deliberately does *not* try to migrate all 15 indicators at once. Proves the mechanism — Chart actually reading `FeaturesUpdated` instead of computing locally — on the one indicator Feature Engine already has (`sma_9/20/50`, 1m). Everything in Stage 3 is a repeat of this shape per indicator, so getting this one right first is the highest-leverage step in the whole plan.

- [ ] **1.1** — Backend: add a small REST read endpoint for chart-side backfill (either extend `GET /intelligence/state` or add a narrower `GET /features/state?symbol=&timeframe=` — decide based on whether the Feature Engine panel and Chart should share one response shape or want different ones; lean toward sharing, since `FeatureEngine.get_snapshot()` already returns exactly this shape). Purpose: a newly opened chart pane needs *some* value immediately, not just from the next live tick.
- [ ] **1.2** — Frontend: new hook, e.g. `useFeatureEngineValues(symbol, timeframe)`, parallel in shape to the existing `useIntelligenceState.ts` — REST call on mount for backfill, subscribe to the already-wired `features.updated` WS channel for live updates.
- [ ] **1.3** — Frontend: `utils/indicators.ts`'s `computePriceIndicator` gains a Feature-Engine-backed path for `SMA` specifically when `timeframe === "1m"` and `period ∈ {9, 20, 50}` (today's backend-supported set) — falls back to the existing local `sma.ts` computation *with a visible "not backend-sourced" indicator* otherwise, per the surfaced-approximation constraint above. Not a silent branch.
- [ ] **1.4** — Verify: real backend + real frontend running together, a chart pane's SMA(9) on 1m matches the Feature Engine panel's `sma_9` value tick-for-tick, confirmed via the already-existing panel as ground truth (no need to hand-verify the math again — decision #45/#49 already did that).

**Exit criteria:** one indicator, one timeframe, provably sourced from the backend on a real running chart (to the extent verifiable without a live browser session — flag the click-through gap explicitly, same as every prior frontend decision). This stage is the template every Stage 3 step repeats.

---

## 5. Stage 2 — Timeframe coverage (blocks everything except 1m)

**This is the real architectural fork (D2) — resolve it explicitly before writing code here, not while writing code.**

**Option A — Event-driven.** `candle_aggregator.py`'s hierarchical 1m→5m→15m→1h building logic gets a companion that, instead of only running on-demand inside a `GET /market/candles` request, also runs continuously and emits a new event (e.g. `AggregatedCandleClosed`, or simply publishes `CandleClosed` with `timeframe="5m"` etc.) the moment a coarser bucket actually closes. Feature Engine subscribes to it exactly like it already subscribes to the 1m `CandleClosed`. Consistent with "Feature Engine reacts to events, never polls" — the same shape as everything else in the module — but it's genuinely new continuous background work, not a small addition to an existing on-read function.

**Option B — On-demand.** Feature Engine (or a route in front of it) calls `candle_aggregator.aggregate_from_recorded()` directly when a 5m/15m/1h value is requested, computing SMA/EMA/etc. over the returned bars at request time rather than maintaining rolling state per timeframe. Cheaper to build, but reopens the "recompute on every read vs. maintain memory" question decision #45 already argued through and rejected for 1m (DB round-trip cost at ~100-symbol scale, and it stops being "compute once" in the push sense — it becomes compute-on-every-consumer-request, which is closer to what the frontend does today than what Feature Engine is supposed to replace it with).

- [x] **2.1** — Decide A vs. B (or a hybrid: event-driven for 1m/5m since those are cheap and frequent, on-demand for 1h/1d since they're rare) and write the decision into `confirmed-decisions.md` with the reasoning, before any code. **Resolved: Option A2** — event-triggered off the existing 1m `CandleClosed`, delegating cold-start backfill to `candle_aggregator.aggregate_from_recorded()` rather than maintaining a second accumulator or polling. See decision #51.
- [x] **2.2** — Implement whichever is chosen. Scope to SMA only again at this point — don't combine "new timeframe" and "new indicator" risk in the same step. **Done** — `candle_aggregator.bucket_start_for()`/`completes_bucket()` (new public functions, shared with the read-side aggregator rather than reimplemented), `feature_engine/engine.py` extended to check all three widths on every 1m close. A real design simplification was found mid-implementation, not planned upfront: `FeatureSet` only ever carries `close`, so no DB read is needed at bucket-completion time at all — see decision #51 for the full reasoning.
- [ ] **2.3** — Extend Stage 1's frontend path to request/accept non-1m timeframes. **Not started** — Stage 1 itself (the Chart actually consuming Feature Engine for anything, even 1m SMA) hasn't been built yet; this step depends on it.
- [ ] **2.4** — Verify against a real chart pane switched to 5m/15m/1h, each cross-checked against a manually-computed SMA over the same visible candles as a sanity check (not just "the number changed"). **Not started**, same dependency as 2.3. Backend-side correctness (2.1/2.2) verified independently via `test_candle_aggregator.py`/`test_feature_engine.py` — 122 passed, stable across 4 runs, including a real Postgres cold-start test — but that's Feature Engine's own output being correct, not yet the Chart displaying it.

**Exit criteria:** SMA sourced from Feature Engine on every timeframe the chart actually supports (1m/5m/15m/1h — 1d and 4h have their own pre-existing caveats from decision #44 and can be scoped out explicitly rather than silently skipped).

---

## 6. Stage 3 — Expand indicator coverage, one indicator per step

Each row below is its own step: implement backend computation → add to `FeatureSet.features` → wire into the Stage 1/2 frontend path → verify → flag (don't yet delete) the old frontend file. Do not start the next row until the current one is verified end-to-end — this is the stage most likely to span many separate sessions, so each row needs to be a safe stopping point on its own.

| Step | Indicator | Backend shape | Notes |
|---|---|---|---|
| 3.1 | EMA | Full-window recompute, seeded by SMA then recursed forward — resolves D3 | **Done (decision #52).** Shares the same rolling window SMA already uses; window capacity widened to `max(sma_max, ema_max*seed_multiplier)`. Stricter warm-up than SMA by design — proven end-to-end. 126/126 backend tests passing. |
| 3.2 | VWAP | Session-anchored accumulator (cumulative price×volume / cumulative volume), reset at session boundary via `MarketClock` | **Done (decision #53).** Symbol-keyed (not per-timeframe) — computed once from 1m bars, same value attached to every timeframe's FeatureSet, since VWAP doesn't actually vary by chart timeframe. New `MarketClock.is_regular_session()`. 134/134 backend tests passing. |
| 3.3 | Previous Day Close/High/Low (PDC/PDH/PDL) | Looked up once per session from the prior day's already-closed daily bar, not recomputed per tick | Cheapest of the remaining ones — a lookup, not a rolling computation |
| 3.4 | Premarket High/Low (PMH/PML) | Running max/min over the current session's premarket window only | Resets daily at the session-boundary already defined for VWAP |
| 3.5 | Camarilla Pivots (PP, R1–R4, S1–S4) | Pure function of 3.3's PDH/PDL/PDC — no new state, just formula applied to values already computed | Natural to build immediately after 3.3, reusing its output |
| 3.6 | VPOC | Per D5 — likely its own field/shape, not a plain float in `features` | Flag this explicitly to Saqib as "found a real schema question" the moment it's reached, rather than forcing it into the existing shape to keep the table tidy |

**Exit criteria per step:** the indicator appears correctly in `FeatureEngine.get_snapshot()` / the Feature Engine panel first (proves the math, independent of the chart), *then* gets wired into the chart path. Two separate verification points per indicator, not one combined leap.

---

## 7. Stage 4 — Retire the frontend calculation files

Only once every indicator that file computes has a working backend equivalent wired into the chart.

- [ ] **4.1** — For each of `sma.ts`, `ema.ts`, `vwap.ts`, `previousDayLevels.ts`, `premarketLevels.ts`, `camarillaPivots.ts`, `vpoc.ts`: confirm zero remaining callers via `utils/indicators.ts`'s dispatcher.
- [ ] **4.2** — Flag each (not delete) in `confirmed-decisions.md`, same treatment `GridPresetPicker.tsx`/`resample.ts` got — a record of *why* it's now dead, not a silent removal.
- [ ] **4.3** — `sessions.ts` is shared infrastructure several of the above depend on for session-boundary math — check whether anything else still needs it (`VolumeAvgIndicatorConfig`, session-aware chart code) before flagging it alongside the rest; it may outlive the indicator files that currently import it.
- [ ] **4.4** — Decide, separately and explicitly, whether to actually delete the flagged files in a later pass or leave them flagged-but-present indefinitely — this is Saqib's call each time, per standing practice, not a default outcome of flagging.

---

## 8. Stage 5 — Configurable/arbitrary periods (D4)

Only worth doing if Stage 1–4 preserved the "add any period" UX and it's now the visible gap (e.g., someone adds `SMA(37)` and sees the "not backend-sourced" flag from Stage 1.3 persistently). Two shapes, not decided here:

- **5a — Per-symbol requested-period registry.** Chart tells the backend (via a small REST call or a field on the subscribe request) "I need `SMA(37)` on NVDA 5m," Feature Engine adds it to that symbol's computed set going forward. Matches "compute once" if multiple chart panes end up wanting the same custom period — it's computed once server-side regardless of how many panes display it.
- **5b — Leave uncommon periods client-side permanently**, explicitly scoped (not silently) as the one remaining exception to "everything comes from Feature Engine," with the visible flag from 1.3 staying in place by design rather than as a temporary gap.

**This step is explicitly optional and last** — flagged so it doesn't get skipped from the plan entirely, not because it's assumed necessary.

---

## 9. How to resume this migration in a new session

1. Read this file's checkboxes to find the last completed step.
2. Read `confirmed-decisions.md`'s most recent entries — if a step above says "write the decision before coding," check whether that entry already exists before re-deciding it.
3. Read `phase-roadmap.md`'s Phase 4 status paragraph for the one-paragraph summary of where the backend stands overall.
4. Do not skip ahead to a later stage's indicator/timeframe work if an earlier stage's verification checkbox isn't ticked — each stage is a real dependency of the next, not just an ordering suggestion.
5. Update this file's checkboxes as part of the same change that completes a step — same discipline as every other doc in this project.
