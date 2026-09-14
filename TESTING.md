# TESTING.md — Decision #133: Backtest Results frontend panel

## What this delivery is

A new, frontend-only "Backtest Results" panel — the first thing in this
codebase that renders a persisted `StrategyOutcome` row with
`is_backtest=True` (Backtest Runner v1, decision #128) anywhere outside
a `curl` call or a direct Postgres query. `GET /intelligence/
strategy-outcomes` (decision #123) got real `is_backtest`/
`backtest_run_id` filtering from decision #130 specifically so those
rows would be queryable in isolation from (someday) real live trades —
nothing had ever rendered them before this.

Full reasoning lives in `docs/decisions/archive/122-133.md` (decision
#133 — see "A note on this file's structure" in the note below on why
it's in the archive, not the open file); this file covers what to run
to verify it and what a reader should know before touching this panel
again.

**Zero backend changes.** `GET /intelligence/strategy-outcomes`,
`fetchStrategyOutcomes()`, and `StrategyOutcomeWireShape` already
covered everything this panel needed — confirmed directly against the
real route/client code before writing anything, not assumed from the
task prompt.

## Files changed

**New:**
- `frontend/src/hooks/useBacktestOutcomes.ts` — new hook, calling the
  already-existing `fetchStrategyOutcomes()` with `isBacktest` fixed
  `true`. Deliberately separate from `useStrategyOutcomes.ts` (untouched
  — see that file's own comment naming this exact panel as its deferred
  scope). Exposes a caller-visible `error` state distinct from empty
  data (same deviation `usePerformanceAnalytics.ts` already
  established); keeps the full `StrategyOutcomeWireShape`, not a
  narrowed display row, since the panel's expand-in-place view needs
  `evidence`/`market_state_at_*`/`context_at_*`. One-shot on mount,
  reactive to `backtestRunId`/`limit` changes, plus manual `refetch()` —
  no WebSocket subscription (no `OutcomeRecorded`-shaped event exists
  anywhere in `backend/app/schemas/events/`, confirmed by grep).
- `frontend/src/components/backtest-results/BacktestResultsPanel.tsx` —
  new. Fifth collapsible sibling panel, mounted in `App.tsx` alongside
  `InfoTab`/`FeatureEnginePanel`/`ScannerPanel`/`BacktestPanel`, reusing
  `ScannerPanel.tsx`'s exact `MIN_WIDTH`/`MAX_WIDTH`/`COLLAPSED_WIDTH`
  (64/480/36) and resize-handle behavior. Local component state for
  collapsed/width, not `WorkspaceContext.tsx` — same reasoning
  `BacktestPanel.tsx`'s own comment already gives for itself. Defaults
  to `is_backtest=true`, `limit=500` (the route's own hard cap), no
  `run_id` filter — "everything this table currently has," per the
  route's own docstring confirming zero real LIVE rows exist today.
  Free-text `run_id` filter (Enter or Apply button, Clear to reset)
  always applies alongside the fixed `is_backtest=true`, so this panel's
  own state machine cannot reach the route's real 400 (`backtest_run_id`
  without `is_backtest=true`) — only a malformed UUID can still 400,
  handled by the hook's `error` state, not conflated with an honest
  empty result. Expand-in-place per row (chosen after confirming no
  modal/dialog/portal pattern exists anywhere in `frontend/src/` today)
  surfaces every scalar field plus the five JSON blobs a summary row
  can't show. `realized_pnl`/`realized_r` render via unrounded
  `String()` with an explicit sign, not `.toFixed(2)` — a deliberate,
  narrow divergence from every other quick-glance summary in this
  codebase, since this task's own scope named these two fields
  specifically ("don't round in a way that hides sign or precision").
- `docs/decisions/archive/122-133.md` — new. See "Decision-log rollover"
  below.

**Modified:**
- `frontend/src/App.tsx` — new `BacktestResultsPanel` import, mounted
  directly after `BacktestPanel` in both `FullWorkspaceShell` and
  `PoppedOutWindowShell`'s `<main>`.
- `docs/decisions/confirmed-decisions.md` — decision #133 was appended
  here, which crossed the ~100KB rollover trigger; the file was then
  frozen to `archive/122-133.md` and replaced with a fresh, empty open
  file starting at #134 (see below). The file as delivered contains no
  entries yet — that's expected, not a mistake.
- `docs/decisions/INDEX.md` — new #133 row; file-location column for
  rows #122–#132 updated from `confirmed-decisions.md` to
  `archive/122-133.md`.
- `docs/architecture/strategy-engine-design.md` — one new as-built note
  + diagram appended to the end of §7, extending (not duplicating) the
  existing #128/#130 diagrams, showing the new route → hook → panel
  read path and its three real outcomes (empty / populated / 400).

**Confirmed untouched** (checked via `diff -rq` against a freshly
re-pulled clone, both before writing any code and again immediately
before packaging): everything under `backend/`, `useStrategyOutcomes.ts`,
`api-client.ts`, `InfoTab.tsx`, `AIAnalysisPanel.tsx`,
`backtest/BacktestPanel.tsx`, `WorkspaceContext.tsx`.

## Decision-log rollover (same change as #133, not a separate delivery)

Appending decision #133 pushed `confirmed-decisions.md` to 102,863
bytes — past the ~100KB trigger `docs/decisions/README.md` sets for a
rollover. Per that file's own maintenance protocol:

1. `confirmed-decisions.md` (decisions #122–#133) moved verbatim to
   `docs/decisions/archive/122-133.md`, with its live-file header
   replaced by the standard frozen-archive header (matching
   `archive/107-121.md` and every earlier archive's own convention).
2. A fresh, empty `confirmed-decisions.md` was started, ready for #134
   onward, with its own "note on this file's structure" updated to list
   all six archive ranges (`001-060` through `122-133`).
3. `INDEX.md`'s file-location column updated for every row in #122–#133
   in the same change (not a follow-up) — see "Files changed" above.

This is the sixth rollover in this project's history (after #79, #80,
#106, and #121's own two), same size-driven trigger each time, no
different rule.

## What was deliberately NOT built

- **No `isBacktest` toggle on `useStrategyOutcomes.ts`.** That hook
  backs "Recent Closed Trades," which is deliberately live-only by
  design; this is a new, separate hook for a new, separate purpose.
- **No changes to `api-client.ts`.** `fetchStrategyOutcomes()` and
  `StrategyOutcomeWireShape` already covered everything this panel
  needed — confirmed directly before writing any new wrapper.
- **No backend changes of any kind.** The task's own scope was zero
  backend changes since everything the screen needs was already built
  and already live on `main`; confirmed via `diff -rq`.
- **No pagination UI.** `limit=500` is the route's own hard cap, passed
  explicitly; today's real row count is nowhere near it.
- **No modal/dialog for the full-record view.** No such pattern exists
  anywhere in this codebase's frontend; expand-in-place was chosen
  instead — see the panel component's own comment and decision #133 for
  the full reasoning.
- **No `WorkspaceContext.tsx` changes.** The panel's collapsed/width
  state is local component state, same choice `BacktestPanel.tsx`
  already made for itself and for the same reason (no server-side push
  to sync, no real reason to persist this specific panel's state across
  a reload).
- **No fetching of strategy/scenario lists at runtime, no link into
  `BacktestPanel.tsx`.** This panel is deliberately standalone per the
  task's own scope — it works whether or not `BacktestPanel.tsx` exists
  in a given checkout, and never imports from it.

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
untouched clone before this delivery's own changes were made, so these
are a known baseline, not a regression. `npx vite build` succeeds with
no errors or warnings beyond its own standard build output.

**Manual check (no backend test suite involved — this delivery has no
Python changes to test):**

1. Start the backend and frontend as usual.
2. Trigger at least one backtest run via the existing `BacktestPanel.tsx`
   (or `POST /backtest/run` directly) so `strategy_outcomes` has at
   least one `is_backtest=True` row.
3. Open the new "Backtest Results" panel (rightmost of the five sidebar
   panels, starts collapsed like every sibling). It should show that
   row without any filter applied.
4. Copy the `run_id` from the `BacktestPanel.tsx` result (or the new
   panel's own expanded detail view, which shows `backtest_run_id` on
   every row) into the new panel's `run_id` field and press Enter — the
   list should narrow to just that run's rows; pressing Clear should
   return to the full view.
5. Type a clearly-invalid string (e.g. `not-a-uuid`) into `run_id` and
   apply it — the panel should show a distinct red/error message (the
   backend's real 400 detail), not an empty-state message.
6. Toggle a row's `▸`/`▾` — it should expand in place to show the full
   record (all scalar fields plus the five JSON blobs), not navigate
   away or open a separate window.

## A note on scope discipline

This task's own prompt named a concurrent backend session working a
historical-data-provider seam in `backend/app/backtest_runner/`,
`backend/app/feature_engine/engine.py`, and `backend/tests/` — file-
disjoint from this delivery by construction. Re-confirmed directly (not
assumed) via a fresh tarball pull and a `docs/decisions/` diff at both
session start and immediately before writing decision #133 — no drift
either time, no collision to reconcile.
