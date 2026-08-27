# Axis-label name toggle (all indicators), plus verification on the other two reports

Copy these into your repo root, overwriting the existing paths — this
replaces the previous drop entirely (you hadn't applied it yet), so this
one zip has everything. Full reasoning is in
`docs/decisions/confirmed-decisions.md` #81 and #82.

**Merged against your latest push before this drop was built:** your
`c74f28e` ("Scanner rvol universe v1, premarket accumulator design doc")
touches `frontend/src/state/WorkspaceContext.tsx` and `frontend/src/
types/workspace.ts` — two files this drop also touches. Merged clean, no
conflicts (your scanner changes and mine sit in entirely separate
sections of both files), and re-verified with `tsc -b`/`vite build`
after merging, not just before. The copies of those two files in this
zip already contain both your scanner-universe work and this drop's
changes together — safe to overwrite, nothing from your push is lost.

## Your 4 screenshots, one at a time

**Screenshot 1 (labels have no on/off option) — fixed, all four
indicator types, not just the ones in the screenshot.** Real bug, not
cosmetic: Lightweight Charts renders an indicator series' name (`title`)
unconditionally whenever it's a non-empty string — it does **not** check
`lastValueVisible` (your existing "Show price tag" checkbox) first. So
unchecking that box for SMA 9/20/50 hid the number but left the colored
name badge floating on the axis with nothing attached to it, exactly as
your screenshot shows. Confirmed against the library's own issue
tracker, not guessed.

A new, independent **"Show label"** checkbox now sits right under "Show
price tag" on every indicator that has one: Indicators panel (SMA/EMA/
VWAP), Levels panel (PDH/PDL/PMH/PML/Camarilla/VPOC), Volume Avg lines,
and Daily Levels (labeled "Show 'DL-N' label" there, matching that
panel's own button style instead of a checkbox, since Daily Levels
doesn't use checkboxes anywhere else). One open item below.

**Screenshot 2 (Volume Bars — no opacity interface) — re-verified by
actually executing the real code, not just re-reading it.** There's no
working browser available in this sandbox (confirmed by attempting to
install one, not assumed), so instead of asking you to trust a second
read of the source, I ran the real, unmodified `ColorField` component
and the real `normalizeSubWindow` backfill logic through Node directly.
Concretely: `upOpacity` comes out as a real number (100) both for a
brand-new config and for a simulated legacy session missing that field
entirely (the backfill fills it in correctly), and rendering the actual
component with those exact values produces real HTML containing
`<input type="range">` and "100%" — confirmed it only disappears when
opacity is genuinely `undefined`. So the code, as it exists right now,
categorically produces this slider given the data the app actually
generates. If you're still not seeing it: hard-refresh or restart your
dev server first (this smells like a stale build/cache, not missing
code); if it's still missing after that, send a fresh screenshot or your
browser console output and I'll dig further from there — that would be
real evidence of something my testing didn't catch.

**Screenshots 3/4 (Daily Levels panel cut off, no scrollbar) —
re-verified against git history, not just the decision doc.** Rather
than trust `confirmed-decisions.md`'s own claim, I pulled the actual
`git show` diff of the fix commit (`b9f97a7`, Aug 25 — the `MIN_USABLE_
HEIGHT` floor that was inflating the panel's max-height past real
available space, removed) and checked the file's full history to
confirm nothing since has touched or reverted it. Your screenshot is the
original evidence that prompted that fix, not new proof the current code
is still broken — and it's consistent with your own annotation, too:
you noted the Indicators panel already had a working scrollbar, which
lines up with that panel having its own separate, always-on inner list
scroll unrelated to the bug; Daily Levels has no such inner scroll and
was fully exposed to the outer positioning bug the commit fixed. What I
genuinely can't do here is re-verify it visually myself — this depends
on live browser layout (`window.innerHeight`, `getBoundingClientRect`)
that doesn't exist outside a real browser, and none is available in this
environment. If you've pulled latest and it's still cut off in an actual
browser, that's a real signal I haven't been able to test against —
tell me and I'll dig further rather than assume it's resolved.

## What changed and why (decisions #81, #82)

- `frontend/src/types/workspace.ts` — new `showNameLabel` field (plural
  `showNameLabels` on `DailyLevelsConfig`, matching its existing plural
  naming) on all four indicator config types: `PriceIndicatorInstance`,
  `HorizontalLevelInstance`, `VolumeAvgLineConfig`, `DailyLevelsConfig`.
  Defaulted `true` everywhere it's created — matches the always-on
  behavior every existing session was already rendering, so nothing
  changes for you until you actually uncheck the new box.
- `frontend/src/utils/indicators.ts` — threaded through
  `computePriceIndicator` (SMA/EMA/VWAP, backend and local-fallback) and
  `computeHorizontalLevel`.
- `frontend/src/components/chart/ChartWidget.tsx` — all four rendering
  effects (indicator Line-series, horizontal-levels price-line,
  volume-avg price-line, daily-levels price-line) resolve the actual
  `title` string from the new flag instead of passing the label straight
  through unconditionally. This line is the actual bug fix in each case;
  everything else is plumbing to reach it.
- `frontend/src/components/sub-window/SubWindowMenu.tsx` — checkbox
  added to `OverlayIndicatorRow`, `HorizontalLevelRow`, and
  `VolumeAvgLineRow` (all three: same styling/placement as the existing
  "Show price tag" checkbox right above it); button added to the Daily
  Levels panel matching its own highlighted-button style.
- `frontend/src/state/WorkspaceContext.tsx` — `normalizeSubWindow`
  backfills the new field(s) to `true` for any workspace saved before
  they existed, on all four config types, same pattern already used for
  `showPriceLabel`/opacity backfills — your existing saved layouts won't
  lose their labels on next load.
- `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md` —
  decisions #81 and #82 recorded.

**Verified:** `npx tsc -b` (filtered for the pre-existing, unrelated
`GridPresetPicker.tsx` errors — not touched by this drop) and `npx vite
build` both clean, after both rounds of edits. No backend files touched,
so no `pytest` run applies. **Not verified: an actual browser** for the
label-toggle changes themselves — no browser available in this sandbox;
derived from Lightweight Charts' own documented `title`/`lastValueVisible`/
`axisLabelVisible` behavior against your screenshot, not confirmed by
reproducing the floating badge and watching it resolve on screen. (The
Volume Bars opacity and Daily Levels scrollbar re-verifications above
used real code execution and real git history instead, for exactly this
reason — trying to close that gap wherever the tooling allows it, not
just re-asserting.)

## Known, deliberately deferred / open questions

**A possible correction to #81's own stated limit, surfaced but not
acted on.** #81 said PDH/PDL-style levels could only get "name + price"
or "price only," never "name only, no price," because it assumed
`createPriceLine`'s `title` and `axisLabelVisible` draw as one fused
tag. Re-reading Lightweight Charts' own shipped type declarations while
wiring #82, they're documented as two independent things —
`axisLabelVisible` governs only the price-scale value, `title` is
described as rendering separately "on the chart pane." If that's
accurate, "name only, no price" may already work for all three
`createPriceLine`-based types, not just the version shipped. **Not
verified in a browser** — flagging this rather than quietly upgrading
the earlier claim or quietly leaving it as stated. Try unchecking "Show
price tag" while leaving "Show label" checked on a PDH line and let me
know what you actually see; that single data point settles it.

Nothing else deferred from the original four screenshots.
