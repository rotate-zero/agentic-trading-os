# Backtest Results panel — a viewer for persisted BacktestRunner outcomes

Copy this into your repo root, overwriting the existing paths listed
below. Frontend-only delivery: **zero changes to any backend file.**
Confirmed by `diff -rq` against a freshly re-pulled clone of current
`main` before writing anything, and again immediately before packaging
this delivery — the only files that differ anywhere in the repo are the
ones listed below.

## What this closes

Backtest Runner v1 (decision #128) has been writing real, persisted
`StrategyOutcomeRecord` rows since it shipped, and decision #130 gave
`GET /intelligence/strategy-outcomes` real `is_backtest`/
`backtest_run_id` filtering specifically so those rows would be
queryable in isolation from (someday) real live trades. Nothing had
ever rendered them. Before this delivery, seeing what a backtest
actually did to a `StrategyOutcome` — win/loss, `realized_r`,
`exit_reason`, the full `evidence`/`market_state_at_entry`/
`context_at_entry` blobs — meant querying Postgres by hand or hitting
the route with `curl`. This delivery closes exactly that gap: a new
"Backtest Results" panel, and nothing else.

## Files changed

- `frontend/src/hooks/useBacktestOutcomes.ts` — **new.** Calls the
  already-existing `fetchStrategyOutcomes()` with `isBacktest` fixed
  `true` by this hook's own default. Deliberately a new, separate hook
  from `useStrategyOutcomes.ts` (untouched) — that hook backs "Recent
  Closed Trades," pinned live-only by design, and its own comment
  already named this exact panel as deferred, separate scope. Exposes a
  caller-visible `error` state distinct from empty data (the same
  deliberate deviation `usePerformanceAnalytics.ts` already established
  over `useStrategyOutcomes.ts`'s own console-and-swallow shape) so a
  real backend failure never renders identically to "this table/run
  genuinely has no rows." Keeps the full `StrategyOutcomeWireShape`
  (not a narrowed display row) since the panel's own expand-in-place
  view needs `evidence`/`market_state_at_entry`/`market_state_at_exit`/
  `context_at_entry`/`context_at_exit`, all of which
  `useStrategyOutcomes.ts`'s own narrower `StrategyOutcomeRow` type
  drops. One-shot fetch on mount, re-fetches when `backtestRunId`/
  `limit` change, plus an exposed `refetch()` for a manual "Refresh"
  action — no WebSocket subscription, since no `OutcomeRecorded`-shaped
  event exists anywhere in `backend/app/schemas/events/` (grepped, not
  assumed).
- `frontend/src/components/backtest-results/BacktestResultsPanel.tsx` —
  **new.** A fifth collapsible sibling panel, mounted in `App.tsx`
  alongside `InfoTab`/`FeatureEnginePanel`/`ScannerPanel`/
  `BacktestPanel`, reusing `ScannerPanel.tsx`'s own
  `MIN_WIDTH`/`MAX_WIDTH`/`COLLAPSED_WIDTH` (64/480/36) and
  resize-handle behavior verbatim, starting collapsed like every other
  sibling. Local component state for collapsed/width — same "no
  server-side push to sync, no real reason to persist this panel's
  state across a reload" reasoning `BacktestPanel.tsx`'s own comment
  already gives for itself, not threaded through `WorkspaceContext.tsx`.

  Defaults to `is_backtest=true`, `limit=500` (the route's own hard
  cap, `Query(50, le=500)`, passed explicitly rather than inherited from
  the route's live-oriented default of 50), no `run_id` filter —
  "everything this table currently has," since `strategy_outcomes` has
  zero real LIVE rows in production today (confirmed directly against
  the route's own docstring). A free-text `run_id` field (Enter or an
  Apply button, plus Clear) narrows further, always alongside the fixed
  `is_backtest=true` — this panel's own state machine has no path to
  the route's real, enforced 400 (`backtest_run_id` without
  `is_backtest=true`); only a malformed (non-UUID) `run_id` string can
  still 400, and that's surfaced via the hook's own `error` state, never
  rendered as a silently-empty result.

  Each row shows `symbol`/`strategy_name`/`direction` (colored the same
  bull/bear way `AIAnalysisPanel.tsx`'s `OpportunityRow` already
  colors BUY/SELL), `entry_price`/`exit_price`, `exit_reason`, a
  human-formatted `holding_seconds`, `entry_filled_at`/`exit_filled_at`,
  and `realized_pnl`/`realized_r`. The last two render via plain,
  unrounded `String()` with an explicit `+`/`-` sign rather than
  `.toFixed(2)` — every other quick-glance summary in this codebase
  (`InfoTab.tsx`'s `RecentClosedTrades`, `ScannerPanel.tsx`'s feature
  chips, `AIAnalysisPanel.tsx`'s `structuralTarget`) rounds to 2
  decimals, the right tradeoff for a glanceable feed; this panel exists
  specifically to let someone verify what a backtest actually recorded,
  and the task this delivery closes named these two fields by name
  ("don't round in a way that hides sign or precision") — a deliberate,
  narrow divergence, not applied anywhere else in the row.

  A per-row `▸`/`▾` toggle expands the record in place — evidence/
  market-state/context blobs a summary row can't show inline, plus
  every remaining scalar field (`outcome_id`, `opportunity_id`,
  `backtest_run_id`, timestamps, quantities, `structural_target`/
  `structural_invalidation`/`final_stop`/`final_target`,
  `confidence_at_signal`, etc.) in a label/value grid. Expand-in-place
  was chosen over a modal or separate details pane after checking
  directly: no modal/dialog/portal pattern exists anywhere in
  `frontend/src/` today (grepped) — introducing one for this single view
  would add a UI paradigm this codebase doesn't otherwise use.

  A genuinely empty result (`{"outcomes": []}`, a normal 200) renders as
  a plain "no backtest outcomes recorded yet" (or "no outcomes found for
  that run_id" when a filter is applied) — never an error state, same
  discipline `BacktestPanel.tsx` already applies to `outcomes_recorded: 0`.

- `frontend/src/App.tsx` — new `BacktestResultsPanel` import, mounted
  directly after `BacktestPanel` in both `FullWorkspaceShell` and
  `PoppedOutWindowShell`'s `<main>` (this app has no routing library —
  both shells mount every sibling panel identically, per `App.tsx`'s own
  comment on its two fixed shapes).
- `docs/architecture/strategy-engine-design.md` — one new as-built note
  + diagram appended to §7, extending (not duplicating) the existing
  #128/#130 diagrams, showing this delivery's route → hook → panel read
  path and its three real outcomes (empty / populated / 400).
- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md` —
  new decision #133. Appending it pushed `confirmed-decisions.md` past
  the ~100KB rollover trigger, so the same change also performed the
  sixth rollover: `docs/decisions/archive/122-133.md` is new (decisions
  #122–#133, moved verbatim with the standard frozen-archive header),
  `confirmed-decisions.md` was reset to a fresh, empty open file
  starting at #134, and `INDEX.md`'s file-location column for rows
  #122–#132 was updated to point at the new archive file in the same
  change (not a follow-up).
- `TESTING.md` — deleted and rewritten from scratch as this delivery's
  own file (the version it replaces was decisions #131/#132's own).

## What this does NOT do

- Does not touch `useStrategyOutcomes.ts`, `api-client.ts`,
  `InfoTab.tsx`, `AIAnalysisPanel.tsx`, or `backtest/BacktestPanel.tsx`
  — all four were read-only reference per this task's own scope, and
  confirmed untouched by `diff -rq`.
- Does not add a `WorkspaceContext.tsx` change, a modal/dialog
  primitive, pagination, or a backend route/field of any kind — all
  explicitly out of scope; see `TESTING.md`'s own "What was deliberately
  NOT built" section for the full list and reasoning.
- Does not link into `BacktestPanel.tsx` or fetch strategy/scenario
  lists at runtime — this panel works as a fully standalone screen
  whether or not `BacktestPanel.tsx` exists in a given checkout, per
  this task's own required-reading note.

## Verification

`npx tsc -b` / `npx vite build` clean, only the four known decision #35
`GridPresetPicker` errors — confirmed against a freshly re-pulled,
untouched clone first, then re-confirmed identical after this delivery's
own changes. Full detail, including manual verification steps for the
new panel itself, is in `TESTING.md`.
