# TESTING.md — Decision #134: Link Backtest Panel and Backtest Results Panel by run_id

## What this delivery is

Both `BacktestPanel.tsx` (decision #131, the trigger) and
`BacktestResultsPanel.tsx` (decision #133, the viewer) already worked
independently. `useBacktestOutcomes.ts`'s own docstring named the gap
explicitly before this delivery: the Runner is "triggered from a
separate, unlinked panel ... so a manual refresh is the only way to see
a just-finished run's rows without a full reload." Today, using both
panels together meant: run a backtest, get a `run_id` back, select and
copy it out of a plain `<span>`, click over to the other panel, paste it
into a free-text field. Every piece of information needed to skip that
was already sitting in React state on the same page — this delivery
connects it.

Full reasoning lives in `docs/decisions/confirmed-decisions.md` (decision
#134 — the open file, no archive/rollover involved this time); this file
covers what to run to verify it and what a reader should know before
touching either panel again.

**Zero backend changes.** No route, no wire-shape change, no new hook.
Confirmed directly (not assumed) before writing anything: everything
needed was already sitting in `useBacktestRun`'s `result.run_id` and
`useBacktestOutcomes`'s existing `backtestRunId` param.

## Files changed

**Modified — all frontend:**

- `frontend/src/state/WorkspaceContext.tsx` — new `lastBacktestRunId:
  string | null` field on `MainWindowState`, plus `setLastBacktestRunId`,
  modeled directly on the existing `featureEnginePanelSymbol` /
  `setFeatureEnginePanelSymbol` pair (same value-plus-setter shape,
  exposed the same way through `useWorkspace()`, flattened from
  `activeWindow` the same way). Per-Main-Window, not global — both
  panels are mounted once per active-window shell in `App.tsx`, exactly
  the same footprint `featureEnginePanelSymbol` already has, so the same
  scoping was reused rather than inventing a new one. `makeMainWindow()`
  seeds it `null`. `normalizeMainWindow()` now back-fills
  `lastBacktestRunId: w.lastBacktestRunId ?? null` for any session saved
  before this field existed — previously that function only normalized
  `subWindows`; this is the first MainWindowState-level field to need
  its own backfill, added following the same "old localStorage sessions
  shouldn't throw at render time" principle `normalizeSubWindow()`
  already established for its own fields.
- `frontend/src/types/workspace.ts` — `lastBacktestRunId: string | null`
  added to the `MainWindowState` interface, with a comment pointing back
  at `featureEnginePanelSymbol` as the pattern it follows.
- `frontend/src/components/backtest/BacktestPanel.tsx` — `BacktestForm`
  now calls `setLastBacktestRunId(result.run_id)` in a `useEffect` keyed
  on `status === "done" && result` — the exact same moment it already
  renders that value in `ResultsView`, not a separate action or a second
  code path that could drift from the render logic. `ResultsView` gained
  one plain, non-interactive line of text noting the run_id is now
  prefilled in the Results panel's filter. Deliberately **not** a
  clickable "View in Results" link/button — see "What was deliberately
  NOT built" below for why.
- `frontend/src/components/backtest-results/BacktestResultsPanel.tsx` —
  `BacktestResultsBody` gained a two-state `"auto"` / `"manual"` filter
  mode (see "The one real design decision" below for the full
  reasoning). `runIdInput`/`appliedRunId` now seed from
  `useWorkspace().lastBacktestRunId` at mount and keep following it while
  in `"auto"` mode; Apply or Clear switches to `"manual"` and freezes;
  a new "↺ Follow latest run" control switches back. A small "auto" badge
  and a "(auto)" / "(manually set)" suffix on the existing "Showing
  run_id=..." line make the current mode visible, not just internal
  state. `useBacktestOutcomes` itself is called exactly as before — same
  hook, same params shape, no new hook needed.
- `frontend/src/hooks/useBacktestOutcomes.ts` — docstring only, no
  behavior change. The paragraph describing the "separate, unlinked
  panel" gap was rewritten to state precisely what's now closed (the
  run_id link, and — as an unplanned side effect of this hook's own
  pre-existing reactive `[limit, backtestRunId]` dependency array — the
  "manual refresh is the only way" framing too, while a person is
  auto-following) versus what's still true (manual refresh remains
  meaningful for a run triggered from a different tab/operator, or while
  pinned to a different run_id manually).
- `docs/decisions/confirmed-decisions.md` — decision #134 appended (open
  file, no rollover — well under the ~100KB trigger at ~7KB).
- `docs/decisions/INDEX.md` — new #134 row.
- `docs/architecture/strategy-engine-design.md` — one new as-built note
  in §7, with two diagrams per this task's own diagram requirement: (1)
  cross-component data flow — `BacktestPanel` → `WorkspaceContext` →
  `BacktestResultsPanel`, and (2) the internal `"auto"`/`"manual"` mode
  state machine inside `BacktestResultsBody`.

**Confirmed untouched** (checked via `diff -rq` against a freshly
re-pulled clone, both before writing any code and again immediately
before packaging): everything under `backend/`, `api-client.ts`,
`useContextSnapshot.ts`, `useOpportunities.ts`,
`useOpportunityConflicts.ts`, `useStrategyOutcomes.ts`,
`usePerformanceAnalytics.ts`, `App.tsx` (no new import needed — both
panels were already mounted), `CHANGES.md`.

## The one real design decision this task called for

The task's own scope was explicit: `lastBacktestRunId` must be a
**default**, never a forced value, and a run finishing elsewhere must
**never silently overwrite an in-progress manual lookup** already sitting
in the Results panel's filter. A silent default either way (always
follow / never follow after first paint) would have violated one half or
the other of that requirement, so this needed a real, stated mechanism:

`BacktestResultsBody` starts in `"auto"` mode, seeded from whatever
`lastBacktestRunId` already is at mount (so a page reload or a
freshly-expanded panel picks up the last known run immediately, not just
runs that finish after the panel is already open). A `useEffect` keyed on
`[lastBacktestRunId, mode]` keeps `runIdInput`/`appliedRunId` in sync with
the shared value for as long as `mode === "auto"`.

The moment the person clicks **Apply** (with typed text) **or Clear**,
mode switches to `"manual"` and freezes — both are real, deliberate
choices (Clear's own "show everything" is itself a manual choice, not a
reset back to auto), and from that point a run finishing elsewhere
updates the *shared* value (so `BacktestPanel.tsx`'s own "prefilled" note
stays accurate) but no longer touches *this panel's* filter. A small
explicit "↺ Follow latest run" button is the only way back to `"auto"` —
collapsing and re-expanding the panel would also reset it, since
`BacktestResultsBody` unmounts on collapse, but that's not a discoverable
way to ask for it, so the explicit control was added rather than relying
on that side effect alone.

## What was deliberately NOT built

- **No clickable "View in Results" link in `BacktestPanel.tsx`.** Item
  4 in this task's own scope named this as a genuine either-way call.
  The Results panel's own collapsed/width state is deliberately local
  component state, not threaded through `WorkspaceContext.tsx` — its own
  header comment already states this explicitly, unchanged by this
  delivery. Making the run_id note "actionable" would have meant either
  reversing that local-state design (for a convenience this task didn't
  ask for) or inventing a second, narrower coupling on top of the one
  piece of shared state this task actually needed. A plain informational
  note was judged sufficient; automatic prefill already does the real
  work.
- **No new hook.** The existing `useBacktestRun`/`useBacktestOutcomes`
  pair already expressed everything needed — `useBacktestOutcomes`'s own
  pre-existing reactive params did the rest for free.
- **No backend changes of any kind.**
- **No changes to `api-client.ts`, `App.tsx`, or any of the four
  explicitly-excluded hooks** (`useContextSnapshot.ts`,
  `useOpportunities.ts`, `useOpportunityConflicts.ts`,
  `usePerformanceAnalytics.ts`) — confirmed via `diff -rq`.
- **No persistence changes beyond the one new field.** `lastBacktestRunId`
  rides the existing `MainWindowState` autosave/backfill machinery
  unchanged; no new localStorage key, no new cross-tab sync payload
  shape (`crossTabSync.ts` untouched — it already syncs the whole
  `mainWindows` array wholesale).

## How to verify

**Frontend build (this delivery touches nothing else):**

```bash
cd frontend
npm install   # first time only
npx tsc -b
npx vite build
```

Expected: `npx tsc -b` reports exactly the four pre-existing decision
#35 `GridPresetPicker` errors (`GRID_PRESETS` not exported, `preset`/
`setPreset` not on `WorkspaceContextValue`, one implicit-`any`
parameter) and nothing else — confirmed against a freshly re-pulled,
untouched clone immediately before this delivery's own changes were
made, so these are a known baseline, not a regression. `npx vite build`
succeeds with no errors or warnings beyond its own standard build
output.

**Manual check (no backend test suite involved — this delivery has no
Python changes to test):**

1. Start the backend and frontend as usual.
2. Expand both the "Backtest" and "Backtest Results" sidebar panels.
3. Trigger a backtest run via `BacktestPanel.tsx`. Wait for it to finish.
4. Confirm the Results panel's `run_id` filter field auto-populates with
   the just-finished run's `run_id` with no typing/pasting, an "auto"
   badge appears next to "Filter by run_id," and the results list narrows
   to that run's rows automatically (no manual "Refresh" click needed).
5. Manually type a different (or blank) value into the Results panel's
   filter and press Apply or Clear. Confirm the "auto" badge disappears
   and a "↺ Follow latest run" button appears.
6. Trigger a second backtest run. Confirm the Results panel's filter
   does **not** change while in manual mode — it should still show
   whatever was set in step 5.
7. Click "↺ Follow latest run." Confirm the filter jumps to the
   second run's `run_id` and the "auto" badge returns.
8. Reload the page. Confirm the Results panel (once expanded) still
   defaults its filter to the most recent run for this Main Window tab
   (persistence via the existing session-autosave path).
9. Open a second Main Window tab (via the `+` tab control). Confirm its
   own Results panel filter starts independent of the first tab's —
   per-Main-Window scoping, same as `featureEnginePanelSymbol`.

## A note on scope discipline

This task's own prompt named a concurrent backend session working a
historical-data-provider gap in `backend/app/backtest_runner/` and
likely `backend/app/api/routes/backtest.py`. This delivery is
frontend-only by construction and shares zero files with that boundary
list. Re-confirmed directly (not assumed) via a fresh tarball pull and a
three-source decision-log cross-check (`INDEX.md` last row,
`confirmed-decisions.md` tail, archive file list) at both session start
and again immediately before writing decision #134 — no drift either
time, no collision to reconcile, #134 was free.
