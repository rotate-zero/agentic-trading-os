# Chart Style (Candlestick vs. Bar) — decision #73

## What's in this zip
Unzip directly into the project root — every path already matches the repo layout, so files land in place:

```
frontend/src/types/workspace.ts                      (edited)
frontend/src/state/WorkspaceContext.tsx               (edited)
frontend/src/components/chart/ChartWidget.tsx         (edited)
frontend/src/components/sub-window/SubWindow.tsx      (edited)
frontend/src/components/sub-window/SubWindowMenu.tsx  (edited)
docs/decisions/confirmed-decisions.md                 (appended — decision #73)
docs/architecture/system-design.md                    (§4.11 — new Chart Style paragraph)
```

Backend untouched — this is a pure frontend rendering feature, no new dependency (uses `lightweight-charts`' existing native `addBarSeries`, already in `package.json`).

## What it does
Sub-window menu → **Chart Style** (between Candles and Background) → toggle between **Candlestick** and **Bar (OHLC)**. Defaults to Candlestick for every new sub-window and for any layout saved before this change; per-window choice persists the same way every other display setting already does.

## Already verified here (sandboxed, no browser)
- `npm install && npx tsc -b` — clean, only the known pre-existing `GridPresetPicker.tsx` errors (decision #35) remain.
- `npx vite build` — clean production build.
- Confirmed only the 5 frontend files above changed (no backend files touched).

## Please verify
1. `cd frontend && npm install` (only if `lightweight-charts` version changed — it didn't, so this is likely a no-op) → `npx tsc -b` (filter `GridPresetPicker`) → `npx vite build`.
2. Real browser click-through (this sandbox can't do this): open a sub-window, toggle Chart Style back and forth a few times —
   - Series visually switches between candles and OHLC bars.
   - Zoom/pan position is preserved across the toggle (not reset).
   - Any horizontal levels / overlays / Daily Levels lines you have showing reattach correctly and don't vanish after a switch.
   - "Reset to default" returns it to Candlestick.
3. Reload the page (or a saved layout from before this change, if you have one) — confirm it still renders Candlestick with no console errors from the missing `chartStyle` field.
