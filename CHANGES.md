# CHANGES — World View frontend surfacing (decision #154)

Base: `main`, re-pulled immediately before packaging — and re-pulled a
second time when that re-check turned up a real change: the parallel
backend `test_feature_engine.py` cross-test-contamination fix mentioned
in this task's own prompt landed on `main` as **decision #153** between
an earlier recheck (which still showed #152 as latest) and packaging.
Confirmed zero file overlap by `diff -rq` isolating their delivery's
exact footprint (`backend/tests/test_feature_engine.py`, `CHANGES.md`,
`TESTING.md`, the two decision-log files) before reassigning this
delivery from a tentatively-held #153 to the actual next-available
**#154**, rebasing this working tree onto the corrected pull, and
re-verifying the number once more immediately before writing decision
#154's own entry. Frontend-only.

## What changed

`GET /intelligence/world-view` (decision #150) had zero frontend
representation until this delivery. Scoped deliberately narrow rather
than as a full re-rendering of the payload — see decision #154's own
entry in `docs/decisions/confirmed-decisions.md` for the full reasoning.
`market_state`/`context` are **not** re-rendered anywhere in this
delivery (both already have their own complete, live-updating UI
elsewhere); only `performance` (all-time, live+backtest side by side —
a shape nothing else in this codebase shows) and `portfolio` (rendered
honestly as "not available") are surfaced.

### New files

- `frontend/src/hooks/useWorldView.ts` — fetch-based, one-shot on mount,
  `refetch()` exposed, no poll/WebSocket (World View has no event source
  of its own by design). Distinguishes a caller-visible `error` from
  genuinely empty data, same as `usePerformanceAnalytics.ts`. Deliberately
  does not normalize/expose `market_state`/`context`.

### Modified files (additive only)

- `frontend/src/services/api-client.ts` — new `fetchWorldView()` +
  `WorldViewSnapshotWireShape`/`WorldViewPerformanceWireShape`/
  `WorldViewPerformancePopulationWireShape`, appended at file end.
  Reuses `MarketStateSnapshotWireShape`/`ContextSnapshotWireShape`/
  `HourlyWinRateWireShape`/`SessionTypeExpectancyWireShape` rather than
  re-declaring the same shapes.
- `frontend/src/components/workspace/InfoTab.tsx` — new
  `WorldViewSummary` component, added directly below
  `StrategyPerformanceSummary` in `GeneralContent`.
  `StrategyPerformanceSummary`'s own internals are untouched. Two new
  small pure helpers, `aggregateWinRate()`/`aggregateExpectancy()` —
  sum/weighted-average of already-real per-row backend numbers, not an
  invented composite score.
- `docs/architecture/trading-intelligence-architecture.md` §15 — one new
  as-built note with two diagrams (cross-component data flow; internal
  aggregation flow inside `WorldViewSummary`).
- `docs/decisions/confirmed-decisions.md` — new entry #154.
- `docs/decisions/INDEX.md` — new row for #154.

## What did NOT change

- Nothing under `backend/`.
- `frontend/src/hooks/useMarketState.ts`,
  `frontend/src/hooks/useContextSnapshot.ts`,
  `frontend/src/hooks/usePerformanceAnalytics.ts`,
  `frontend/src/hooks/useStrategyOutcomes.ts` — all byte-for-byte
  unchanged.
- `StrategyPerformanceSummary`/`MarketSessionSummary`/
  `MarketStateSummary`/`RecentClosedTrades` in `InfoTab.tsx` — internals
  untouched; only a new sibling section was added.
- `docs/architecture/system-design.md` — references `world_view/` only in
  a file-tree listing; no frontend-coverage claim existed there to
  correct or extend.
- `confirmed-decisions.md`'s archive rollover — past its ~100KB trigger
  and now unblocked (decision #150's own deferral), but deliberately not
  bundled into this unrelated delivery. Flagged as a standing follow-up.
- Decision #153's own entry, row, and files (`test_feature_engine.py`) —
  that delivery landed first; this one only appends after it.

## Verification

`npx tsc -b` output byte-identical to a fresh-clone baseline (only the
four known decision #35 `GridPresetPicker` errors). `npx vite build`
clean: baseline's 98 modules plus exactly 1 (`useWorldView.ts`), no
errors or warnings. Full footprint confirmed by `diff -rq` against a
freshly pulled, untouched clone immediately before packaging. See
`TESTING.md` for the complete verification account.
