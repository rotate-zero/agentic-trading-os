# Trading Workspace — Frontend (Phase 1)

Phase 1 of `system-design.md` §7, now including: multiple Main Windows (tabs) with globally-linked connectors, an 8x8 free-form grid picker, a relocated ticker search, a nested Sub Window menu, a fixed-candle-count zoom stepper, per-sub-window background + grid line color, a round radar-style bar-progress timer, customizable volume-average lines, and a no-database save/load system for layouts. Still no backend, no broker.

## Run it

```bash
npm install
npm run dev
```

## Save/load — what "no database yet" means here

Everything persists to the browser's `localStorage`, in two layers:

1. **Session autosave** (silent, automatic) — every Main Window, every sub-window's config, and the global connector-symbol map are saved on every change and restored on page load. Refreshing the browser, or coming back tomorrow, picks up exactly where you left off with zero action required.
2. **Named Saved Layouts** (`Layouts` button, top right) — explicitly save the *active* Main Window under a name, list/load/delete saved layouts, and Export/Import them as a JSON file (useful as a backup, or to move layouts to another browser/machine, since localStorage alone doesn't travel).

Loading a saved layout replaces the currently active tab's grid + sub-windows, and re-asserts that layout's connector symbols globally (so it looks exactly like it did when saved, even if those connectors have since been used for something else elsewhere).

**This isn't throwaway work.** The JSON shape `SavedLayout` produces (see `types/workspace.ts`) is deliberately the same shape system-design.md's `workspace_layouts` table is meant to hold — when a real backend exists, `saveCurrentLayout`/`loadLayout` in `state/WorkspaceContext.tsx` become API calls instead of `localStorage` calls, and nothing about the data shape needs to change.

## New in this version

- **Candle count stepper** — the "Candles" submenu is now `All` plus a `− [count] +` stepper (5–500, step 5) instead of a fixed preset list. First press away from "All" always lands on 20.
- **Per-sub-window background color** — new "Background" submenu: a native color swatch picker plus a hex text field (red-outlined while invalid), with a one-click reset to the theme default. Applied live via `chart.applyOptions` so it never resets zoom/pan.
- **Grid line color** — the same "Background" submenu now also has a "Grid lines" field, identical format (swatch + hex, red-outlined while invalid). Lives alongside background color in `SubWindowConfig.gridColor`, applied live the same way, one combined "Reset to default" for both.
- **Round radar timer badge** — a small badge in the top-right corner of each sub-window's chart sweeps clockwise (via CSS `conic-gradient`) to show progress through the current timeframe bar, 0–100%. Default on, green, in a fixed dark-grey/black-bordered frame so it stays legible regardless of the chart's own background color. New "Timer" submenu: on/off toggle, sweep color (swatch + hex), reset to default. No live Market Clock exists yet (that's Phase 2), so progress is approximated from wall-clock time modulo the timeframe's duration — see `utils/timerProgress.ts` for the swap-out point once real bar-open timestamps exist.
- **Volume average lines** — up to 4 horizontal average-volume lines drawn on the volume pane: a "Day Avg" (average of every bar currently loaded at the selected timeframe — an approximation until a real session boundary exists in Phase 2/3) plus three trailing N-bar averages (default 3/6/9, adjustable 2–50 via a `− [count] +` stepper). New "Volume Avg" submenu: master on/off, a checkbox + color (swatch + hex) + bar-count stepper per line. Off by default, same opt-in convention as the Indicators list. See `utils/volumeAverages.ts`.

All three respect this repo's own conventions: applied live without resetting chart zoom/pan, persisted through the existing session-autosave/Saved-Layouts/export-import paths (`SubWindowConfig` grew `gridColor`, `timer`, `volumeAvg`), and backward-compatible — sessions or exported layout files saved before this change are back-filled with defaults for the new fields on load (see `normalizeSubWindow` in `state/WorkspaceContext.tsx`) instead of breaking.

## Everything from the previous version still applies

Global connectors across Main Windows, the 8x8 hover grid, the relocated always-visible ticker search, and the nested Timeframe/Indicators/Connector/Candles/Background menu — see prior README revisions in git history / earlier zips for the detailed verification notes on those.

## Verified, not just type-checked

This round's features were exercised end-to-end with a scripted headless-browser pass: incrementing the candle stepper (confirmed landing on 20, then 25, then 30), setting a custom background hex and confirming it rendered, saving a named layout, and — the most important one — a full page reload confirming session autosave actually restored everything (tab, candle count, background color, saved layout list).
