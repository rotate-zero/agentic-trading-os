# TESTING — pending decision (temp id: `market-state-frontend-surfacing`) — Market State Engine's live snapshot surfaced in the frontend

Frontend-only delivery. No `backend/` files touched, so no backend `pytest` run applies.

## Pre-work verification

- Fresh tarball pull (`codeload.github.com/.../refs/heads/main`) at task start, per this task's own explicit instruction not to assume prior state.
- Read, in order: `docs/decisions/README.md`; `docs/decisions/INDEX.md`'s last several rows; decision #125 in full (`docs/decisions/archive/122-133.md`) as the direct pattern to mirror; `backend/app/market_state_engine/engine.py`'s `get_snapshot()` method and module docstring in full; `backend/app/models/market_state.py`; `backend/app/schemas/events/market_state.py` (the real `MarketState`/`CrossSymbolState` Pydantic schemas `get_snapshot()` actually serializes — cross-checked against, not assumed identical to, the DB column names in `models/market_state.py`); `backend/app/api/routes/intelligence.py`'s `GET /market-state` route in full; `frontend/src/hooks/useContextSnapshot.ts` in full as structural template; `frontend/src/components/workspace/InfoTab.tsx` and `frontend/src/components/ai-panel/AIAnalysisPanel.tsx` in full, current state; `backend/app/api/websocket/channels.py` in full (confirmed no `EventType.MARKET_STATE_CHANGED` entry in `EVENT_TO_CHANNEL` — not touched).
- Grepped all of `frontend/src/` for `market-state`/`market_state`/`MarketState` before writing anything: confirmed the only existing mentions were unrelated `StrategyOutcome` snapshot field names (`market_state_at_entry`/`market_state_at_exit`), not this route — zero real prior usage.

## Immediately before writing/packaging

- Re-pulled a fresh tarball **twice** partway through this task's session, each prompted directly ("Git is updated. Continue."), and diffed each pull against the prior state.
  - **First re-pull:** found `ibkr-historical-backtest-provider` (backend-only, new `POST /backtest/run/ibkr`) had landed. `diff -rq` confirmed it touches only backend files (`backtest.py`, `broker.py`, `fixture_provider.py`, `historical_provider_guard.py`, new `ibkr_historical.py`, `runner.py`, `ibkr_adapter.py`, `config.py`, three test files), `docs/architecture/backtest-runner-design.md`, and `docs/decisions/future-ideas.md` (closing future-idea #25) — zero overlap with this delivery.
  - **Second re-pull:** found `market-state-changed-websocket-channel` had landed — the exact `EVENT_TO_CHANNEL[MARKET_STATE_CHANGED]` routing gap this task's own prompt anticipated. `diff -rq` confirmed it touches only `channels.py`, `backend/tests/test_websocket_channels.py`, and `docs/architecture/system-design.md` §10.3's `MarketStateChanged` row — the SAME row this delivery also edits. Resolved as a merge, not an overwrite: this delivery's own sentence was appended after that entry's own sentence in the same table cell, and every place this delivery had claimed "no channel exists" (the hook's own `POLL_INTERVAL_MS` comment, `trading-intelligence-architecture.md` §4's as-built note and diagram) was corrected to say the channel now exists but is not yet consumed by this hook, rather than left stale and misleading.
  - Both times, this delivery's own six edited/new files were re-verified as byte-identical-base against the pull immediately before and after, confirming clean re-application with no other merge conflicts.
- Three-source decision-number check (`INDEX.md` tail, `confirmed-decisions.md` tail, `docs/decisions/archive/` file list), re-run after both re-pulls: latest real number is still #142 both times; all five PENDING entries (`data-feed-status-indicator`, `broker-connection-panel`, `ibkr-historical-backtest-provider`, `market-state-changed-websocket-channel`, and this delivery's own) now anticipate the same next-available number, **143** — none has merged or been assigned yet as of final packaging.
- Confirmed by inspection and by `diff -rq` against the final fresh clone that this delivery is file-disjoint from all four other pending/landed deliveries except for the one shared, merged line in `system-design.md` §10.3 noted above.
- **Flag for Saqib, not resolved silently:** appending this delivery's own decision-log entry pushed `confirmed-decisions.md` to 112,132 bytes, well past the ~100KB rollover threshold `docs/decisions/README.md` documents (it was already past that threshold before this entry, from the two deliveries that landed mid-session). Not rolled over here — see the dedicated note at the end of this delivery's own entry in `confirmed-decisions.md` for why (archiving would strand five still-unnumbered PENDING entries' own "update at merge time" instructions) and the recommended timing.

## Frontend build verification

Baseline established on a freshly-pulled, untouched second clone (same `npm install`, same lockfile) before attributing any error to this delivery:

```
$ cd frontend && npx tsc -b
src/components/workspace/GridPresetPicker.tsx(2,10): error TS2305: Module '"../../types/workspace"' has no exported member 'GRID_PRESETS'.
src/components/workspace/GridPresetPicker.tsx(6,11): error TS2339: Property 'preset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(6,19): error TS2339: Property 'setPreset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(19,30): error TS7006: Parameter 'p' implicitly has an 'any' type.

$ npx vite build
✓ 95 modules transformed.
✓ built in 3.93s
```

Working tree, same commands, same result set:

```
$ npx tsc -b --force
src/components/workspace/GridPresetPicker.tsx(2,10): error TS2305: Module '"../../types/workspace"' has no exported member 'GRID_PRESETS'.
src/components/workspace/GridPresetPicker.tsx(6,11): error TS2339: Property 'preset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(6,19): error TS2339: Property 'setPreset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(19,30): error TS7006: Parameter 'p' implicitly has an 'any' type.

$ npx vite build
✓ 96 modules transformed.
✓ built in 4.04s
```

Identical four pre-existing decision-#35 `GridPresetPicker` errors, same lines, both sides — zero new `tsc` errors introduced. `vite build`: 95 modules on the untouched baseline, 96 on the working tree — exactly +1, the one new `useMarketState.ts` file (`api-client.ts`/`InfoTab.tsx`/`AIAnalysisPanel.tsx` are edits to already-counted modules, not new ones). Both trees built with `node_modules` installed fresh from the same `package-lock.json`; `frontend/tsconfig.tsbuildinfo` is a generated build cache (absolute paths, timestamps) that differs across any two separate checkouts regardless of source changes, so it's excluded from this delivery's zip rather than treated as part of the footprint.

## No companion frontend test file

Re-confirmed by search immediately before writing this delivery that no hook in this codebase has its own test file today (same practice decisions #123/#125/#126 already document as still true) — `useMarketState.ts` doesn't introduce a new testing convention solely for itself.

## Verification — footprint

`diff -rq` against a freshly-pulled untouched second clone confirms the only files touched are:

- `frontend/src/services/api-client.ts` (extended)
- `frontend/src/hooks/useMarketState.ts` (new)
- `frontend/src/components/workspace/InfoTab.tsx` (extended)
- `frontend/src/components/ai-panel/AIAnalysisPanel.tsx` (extended)
- `docs/architecture/trading-intelligence-architecture.md` (extended, §4)
- `docs/architecture/system-design.md` (one sentence added to the `MarketStateChanged` §10.3 row, merged onto the same row `market-state-changed-websocket-channel` also edited)
- `docs/decisions/confirmed-decisions.md` (this entry, appended)
- `docs/decisions/INDEX.md` (matching row, appended)
- `CHANGES.md`, `TESTING.md` (this file, delete-first rewrite)

Confirmed untouched: everything under `backend/`; `frontend/src/App.tsx`; `frontend/src/services/websocket-client.ts`; `backend/app/api/websocket/channels.py`; `frontend/src/hooks/useOpportunities.ts`, `useOpportunityConflicts.ts`, `useContextSnapshot.ts`, `useStrategyOutcomes.ts`, `usePerformanceAnalytics.ts`, `useBacktestOutcomes.ts`, `useBacktestRuns.ts`; any Finnhub/Polygon/broker-panel component or hook.
