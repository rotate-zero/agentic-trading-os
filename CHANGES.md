# Indicators: SMA, EMA, VWAP, Previous Day Levels, Pre-Market Levels, Camarilla Pivots, VPOC

Copy these into your repo root, overwriting the existing paths. This zip is
the full current state of the indicator feature (both rounds combined) — you
don't need last time's zip anymore. Full reasoning is in
`docs/decisions/confirmed-decisions.md` #40 and #41; `docs/architecture/
system-design.md` §4.11 has the companion architecture note.

## New files — one calculation file per indicator (frontend/src/indicators/)
- `types.ts` — shared `IndicatorPoint` type
- `sessions.ts` — US-Eastern trading-day / pre-market / regular-session
  classification (`Intl.DateTimeFormat`, DST-correct). Shared by everything
  below except sma/ema.
- `sma.ts`, `ema.ts` — moved here from the old `utils/indicators.ts`
- `vwap.ts` — session VWAP, anchored to regular-session open (9:30 ET)
- `previousDayLevels.ts` — Close/High/Low together (same previous-session lookup)
- `premarketLevels.ts` — today's developing pre-market High/Low
- `camarillaPivots.ts` — all 9 levels (PP, R1-4, S1-4), standard formula
- `vpoc.ts` — previous day's Volume Point of Control (24-bucket approximation
  by typical price — documented as such, since real volume profile needs
  tick data this app doesn't have)

## Modified files
- `frontend/src/types/workspace.ts` — `OverlayIndicatorType` extended to
  `SMA | EMA | VWAP` (EMA fully retired off the old fixed-preset system);
  new `HorizontalLevelInstance` model for Previous Day/Pre-Market/Camarilla/
  VPOC with `lineStyle` (solid/dashed/dotted) and `showPriceLabel`;
  `PriceIndicatorInstance` also gained `showPriceLabel`; line-width step for
  overlays changed to **0.5** (see thickness fix below).
- `frontend/src/utils/indicators.ts` — now purely a dispatcher importing
  from `indicators/*`, no calculation logic of its own.
- `frontend/src/components/chart/ChartWidget.tsx` — new `horizontalLevels`
  prop rendered via `createPriceLine`; fractional line-width support for
  overlay series (see below); `showPriceLabel` wired to `lastValueVisible`/
  `axisLabelVisible`.
- `frontend/src/components/sub-window/SubWindow.tsx` — simplified now that
  only the instance-based system exists.
- `frontend/src/components/sub-window/SubWindowMenu.tsx` — "Indicators" root
  row (SMA/EMA/VWAP, unified) and new "Levels" root row (Previous Day/
  Pre-Market/Camarilla/VPOC, grouped add-buttons, per-instance line-style
  selector and price-tag checkbox).
- `frontend/src/state/WorkspaceContext.tsx` — real EMA9/EMA20 migration this
  time (colors were known, unlike SMA20/SMA50 last round); defaults updated.
- `docs/architecture/system-design.md`, `docs/decisions/confirmed-
  decisions.md` — decision #41, version bump.

## The thickness fix
Read the actual shipped `lightweight-charts` v4.2.3 source
(`lightweight-charts.production.mjs`) rather than guessing. Two renderers,
two behaviors:
- **Overlay line series** (`addLineSeries` — SMA/EMA/VWAP): passes
  `lineWidth` straight into the canvas 2D context's `lineWidth` (a float
  property) with **no rounding anywhere**. Only the TS type `1|2|3|4`
  restricted it — not the runtime. So these now step by **0.5**, and 1.5
  genuinely renders as real intermediate thickness. This should directly
  fix "1 is almost okay, 2 is too thick."
- **Horizontal levels** (`createPriceLine` — Previous Day/Camarilla/etc.):
  the renderer does `Math.floor(width * pixelRatio)` — fractional values get
  floored back to an integer, unreliably. These stay on integer 1-4 steps.

This is coupled to the exact installed lightweight-charts version — worth
re-checking after any future upgrade.

## Honest gap — not a bug
PDC/PDH/PDL, Camarilla, and VPOC all need **yesterday's** candles. Right now
neither mock data (~4 hours) nor live data (no historical backfill yet, per
decision #39) spans more than one day, so `getPreviousTradingDayCandles`
returns `[]` and these five indicator groups won't show anything to add
meaningfully yet. The math is correct and ready — it activates automatically
once real multi-day history exists, no code change needed.

## Verified
- `npx tsc -b` — clean (only the pre-existing `GridPresetPicker.tsx` dead
  code from decision #35 remains).
- `npx vite build` — succeeds.
- **Not verified**: still no live browser click-through in this sandbox —
  two rounds of chart-indicator work now without one. Worth doing before a
  third round builds more on top.
