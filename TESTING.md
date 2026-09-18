# TESTING — pending delivery `market-state-websocket-upgrade`

This is a replacement repo-root `TESTING.md` (the version it replaces was
`world-view-v1`'s own) — **this delivery is frontend-only**, so this file
covers exactly this delivery; `world-view-v1`'s own backend verification
detail is not reproduced here (it's fully recorded in its own decision-log
entry, `docs/decisions/confirmed-decisions.md`, temp id `world-view-v1`).

## What changed

`frontend/src/hooks/useMarketState.ts` upgraded from fetch + 5-second poll
to WebSocket-primary, subscribing to the `intelligence.market-state`
channel (temp id `market-state-changed-websocket-channel`, already merged)
via `workspaceSocket`, mirroring `useContextSnapshot.ts`'s decision #126
pattern. The poll was removed entirely (not kept as a fallback) — see this
delivery's own decision-log entry for the full reasoning. Public return
shape (`symbolState`, `market`, `loading`, `refetch`) is unchanged; no
consumer file needed edits.

Docs updated in the same change: `docs/architecture/system-design.md`
§10.3 (corrected `MarketStateChanged` row + two new diagrams) and
`docs/architecture/trading-intelligence-architecture.md` §4 (prose +
both existing diagrams updated to reflect the current WS-primary design,
superseding the now-stale fetch/poll-only versions).

## Base

- Base tarball first pulled at task start; re-pulled a second time
  immediately before packaging (three-source re-check protocol), since a
  parallel World View session (temp id `world-view-v1`) landed on `main`
  mid-task. This delivery's committed diff is against that second,
  current pull.
- Confirmed via `diff -rq` against a fresh untouched clone: `world-view-v1`
  is entirely backend (`backend/app/world_view/`,
  `backend/tests/test_world_view.py`, `backend/app/api/routes/
  intelligence.py`) — zero overlap with this delivery's own footprint,
  in either direction.

## How to verify

```bash
cd frontend
npm install
npx tsc -b
npx vite build
```

Both commands are clean. `npx tsc -b` reproduces only the four known,
pre-existing decision #35 `GridPresetPicker` errors
(`GRID_PRESETS`/`preset`/`setPreset` missing, one implicit-`any`) —
confirmed identical, line for line, against a `tsc -b` run captured on a
fresh untouched clone before this change. `npx vite build` produces the
same module count (96 transformed) and completes with no errors.

No frontend hook in this codebase has its own automated test file
(confirmed by search before starting — matches this project's existing
test-free hook practice, decision #123's own precedent), so none was
added for this change either.

Manual/runtime verification of the actual WebSocket round-trip
(subscribe → real `MarketStateChanged` push → `load()` re-fetch →
re-render) was not performed in this sandbox — no live Finnhub/Polygon
feed or running frontend dev server against a live backend was available
here. The channel itself (`intelligence.market-state`) and its two real
envelope shapes were already proven end-to-end by
`market-state-changed-websocket-channel`'s own `test_websocket_channels.py`
(backend, real `TestClient`/WebSocket delivery, not mocked) — this
delivery's own correctness rests on: (a) that channel test coverage,
(b) `useContextSnapshot.ts`'s own decision #126 subscribe/handle/
unsubscribe pattern already proven live in production use for the
identical mechanics, and (c) direct code review confirming the sentinel
branch (`"__MARKET__"` vs. a real ticker vs. any other symbol) matches
`channels.py`'s own documented convention exactly.

## What wasn't covered

- No live WebSocket round-trip test (see above) — recommend Saqib smoke-test
  this against a running backend with a live/replay feed before relying on
  it in the live workspace.
- No new automated test file, per this codebase's existing hook-testing
  convention (not a gap specific to this delivery).

## Manual merge notes

- `docs/decisions/confirmed-decisions.md`: this delivery's own PENDING
  entry (temp id `market-state-websocket-upgrade`) was appended after
  `world-view-v1`'s entry, which was already on `main` at re-pull time.
  If another parallel PENDING entry lands between this delivery's
  packaging and its merge, append after that entry instead — do not
  reorder or renumber anything already on `main`.
- `docs/decisions/INDEX.md`: unchanged by this delivery (correct — no
  real number assigned yet, per standing rule). Do NOT add a row for
  `market-state-websocket-upgrade` until a real number is assigned at
  merge time via a fresh three-source re-check.
- No file-level conflict expected with `world-view-v1`, the IBKR
  real-data backtest track (`BacktestPanel.tsx`/`useBacktestRun.ts`,
  untouched here), or any other currently-PENDING entry — this
  delivery's footprint (`useMarketState.ts`, `system-design.md`,
  `trading-intelligence-architecture.md`, `confirmed-decisions.md`,
  this file) shares no file with any of them.
