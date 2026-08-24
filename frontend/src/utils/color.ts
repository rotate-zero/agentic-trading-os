// General-purpose color helpers shared across every indicator that takes a
// hex color + opacity (decision #76) — SMA/EMA/VWAP lines, horizontal
// levels, Timer, Volume Avg lines, Volume Bars, Daily Levels, HUD
// background/text, and the chart's own background/grid. Originally lived
// in utils/hud.ts (the HUD box was the first indicator to get a
// background + opacity pair) but moved here once every other color field
// in the app grew its own opacity field too — nothing about this math is
// HUD-specific.

// Hex + 0-100 opacity -> rgba(...) string. Every color config field in
// this app stores color as plain opaque hex (so the native
// <input type="color"> picker — which can't represent alpha — still
// works directly against it) and opacity as a separate 0-100 number;
// this is the one place those two combine into something an actual
// renderer (Lightweight Charts' canvas-based series/price-line colors,
// or a plain CSS style) can use. Never store the combined rgba() string
// in config itself — always the two source fields, combined here only at
// the point of rendering.
export function hexWithOpacity(hex: string, opacityPct: number): string {
  const clean = hex.replace("#", "");
  const full = clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  const a = Math.max(0, Math.min(100, opacityPct)) / 100;
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}
