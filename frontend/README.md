# Trading Workspace — Frontend (Phase 1)

Phase 1 of `system-design.md` §7, now including: multiple Main Windows (tabs) with globally-linked connectors, an 8x8 free-form grid picker, a relocated ticker search, a nested Sub Window menu, a fixed-candle-count zoom stepper, per-sub-window background color, and a no-database save/load system for layouts. Still no backend, no broker.

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

## Everything from the previous version still applies

Global connectors across Main Windows, the 8x8 hover grid, the relocated always-visible ticker search, and the nested Timeframe/Indicators/Connector/Candles/Background menu — see prior README revisions in git history / earlier zips for the detailed verification notes on those.

## Verified, not just type-checked

This round's features were exercised end-to-end with a scripted headless-browser pass: incrementing the candle stepper (confirmed landing on 20, then 25, then 30), setting a custom background hex and confirming it rendered, saving a named layout, and — the most important one — a full page reload confirming session autosave actually restored everything (tab, candle count, background color, saved layout list).
