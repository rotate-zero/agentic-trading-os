# TESTING.md — CONTEXT_CHANGED → WebSocket channel (decision #126)

## What changed

`ContextEngine.evaluate_all()`/`evaluate_for_symbol()` already published
`ContextChanged` (decisions #92/#96) — the gap was that `EVENT_TO_CHANNEL`
(`app/api/websocket/channels.py`) had no routing entry for it, so nothing
relayed those events to the frontend. Decision #125 built
`useContextSnapshot.ts` as fetch-plus-60s-poll specifically because of this
gap. This delivery closes it and switches the hook to WebSocket-primary,
keeping the poll as a safety-net fallback. Full reasoning: decision #126,
`docs/decisions/confirmed-decisions.md`.

### Files touched

- `backend/app/api/websocket/channels.py` — one added `EVENT_TO_CHANNEL`
  entry, `EventType.CONTEXT_CHANGED: "intelligence.context"`, plus a
  comment. No other change to this file.
- `backend/tests/test_websocket_channels.py` (new) — 4 tests.
- `frontend/src/hooks/useContextSnapshot.ts` (extended) — WebSocket
  subscription added; `POLL_INTERVAL_MS`'s comment and the hook's own
  docstring rewritten to reflect WS-primary/poll-fallback. Public
  interface (`UseContextSnapshotResult`, `CalendarContext`,
  `FundamentalsContext`, `NewsContext`) unchanged.
- `docs/architecture/system-design.md` — one sentence added to §10.3's
  `ContextChanged` row, noting the new channel and this decision.
- `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md` — new
  decision #126 entry + index row.
- This file.

**No other backend file touched.** `ContextEngine`'s publish side and
`ContextChanged`'s payload schema are read-only references for this task —
confirmed unchanged by `diff -rq` (see below).

**Not touched, confirmed by reading them:** `useOpportunities.ts`,
`useOpportunityConflicts.ts`, `useStrategyOutcomes.ts`, `InfoTab.tsx`,
`AIAnalysisPanel.tsx`, `api-client.ts`, `app/api/routes/intelligence.py`,
`app/trading_intelligence/performance_queries.py`,
`app/backtest_runner/` (the parallel session's own module).

## A note on repo state during this task

Mid-task, a separate parallel session's Backtest Runner Unit 4 landed on
`main` (`app/backtest_runner/runner.py`, `backend/tests/test_backtest_runner.py`,
its own `CHANGES.md` entry — no decision-log entry of its own yet).
Confirmed file-disjoint from this task by re-diffing against a fresh pull;
this delivery is rebased onto that updated `main`, not the earlier,
now-stale snapshot this session started from. All numbers below are
against that current `main`.

## Backend validation

Real local Postgres (already provisioned this session), migrations applied
via `alembic upgrade head`.

```
cd backend
python -m pytest tests/ -q
```

**Baseline** (freshly-pulled current `main`, before this task's changes):
669 tests collected. Ran 3x: 1–2 failures each run, always drawn from the
same 4-member set below — never a fixed 2, matching decision #119's
description of intermittent, order/timing-sensitive failures.

**After this task's changes:** 673 tests collected (exactly +4 — this
task's own `test_websocket_channels.py`). Ran 3x: 1–2 failures each run,
same 4-member set, zero new failures, zero regressions.

**The known flaky cluster** (decision #119, reproduced on both the
baseline and the working tree across these runs — not introduced by this
task):
- `tests/test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
- `tests/test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`
- `tests/test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction`
- `tests/test_feature_engine.py::test_feature_engine_backfills_from_persisted_history_on_cold_start`

All 4 pass individually in isolation every time — confirmed by running the
4th one 5x alone — consistent with #119's own "order/timing-sensitive
under full-suite load" description. This is a standing fixture, not
something this task introduced or needs to fix.

**New in this delivery**, `test_websocket_channels.py` (4/4 passing,
stable across 5 consecutive runs): real (not mocked) WebSocket
routing/delivery via `TestClient`'s `.portal`, so `bus.publish()` runs on
the same event loop as the app's lifespan and the WebSocket session —
avoiding the "Queue bound to a different event loop" failure mode decision
#38 documents. No database write happens in this feature's own code path;
the real Postgres connection present is inherited from `ContextEngine`'s
own real startup bootstrap (reads `scanner_universe_symbols`), which these
tests had to account for — see decision #126's own entry for the real
background-noise finding this surfaced and how the tests were made robust
against it.

## Frontend validation

```
cd frontend
npx tsc -b
npx vite build
```

Both clean on the working tree, identical to a freshly-pulled current-
`main` baseline: only the four known pre-existing `GridPresetPicker`
errors (decision #35), same lines, in both. No new errors introduced.

## Fresh-clone diff verification

`diff -rq` against a freshly-pulled untouched clone of current `main`
confirms the change set is exactly the "Files touched" list above, plus
this file — nothing else, including no accidental touch to the parallel
Backtest Runner session's files.

## Known limitations / not done here

- No companion frontend test file for `useContextSnapshot.ts` — this
  codebase has no test convention for hooks today (re-confirmed, same as
  decision #125 found), so this task doesn't introduce one solely for
  itself.
- This delivery does not touch, and is not blocked by, the parallel
  Backtest Runner or Performance Analytics work in flight elsewhere in the
  repo.
