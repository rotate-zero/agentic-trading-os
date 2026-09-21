# TESTING.md — Strategy Performance strategy-name filter (decision #162)

Temp id during parallel work: `strategy-performance-name-filter`.

## What changed

Frontend-only, one code file: `frontend/src/components/workspace/InfoTab.tsx`,
inside `StrategyPerformanceSummary` and the comment block above it.

- **New control:** a native `<select>` labelled "Strategy" — "All strategies"
  (default) plus the 7 names from `BACKTEST_STRATEGY_NAMES` (imported from
  `api-client.ts`, which is untouched). It sits between the Live/Backtest
  header row and the source line.
- **State:** `strategyName`, `undefined` = All strategies. Passed straight into
  the existing `usePerformanceAnalytics({ isBacktest, strategyName })` call. No
  hook, api-client, route or backend change was needed (the hook already had
  `strategyName` in its filters and its `load()` dependency array since #127).
- **Header:** names the strategy when one is selected —
  "Strategy Performance — Backtest · ORB".
- **Empty state:** Backtest + a selected strategy now names it; Backtest + All
  strategies and Live are byte-for-byte unchanged (Live's reason — no Execution
  Engine — is strategy-independent).
- **Comment block:** the #137 paragraph claiming "no existing source of
  selectable strategy names" is replaced with a correction citing #127/#131/
  #133/#137/#138 and this decision.
- **`strategyVersion`:** deliberately NOT exposed (no version source exists;
  backend requires a name with it). Deferred, stated in the code comment and the
  decision entry.

Docs, same delivery: `docs/architecture/strategy-engine-design.md` §5 (correction
pointer on the #137 note + a short as-built note with two delta diagrams), the
decision entry in `docs/decisions/confirmed-decisions.md`, and its `INDEX.md` row.

## Verified

| Check | Result |
|---|---|
| `npx tsc -b` | Output identical to a fresh untouched clone: only the 4 known #35 `GridPresetPicker` errors, zero new |
| `npx vite build` | Clean. Built CSS is byte-identical before/after (same hash) — no new Tailwind classes |
| Throwaway vitest/jsdom harness (real edited `InfoTab.tsx` + real hook + real `api-client.ts`; only unrelated sibling hooks and `fetch` stubbed) | 8/8 passed |
| Harness mutation checks | 3 mutations, each failed exactly one test: reverting to the pre-#137 `loading && isEmpty` gate; removing the hook's cancelled guard; sending `""` instead of `undefined` |
| Footprint | `diff -rq` against a freshly re-pulled clone — see the decision entry's footprint paragraph |

The harness lives outside the repo and is NOT shipped (no frontend test
framework exists here; adding one is a `package.json` change and its own
decision). What it asserted: default requests carry no `strategy_name`;
selecting ORB sends `strategy_name=ORB&is_backtest=true` to both routes and
shows "Loading…" instead of the previous rows; `Volume Spike` is sent as
`Volume%20Spike`; returning to All drops the param entirely; the selection
survives the Live toggle (`is_backtest=false`); the three empty/error messages;
a rapid ORB→Gap change never lets ORB's late response overwrite Gap's.

## Not covered

- **No real backend/Postgres round trip.** `fetch` was stubbed. The backend is
  untouched, and `test_performance_analytics_routes.py` (#127) already covers the
  route-side `strategy_name` filtering; I did not re-run the backend suite.
- **No browser paint check.** The harness runs inside React's `act`, so it cannot
  observe a paint between commit and effect. That rests on React 18.3.1
  (`createRoot`) flushing passive effects synchronously for discrete events —
  the same mechanism #137's Live/Backtest toggle already relies on.
- **Which strategies have backtest data.** Not checked; the empty message is
  deliberately worded so a strategy with zero recorded outcomes reads honestly.

## Manual check (5 minutes, needs the backend and one backtest run)

1. Run a backtest from the Backtest panel (e.g. ORB or FirstPullback on a
   fixture scenario).
2. Info tab → General → Strategy Performance. Default should read
   "— Backtest" with "All strategies" selected.
3. Pick the strategy you ran. Header becomes "— Backtest · <name>"; browser
   Network tab shows `...?strategy_name=<name>&is_backtest=true` on both routes.
4. Pick a strategy you did not run. Expect the strategy-named empty message.
5. Click Live with a strategy still selected. Selection stays; Live's original
   empty message shows.
6. Pick "All strategies". Requests drop `strategy_name`.

## Merge notes

- **Zero file overlap** with `backtest-sweep-frontend-ui` (`BacktestPanel.tsx`,
  `BacktestResultsPanel.tsx`, `api-client.ts`, `useBacktest*.ts`) — no manual
  merge expected. `CHANGES.md` was deliberately not touched.
- **`TESTING.md`** is delete-first per repo convention. If another delivery's
  `TESTING.md` lands too, keep both as sections.
- **Decision number.** `main` was at #161 on every check (start, mid-task, just
  before packaging), so this delivery took #162. If another delivery lands
  #162 first, renumber only this delivery's own references:
  `grep -rn "#162" frontend/src/components/workspace/InfoTab.tsx docs/architecture/strategy-engine-design.md docs/decisions/confirmed-decisions.md docs/decisions/INDEX.md TESTING.md`
  (the entry heading and INDEX row are the two that must match). Never change
  another delivery's number.

## Found, not fixed (out of this task's file boundary)

`api-client.ts:830` (the `BACKTEST_STRATEGY_NAMES` comment block) and `:914`,
`useBacktestRun.ts:7`, and `backtest-runner-design.md` §7 (lines 153/570) still
cite "decision #130" for `POST /backtest/run`; #131 says that citation should be
#131. #131's own footprint list never corrected `api-client.ts`. Suitable for a
small future citation-only task.
