# Dropdown scrollbar fix + opacity everywhere — TESTING.md

This is a standalone follow-up on top of your pushed `main` (which
already has Chart Style #73, plus the HUD box / Volume Avg label toggle
from `hud-and-label-toggle-v2.zip`, if you've applied that one). It does
NOT touch anything from those — only adds the two things below.

## 1. Dropdown panels getting cut off (decision #76)

Root cause: every dropdown panel in the app opens `top-full` below its
toggle button. The one existing fix (`max-h-[80vh]`) capped the panel's
own height, but didn't account for WHERE the panel was anchored — a
sub-window near the bottom of the grid has its toolbar already most of
the way down the screen, so "80% of viewport" measured from there still
runs off the bottom, with no scrollbar ever appearing.

Fixed with one shared hook (`useDropdownPlacement.ts`) applied to all 5
real dropdown panels in the app:
- `SubWindowMenu`'s main indicator menu (the one in your screenshot)
- `SubWindowMenu`'s ticker-search suggestion box
- `LayoutsMenu`
- `GridPicker`
- `FeatureEnginePanel`'s symbol search

It measures the toggle button's actual position and flips the panel
upward with an accurate max-height when there isn't room below, instead
of a flat viewport fraction. (`GridPresetPicker.tsx` was confirmed dead
code — not imported anywhere — so it's untouched; it can't be the source
of a bug nobody can trigger.)

**To verify:** open the HUD menu (or any menu) on a sub-window near the
bottom of a busy grid layout — the panel should now either fit and scroll
internally, or flip to open upward, instead of getting cut off with no
way to see the rest.

## 2. Opacity on every hex color field (decision #77)

Every color-bearing indicator now has an opacity slider next to its
existing hex picker: SMA/EMA/VWAP lines, horizontal levels (PDH/PDL/
Camarilla/VPOC/etc.), Timer sweep, Volume Avg lines, **Volume Bars
up/down/single — your stated priority**, Daily Levels, HUD background +
text, and the chart's own background/grid lines.

Every opacity defaults to 100 (fully opaque) — nothing looks different
until you actually move a slider. This delivery doesn't pick an opinion
about what looks good (e.g. semi-transparent volume bars, a common
convention elsewhere); it just makes it dial-able everywhere a color
already was.

**To verify — Volume Bars specifically, since that was the priority:**
Menu → Volume Bars → pick a color mode → each color (Up/Down or Bar
color) now has a slider next to its hex field. Drag it down and the
volume histogram bars should visibly fade.

**Other spots to spot-check:** Indicators (SMA/EMA/VWAP) → each instance
row now has a small slider after its hex field. Levels → same. Daily
Levels, Timer, Background, HUD → each `ColorField` row now has a slider
built in.

## Files touched
- `frontend/src/types/workspace.ts` — `opacity`/`upOpacity`/`downOpacity`/`singleOpacity`/`textOpacity`/`backgroundOpacity`/`gridOpacity` added across every color config, all defaulting to 100
- `frontend/src/utils/color.ts` — new: `hexWithOpacity()`, moved out of `utils/hud.ts` (no longer HUD-specific)
- `frontend/src/utils/hud.ts` — re-exports `hexWithOpacity` for backward compat
- `frontend/src/utils/indicators.ts` — threads `opacity` through `computePriceIndicator`/`computeHorizontalLevel`
- `frontend/src/hooks/useDropdownPlacement.ts` — new: the shared placement hook
- `frontend/src/components/chart/ChartWidget.tsx` — applies opacity at every render call site
- `frontend/src/components/chart/TimerBadge.tsx` — applies opacity to the sweep color
- `frontend/src/components/chart/HudBox.tsx` — applies the new `textOpacity`
- `frontend/src/components/sub-window/SubWindowMenu.tsx` — dropdown placement fix (both its dropdowns) + opacity sliders on `ColorField` and all 3 inline per-instance color pickers
- `frontend/src/components/sub-window/SubWindow.tsx` — untouched by this delivery, included only because it's unaffected (no diff beyond what you already have)
- `frontend/src/components/workspace/GridPicker.tsx`, `LayoutsMenu.tsx` — dropdown placement fix
- `frontend/src/components/intelligence/FeatureEnginePanel.tsx` — dropdown placement fix on its symbol search
- `frontend/src/state/WorkspaceContext.tsx` — back-fill migration for every new opacity field
- `docs/decisions/confirmed-decisions.md` — decisions #76 and #77
- `docs/architecture/system-design.md` — unchanged in this delivery (included for completeness/consistency, matches what you already have if you applied the previous zip)

## How to apply
Unzip directly into your project root — merges into the existing tree, no
files deleted or moved.

## How to verify
1. `cd frontend && npm ci` (only if `node_modules` isn't already present)
2. `npx tsc -b 2>&1 | grep -v "GridPresetPicker"` — clean (already run here)
3. `npx vite build` — clean (already run here)
4. `npm run dev` — walk through both checklists above
5. Existing saved sessions should load with every opacity at 100% (no
   visual change) and dropdown panels should just work better — no manual
   migration needed on your end.

## Not verified
No live browser click-through was possible in this environment. Verified
via `tsc`/`vite build` and manual diffing against your pushed `main` to
confirm nothing outside these two changes was touched — not visually
confirmed in a running browser. The dropdown flip-to-upward behavior in
particular is worth testing on an actual busy multi-row grid layout,
since that's the scenario it's specifically fixing.
