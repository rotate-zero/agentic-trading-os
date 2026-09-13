# TESTING.md — Backtest Runner frontend panel

## What changed and why

New `BacktestPanel.tsx` + `useBacktestRun.ts` give `POST /backtest/run`
(decision #130) its first real UI caller — pick a strategy, pick a
fixture scenario, submit, see the real `BacktestRunResult` come back.
Full reasoning lives in `CHANGES.md`; this file covers what to run to
verify it and what was deliberately not covered.

This is a frontend-only delivery — **zero backend files changed**,
confirmed by `diff -rq` against a fresh clone. The existing backend test
suite (`backend/tests/`) is unaffected by construction; it was not
re-run as part of this delivery.

## Files changed

- `frontend/src/components/backtest/BacktestPanel.tsx` — new.
- `frontend/src/hooks/useBacktestRun.ts` — new.
- `frontend/src/services/api-client.ts` — `triggerBacktest()`,
  `BacktestRunResultWireShape`/`DiscardedSignalWireShape`,
  `BACKTEST_STRATEGY_NAMES`/`BACKTEST_SCENARIOS` appended.
- `frontend/src/App.tsx` — `<BacktestPanel />` mounted in both workspace
  shells.
- `docs/architecture/strategy-engine-design.md` — §7 gains one as-built
  note + one diagram.

## Automated verification (what was actually run)

Real local `npm install` + `npx tsc -b` + `npx vite build`, run twice:
once against a freshly re-pulled, untouched clone of current `main`
(the baseline), once against this delivery's own working copy, output
diffed directly rather than eyeballed.

- **`npx tsc -b`**
  - Baseline: 4 errors, all in `src/components/workspace/GridPresetPicker.tsx`
    (the known decision-#35 dead-code file — `GRID_PRESETS`/`preset`/
    `setPreset` don't exist on the real `WorkspaceContextValue`, plus one
    implicit-`any` parameter).
  - This delivery: **identical 4 errors, same file, same lines, nothing
    else.** `BacktestPanel.tsx`/`useBacktestRun.ts`/the `api-client.ts`
    additions introduce zero new type errors.
- **`npx vite build`**
  - Baseline: clean, 86 modules transformed.
  - This delivery: clean, **88 modules transformed** — exactly +2, the
    two new files. No warnings beyond Vite's own standard output.

No frontend unit tests were added for `useBacktestRun.ts`, matching this
codebase's existing, established practice for hooks
(`useStrategyOutcomes.ts`, `useOpportunityConflicts.ts`,
`useContextSnapshot.ts`, `usePerformanceAnalytics.ts` — none of them
have a test file either; decision #123 says so explicitly for the first
two). A hook whose only real behavior is "call one function, track four
pieces of state, run a `setInterval`" doesn't clear the bar this
codebase already draws for adding one.

## Manual verification (what to actually click through)

This panel's core behavior — a genuinely-synchronous ~2 minute HTTP
call — isn't meaningfully provable by `tsc`/`vite build` alone. To
verify it end to end:

1. **Bring up a real backend against a real Postgres**, same pattern
   this project always uses:
   ```
   CREATE USER trading WITH PASSWORD 'trading' SUPERUSER;
   CREATE DATABASE trading_workspace OWNER trading;
   ```
   `alembic upgrade head`, then run the FastAPI app (`uvicorn` per
   `backend/`'s own README/Dockerfile) so `http://localhost:8000` is
   live.
2. **Run the frontend dev server** (`npm run dev` in `frontend/`) and
   open the workspace. The Backtest panel starts collapsed on the far
   right, alongside Scanner — click `«` to expand it.
3. **Guaranteed-fire path.** Strategy `FirstPullback`, scenario
   `first_pullback_vwap_dip`, any symbol (e.g. `ZBTR1`). Click Run
   Backtest.
   - Expect: the form disables immediately, a live
     `Running… 0s elapsed` line appears and counts up in real time
     (`1s`, `2s`, ... `1m 5s`, ...) for roughly 130 real seconds — not a
     generic spinner.
   - Expect on completion: `outcomes_recorded: 1`, `discarded_signals:
     None.`, real-looking `run_id`/`sweep_id` UUIDs, form re-enables.
4. **Honest-zero path.** Strategy `ORB`, scenario
   `volume_gated_baseline`, any symbol. Click Run Backtest.
   - Expect: same live elapsed counter (~120s), then
     `outcomes_recorded: 0` rendered in the *same* neutral styling as
     step 3's `1` — no red, no "error" framing, no warning icon.
5. **Double-click / concurrent-submit guard.** While a run from step 3
   or 4 is still in flight, try clicking Run Backtest again (or pressing
   Enter in the Symbol field).
   - Expect: nothing happens — the button and every field are disabled
     for the whole run, so there's no way to fire a second request from
     this panel while one is outstanding.
6. **Validation-error path.** Stop the backend (or point
   `VITE_API_BASE_URL` at a dead port), then submit a valid-looking
   form.
   - Expect: after the fetch fails, the panel leaves `running` and shows
     a plain-text error message (via `ApiError`'s own `detail`/`message`)
     in the panel body — not a silent failure, not a crash.
7. **Resize/collapse.** Drag the panel's left-edge handle; confirm it
   respects the same 64px/480px min/max as Scanner. Collapse and
   re-expand; confirm in-progress or completed run state is preserved
   (it's local component state, not remounted by collapsing).

Steps 3 and 4 are the two real, ~130s+~120s waits — budget about 5
minutes total if running both. This mirrors exactly what
`test_backtest_routes.py`'s own two deliberately-slow HTTP tests already
prove on the backend side (decision #130); this manual pass is the
frontend's own equivalent, since there's no practical way to unit-test
"does the browser correctly render a real-time counter across a real
2-minute wait" without literally waiting it out.

## What was deliberately NOT tested

- **No automated end-to-end test.** Spinning up `TestClient` +
  Playwright/Cypress against a real Postgres for a ~130s-per-case UI
  flow isn't something this codebase has any existing harness for
  (frontend has no test runner configured at all — `package.json`'s
  `scripts` are `dev`/`build`/`preview` only, confirmed directly) and
  adding one would be a much larger, separate undertaking than this
  task's own scope.
- **No re-run of the backend suite.** Zero backend files changed; see
  `CHANGES.md`'s footprint confirmation.
