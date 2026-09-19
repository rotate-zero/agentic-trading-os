# TESTING — World View frontend surfacing (decision #154)

Frontend-only delivery. No backend files touched — `backend/` verification
is unaffected by this change and was not re-run.

## What changed

- New `frontend/src/hooks/useWorldView.ts`
- Additive-only block appended to `frontend/src/services/api-client.ts`
  (`fetchWorldView()` + `WorldViewSnapshotWireShape`/
  `WorldViewPerformanceWireShape`/`WorldViewPerformancePopulationWireShape`)
- New `WorldViewSummary` section in
  `frontend/src/components/workspace/InfoTab.tsx`'s `GeneralContent`,
  directly below `StrategyPerformanceSummary`
- `docs/architecture/trading-intelligence-architecture.md` §15 — one new
  as-built note with two diagrams
- `docs/decisions/confirmed-decisions.md` (#154) + `docs/decisions/INDEX.md`
  (new row)

## How this was verified

1. **Baseline captured on a fresh, untouched clone before any edit.**
   `npx tsc -b` produced exactly the four known decision #35
   `GridPresetPicker` errors (`GRID_PRESETS` missing export, `preset`/
   `setPreset` missing on `WorkspaceContextValue`, one implicit-`any`
   parameter). `npx vite build` produced 98 modules transformed, clean.

2. **Post-change `npx tsc -b` output diffed byte-for-byte against that
   baseline.** Identical — the same four known `GridPresetPicker` errors,
   nothing added, nothing removed.

3. **Post-change `npx vite build`.** Clean, 99 modules transformed —
   baseline's 98 plus exactly 1 (the one new file, `useWorldView.ts`). No
   errors or warnings.

4. **Manual read-through against the real backend contract**, not assumed
   from the task prompt's own description:
   - `backend/app/world_view/composite.py` read in full, including its
     own docstring — confirmed `WorldViewSnapshot`'s exact fields
     (`symbol`, `market_state`, `context`, `performance`, `portfolio`)
     and that `market_state`/`context` are `MarketStateEngine`/
     `ContextEngine`'s own unmodified `get_snapshot(symbol)` envelopes —
     this is why `WorldViewSnapshotWireShape` reuses
     `MarketStateSnapshotWireShape`/`ContextSnapshotWireShape` verbatim
     rather than re-declaring them.
   - `backend/tests/test_world_view.py` read to independently confirm the
     same shape from the test side, not solely from the implementation.
   - `backend/app/api/routes/intelligence.py`'s `GET /world-view` route
     read directly — confirmed it's a thin passthrough with an optional
     `symbol`, matching `fetchWorldView()`'s own optional-`symbol`
     convention.

5. **Zero prior frontend representation confirmed by grep** across
   `frontend/src/` for `world-view`/`world_view`/`WorldView`/
   `useWorldView` before writing any code — no matches.

6. **Footprint confirmed by `diff -rq` against a fresh clone** pulled
   immediately before packaging: exactly
   `frontend/src/components/workspace/InfoTab.tsx`, new
   `frontend/src/hooks/useWorldView.ts`, the additive block in
   `frontend/src/services/api-client.ts`,
   `docs/architecture/trading-intelligence-architecture.md`,
   `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`,
   this file, and repo-root `CHANGES.md`. Nothing under `backend/`
   touched; `StrategyPerformanceSummary`/`MarketSessionSummary`/
   `MarketStateSummary`/`RecentClosedTrades`'s own internals untouched;
   `useMarketState.ts`/`useContextSnapshot.ts`/
   `usePerformanceAnalytics.ts`/`useStrategyOutcomes.ts` untouched.

## Not covered (deliberately out of scope for this delivery)

- No frontend test file was added — matches this codebase's existing
  test-free hook/component/API-client-wrapper practice (decisions
  #123/#152's own precedent); no pattern for one exists here yet.
- `confirmed-decisions.md`'s archive rollover (past its ~100KB trigger,
  now unblocked) was **not** performed as part of this delivery — see
  decision #154's own entry for why, and its own note that this is a
  separate, standing follow-up.
