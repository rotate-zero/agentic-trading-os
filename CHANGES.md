# Zoom fix, candle-close latency fix, volume bar customization, 4h/1d timeframes, multi-monitor pop-out

Copy these into your repo root, overwriting the existing paths. Full reasoning
for all five items is in `docs/decisions/confirmed-decisions.md` #42; the
multi-monitor and volume-bar architecture notes are in
`docs/architecture/system-design.md` §4.11.

**Docs note:** `docs/architecture/system-design.md` and
`docs/decisions/confirmed-decisions.md` are included here as full files (per
your usual "copy and overwrite" instruction), but a `docs-decision-42.patch`
unified diff is also included as a safer alternative if either file has
moved since this zip was built — apply with `git apply docs-decision-42.patch`
from the repo root instead of copying the full files, and it'll fail loudly
on a conflict rather than silently deleting anything (see confirmed-decisions
#35's note-to-self on exactly that failure mode).

## Backend

- `backend/app/services/tick_ingest.py` — **fixes the late-candle bug.**
  `TickIngestBridge` now has a wall-clock-driven `_flush_loop` that
  force-closes a stale candle bucket ~250ms after every minute boundary,
  instead of waiting for the next trade tick to arrive (root cause of the
  reported 09:34 candle not showing up until 09:34:42). New `stop()` method
  to cancel this loop cleanly.
- `backend/app/services/broker_registry.py` — calls the new
  `TickIngestBridge.stop()` in `take_over_streaming()` and
  `clear_streaming_provider()`, so the new background loop doesn't leak on
  every reconnect.
- `backend/tests/test_tick_ingest.py` — existing tests updated to call
  `bridge.stop()` in cleanup; new regression test
  `test_stale_bucket_closes_on_wall_clock_even_without_a_new_tick` drives
  `_flush_stale_buckets()` directly with a controlled timestamp and
  reproduces the exact reported symptom.
  **Verified: full suite 65/65 passing, no leaked-task warnings.**

## Frontend

- `frontend/src/components/chart/ChartWidget.tsx` — **fixes the zoom-reset
  bug.** New `diffCandles()` tells a live update (last bar refreshed, or one
  new bar appended) apart from a genuine reset (symbol/timeframe switch, or
  the candle-count stepper changing). Only a reset now calls
  `setData()` + re-pin; a live update calls `series.update()` instead, which
  never touches zoom/pan and only auto-scrolls the new bar into view if
  you're already at the live edge. Also wired up `volumeBars` — recolors/
  hides the histogram pane independently of the data effect, so a
  color/mode change never disturbs zoom either.
- `frontend/src/types/workspace.ts` — `Timeframe` gains `4h`/`1d`; new
  `VolumeBarsConfig` type + `createDefaultVolumeBarsConfig()`.
- `frontend/src/utils/resample.ts`, `frontend/src/utils/timerProgress.ts` —
  `4h`/`1d` entries added to each timeframe-keyed map.
- `frontend/src/components/sub-window/SubWindowMenu.tsx` — new "Volume Bars"
  submenu: show/hide, 2-color vs 1-color, hex pickers, reset-to-default.
- `frontend/src/components/sub-window/SubWindow.tsx` — passes
  `config.volumeBars` through to `ChartWidget`.
- `frontend/src/state/WorkspaceContext.tsx` — `volumeBars` added to every
  `SubWindowConfig` construction site and to `normalizeSubWindow`'s
  back-fill (old sessions default to the two-color green/red look, not an
  empty state). Also: cross-tab live sync (see below) and the new
  `lockedMainWindowId` prop for the pop-out view.
- `frontend/src/state/crossTabSync.ts` — **new file.** `BroadcastChannel`-
  based cross-tab sync: a tab that changes something pings other open tabs
  to re-read `localStorage`, rather than each tab reading it once at load
  and never again. Degrades gracefully (no sync, same as before) if
  `BroadcastChannel` isn't available.
- `frontend/src/App.tsx` — **rewritten.** Minimal hand-rolled router (`/` vs
  `/window/:id`, no library) — the popped-out view reuses the same
  `SubWindowGrid`/`InfoTab`/`GridPicker`/`LayoutsMenu` the main workspace
  uses, just without the tab strip, plus a link back to `/`.
- `frontend/src/components/workspace/MainWindowTabs.tsx` — new pop-out
  button (⧉) per tab, opens `/window/:id` via `window.open()`.

  **Verified:** `tsc -b` and `vite build` both succeed — only the
  pre-existing, already-flagged `GridPresetPicker.tsx` dead code (decision
  #35) remains. SPA fallback for `/window/:id` checked against a real
  `vite preview` server (`200` for both `/` and `/window/mw-1`).
  **Not verified:** no live browser click-through of any of this — same
  caveat carried forward from the last two rounds of UI work (decisions
  #40, #41). Worth prioritizing before more UI work stacks on top.

## Not included in this drop

- The multi-monitor discussion surfaced one open question, already resolved
  with you and built as agreed: original tab keeps a popped-out window as a
  live mirror (not removed from the strip). No further discussion needed
  there.
- Production static-hosting SPA-fallback config (nginx `try_files` or
  equivalent) isn't needed yet — `vite dev`/`vite preview` both already
  handle `/window/:id` correctly — but will be needed whenever this actually
  gets deployed somewhere other than `localhost:5173`.
