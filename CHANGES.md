# SMA indicator system — changed files

Copy these into your repo root, overwriting the existing paths. Full reasoning
is in `docs/decisions/confirmed-decisions.md` #40; `docs/architecture/system-design.md`
§4.11 has the companion architecture note.

## Files
- `frontend/src/types/workspace.ts` — new `PriceIndicatorInstance` model
  (id/type/enabled/period/color/lineWidth), `SubWindowConfig.priceIndicators`.
  Removed `SMA20`/`SMA50` from the old fixed `IndicatorType` (superseded).
  `EMA9`/`EMA20` untouched.
- `frontend/src/utils/indicators.ts` — new `computePriceIndicator()`, SMA case.
- `frontend/src/components/chart/ChartWidget.tsx` — `lineWidth` support on
  `IndicatorSeries`; fixed a real bug where an existing series never picked
  up color/width changes after creation (only mattered once indicators
  became live-editable).
- `frontend/src/components/sub-window/SubWindow.tsx` — merges legacy EMA
  series + new SMA instance series into one list for `ChartWidget`.
- `frontend/src/components/sub-window/SubWindowMenu.tsx` — new "SMA" root
  menu row + submenu: add/remove instances, per-instance period stepper,
  line-width stepper, color picker + hex field (`SmaIndicatorRow`).
- `frontend/src/state/WorkspaceContext.tsx` — default sub-window seeds
  updated (`sw-1`, 5m timeframe, now ships with 9/20/50 SMA as a live demo
  of the feature); `normalizeSubWindow` back-fills `priceIndicators: []` for
  existing localStorage sessions and strips any persisted `SMA20`/`SMA50`.
- `docs/architecture/system-design.md` — §1 non-goal footnote + new §4.11
  paragraph distinguishing client-side chart overlays from Feature Engine
  output; version bump.
- `docs/decisions/confirmed-decisions.md` — decision #40, full reasoning.

## Verified
- `npx tsc -b` — no new errors (only the pre-existing, already-flagged
  `GridPresetPicker.tsx` dead code from decision #35).
- `npx vite build` — succeeds.
- **Not verified**: no live browser click-through was done in this pass (no
  browser available in the sandbox this was built in). The `applyOptions`
  live-color/width fix and the SMA submenu's on-screen behavior are correct
  by inspection and by the checks above, not by clicking through them —
  worth doing before treating this as fully proven, same bar decisions
  #32/#34/#36/#37/#38 met.

## Deliberately not done
- No `"1d"` / daily timeframe added to the frontend `Timeframe` union. The
  backend already accepts `timeframe="1d"` on `/market/candles`, but
  decision #39 leaves the free-tier daily-bar *source* unresolved, and
  `useLiveCandles` currently only ever fetches/resamples from a rolling
  1-minute buffer regardless of the sub-window's selected timeframe. Adding
  `"1d"` today would render a cosmetic daily chart with at most one
  incomplete bar — not real data. This SMA system is timeframe-agnostic and
  needs zero changes once #39 resolves and real daily backfill is wired up.
- EMA9/EMA20 left on the old fixed-preset model — migrating them to the new
  instance shape is a natural follow-up, not done here (out of scope).
