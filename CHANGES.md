# Strategy Performance gains a Live/Backtest toggle

Copy this into your repo root, overwriting the existing paths listed
below. Frontend-only delivery: **zero changes to any file under
`backend/`.** Confirmed by `diff -rq` against a freshly re-pulled clone
of current `main`, both before writing anything and again immediately
before packaging this delivery.

**Decision number:** observed next number at session start was `#137`
(decision-log tail — `INDEX.md`/`confirmed-decisions.md` — re-checked
against a second fresh clone immediately before packaging; unchanged
from session start, no collision this time). **Final number assigned:
#137.**

## What this closes

`StrategyPerformanceSummary` (`InfoTab.tsx`) has, since decision #127,
called `usePerformanceAnalytics()` with **no arguments** — meaning
`isBacktest` was always `undefined`, meaning the backend's own default
(`false`, live-only) was the only thing this section could ever request.
Since no Execution Engine/Position Monitor exists yet to write a live
`StrategyOutcome` row, that section was structurally guaranteed to read
"no data yet" forever — regardless of how many backtests ran, even
though real backtest-derived rows exist today (decision #128 onward).
This delivery adds a Live/Backtest toggle so the same section can show
either, closing the last "backend capability nobody can see" gap in this
particular read path.

## Files changed

- `frontend/src/components/workspace/InfoTab.tsx` — the only code
  change. `StrategyPerformanceSummary` gained:
  - Local state `view: "live" | "backtest"` (default `"live"` —
    preserves prior behavior for anyone who never touches the toggle).
  - A two-button toggle, styled to match this file's own existing
    General/connector tab buttons.
  - `usePerformanceAnalytics({ isBacktest })` with an explicit
    `true`/`false`, never `undefined`.
  - An always-visible header label — `Strategy Performance — Live` /
    `— Backtest` — not just an implied state from button highlighting.
  - A small provenance line, shown only in Backtest mode: "Derived from
    backtest StrategyOutcome data — not live trading results."
  - Mode-specific empty-state copy (see below).
  - **A real gap found and fixed while wiring the toggle, not part of
    the original ask:** `usePerformanceAnalytics.ts`'s `load()` sets
    `loading=true` but doesn't clear `winRateByHour`/`sessionExpectancy`
    until the new fetch resolves — invisible before this decision, since
    the hook was always called with static filters. A toggle-driven
    `isBacktest` change makes that a real, visible defect: switching
    Live → Backtest would keep showing the old Live numbers under a
    header that already says "Backtest," for the duration of the new
    request. Fixed **entirely inside `InfoTab.tsx`** — the render's
    loading gate widened from `loading && isEmpty` to `loading` alone.
    `usePerformanceAnalytics.ts` itself is untouched.
- `docs/architecture/strategy-engine-design.md` — §5 (Performance
  Intelligence) gained one new as-built note with two diagrams
  (cross-component data flow; internal toggle/loading-gate state flow).
- `docs/decisions/confirmed-decisions.md` / `INDEX.md` — decision #137.
- `TESTING.md` — rewritten for this delivery (delete-first).

## Deliberately NOT built

`strategyName`/`strategyVersion` filters — both routes and the hook
already support them, but no source of selectable strategy names exists
anywhere in this codebase today. Adding selectors is a separate,
later task (real UX/data-source design), not a natural extension of a
two-state Live/Backtest toggle. Stated as a deferral, not an omission.

## Tests

`npx tsc -b` — identical to the untouched-clone baseline: exactly the 4
known `#35 GridPresetPicker` errors, zero new. `npx vite build` — clean.
No frontend test framework exists in this codebase to add an automated
check to; query-string construction (`is_backtest=true`/`false`) was
verified by direct source trace of `_performanceAnalyticsQuery`
(`api-client.ts`, unchanged), not a live network observation — no
backend/Postgres was available in this delivery's own environment. See
`TESTING.md` for the full manual verification checklist.
