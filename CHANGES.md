# Backtest Runner frontend panel — a UI for `POST /backtest/run`

Copy this into your repo root, overwriting the existing path.
Frontend-only delivery: **zero changes to any backend file.** Confirmed
by `diff -rq` against a freshly re-pulled clone of current `main` before
writing anything, and again immediately before packaging this delivery —
the only files that differ anywhere in the repo are the ones listed
below.

## What this closes

`POST /backtest/run` (decision #130) is a real, working route, but its
own module docstring is explicit that it never got a UI: "this route
does not add any Performance Analytics UI for inspecting results ... a
caller wanting the raw persisted rows can already query the existing
route separately." Before this delivery, running a backtest meant
constructing a raw HTTP request by hand (`curl`, Postman, a scratch
script) and reading raw JSON back. This delivery closes exactly that gap
— a way for a person to pick a strategy and a scenario, click Run, and
see what came back — nothing more. It does not add a backtest-results
browser, does not touch Performance Analytics, and does not link into
"Recent Closed Trades."

## Files changed

- `frontend/src/components/backtest/BacktestPanel.tsx` — **new.** A
  fourth collapsible sibling panel, mounted in `App.tsx` alongside
  `InfoTab`/`FeatureEnginePanel`/`ScannerPanel`, matching
  `ScannerPanel.tsx`'s own collapsible-width convention verbatim
  (`MIN_WIDTH`/`MAX_WIDTH`/`COLLAPSED_WIDTH` = 64/480/36, same resize
  handle, starts collapsed). Contains the strategy/scenario/symbol form,
  a live "Running… Nm Ns elapsed" state for the genuinely long
  synchronous wait, and a results view rendering `BacktestRunResult`'s
  real fields verbatim.
- `frontend/src/hooks/useBacktestRun.ts` — **new.** Owns one
  `POST /backtest/run` call's lifecycle (`idle`/`running`/`done`/`error`)
  and a real-time-derived elapsed-seconds ticker. Not a polling/WS hook
  like every other hook in this codebase — there's nothing to keep
  fresh, just one long-running action to track honestly.
- `frontend/src/services/api-client.ts` — new `triggerBacktest()`
  wrapper (POST with query params baked into the URL, matching
  `subscribeSymbol`'s existing convention exactly — no body, no change
  needed to this file's `API_BASE_URL`/`fetch` usage for a POST), new
  `BacktestRunResultWireShape`/`DiscardedSignalWireShape` types, and the
  hardcoded `BACKTEST_STRATEGY_NAMES`/`BACKTEST_SCENARIOS` lists (see
  "Real values, hardcoded on purpose" below).
- `frontend/src/App.tsx` — `<BacktestPanel />` mounted in both workspace
  shells (`FullWorkspaceShell`/`PoppedOutWindowShell`), directly after
  `<ScannerPanel />`. No routing change — this app's two fixed shapes
  (`/` and `/window/:id`) were confirmed as the real, current constraint
  before writing anything; a new panel, not a new route, is what this
  app's own architecture calls for.
- `docs/architecture/strategy-engine-design.md` — §7 (Backtest Runner)
  gains one new as-built note + one new diagram, directly after the
  existing decision-#130 note, extending that section rather than
  duplicating it — the one place a reader tracing "what happened to
  `POST /backtest/run`" would naturally look next.

## Real values confirmed directly, not assumed

- **7 strategy names**, read straight off each strategy's own
  `default_config()` in `backend/app/strategy_engine/*_strategy.py`:
  `ORB`, `Gap`, `Volume Spike` (note the real space — the other six
  don't have one), `FirstPullback`, `Reversal`, `Momentum`, `VWAP`.
- **4 scenario names + candle counts**, read straight off
  `backend/app/backtest_runner/scenarios.py`'s own `_SCENARIO_FILES`:
  `first_pullback_vwap_dip` (130), `reversal_vwap_break` (140),
  `vwap_neutral_conquest` (140), `volume_gated_baseline` (120).
- **Response shape**, read straight off `runner.py`'s real
  `BacktestRunResult` dataclass and `test_backtest_routes.py`'s own
  assertions: `run_id`, `sweep_id`, `outcomes_recorded`,
  `discarded_signals: [{signal_candle_ts, reason, detail}]`.

## Real values, hardcoded on purpose (not fetched)

Both lists above are hardcoded in `api-client.ts` rather than fetched
from the backend at runtime. Neither `default_registry()`
(`scheduler.py`) nor `available_scenarios()` (`scenarios.py`) is
reachable over HTTP anywhere in this codebase today — exposing either
would mean adding a new backend route, or editing `scheduler.py` /
`scenarios.py` directly, both explicitly outside this task's
frontend-only file boundary (`backend/app/api/routes/backtest.py`,
`backend/app/backtest_runner/scenarios.py`, and
`backend/app/strategy_engine/scheduler.py` were read-only reference for
this delivery). `POST /backtest/run`'s own 400 error body already lists
the real valid values live, so a future drift between this hardcoded
list and the backend fails loudly (a clear 400, surfaced as `error` in
the panel) rather than silently.

## The 2–2.5 minute wait, handled honestly

Confirmed directly from `backtest.py`'s own docstring and
`test_backtest_routes.py`'s own deliberately-slow tests: each replayed
candle costs a real, measured ~1 second of `EngineBackedReplayStateProducer`
engine-settle time, so a 120–140 candle scenario is a genuine 2–2.5
minute synchronous HTTP round trip — not a bug, not a timeout to work
around. `BacktestPanel.tsx` is built around that being true:

- The submit control (and every form field) disables itself the instant
  a run starts — this route's own `engine_singleton_guard.py` would
  serialize a genuine concurrent call anyway (confirmed directly in that
  module and exercised by Unit 5's own concurrency test per decision
  #128), so this is a UI-level courtesy that avoids firing a second,
  wasted ~2-minute request from the same tab, not a claim that the
  backend needs it.
- While running, the panel shows a live `"Running… Nm Ns elapsed"` state
  derived from a real `Date.now()` delta on each tick (not a naive
  incrementing counter, which a throttled background tab could
  understate) — plus the selected scenario's own expected candle count,
  so the wait has a visible point of reference rather than reading like
  a stall.

## `outcomes_recorded: 0` — rendered as a fact, not an error

Confirmed directly (`scenarios.py`'s own module docstring, and by
reading each of the 7 strategies' real MATCH-stage code): 4 of the 7
strategies (ORB, Gap, Volume Spike, Momentum) hard-gate MATCH on
`volume_regime_score`, which is structurally always `0.0` in any
BacktestRunner replay today — no historical-data provider is wired into
the replay stack. Running any of those four against any scenario, or
running any strategy against the honest `volume_gated_baseline`
fallback, is expected to return `outcomes_recorded: 0`. The panel
renders that value with the same neutral styling regardless of what it
is — never a red/error treatment, never hidden — matching the route's
own explicit "this is not a sign anything is broken" framing.

## What was deliberately NOT built

- **No fetched strategy/scenario list.** See "hardcoded on purpose"
  above — the real blocker is that no backend route exposes either list
  today, not a preference.
- **No link into "Recent Closed Trades" / Performance Analytics UI.**
  Explicitly out of scope, matching the trigger route's own stated
  boundary (`InfoTab.tsx`, `AIAnalysisPanel.tsx`,
  `useStrategyOutcomes.ts`, `usePerformanceAnalytics.ts` were untouched
  and confirmed so by `diff -rq`).
- **No `WorkspaceContext.tsx` wiring for collapsed/width state.** Local
  component state instead — see `BacktestPanel.tsx`'s own comment and
  `strategy-engine-design.md`'s new note for the reasoning. Flagged
  explicitly as a deliberate, reconsiderable choice, not a silent
  deviation from the Scanner/FeatureEngine panel pattern.
- **No decision-log entry.** This task's own brief didn't ask for one,
  and a parallel session was separately flagged as retroactively
  documenting the trigger route's own backend delivery — adding a new
  numbered entry here risked exactly the kind of collision this
  project's decision log has hit before (#98/#99, #111/#112, #114/#115,
  #127/#128). `strategy-engine-design.md`'s new note says as much
  explicitly and leaves folding this into a numbered decision as
  Saqib's call at merge time.
- **No edit to `system-design.md` / `trading-intelligence-architecture.md`.**
  Both were checked directly for a frontend-panel-layout description
  before writing anything. Neither has a "living" one:
  `trading-intelligence-architecture.md` has no App.tsx/panel-layout
  content at all, and `system-design.md`'s own directory tree (§8) is
  aspirational/stale — it doesn't even list the `components/intelligence/`
  or `components/scanner/` folders that already exist and are already
  built, so editing it to add `components/backtest/` would misrepresent
  a stale tree as current rather than genuinely fix it. Noted here
  rather than silently patched or silently skipped, same posture
  decision #130 itself already took for a different pre-existing
  staleness in the same file.

## Verification

- `npx tsc -b`: clean except the known 4 decision-#35 `GridPresetPicker`
  errors — confirmed to be exactly those 4 and nothing else by running
  the identical command against a freshly re-pulled, untouched clone
  side by side with this delivery's own working copy.
- `npx vite build`: clean, 88 modules transformed (86 on the untouched
  baseline clone — exactly +2, the two new files).
- No backend test suite run — this delivery makes zero backend changes,
  confirmed by `diff -rq`, so the backend suite is unaffected by
  construction, not by assumption.
