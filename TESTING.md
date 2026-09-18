# TESTING — pending delivery `backtest-panel-ibkr-real-data-option`

This is a replacement repo-root `TESTING.md` (the version it replaces was
`market-state-websocket-upgrade`'s own) — **this delivery is frontend-only**,
covering exactly this delivery; earlier deliveries' own verification detail
is not reproduced here (each is fully recorded in its own decision-log
entry, `docs/decisions/confirmed-decisions.md`).

## What changed

`BacktestPanel.tsx` gains a "Real IBKR data" trigger mode alongside the
existing fixture-scenario mode, wiring the previously-unreachable-except-
by-hand `POST /backtest/run/ibkr` (temp id `ibkr-historical-backtest-provider`,
already merged) into the same panel. New `easternTime.ts` (DST-aware
America/New_York wall-clock conversion, no library), new sibling hook
`useIbkrBacktestRun.ts`, and an additive-only block appended to
`api-client.ts` (`triggerIbkrBacktest()`, `IbkrBacktestError`). Full
reasoning, design forks, and the DST-conversion bug found and fixed during
implementation are recorded in this delivery's own decision-log entry —
not duplicated here.

## Base

- Base tarball first pulled at task start; re-pulled twice more during the
  task (three-source re-check protocol) as two parallel sessions landed on
  `main` mid-task: `world-view-v1` (backend-only: `backend/app/world_view/`,
  `backend/tests/test_world_view.py`, `backend/app/api/routes/intelligence.py`)
  and `market-state-websocket-upgrade` (`frontend/src/hooks/useMarketState.ts`
  plus two architecture docs). Both confirmed zero file overlap with this
  delivery in either direction — `market-state-websocket-upgrade`'s own
  entry explicitly names `BacktestPanel.tsx`/`useBacktestRun.ts` as "the
  parallel IBKR real-data backtest track's own boundary" and confirms it
  left them untouched. This delivery's committed diff is against the third,
  final pull, taken immediately before packaging.

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
baseline's 96 transformed modules plus exactly 2 (the two new files:
`easternTime.ts`, `useIbkrBacktestRun.ts`), no errors or warnings.

## Manual verification of `easternTime.ts`'s DST conversion

No frontend test file exists for any hook, component, or API-client
wrapper in this codebase (confirmed by search before starting — matches
this project's existing test-free frontend practice, decision #123's own
precedent), so none was added here either, per Saqib's own explicit
instruction not to introduce a new frontend test framework for this
change. The one genuinely non-trivial piece of new logic — the DST-aware
Eastern-time conversion — was instead hand-verified: transpiled standalone
with `esbuild` and run under plain Node, with the process `TZ` deliberately
set to `Asia/Dhaka` (not America/New_York) to prove the result comes from
the named IANA zone argument rather than the process's own local time.

Cases exercised, all passing on the final version:

- Regular-session open in EST (Jan) and EDT (Jul) — correct UTC instants.
- Extended-session open (04:00 ET) and close (20:00 ET) in EDT.
- The spring-forward gap itself (`2027-03-14T02:30`, which never occurs) —
  correctly rejected.
- The minute immediately before the gap (`01:59`) and immediately after it
  (`03:00`) — both correctly accepted. **`03:00` was the case that caught a
  real bug**: a naive one-shot "guess the offset, apply once" version of
  the DST-conversion trick incorrectly rejected this genuinely valid
  wall-clock time as nonexistent, because the offset applicable to the
  *naive guess* differed from the offset applicable to the *resolved*
  instant, right at the transition boundary. Fixed with a second
  refinement pass before the round-trip check; re-verified after the fix.
- The fall-back ambiguous hour (`2027-11-07T01:30`, which occurs twice) —
  accepted, resolving to one real occurrence. Documented, accepted
  limitation (see `easternTime.ts`'s own header comment): genuinely
  indistinguishable from a bare wall-clock string without a timezone
  library, and out of scope for backtest ranges targeting market hours.
- Malformed input (`"not-a-date"`) and an invalid calendar date
  (`2027-02-30`) — both correctly rejected.
- Regular-session (9:30–16:00 ET) and extended-session (4:00–20:00 ET)
  window math — 390 and 960 minutes respectively, matching the backend
  route's own docstring figures ("about 6.5 minutes" / "up to about 16
  minutes") exactly.

Not committed as an automated test file, since no pattern for one exists
in this codebase yet, matching Saqib's own explicit instruction.

## What wasn't covered

- No live `POST /backtest/run/ibkr` call was made against a real IB
  Gateway/TWS session from this delivery (no such session is reachable in
  this sandbox — see the `ibkr-historical-backtest-provider` entry's own
  prior notes on that same constraint). This delivery's own correctness
  rests on: (a) the route's own already-merged backend implementation and
  tests, (b) direct code review confirming the request/response contract
  and full error taxonomy against `backtest.py`/`ibkr_historical.py`
  rather than assumed from either route's docstring, and (c) the
  hand-verification above for the one piece of new client-side logic with
  real correctness risk. Recommend Saqib smoke-test the "Real IBKR data"
  mode end to end against a running backend with a real, reachable
  Gateway before relying on it.
- No automated frontend test file, per this codebase's existing
  test-free frontend convention (not a gap specific to this delivery).

## Manual merge notes

- `docs/decisions/confirmed-decisions.md`: this delivery's own PENDING
  entry (temp id `backtest-panel-ibkr-real-data-option`) was appended
  after `market-state-websocket-upgrade`'s entry, which was already on
  `main` at the final re-pull. If another parallel PENDING entry lands
  between this delivery's packaging and its merge, append after that
  entry instead — do not reorder or renumber anything already on `main`.
- `docs/decisions/INDEX.md`: unchanged by this delivery (correct — no
  real number assigned yet, per standing rule). Do NOT add a row for
  `backtest-panel-ibkr-real-data-option` until a real number is assigned
  at merge time via a fresh three-source re-check.
- No file-level conflict expected with any currently-PENDING entry — this
  delivery's footprint (`BacktestPanel.tsx`, `easternTime.ts`,
  `useIbkrBacktestRun.ts`, the additive block in `api-client.ts`,
  `backtest-runner-design.md`, `confirmed-decisions.md`, this file,
  `CHANGES.md`) shares no file with any of them.
