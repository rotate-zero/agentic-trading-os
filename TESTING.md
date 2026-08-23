# HUD text box + Volume Avg label toggle — corrected, rebuilt on top of the
# Chart Style patch — TESTING.md

## Important — this replaces BOTH earlier zips from this session

The two zips sent earlier (`volume-avg-label-toggle.zip` and
`hud-and-label-toggle.zip`) were built from a repo snapshot fetched
*before* you pushed the Chart Style (candlestick/bar) patch. Applying
either of them as-is would have silently deleted that patch's code —
not just its two doc entries — because it touched the exact same five
files: `types/workspace.ts`, `WorkspaceContext.tsx`, `ChartWidget.tsx`,
`SubWindow.tsx`, `SubWindowMenu.tsx`. Its decision-log entry was also
`#73`, colliding with what would have been my own `#73`.

**Do not apply either of the earlier zips.** This one is a fresh fetch of
your actual pushed `main`, with the Volume Avg label-toggle fix and the
HUD box re-applied on top of it — every line of the Chart Style patch
(the `ChartStyle` type, the live series-swap effect, the three
overlays/horizontalLevels/dailyLevels effects' `chartStyle` dependency
fix, the menu screen) is verified byte-for-byte unchanged; see the diff
notes at the bottom of this file if you want to check that claim
yourself.

## What's in this zip
1. **Volume Avg price-label toggle** (decision #74) — every Volume Avg
   line (Day/3-Bar/6-Bar/9-Bar) now has its own "Show price tag" checkbox.
2. **On-chart HUD text box** (decision #75) — Menu → **HUD**. Up to 3
   independently-toggleable lines, each a free mix of text + variables
   (GAP, DAY, ATR, P/L, RVOL, VOL), background hex+opacity, text hex,
   left/right position. Defaults: Line 1 = GAP + DAY, Line 2 = ATR(14) +
   P/L, Line 3 = RVOL + VOL. ATR note: you asked for ATR[5]; the backend
   only computes a single global period (currently 14, `core/config.py`'s
   `feature_engine_atr_period`) — you chose ATR(14) over a new backend
   period or an unverified frontend approximation when this was flagged
   in chat.
3. **Chart Style** (decision #73, already yours, unmodified) — included
   only because it lives in the same files; nothing about it changed here.

## Files touched
- `frontend/src/types/workspace.ts` — HUD types + `showPriceLabel` field, alongside the existing `ChartStyle`/`chartStyle`
- `frontend/src/utils/hud.ts` — new: variable catalog, formatters, `hexWithOpacity`
- `frontend/src/hooks/useHudFeatures.ts` — new: live values off the existing `/intelligence/state` snapshot
- `frontend/src/components/chart/HudBox.tsx` — new: the overlay itself
- `frontend/src/components/chart/ChartWidget.tsx` — HUD render + label-toggle fix; Chart Style's series-swap logic untouched
- `frontend/src/components/sub-window/SubWindow.tsx` — calls `useHudFeatures`, passes it through; `chartStyle` prop untouched
- `frontend/src/components/sub-window/SubWindowMenu.tsx` — new HUD menu panel + `HudLineEditor`; Volume Avg checkbox; Chart Style screen untouched
- `frontend/src/state/WorkspaceContext.tsx` — `hud` wired into every construction site + back-fill; `chartStyle` wiring untouched
- `docs/decisions/confirmed-decisions.md` — decisions #74 and #75 appended after the real #73 (Chart Style)
- `docs/architecture/system-design.md` — HUD paragraph appended after the Chart Style paragraph

## How to apply
Unzip directly into your project root — paths already match
(`frontend/src/...`, `docs/...`), so it merges into the existing tree; no
files are deleted or moved. Since this is a full rebuild on top of your
actual pushed `main`, every file in this zip is safe to overwrite what's
currently on disk.

## How to verify
1. `cd frontend && npm ci` (only if `node_modules` isn't already present)
2. `npx tsc -b 2>&1 | grep -v "GridPresetPicker"` — clean (already run here)
3. `npx vite build` — clean (already run here)
4. `npm run dev` — confirm Chart Style still works exactly as before
   (Menu → Chart Style → toggle candlestick/bar, zoom/pan should survive
   the switch, overlays/levels should stay attached)
5. Menu → **Volume Avg** → each line's "Show price tag" checkbox
6. Menu → **HUD** → toggle on, confirm the 3 default lines render
   top-left over the candles; try disabling a line, adding a text/variable
   segment, changing colors/opacity/alignment
7. Existing saved sessions should load with HUD off and Chart Style at
   candlestick by default — both back-filled in `WorkspaceContext.tsx`

## Not verified
No live browser click-through was possible in this environment. Verified
via `tsc`/`vite build` and a manual diff against your actual pushed
`main` (confirming the Chart Style patch's code is byte-for-byte
preserved) — not visually confirmed in a running browser.
