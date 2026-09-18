# CHANGES — pending delivery `backtest-panel-ibkr-real-data-option`

Base commit: fresh `main` re-pulled immediately before packaging (after
`world-view-v1` and `market-state-websocket-upgrade` both landed mid-task;
confirmed file-disjoint from this delivery both ways).

## What changed

- Added a "Real IBKR data" trigger mode to `BacktestPanel.tsx`, alongside
  the existing "Fixture scenario" mode — a two-way toggle; `strategy_name`
  and `symbol` shared across both modes (same field, mode-conditional
  caption), `scenario` shown only in fixture mode, a new Eastern-time
  date-range section (with "Regular session"/"Extended session" presets)
  shown only in ibkr mode.
- New `frontend/src/components/backtest/easternTime.ts`: DST-aware
  America/New_York wall-clock ↔ UTC conversion (no new dependency —
  `Intl.DateTimeFormat`), round-trip validated. A real false-negative bug
  in the naive one-shot version of this conversion (rejecting a genuinely
  valid wall-clock time right at a DST boundary) was caught by that
  round-trip check during implementation and fixed with a two-step
  fixed-point solve — see this delivery's own decision-log entry for the
  full derivation and verification.
- New `frontend/src/hooks/useIbkrBacktestRun.ts`: sibling to
  `useBacktestRun.ts`, not a mode branch inside it — different request
  shape, error taxonomy (`code` field), and timing profile (~6.5-16min vs
  ~120-140s). `useBacktestRun.ts` itself is unchanged.
- Additive-only block appended to `frontend/src/services/api-client.ts`:
  `triggerIbkrBacktest()`, `IbkrBacktestError` (extends `ApiError`, adds
  `code: string | null`), and a dedicated `parseIbkrErrorDetail()` — the
  first place in this codebase where a route's `detail` is object-shaped
  (`{code, message}`) rather than a plain string. The shared
  `parseErrorDetail()`/`ApiError` and every existing caller are untouched.
- `BacktestPanel.tsx` classifies every real error this route can return
  (409 live-data guard, `invalid_backtest_request` 422, and all eight
  `ibkr_*` codes from `ibkr_historical.py`'s error hierarchy plus its
  `ibkr_backtest_not_configured` 503) into a specific heading with the
  backend's own message shown verbatim underneath — safe generic fallback
  for any unrecognized code or unparseable response.
- Mode toggle and both submit paths disabled while either mode's own hook
  reports `"running"`; every result/error render block is gated on mode
  together with that mode's own hook state, so one mode's result/error can
  never render while the other mode is selected; `setLastBacktestRunId`
  (decision #134) fires from two independent per-hook effects.
- Updated `docs/architecture/backtest-runner-design.md` §7: corrected
  decision #130's own as-built note (no longer describes the panel as
  fixture-scenario-only) and added a new as-built note with two diagrams
  (cross-component trigger flow; the internal ET-conversion/validation/
  error-classification flow).
- Appended the unnumbered `backtest-panel-ibkr-real-data-option` entry to
  `docs/decisions/confirmed-decisions.md`. No real number or `INDEX.md`
  row assigned (ten-way collision on next-available-143 — see that entry
  for the full list).
- Replaced repo-root `TESTING.md` and this file with this delivery's own
  content.

## No backend changes

`POST /backtest/run/ibkr` and its full error taxonomy already existed
(temp id `ibkr-historical-backtest-provider`, already merged) — confirmed
by direct read of `backtest.py`/`ibkr_historical.py` before writing any
frontend code. Nothing under `backend/` was touched by this delivery.

## Validation summary

- `npx tsc -b`: clean — only the four known, pre-existing decision #35
  `GridPresetPicker` errors, confirmed identical against a fresh untouched
  clone's own baseline run.
- `npx vite build`: clean, 98 modules transformed (baseline's 96 + the 2
  new files), no errors.
- No automated frontend test file exists in this codebase (confirmed by
  search) — none added, matching existing practice; the one non-trivial
  piece of new logic (`easternTime.ts`'s DST conversion) was instead
  hand-verified under Node — see `TESTING.md` for the full case list,
  including the bug it caught.

## Related, not changed

`market-state-websocket-upgrade` (landed on `main` mid-task) already
flagged `confirmed-decisions.md` as well past its ~100KB rollover trigger,
now at a nine-way (ten-way after this entry) collision of unnumbered
PENDING entries blocking a safe rollover. This delivery adds one more such
entry and does not attempt the rollover, for the same reason already on
record.
