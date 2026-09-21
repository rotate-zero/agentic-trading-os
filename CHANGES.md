# CHANGES — decision #169: Phase 4 scale/load investigation (`phase4-scale-load-measurement`)

## Current delivery

Added `backend/scripts/measure_live_pipeline_scale.py`, an opt-in measurement
harness (not pytest-collected) that finally measures Phase 4's exit
criterion — "100-symbol streaming with a `FeatureSet` per symbol and no
dropped ticks" — which decision #164 recorded as never having been
demonstrated. The harness wires the real live-path objects (`FeatureEngine`,
`LevelInteractionEngine`, `MarketStateEngine`, `ContextEngine`,
`StrategyScheduler`, `OpportunityCache`, `CandleRecorder`, `LiveTickRelay`),
in the same classes and start order `main.py`'s `lifespan()` uses, against a
real scratch PostgreSQL 16 database, driven by a synthetic in-process tick/
candle provider — never a real feed. It ramps N = 1, 10, 25, 50, 100
synthetic symbols through a tick-ingestion stage and a candle-burst stage,
using `queue.join()` (the same primitive `MarketStateEngine.settle_replay()`
already uses) for authoritative per-stage drain detection rather than
polling published-event counts, which would have been wrong for
`LevelInteractionEngine` specifically (it only publishes on a zone
transition, not once per item processed — verified directly against
`level_interaction_state`'s own `updated_at` timestamps during the harness's
own smoke test).

**Result: at N=100 with a 16-candle burst, both engines fully drained with
exact 100/100 per-symbol coverage and no drops — FeatureEngine in 2.74s,
LevelInteractionEngine in 4.19s, both 14–20x inside the 60-second
per-candle-minute production budget.** A supplementary stress point (same
N=100, a 60-candle burst — beyond the requested ramp, added because it was
cheap and directly answers "how much margin") still held 100/100 coverage at
9.11s/14.03s. `MarketStateChanged` coalescing under a fast synthetic burst
(400 of a possible 1,600 at N=100/K=16) is `DebounceScheduler` (decision
#10/#155) working exactly as designed, not evidence of a drop.

Three real methodology bugs were found and fixed during the harness's own
development — documented as findings in the decision entry rather than
silently patched: (1) an event-count-based drain check that would have
declared "done" while `LevelInteractionEngine` still had real backlog; (2) a
tight burst-publish loop that gave downstream worker tasks zero chance to
run between bursts, because an unbounded `asyncio.Queue.put()` never
actually suspends the coroutine; (3) this harness's own synthetic
historical `candle_ts` colliding with `TickIngestBridge`'s real-wall-clock
stale-bucket safety net, producing a harmless but noisy spurious
duplicate-candle warning — never occurs in production, where `candle_ts`
always tracks real time.

`docs/roadmap/phase-roadmap.md`'s Phase 4 status paragraph: the exit-criterion
sentence rewritten from "has not been demonstrated in the repository record"
to a measured statement citing decision #169's numbers and what remains
unmeasured (real feed, real tick burstiness, provider symbol caps).

**Deliberately NOT touched:** `docs/architecture/scanner-design.md`'s §7
"100-symbol concurrency prerequisite" bullet — that bullet is about Finnhub's
free-tier WebSocket symbol-count ceiling (a data-*provider* question, still
genuinely open, unrelated to and unverified by this backend-processing
measurement) — reported as a follow-up in the decision entry rather than
edited, since this measurement's synthetic in-process provider never
exercised a real feed. Investigation only: no fix implemented, no option
chosen — four options laid out for Saqib in the decision entry. Zero
`backend/app/**`, `frontend/**`, or `backend/tests/**` changes.

## Boundary

Exactly five files change: the new harness script, `docs/roadmap/phase-roadmap.md`
(one sentence), the two live decision-log files, `CHANGES.md`, and
`TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #168: Execution Engine & Portfolio State design doc landed (`execution-engine-design`)

## Current delivery

Added `docs/architecture/execution-engine-design.md`, a DRAFT design pass for
the Execution Engine and Portfolio State — the modules that gate D17's live
half and the whole Decision/Governor/Planning tail. It contains a verified
built / partial / not-built inventory of the downstream pipeline (every claim
cited to `path:symbol` and machine-checked); thirteen places where the as-built
code disagrees with the prose design; a field-by-field map of what one live
`StrategyOutcome` needs; three candidate first slices compared (simulated-venue
auto path recommended; manual-first and IBKR paper analysed); component designs
with data-flow and internal-flow diagrams for the Execution Engine, a simulated
venue, Portfolio State, a minimal Position Monitor, and an `OutcomeRecorder`;
fourteen open forks `EX-1…EX-14`; deferred prerequisites; and proposed
acceptance criteria for a later build task.

`docs/architecture/system-design.md` gains pointers only (companion-doc entry,
one paragraph each under §4.6 and §4.9). Decision #168 and its `INDEX.md` row
record the delivery. **Nothing is decided, built, or migrated**; every fork is
left for Saqib.

No backend or frontend application code, schema, migration, event model, or
test changed.

## Boundary

Exactly six files change: the new design doc, `system-design.md` (pointers
only), the two live decision-log files, `CHANGES.md`, and `TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #167: close the two low-risk documentation status-drift follow-ups

## Current delivery

Closed the two low-risk documentation drift follow-ups recorded by decision
#164. The `docs/README.md` folder table now identifies the existing standalone
`trading-intelligence-overview.md` diagram while leaving the accurate `api/`
placeholder unchanged. Phase 3 in `milestone-tracker.html` now reflects the
verified Finnhub streaming, Polygon historical/fallback, and manually connected
IBKR both-role provider architecture; its exit criterion and three-item shape
are unchanged.

No backend or frontend application code changed.

## Boundary

Exactly six documentation files change: `docs/README.md`,
`milestone-tracker.html`, the two live decision-log files, `CHANGES.md`, and
`TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #166: explicitly exclude the deferred GridPresetPicker sketch

## Current delivery

Restored a clean active frontend TypeScript/build baseline by explicitly excluding
the unreachable, deferred `frontend/src/components/workspace/GridPresetPicker.tsx`
sketch from `frontend/tsconfig.json`. The sketch remains untouched and workspace
preset save/export remains deferred; no live frontend source, backend code, or
dependencies changed.

Updated the frontend-build note in `backend/README.md`, Future Ideas entry 18, the
decision index/log, and this task's testing record. The active program now passes
`npx tsc -b`, and `npm run build` passes its TypeScript and Vite stages.

## Boundary

Exactly seven files change: `frontend/tsconfig.json`, the scoped frontend note in
`backend/README.md`, Future Ideas entry 18, the two live decision-log files,
`CHANGES.md`, and `TESTING.md`. No backend tests are run because no backend code
changes.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #165: first-class sweep outcome filtering

## Current delivery

Added first-class `sweep_id` filtering to `GET /intelligence/strategy-outcomes`
and removed the sweep-results frontend fan-out. The route validates UUIDs,
requires `is_backtest=true`, joins through `backtests.run_id`, preserves
global newest-first ordering and limit semantics, and AND-combines with
`backtest_run_id`. The sweep hook now makes exactly two requests per refresh:
run metadata plus all sweep outcomes. The runs response remains visible so
zero-outcome runs are not hidden.

Updated the route regression coverage, API-client documentation, architecture
record, decision log, and task-specific testing record. Sweep execution,
outcome persistence, rendering, schemas, models, and migrations are unchanged.

## Boundary

Exactly nine files change in the completed delivery: the existing route and
route test, the API client and sweep hook, the backtest-runner architecture
record, the two live decision-log files, `CHANGES.md`, and `TESTING.md`.

<!-- Previous delivery record retained below. -->

# CHANGES — decision #164: documentation status synchronization

## Current delivery

Documentation-only synchronization of the two living status surfaces that had
fallen behind the implementation and decision record. No architecture or
product decision changes, and no backend or frontend files change.

- `docs/roadmap/phase-roadmap.md` — updates only `Status (living)`: refreshes
  Phase 2–4 facts, replaces the false Phase 5–6 “not started” statement with
  verified built/partial/not-started boundaries, records that the Phase 4
  100-symbol/no-dropped-ticks exit criterion is not demonstrated, and adds the
  delivery's single plain-text status diagram.
- `docs/architecture/scanner-design.md` — changes only the header `Status` line
  to distinguish the real on-demand scanner/universe/UI implementation from
  the continuous cadence, promotion, discovery, and spread work that remains
  unbuilt. The `DRAFT` label stays unchanged.
- `docs/decisions/INDEX.md` — removes the stale hardcoded upper bound from the
  introduction and adds this delivery's row at final numbering.
- `docs/decisions/confirmed-decisions.md` — appends one documentation-sync
  decision; existing entries remain immutable.
- `TESTING.md` — fresh task-specific evidence, collision, continuity, link,
  footprint, and archive verification record.

Read-only drift findings outside this exact boundary are reported in
`TESTING.md` and the new decision entry; none were corrected here.

## Boundary

Exactly six files change: this file, `TESTING.md`, the roadmap, the scanner
design header, and the two live decision-log files. No other documentation,
application code, test code, migration, archive, Git history, or existing
decision content changes.
