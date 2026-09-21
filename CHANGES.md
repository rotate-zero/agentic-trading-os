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
