import { useCallback, useLayoutEffect, useState, type RefObject } from "react";

/**
 * Shared placement logic for every "absolute-positioned, anchored to a
 * `relative` wrapper, opens top-full below the anchor" dropdown panel in
 * this app (SubWindowMenu, LayoutsMenu, GridPicker, FeatureEnginePanel's
 * ticker search).
 *
 * Fixes a recurring bug (first patched once already, insufficiently, on
 * SubWindowMenu's own panel — see that file's inline comment on the old
 * fix and confirmed-decisions.md's entry on this hook): a flat
 * `max-h-[80vh]` — or no max-height at all, as GridPicker/LayoutsMenu had
 * — caps the panel's OWN height, but does nothing about WHERE it's
 * anchored. A sub-window near the bottom of a busy grid layout has its
 * toolbar already most of the way down the viewport, so "80% of the
 * viewport" measured from there still runs well past the bottom of the
 * screen. `overflow-y-auto` only produces a scrollbar when content
 * exceeds the panel's OWN max-height box — it does nothing when the
 * panel itself is simply positioned beyond the visible viewport, which
 * is what was actually happening (no scrollbar ever appeared; the
 * content was just cut off by the browser window's edge).
 *
 * This measures the anchor's actual position when the panel opens (and
 * keeps it current across resizes/scrolls while open) and picks
 * whichever direction — down or up — has more room, capping max-height
 * to the REAL remaining space in that direction rather than a fixed
 * viewport fraction. Every consumer still needs `overflow-y-auto` on the
 * panel itself for the case where even the larger of the two directions
 * isn't enough (e.g. a very short/small browser window) — this hook
 * only picks the better anchor point and an accurate ceiling; the
 * existing overflow-y-auto class is what turns that ceiling into an
 * actual scrollbar when content still exceeds it.
 */
export interface DropdownPlacement {
  vertical: "down" | "up";
  maxHeight: number; // px — the real remaining space in the chosen direction
}

const VIEWPORT_MARGIN = 8; // px kept clear from the browser edge
const MIN_USABLE_HEIGHT = 160; // px — below this, flipping direction stops helping much, so just take what's there

export function useDropdownPlacement(open: boolean, anchorRef: RefObject<HTMLElement>): DropdownPlacement {
  const [placement, setPlacement] = useState<DropdownPlacement>({ vertical: "down", maxHeight: 400 });

  const recompute = useCallback(() => {
    const el = anchorRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_MARGIN;
    const spaceAbove = rect.top - VIEWPORT_MARGIN;
    // Prefer "down" (matches every panel's existing top-full markup, so
    // the common case renders identically to before) unless there's
    // genuinely not enough room and "up" offers meaningfully more —
    // avoids flip-flopping right around the screen's midpoint.
    if (spaceBelow < MIN_USABLE_HEIGHT && spaceAbove > spaceBelow) {
      setPlacement({ vertical: "up", maxHeight: Math.max(spaceAbove, MIN_USABLE_HEIGHT) });
    } else {
      setPlacement({ vertical: "down", maxHeight: Math.max(spaceBelow, MIN_USABLE_HEIGHT) });
    }
  }, [anchorRef]);

  useLayoutEffect(() => {
    if (!open) return;
    recompute();
    window.addEventListener("resize", recompute);
    window.addEventListener("scroll", recompute, true); // capture: catches scroll on any ancestor, not just window
    return () => {
      window.removeEventListener("resize", recompute);
      window.removeEventListener("scroll", recompute, true);
    };
  }, [open, recompute]);

  return placement;
}
