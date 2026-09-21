# TESTING — decision #163: `POST /backtest/sweep` frontend UI

## What changed

Frontend-only. A third "Sweep" trigger mode in `BacktestPanel.tsx`, a new
`sweep_id` filter type in `BacktestResultsPanel.tsx`, two new hooks
(`useBacktestSweepRun.ts`, `useBacktestSweepOutcomes.ts`), a new
`lastBacktestSweepId` WorkspaceContext field, a small additive `enabled`
param on `useBacktestOutcomes.ts`, a purely additive `triggerBacktestSweep()`
+ wire types in `api-client.ts`, five corrected stale `(decision #130)` →
`(decision #131)` citations, and a new as-built note + two doc correction
pointers in `backtest-runner-design.md`. See `confirmed-decisions.md`
entry #163 for the full reasoning behind every choice below.

## Verification performed

### 1. Static/type checking

```
cd frontend
npm install
npx tsc -b
```

Result: only the four known, pre-existing #35 `GridPresetPicker.tsx`
errors (confirmed against a fresh untouched clone before this delivery
began, and re-confirmed identical after every edit in this delivery,
including the final decision-number substitution pass). Zero new errors.

```
npx vite build
```

Result: clean, `101 modules transformed`, no warnings, no errors.

### 2. Manual verification checklist (no frontend test framework exists in
this codebase — decision #123's/#162's own precedent; no hook's actual
runtime logic changed in a way `tsc -b`/`vite build` don't already verify
structurally, so no test harness was added for this delivery either)

Against a real running backend (`uvicorn app.main:app`, real Postgres):

- [ ] **Sweep trigger, happy path.** Open `BacktestPanel.tsx`, select
      "Sweep" mode, pick a strategy, enter 2–3 symbols
      (e.g. `ZBTR1, ZBTR2`), check 2 scenarios. Confirm the pair count
      shown (`symbols × scenarios`) matches, submit, confirm the
      "Running…" elapsed-time copy appears with no fabricated
      percentage/pair-count claim, and that `SweepResultsView` renders
      `sweep_id`, `pairs_requested/succeeded/failed`, and one
      `SweepPairRow` per pair afterward.
- [ ] **Client-side cap.** Select enough symbols × scenarios to exceed 20
      pairs; confirm the Run button disables and the cap message appears
      before submitting (never a wasted request).
- [ ] **Sweep error.** Trigger with an unknown strategy_name (if
      reachable) or exceed the cap server-side by editing the request;
      confirm the plain-string error renders via the existing
      `ApiError` path.
- [ ] **Auto-link.** After a sweep finishes, open `BacktestResultsPanel.tsx`,
      confirm it auto-switches to (or already shows, if left there)
      `sweep_id` mode with the just-finished `sweep_id` pre-filled and
      "(auto)" shown.
- [ ] **sweep_id filter, manual.** Paste an older `sweep_id` manually,
      confirm `RunsInSweepStrip` lists every resolved run (including any
      with `0 outcome(s)`) and the merged outcomes list below it renders
      correctly sorted by `exit_filled_at` descending.
- [ ] **run_id filter still works unchanged.** Switch back to `run_id`
      mode, confirm existing auto/manual/Apply/Clear/Follow-latest-run
      behavior is byte-for-byte unchanged from before this delivery.
- [ ] **Switching filter types doesn't leave a stale fetch running** —
      confirm via the browser Network tab that only the active filter
      type's own hook issues a real request (the `enabled`/`undefined`
      no-op guards on the inactive one).

### 3. `diff -rq` footprint confirmation

Run against a freshly re-pulled clone of `main` (post-decision-#162):
confirmed the only files that differ are the ones listed in this entry's
own `confirmed-decisions.md` footprint paragraph. Nothing under `backend/`
changed; nothing decision #162 touched (`InfoTab.tsx`,
`strategy-engine-design.md`) changed.

## Known pre-existing state, unrelated to this delivery

- The four `GridPresetPicker.tsx` `tsc -b` errors (decision, informally,
  "#35" per this project's own long-standing shorthand) predate this
  delivery and every other recent one; not touched here.
