# TESTING — Decision #80

## What changed

- `frontend/src/hooks/useDropdownPlacement.ts` — actual code fix.
- `docs/decisions/confirmed-decisions.md` — replaced with a fresh open file; decision #80 is its first (and currently only) entry.
- `docs/decisions/archive/061-079.md` — new frozen archive file (decisions #61–#79, moved verbatim from the old `confirmed-decisions.md`).
- `docs/decisions/INDEX.md` — file-location column updated for #61–#79 → `archive/061-079.md`; new row added for #80.
- `docs/decisions/README.md` — structure table updated to reflect the second archive file and the fresh open file.

No backend files touched.

## How to unzip

From the project root (`agentic-trading-os/`):

```bash
unzip -o decision-80-dropdown-scroll-fix.zip
```

This will:
- Overwrite `frontend/src/hooks/useDropdownPlacement.ts` with the fix.
- Overwrite `docs/decisions/confirmed-decisions.md` with the new (short) open file.
- Add `docs/decisions/archive/061-079.md`.
- Overwrite `docs/decisions/INDEX.md` and `docs/decisions/README.md`.

Nothing else in the repo is touched — no unrelated modules were rewritten.

## What was investigated but NOT changed

**Volume Bars opacity** — already fully implemented (decision #77). `VolumeBarsConfig.upOpacity/downOpacity/singleOpacity` exist, are wired into `SubWindowMenu.tsx`'s "Volume Bars" panel via the same `ColorField` opacity slider every other color field uses, applied at render time via `hexWithOpacity()` in `ChartWidget.tsx`'s `volumeBarColor()`, and backfilled for old saved sessions in `WorkspaceContext.tsx`. Nothing to add here — see decision #80's own text for the full trace.

## Root cause of the "config panel too long, no scroll" report

`useDropdownPlacement.ts` already measured the anchor's real position and picked a `maxHeight` for whichever direction (up/down) had more room — this was decision #76's fix for exactly this class of bug. But its floor value, `Math.max(spaceBelow, MIN_USABLE_HEIGHT)` (and the `spaceAbove` equivalent), could hand back a `maxHeight` **larger than the real remaining space** whenever both directions had less than 160px available — e.g. a narrow/short viewport where the anchor sits close to both screen edges at once. When that happens, the panel gets positioned partly beyond the visible viewport; `overflow-y-auto` never engages because the panel's own box never exceeds its (wrongly inflated) max-height — the browser window's edge clips it first, with no scrollbar. This reproduces exactly what the screenshot showed.

**Fix:** `MIN_USABLE_HEIGHT` now only decides *which direction* to open in. The returned `maxHeight` is always `Math.max(spaceBelow, 0)` or `Math.max(spaceAbove, 0)` — the real measured space, never inflated past it.

This is a **shared hook** — `SubWindowMenu.tsx`, `LayoutsMenu.tsx`, `GridPicker.tsx`, and `FeatureEnginePanel.tsx`'s ticker search all consume it and already wire `maxHeight` into their own `overflow-y-auto` container. So this one change gives every long settings panel in the app (Volume Bars, Daily Levels, and anything added later) a working scrollbar on short/narrow viewports — not a per-panel patch.

## Verification performed

- `npx tsc -b` — clean (filtered for the pre-existing, known-dead `GridPresetPicker.tsx` errors per decision #35).
- `npx vite build` — clean, 79 modules transformed, no errors.
- No backend files touched, so no `pytest` run applies to this delivery.
- **Not verified:** an actual browser at the narrow viewport width shown in the report screenshot. The fix is derived from re-reading `useDropdownPlacement`'s own math against the reported symptom, not from reproducing the clipped view and watching it resolve live. Flagging this per the project's standing "no live browser verification" convention.

## Suggested manual check (since I can't click through a browser)

1. Resize the browser window to something narrow/short (or open on a phone-width viewport) — roughly matching the screenshot's ~362×502.
2. Open a sub-window's settings menu, drill into **Volume Bars** with two-color mode selected (the tallest panel: color-mode toggle + up color/opacity + down color/opacity + reset button).
3. Confirm the whole panel is scrollable and every field (including the "Down" color+opacity row and "Reset to default") is reachable, with no content silently clipped by the window edge.
4. Repeat for **Daily Levels** to confirm the original report screenshot's panel now scrolls too.
